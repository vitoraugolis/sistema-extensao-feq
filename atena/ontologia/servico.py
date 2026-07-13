#!/usr/bin/env python3
"""
Atena · Serviço de Ontologia
============================
Porta de consulta ao grafo (catálogo + vocabulário). É a ÚNICA fonte de
grounding dos agentes e do Revisor — ninguém consulta o grafo direto.

Expõe, com proveniência implícita no próprio grafo:
  • curso(codigo)            -> fatos do curso (dict)
  • captavel(codigo, hoje)   -> {status: futura|aberta|encerrada, captavel: bool}
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
            "tipo": "Especialização (lato sensu)" if FEQ.Especializacao in tipos else "Curso de Extensão",
            "modalidade": str(self.g.value(u, FEQ.modalidade) or ""),
            "horario": str(self.g.value(u, FEQ.horarioAula) or ""),
            "professor": str(self.g.value(u, FEQ.professorResponsavel) or ""),
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
            "aulas_inicio": ini_o.isoformat() if ini_o else None,
            "aulas_fim": fim_o.isoformat() if fim_o else None,
            "url_extecamp": str(self.g.value(u, FEQ.urlExtecamp) or ""),
            "objetivo": str(self.g.value(u, FEQ.objetivo) or "") or None,
            "conteudo_programatico": str(self.g.value(u, FEQ.conteudoProgramatico) or "") or None,
            "insight_venda": str(self.g.value(u, FEQ.insightVenda) or "") or None,
            "alerta": str(self.g.value(u, FEQ.alertaCatalogo) or "") or None,
        }

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
        if ini and hoje < ini:
            status = "futura"
        elif fim and hoje > fim:
            status = "encerrada"
        else:
            status = "aberta"
        return {
            "curso": codigo, "existe": True, "status": status,
            "captavel": status == "aberta",
            "inicio": ini.isoformat() if ini else None,
            "fim": fim.isoformat() if fim else None,
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
        }


if __name__ == "__main__":
    o = Ontologia()
    hoje = datetime.date.today()
    print(f"Ontologia carregada · {len(o.cursos())} cursos · hoje {hoje:%d/%m/%Y}")
    for c in o.cursos():
        cap = o.captavel(c, hoje)
        print(f"  {c}: {cap['status']:9} | elegível(médio)? {o.elegivel('Ensino Médio Completo', c)}")
