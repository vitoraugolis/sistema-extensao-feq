#!/usr/bin/env python3
"""
Atena · Serviço de Ontologia
============================
Porta de consulta ao grafo (catálogo + vocabulário). É a ÚNICA fonte de
grounding dos agentes e do Revisor — ninguém consulta o grafo direto.

Expõe, com proveniência implícita no próprio grafo:
  • curso(codigo)            -> fatos do curso (dict)
  • captavel(codigo, hoje)   -> {status: futura|antecipada|aberta|fora_do_prazo|encerrada, captavel: bool}
  • elegivel(nivel, codigo)  -> bool (comparação ORDINAL, não string)
  • ancoras(codigo)          -> conjunto de âncoras factuais para o Revisor

Sem rede. stdlib + rdflib.
"""
import re
import datetime
from pathlib import Path

from rdflib import Graph, Namespace
from rdflib.namespace import RDF

AQUI = Path(__file__).parent
FEQ = Namespace("https://feq.unicamp.br/ontologia#")

# Prefixos de instituto no catálogo — FEQ (Extensão FEQ) e ECO (Instituto de
# Economia, ver ECO-1100). Única fonte do padrão: ingestão, orquestrador,
# Bibliotecário, Ontologista e Revisor importam daqui em vez de cada um
# hardcodar o próprio regex — foi assim que ECO ficou invisível a todos eles
# de uma vez até este módulo existir. Novo instituto: acrescente o prefixo aqui.
CODE_RE = re.compile(r"(FEQ|ECO)[-_\s]?(\d{3,4})", re.I)


def normalizar_codigo(prefixo, numero):
    """'feq', '331' -> 'FEQ-0331' — catálogo usa 4 dígitos, prefixo maiúsculo."""
    return f"{prefixo.upper()}-{numero.zfill(4)}"


# Identidade institucional por prefixo — o agente se apresenta como "da Extensão FEQ
# Unicamp" só quando o curso é mesmo da FEQ. Sem isso, um lead do ECO-1100 (Instituto
# de Economia) recebia identidade e contato errados (ver specs/08 § "Cursos fora da FEQ").
# Com a preposição já embutida (concordância: "da FEQ" vs "do Instituto de Economia").
_INSTITUICOES = {"FEQ": "da Extensão FEQ Unicamp", "ECO": "do Instituto de Economia (IE) Unicamp"}


def instituicao_do_codigo(codigo):
    """Prefixo do código -> 'da/do <instituto>' (com preposição), p/ compor a frase
    "atendente {instituicao_do_codigo(cod)}" direto, sem repetir a concordância."""
    return _INSTITUICOES.get((codigo or "").split("-")[0].upper(), _INSTITUICOES["FEQ"])


def _dig(x):
    return re.sub(r"\D", "", str(x)) if x is not None else ""


def nivel_de(texto):
    """Mapeia uma escolaridade declarada (FARO/lead) ou nome local ao nível ordinal."""
    t = (texto or "").lower().strip()
    canon = {"medio": "medio", "superiorincompleto": "superiorIncompleto",
             "superiorcompleto": "superiorCompleto", "pos": "pos"}
    if t in canon:
        return canon[t]
    if "incompleto" in t:
        return "superiorIncompleto"
    if "pós" in t or "mestr" in t or "doutor" in t:
        return "pos"
    if "superior" in t or "graduad" in t or "graduaç" in t or "diploma" in t:
        return "superiorCompleto"
    if "médio" in t or "medio" in t or "fundamental" in t:
        return "medio"
    return "medio"


class Ontologia:
    def __init__(self, catalogo=None, vocabulario=None):
        self.g = Graph()
        self.g.parse(vocabulario or AQUI / "vocabulario.ttl", format="turtle")
        self.g.parse(catalogo or AQUI / "catalogo.jsonld", format="json-ld")
        self._idx = {}
        for s in self.g.subjects(FEQ.codigo, None):
            cod = str(self.g.value(s, FEQ.codigo))
            self._idx[_dig(cod)] = s

    # ------------------------------------------------------------------ helpers
    def _uri(self, codigo):
        return self._idx.get(_dig(codigo))

    def _val(self, s, p):
        v = self.g.value(s, p)
        return v

    def cursos(self):
        return sorted(str(self.g.value(u, FEQ.codigo)) for u in self._idx.values())

    def ordem_nivel(self, nivel_local):
        v = self.g.value(FEQ[nivel_local], FEQ.ordem)
        return int(v) if v is not None else None

    def _janela(self, u, prop):
        j = self.g.value(u, prop)
        if not j:
            return None, None
        ini = self.g.value(j, FEQ.inicio)
        fim = self.g.value(j, FEQ.fim)
        return (ini.toPython() if ini else None, fim.toPython() if fim else None)

    def _urls_ancora(self, u):
        """Links legítimos do curso, normalizados — o Revisor aprova só estes."""
        raw = str(self.g.value(u, FEQ.urlExtecamp) or "")
        if not raw:
            return set()
        base = raw.strip().lower().rstrip("/")
        base = re.sub(r"^https?://(www\.)?", "", base)
        return {base.replace("/curso/dados/", "/curso/inscricao/")}

    def politica(self, nome):
        """Texto de uma política comercial global (nó feq:Politica no catálogo).
        Fica na ontologia, e não no código, para o agente só afirmar o que está ancorado
        — e para a coordenação poder mudar a regra pelo Injetor."""
        s = FEQ[nome]
        txt = self.g.value(s, FEQ.texto)
        return str(txt) if txt is not None else None

    def promocao(self, codigo, hoje=None):
        """Promoção VIGENTE do curso, ou None. A filtragem por data é aqui, e não em
        quem chama, porque promoção fora da janela não pode chegar a lead nenhum: o
        preço não existe mais na Extecamp e o Revisor deixaria passar (é âncora)."""
        u = self._uri(codigo)
        if not u:
            return None
        promo = self.g.value(u, FEQ.promocao)
        if not promo:
            return None
        hoje = hoje or datetime.date.today()
        ini, fim = self._janela(promo, FEQ.janela)
        if (ini and hoje < ini) or (fim and hoje > fim):
            return None
        ofertas = []
        lst = self.g.value(promo, FEQ.ofertas)
        for o in (self.g.items(lst) if lst else []):
            n = self.g.value(o, FEQ.numParcelas)
            pct = self.g.value(o, FEQ.percentualDesconto)
            ofertas.append({
                "rotulo": str(self.g.value(o, FEQ.rotulo) or ""),
                "condicao": str(self.g.value(o, FEQ.condicao) or ""),
                "n": int(n) if n is not None else None,
                "valor_parcela": str(self.g.value(o, FEQ.valorParcela) or "") or None,
                "a_vista": str(self.g.value(o, FEQ.aVista) or "") or None,
                "desconto_pct": int(pct) if pct is not None else None,
            })
        return {"rotulo": str(self.g.value(promo, FEQ.rotulo) or ""),
                # como o desconto chega ao aluno na prática. Sem isto o lead vê o valor
                # cheio no formulário da Extecamp e acha que foi enganado — foi o que o
                # operador precisou explicar à mão em 06/08.
                "como_aplicar": str(self.g.value(promo, FEQ.comoAplicar) or "") or None,
                "inicio": ini.isoformat() if ini else None,
                "fim": fim.isoformat() if fim else None,
                "ofertas": ofertas}

    # ------------------------------------------------------------------ público
    def curso(self, codigo):
        u = self._uri(codigo)
        if not u:
            return None
        carga = self.g.value(u, FEQ.cargaHoraria)
        horas = None
        if carga is not None:
            h = self.g.value(carga, FEQ.horas)
            horas = int(h) if h is not None else None
        req = self.g.value(u, FEQ.requisito)
        nm = self.g.value(req, FEQ.nivelMinimo) if req else None
        plano = self.g.value(u, FEQ.planoPagamento)
        ini_i, fim_i = self._janela(u, FEQ.janelaInscricao)
        ini_o, fim_o = self._janela(u, FEQ.janelaOferecimento)
        tipos = set(self.g.objects(u, RDF.type))
        vmin, vmax = self.g.value(u, FEQ.vagasMin), self.g.value(u, FEQ.vagasMax)
        return {
            "codigo": str(self.g.value(u, FEQ.codigo)),
            "nome": str(self.g.value(u, FEQ.nome)),
            "tipo": ("Formação de Especialistas" if FEQ.Especializacao in tipos
                     else "Curso de Difusão" if FEQ.Difusao in tipos
                     else "Curso de Extensão"),
            "modalidade": str(self.g.value(u, FEQ.modalidade) or ""),
            "horario": str(self.g.value(u, FEQ.horarioAula) or ""),
            "formato_aulas_resumo": str(self.g.value(u, FEQ.formatoAulasResumo) or "") or None,
            "formato_aulas_detalhe": str(self.g.value(u, FEQ.formatoAulasDetalhe) or "") or None,
            "professor": str(self.g.value(u, FEQ.professorResponsavel) or ""),
            "professores_adicionais": [str(n) for n in self.g.items(
                self.g.value(u, FEQ.professoresAdicionais))] if self.g.value(u, FEQ.professoresAdicionais) else [],
            "requisito": str(nm).split("#")[-1] if nm else None,
            "carga_horas": horas,
            "publico_alvo": str(self.g.value(u, FEQ.publicoAlvo) or "") or None,
            "certificacao": str(self.g.value(u, FEQ.certificacao) or "") or None,
            "vagas": {"min": int(vmin) if vmin is not None else None,
                      "max": int(vmax) if vmax is not None else None},
            "plano": {
                "n": int(self.g.value(plano, FEQ.numParcelas)) if plano and self.g.value(plano, FEQ.numParcelas) is not None else None,
                "valor_parcela": str(self.g.value(plano, FEQ.valorParcela) or "") if plano else "",
                "a_vista": str(self.g.value(plano, FEQ.aVista) or "") if plano else "",
                "taxa": str(self.g.value(plano, FEQ.taxaInscricao) or "") if plano else "",
            },
            "inscricoes_fim": fim_i.isoformat() if fim_i else None,
            # Data do boleto da inscrição (o lead pergunta "até quando pago?"). Some
            # quando o curso não a declara — nunca é derivada do prazo de inscrição.
            "boleto_data": (str(self.g.value(u, FEQ.dataBoleto))
                            if self.g.value(u, FEQ.dataBoleto) else None),
            "aulas_inicio": ini_o.isoformat() if ini_o else None,
            "aulas_fim": fim_o.isoformat() if fim_o else None,
            "url_extecamp": str(self.g.value(u, FEQ.urlExtecamp) or ""),
            # Link PRONTO para mandar ao lead. Antes só existia a URL /curso/dados/ e o
            # agente tinha de convertê-la para /curso/inscricao/ — nessa transformação ele
            # errou a TURMA e 6 leads receberam FEQ-0480/001 em vez de /009. Nada de pedir
            # ao LLM o que uma substituição de string resolve.
            "url_inscricao": str(self.g.value(u, FEQ.urlExtecamp) or "").replace(
                "/curso/dados/", "/curso/inscricao/"),
            "objetivo": str(self.g.value(u, FEQ.objetivo) or "") or None,
            "conteudo_programatico": str(self.g.value(u, FEQ.conteudoProgramatico) or "") or None,
            "descontos": str(self.g.value(u, FEQ.descontos) or "") or None,
            "insight_venda": str(self.g.value(u, FEQ.insightVenda) or "") or None,
            "alerta": str(self.g.value(u, FEQ.alertaCatalogo) or "") or None,
            # Conteúdo rico da LP (spec 17; fonte: páginas oficiais preexistentes da
            # Extensão FEQ — importado literal, nunca redigido). Estruturas guardadas
            # como JSON-string no grafo; aqui voltam a objetos Python.
            "objetivos": self._json(u, FEQ.objetivosJson, []),
            "habilidades": self._json(u, FEQ.habilidadesJson, []),
            "programa_detalhe": self._json(u, FEQ.programaDetalheJson, []),
            "depoimentos": self._json(u, FEQ.depoimentosJson, []),
            "empresas": self._json(u, FEQ.empresasJson, []),
            "avaliacao": str(self.g.value(u, FEQ.avaliacaoTexto) or "") or None,
            "salario_ref": str(self.g.value(u, FEQ.salarioRef) or "") or None,
            "garantia_7d": str(self.g.value(u, FEQ.garantia7dias) or "") == "true",
            # "basica" | "completa" — qual ficha a Extecamp usa neste curso (vem da
            # proposta de oferecimento). Define quantos campos a etapa 2 pede: pedir
            # dado que a Extecamp não vai receber custa conversão e não serve a ninguém.
            "ficha_inscricao": str(self.g.value(u, FEQ.fichaInscricao) or "") or "completa",
            "captacao_antecipada": str(self.g.value(u, FEQ.captacaoAntecipada) or "") == "true",
            "plataforma": str(self.g.value(u, FEQ.plataformaAula) or "") or None,
            # Oferecimento CONTÍNUO (FEQ-1150): não há turma a formar nem data de início
            # — a Extecamp publica "Disponível na plataforma de X até Y" no lugar do
            # período de oferecimento. Quem escreve "as aulas começam em" precisa olhar isto.
            "continuo": str(self.g.value(u, FEQ.oferecimentoContinuo) or "") == "true",
            "promocao": self.promocao(codigo),
        }

    def _json(self, u, prop, default):
        v = self.g.value(u, prop)
        if v is None:
            return default
        try:
            import json as _json
            return _json.loads(str(v))
        except ValueError:
            return default

    def portfolio(self, hoje=None):
        """Visão resumida de TODOS os cursos — o que o agente sabe que existe."""
        out = []
        for cod in self.cursos():
            c = self.curso(cod)
            cap = self.captavel(cod, hoje)
            out.append({"codigo": c["codigo"], "nome": c["nome"], "tipo": c["tipo"],
                        "inscricao": cap["status"], "a_vista": c["plano"]["a_vista"],
                        "alerta": c["alerta"]})
        return out

    def captavel(self, codigo, hoje=None):
        u = self._uri(codigo)
        if not u:
            return {"curso": codigo, "existe": False}
        hoje = hoje or datetime.date.today()
        ini, fim = self._janela(u, FEQ.janelaInscricao)
        aulas_ini, _ = self._janela(u, FEQ.janelaOferecimento)
        if ini and hoje < ini:
            # A janela da Extecamp ainda não abriu, MAS a coordenação autorizou captar
            # no nosso site (feq:captacaoAntecipada). Estado PRÓPRIO pelo mesmo motivo
            # de 'fora_do_prazo': tratar como 'futura' manda o lead para a lista de
            # espera e perde um inscrito que já era possível hoje. A janela no grafo
            # continua sendo a da Extecamp — é dela que sai o prazo citado ao lead.
            antecip = str(self.g.value(u, FEQ.captacaoAntecipada) or "") == "true"
            status = "antecipada" if antecip else "futura"
        elif fim and hoje > fim:
            # Prazo vencido mas a turma ainda não começou: sobram vagas remanescentes e a
            # coordenação consegue gerar link de inscrição fora do prazo. Estado PRÓPRIO —
            # tratar como "encerrada" faz o agente mandar o lead para a lista de espera e
            # perder uma matrícula que ainda era possível.
            status = "fora_do_prazo" if (aulas_ini and hoje < aulas_ini) else "encerrada"
        else:
            status = "aberta"
        return {
            "curso": codigo, "existe": True, "status": status,
            # segue False: inscrição pública fechada. Quem decide o que fazer com o
            # 'fora_do_prazo' é quem lê o status, não quem lê este booleano.
            "captavel": status in ("aberta", "antecipada"),
            "recuperavel": status == "fora_do_prazo",
            # captamos hoje, mas a Extecamp só abre em `inicio`: quem for MANDAR o link
            # da Extecamp tem de olhar isto, senão manda o lead para uma página que
            # ainda não aceita inscrição.
            "antecipada": status == "antecipada",
            "inicio": ini.isoformat() if ini else None,
            "fim": fim.isoformat() if fim else None,
            "aulas_inicio": aulas_ini.isoformat() if aulas_ini else None,
        }

    def elegivel(self, nivel_lead, codigo):
        u = self._uri(codigo)
        if not u:
            return None
        req = self.g.value(u, FEQ.requisito)
        nm = self.g.value(req, FEQ.nivelMinimo) if req else None
        if not nm:
            return None
        req_local = str(nm).split("#")[-1]
        lead_local = nivel_de(nivel_lead)
        ol = self.ordem_nivel(lead_local)
        orq = self.ordem_nivel(req_local)
        if ol is None or orq is None:
            return None
        return ol >= orq

    def ancoras(self, codigo):
        """Âncoras factuais que o Revisor pode aprovar para este curso."""
        u = self._uri(codigo)
        if not u:
            return None
        plano = self.g.value(u, FEQ.planoPagamento)
        taxa = self.g.value(plano, FEQ.taxaInscricao)
        vp = self.g.value(plano, FEQ.valorParcela)
        av = self.g.value(plano, FEQ.aVista)
        n = self.g.value(plano, FEQ.numParcelas)
        money = {_dig(taxa), _dig(vp), _dig(av)}
        planos = [{"n": int(n) if n is not None else None, "valor_digits": _dig(vp)}]
        # Promoção vigente entra como âncora: sem isso o Revisor bloquearia o próprio
        # preço promocional que a campanha manda o agente anunciar. Fora da janela ela
        # some daqui e volta a ser bloqueada — que é o comportamento correto.
        promo = self.promocao(codigo)
        for o in (promo or {}).get("ofertas", []):
            money |= {_dig(o.get("valor_parcela")), _dig(o.get("a_vista"))}
            if o.get("n") and o.get("valor_parcela"):
                planos.append({"n": o["n"], "valor_digits": _dig(o["valor_parcela"])})
                # total do parcelado: o agente pode citá-lo ("19x de 799, R$ 15.181 no total")
                money.add(_dig(f"{o['n'] * float(o['valor_parcela']):.2f}"))
        money.discard("")
        datas = set()
        for jp in (FEQ.janelaInscricao, FEQ.janelaOferecimento):
            j = self.g.value(u, jp)
            if not j:
                continue
            for dp in (FEQ.inicio, FEQ.fim):
                d = self.g.value(j, dp)
                if d:
                    datas.add(d.toPython().strftime("%Y%m%d"))
        carga = self.g.value(u, FEQ.cargaHoraria)
        horas = None
        if carga is not None:
            h = self.g.value(carga, FEQ.horas)
            horas = int(h) if h is not None else None
        return {
            "codigo": str(self.g.value(u, FEQ.codigo)),
            "money": money,
            "datas": datas,
            "carga_horas": horas,
            "plano": {"n": int(n) if n is not None else None, "valor_digits": _dig(vp), "av_digits": _dig(av)},
            "planos": planos,
            "urls": self._urls_ancora(u),
        }


if __name__ == "__main__":
    o = Ontologia()
    hoje = datetime.date.today()
    print(f"Ontologia carregada · {len(o.cursos())} cursos · hoje {hoje:%d/%m/%Y}")
    for c in o.cursos():
        cap = o.captavel(c, hoje)
        print(f"  {c}: {cap['status']:9} | elegível(médio)? {o.elegivel('Ensino Médio Completo', c)}")
