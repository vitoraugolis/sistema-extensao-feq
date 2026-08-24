#!/usr/bin/env python3
"""
Atena · Orquestrador (máquina de estados).
Recebe a mensagem do lead, monta o contexto ontológico, delega ao cérebro,
passa a saída pelo Revisor (fail-closed) e pelos guardrails, e aplica o efeito
no estado — registrando o trace. NUNCA envia sozinho: a mensagem aprovada vira
PROPOSTA para aprovação humana (envio = tier externa/irreversível).
"""
import re
import sys
import pathlib
import threading

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from atena.ontologia.servico import Ontologia
from atena.revisor.ontologico import revisar
from atena.core.estado import Estado
from atena.core.faro import Faro
from atena.core import contratos as C
from atena.core import guardrails as G


def _fmt_ptbr(dec_str):
    """'16455.75' -> '16.455,75'."""
    if not dec_str:
        return ""
    inteiro, _, dec = str(dec_str).partition(".")
    inteiro = f"{int(inteiro):,}".replace(",", ".")
    return f"{inteiro},{(dec or '00')[:2]:0<2}"


# Uma conversa por vez, POR LEAD. O webhook trata cada mensagem numa thread; quando o
# lead manda duas seguidas (caso real: 2 falas no mesmo minuto), as duas liam o histórico
# ANTES de qualquer resposta ser gravada -> o cérebro respondia a mesma coisa duas vezes e
# a trava anti-loop não via, porque comparava com um histórico já vencido.
# ponytail: dict de locks por card; some só no restart — 1 lock por lead é irrelevante aqui.
_LOCKS = {}
_LOCKS_GUARD = threading.Lock()


def _lock_do_lead(card_id):
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(card_id, threading.Lock())


ESC_RE = re.compile(r"(superior incompleto|incompleto|p[óo]s|mestrad|doutorad|superior|graduad|gradua[çc]|diploma|m[ée]dio|fundamental)", re.I)

# Regra de ouro (anti-alucinação): mensagem de espera FIXA — nunca redigida pelo LLM.
MSG_COORDENACAO = ("Essa informação eu não tenho de pronto. "
                   "Vou confirmar com a coordenação e já te retorno.")


def _inferir_escolaridade(texto):
    m = ESC_RE.search(texto or "")
    return m.group(0) if m else None


AFIRMA_RE = re.compile(
    r"^\s*(sim|s[,.!\s]|claro|tenho|possuo|com certeza|isso|j[áa]\b|conclu[íi]"
    r"|pode\b|manda|envia|beleza|ok\b|quero|por favor)", re.I)
# O requisito NÃO é o mesmo em todo o catálogo (FEQ-0299 e FEQ-0670 pedem só ensino
# médio): perguntar "concluiu o ensino superior?" para esses faria o lead elegível
# se autoexcluir. O texto sai do nivelMinimo do curso.
_NIVEL_PERGUNTA = {"medio": "o ensino médio", "superiorIncompleto": "ou está cursando o ensino superior",
                   "superiorCompleto": "o ensino superior", "pos": "uma pós-graduação"}


def _msg_gate(ont, cod, insistindo=False):
    nivel = ((ont.curso(cod) or {}).get("requisito")) or "superiorCompleto"
    alvo = _NIVEL_PERGUNTA.get(nivel, "o ensino superior")
    if insistindo:
        return (f"Só pra confirmar antes de seguir: você já concluiu {alvo}? "
                "É o que define os próximos passos.")
    return (f"Antes de te orientar, preciso confirmar uma coisa: você já concluiu {alvo}? "
            "É o pré-requisito deste curso.")


def brain_deterministico(ont, lead, mensagem_lead, historico=None, hoje=None):
    """Cérebro mínimo, offline. Substituível por um cérebro LLM (mesmo contrato).
    hoje: injetável p/ teste (a captabilidade depende do calendário real)."""
    dec = _brain_deterministico(ont, lead, mensagem_lead, historico, hoje)
    # Curso descoberto na mensagem (lead sem curso no card) tem de PERSISTIR, senão
    # a próxima mensagem volta à estaca zero.
    if dec.dados.get("curso") is None and not lead.get("curso"):
        cod = next((f"FEQ-{m}" for m in re.findall(r"FEQ[-\s]?(\d{3,4})", mensagem_lead or "", re.I)
                    if ont.curso(f"FEQ-{m}")), None)
        if cod:
            dec.dados["curso"] = cod
    return dec


def _brain_deterministico(ont, lead, mensagem_lead, historico=None, hoje=None):
    cod = lead["curso"]
    if G.detectar_optout(mensagem_lead):
        return C.DecisaoAgente(C.OPTOUT, "lead pediu opt-out",
                               mensagem_ao_lead="Tudo bem! Não vou mais enviar mensagens. Se mudar de ideia, é só chamar.")
    # transparência: perguntou se é robô -> assume que é IA e oferece uma pessoa
    if G.pergunta_se_e_robo(mensagem_lead) and not G.quer_humano(mensagem_lead):
        return C.DecisaoAgente(C.CONTINUAR, "lead perguntou se é robô — resposta transparente",
                               mensagem_ao_lead="Sou sim — um agente de inteligência artificial da "
                                                "Extensão FEQ Unicamp. Consigo te ajudar com "
                                                "informações dos cursos e com a inscrição. Se preferir "
                                                "falar com uma pessoa do time, é só me dizer que eu repasso.")
    if G.quer_humano(mensagem_lead):
        return C.DecisaoAgente(C.ESCALAR, "lead pediu atendimento humano",
                               mensagem_ao_lead="Claro, vou repassar para uma pessoa do time falar "
                                                "com você. Só um momento.",
                               escalonamento_motivo="lead pediu atendimento humano")
    if G.gatilho_sensivel(mensagem_lead):
        return C.DecisaoAgente(C.ESCALAR, "gatilho sensível",
                               escalonamento_motivo="desconto/reclamação/intenção de fechar")
    # Sem curso definido (lead do botão de WhatsApp da LP que não citou curso):
    # DESCOBERTA — pergunta qual curso em vez de escalar. Se a mensagem citar um
    # código válido, adota-o e segue o fluxo normal.
    if not cod or not ont.curso(cod):
        citado = next((f"FEQ-{m}" for m in re.findall(r"FEQ[-\s]?(\d{3,4})", mensagem_lead or "", re.I)
                       if ont.curso(f"FEQ-{m}")), None)
        if citado:
            cod = citado
        else:
            abertos = [c for c in ont.portfolio() if c.get("inscricao") == "aberta"][:3]
            lista = "; ".join(f"{c['codigo']} ({c['nome']})" for c in abertos)
            msg = ("Oi! Sou o Vinícius, da Extensão FEQ Unicamp. "
                   "Sobre qual curso você quer informações? "
                   + (f"Com inscrições abertas agora: {lista}. " if lista else "")
                   + "Se estiver em dúvida, me conta sua formação e o que quer "
                     "melhorar que eu indico o mais adequado.")
            return C.DecisaoAgente(C.CONTINUAR, "lead sem curso definido — descoberta",
                                   mensagem_ao_lead=msg)
    cap = ont.captavel(cod, hoje)
    if cap.get("recuperavel"):
        # prazo vencido com a turma ainda por começar: escala para um humano gerar o link
        # fora do prazo, mas JÁ inicia o roteiro. Antes o lead ficava sem resposta até
        # alguém abrir a Central — e a vaga que ainda existia esfriava.
        return C.DecisaoAgente(
            C.ESCALAR, f"curso {cod} fora do prazo — vaga remanescente",
            escalonamento_motivo="gerar link de inscrição fora do prazo (pedir e-mail ao lead)",
            mensagem_ao_lead=(
                "Oi! O prazo oficial de inscrição deste curso já encerrou, mas a turma ainda "
                "não começou e costumam sobrar vagas remanescentes — a coordenação consegue "
                "gerar um link de inscrição fora do prazo para você. Me manda seu e-mail que "
                "eu peço a geração do link; ele vale 24 horas."))
    if not cap.get("captavel"):
        return C.DecisaoAgente(C.ESCALAR, f"curso {cod} não captável ({cap.get('status')})",
                               escalonamento_motivo=f"captabilidade: {cap.get('status')}")
    esc = _inferir_escolaridade(mensagem_lead) or lead.get("escolaridade")
    # memória mínima: "sim/tenho" logo após a pergunta do gate confirma o nível QUE
    # FOI PERGUNTADO (varia por curso — não assumir superior).
    ultima_agente = next((h["texto"] for h in reversed(historico or []) if h.get("papel") == "agente"), "")
    nivel_curso = ((ont.curso(cod) or {}).get("requisito")) or "superiorCompleto"
    # Curso declarado pela Extecamp SEM escolaridade mínima (feq:nenhum — ex.: o
    # FEQ-1150, difusão de oferecimento contínuo): não existe gate a passar. Perguntar
    # "você concluiu o ensino superior?" a quem já é elegível é o convite à
    # autoexclusão que fez a pergunta virar por-curso (ver _NIVEL_PERGUNTA). Segue
    # direto para preço/link, que é o que o lead veio buscar.
    sem_gate = nivel_curso == "nenhum"
    if not esc and AFIRMA_RE.search(mensagem_lead or "") and (
            "ensino superior" in ultima_agente.lower() or "ensino médio" in ultima_agente.lower()
            or "pós-graduação" in ultima_agente.lower()):
        # .get(): nível fora do mapa (nenhum) cairia em KeyError e derrubaria a conversa
        esc = {"medio": "Ensino Médio Completo", "superiorIncompleto": "Superior Incompleto",
               "superiorCompleto": "Superior Completo", "pos": "Pós-graduação"}.get(nivel_curso)
    if esc or sem_gate:
        eleg = True if sem_gate else ont.elegivel(esc, cod)
        if eleg is False:
            exigido = _NIVEL_PERGUNTA.get(nivel_curso, "o ensino superior")
            return C.DecisaoAgente(C.DESCARTAR, "inelegível (gate acadêmico)",
                                   mensagem_ao_lead=f"Obrigado pelo interesse! Este curso exige {exigido} concluído, "
                                                    "então não seria possível agora. Posso te avisar sobre opções futuras?",
                                   dados={"escolaridade": esc})
        if eleg is True:
            c = ont.curso(cod)
            # lead pediu o link (ou confirmou a oferta de link) -> ENVIA o link
            quer_link = (re.search(r"\blink\b|inscri", mensagem_lead or "", re.I) or
                         ("link de inscrição" in ultima_agente.lower()
                          and AFIRMA_RE.search(mensagem_lead or "")))
            # CAPTAÇÃO ANTECIPADA: captamos hoje, mas a Extecamp só recebe esta turma a
            # partir de cap["inicio"]. Mandar a URL agora joga o lead numa página que
            # não aceita a inscrição dele — ele conclui que o link está quebrado (ou
            # que a vaga acabou) e some. Aqui o agente assume o passo: registra e avisa.
            if cap.get("antecipada"):
                abre = "/".join(reversed((cap.get("inicio") or "").split("-")))
                if quer_link:
                    msg = (f"Sua vaga no {cod} já pode ser garantida agora — eu mesmo registro "
                           "a sua inscrição por aqui. "
                           + (f"O sistema oficial da Unicamp (Extecamp) abre para esta turma em "
                              f"{abre}, e a Extensão FEQ conclui o seu cadastro lá nesse dia, "
                              "te avisando por e-mail. " if abre else "")
                           + "Posso seguir com o seu registro?")
                    return C.DecisaoAgente(C.QUALIFICAR, "elegível — captação antecipada (sem link Extecamp)",
                                           mensagem_ao_lead=msg, dados={"escolaridade": esc})
            elif quer_link and c.get("url_extecamp"):
                prazo = "/".join(reversed((c.get("inscricoes_fim") or "").split("-")))
                msg = (f"Aqui está o link de inscrição do {cod}: "
                       f"{c['url_extecamp']} — as inscrições vão até {prazo}. "
                       f"Qualquer dúvida no preenchimento, é só me chamar.")
                return C.DecisaoAgente(C.QUALIFICAR, "elegível — link de inscrição enviado",
                                       mensagem_ao_lead=msg, dados={"escolaridade": esc})
            p = c["plano"]
            oferta = ("Quer que eu registre a sua inscrição?" if cap.get("antecipada")
                      else "Posso te enviar o link de inscrição?")
            msg = (f"O {cod} são {p['n']}x de R$ {_fmt_ptbr(p['valor_parcela'])}, "
                   f"ou R$ {_fmt_ptbr(p['a_vista'])} à vista. {oferta}")
            return C.DecisaoAgente(C.QUALIFICAR, "elegível + interesse",
                                   mensagem_ao_lead=msg, dados={"escolaridade": esc})
    # anti-repetição: se a última mensagem do agente já foi o gate, varia a frase
    ja_perguntou = "pré-requisito deste curso" in ultima_agente or "já concluiu" in ultima_agente
    return C.DecisaoAgente(C.CONTINUAR, "coletando escolaridade",
                           mensagem_ao_lead=_msg_gate(ont, cod, insistindo=ja_perguntou))


class Orquestrador:
    def __init__(self, estado=None, ont=None, brain=None, faro=None):
        self.est = estado or Estado()
        self.ont = ont or Ontologia()
        self.faro = faro or Faro()
        self.brain = brain or (lambda lead, msg, hist=None: brain_deterministico(self.ont, lead, msg, hist))

    def processar_mensagem(self, card_id, mensagem_lead, tipo="texto", media_id=None,
                           transcricao=None):
        """Serializa por lead: a 2ª mensagem só é tratada depois que a 1ª virou resposta
        gravada, então o cérebro sempre vê o histórico completo (ver _lock_do_lead)."""
        with _lock_do_lead(card_id):
            return self._processar_mensagem(card_id, mensagem_lead, tipo, media_id, transcricao)

    def _processar_mensagem(self, card_id, mensagem_lead, tipo="texto", media_id=None,
                            transcricao=None):
        lead = self.est.get_lead(card_id)
        if not lead:
            raise ValueError(f"lead {card_id} não existe")
        if lead.get("optout"):
            return {"ignorado": "lead em opt-out"}
        historico = self.est.historico(card_id)   # antes de gravar a msg atual
        # O que o agente acabou de responder ainda é PROPOSTA (só entra no histórico quando
        # é aprovada/enviada). Sem isto o cérebro não enxerga a própria fala anterior e
        # repete a mesma resposta para a 2ª mensagem do lead — foi o que apareceu no print.
        pend = self.est.decisao_pendente(card_id)
        if pend and (pend.get("mensagem") or "").strip():
            historico = historico + [{"papel": "agente", "texto": pend["mensagem"],
                                      "ts": pend.get("ts")}]
        self.est.add_conversa(card_id, "lead", mensagem_lead, tipo=tipo, media_id=media_id,
                              transcricao=transcricao)

        # Modo manual (por conversa): o humano conduz. Registramos a mensagem do lead
        # (o operador precisa vê-la) mas NÃO acionamos o cérebro nem geramos proposta.
        # Um alerta avisa que há entrada aguardando resposta manual.
        if (lead.get("modo") or "auto") == "manual":
            self.est.set_lead(card_id, estado_conversa="manual_humano")
            self.est.add_alerta("manual", f"Lead {card_id} em modo manual: nova mensagem aguardando resposta humana")
            return {"modo_manual": True}

        # mídia sem legenda: o cérebro não "ouve"/"vê" -> recebe um rótulo p/ não misfire.
        # 'contatos' vem SEMPRE primeiro e ignora o texto: ali "mensagem_lead" é o JSON
        # estruturado que a Central usa para desenhar o cartão (bolhaConteudo), não fala
        # do lead — mandar esse JSON cru ao LLM seria pior que um rótulo genérico.
        if tipo == "contatos":
            entrada = "[o lead compartilhou um contato]"
        elif transcricao and tipo == "audio":
            # A transcrição vai MARCADA como automática: Whisper erra nome, número e
            # valor, e o agente não pode repetir "você disse R$ 3.000" com a confiança
            # de quem leu texto digitado. O marcador segue o padrão já usado no projeto
            # para contexto de sistema ([INFORMAÇÃO DA COORDENAÇÃO], [GANCHO ...]).
            entrada = ("[áudio do lead, transcrito automaticamente — pode conter erros de "
                       f"transcrição] {transcricao}")
        elif transcricao and tipo in ("image", "sticker"):
            legenda = f' A legenda escrita pelo lead foi: "{mensagem_lead}"' if mensagem_lead else ""
            entrada = ("[imagem enviada pelo lead, descrita automaticamente — a descrição é "
                       f"de um leitor automático, não do lead] {transcricao}{legenda}")
        else:
            entrada = mensagem_lead or {
                "audio": "[o lead enviou um áudio]", "image": "[o lead enviou uma imagem]",
                "video": "[o lead enviou um vídeo]", "document": "[o lead enviou um documento]",
                "sticker": "[o lead enviou uma figurinha]"}.get(tipo, mensagem_lead)
        dec = self.brain(lead, entrada, historico)
        # Trava final de opt-out: só o LEAD pode se descadastrar, com as próprias
        # palavras. Qualquer cérebro (LLM ou futuro) que devolva OPTOUT sem isso é
        # rebaixado — a ação é irreversível e silencia o lead para sempre.
        if dec.proxima_acao == C.OPTOUT and not G.detectar_optout(mensagem_lead):
            self.est.log_acao("orquestrador",
                              f"opt-out do cérebro IGNORADO (lead não pediu): {dec.motivo!r}", card_id)
            dec = C.DecisaoAgente(C.CONTINUAR, dec.motivo, mensagem_ao_lead=dec.mensagem_ao_lead,
                                  dados=dec.dados)
        # anti-loop: repetir a MESMA mensagem é sinal de agente perdido -> humano assume
        ultima_ag = next((h["texto"] for h in reversed(historico) if h.get("papel") == "agente"), None)
        if dec.mensagem_ao_lead and dec.mensagem_ao_lead == ultima_ag:
            dec = C.DecisaoAgente(C.ESCALAR, "loop detectado (mensagem idêntica à anterior)",
                                  mensagem_ao_lead="Vou pedir para alguém da equipe te responder isso — só um instante.",
                                  escalonamento_motivo="agente repetiria a mesma resposta")
        if dec.proxima_acao == C.CONSULTAR:
            # regra de ouro: template fixo ao lead + dúvida na fila da coordenação
            dec.mensagem_ao_lead = MSG_COORDENACAO
            self.est.add_duvida(card_id, dec.dados.get("duvida") or dec.motivo or mensagem_lead,
                                contexto=mensagem_lead)
        rev = {"aprovado": True, "bloqueios": [], "ancoras": []}
        if dec.mensagem_ao_lead:
            rev = revisar(lead["curso"], dec.mensagem_ao_lead, self.ont)
            if not rev["aprovado"]:
                # loop de feedback: 1 tentativa; cérebro determinístico não corrige -> escala
                dec = C.DecisaoAgente(C.ESCALAR, "Revisor bloqueou a mensagem proposta",
                                      escalonamento_motivo="; ".join(rev["bloqueios"]),
                                      dados=dec.dados)
        self._aplicar(card_id, dec, rev)
        return {"decisao": dec.__dict__, "revisor": rev}

    def _aplicar(self, card_id, dec, rev):
        acao = dec.proxima_acao
        campos = {}
        if dec.dados.get("escolaridade"):
            campos["escolaridade"] = dec.dados["escolaridade"]
        novo_curso = dec.dados.get("curso")
        if novo_curso:
            info = self.ont.curso(novo_curso)   # só troca p/ curso que EXISTE no catálogo
            if info:
                campos["curso"] = info["codigo"]
                self.est.add_curso_interesse(card_id, info["codigo"])  # acumula (mantém o anterior)
        campos["estado_conversa"] = C.ESTADO_POR_ACAO.get(acao, "aguardando_resposta")
        if acao == C.OPTOUT:
            campos["optout"] = 1
        elif acao == C.ESCALAR:
            campos["motivo"] = dec.escalonamento_motivo or dec.motivo
            self.est.add_alerta("escalonamento",
                                f"Lead {card_id} escalado: {campos['motivo']}")
        elif acao in C.FASE_POR_ACAO:
            campos["fase"] = C.FASE_POR_ACAO[acao]
            if acao == C.DESCARTAR:
                campos["motivo"] = dec.motivo
        self.est.set_lead(card_id, **campos)
        # espelha no FARO (system of record) — best effort, nunca bloqueia a conversa
        if self.faro.ativo and campos.get("fase"):
            l = self.est.get_lead(card_id)
            if l and l.get("faro_id"):
                try:
                    self.faro.mover(l["faro_id"], campos["fase"])
                except Exception as e:
                    print(f"[faro] espelhamento falhou: {e}", flush=True)

        if dec.mensagem_ao_lead and rev["aprovado"]:
            status = "proposta"   # aguardando aprovação humana (envio = tier externa)
        elif dec.mensagem_ao_lead:
            status = "bloqueada"
        else:
            status = "acao"
        # guia em PDF só faz sentido junto de uma proposta de fato aprovada pelo Revisor
        anexo = "guia_curso" if (status == "proposta" and dec.dados.get("anexo_guia")) else None
        self.est.add_decisao(card_id, agente="orquestrador", acao=acao,
                             mensagem=dec.mensagem_ao_lead, motivo=dec.motivo,
                             ancoras=rev.get("ancoras", []),
                             aprovado_revisor=1 if rev["aprovado"] else 0,
                             bloqueios=rev.get("bloqueios", []), status=status, anexo=anexo)

    def responder_duvida(self, did, resposta):
        """Coordenação respondeu: o cérebro elabora a resposta ao lead, com a resposta
        da coordenação valendo como fonte extra de âncoras (proveniência humana)."""
        d = self.est.get_duvida(did)
        if not d or d["status"] != "aberta":
            return {"erro": "dúvida inexistente ou já tratada"}
        card_id = d["card_id"]
        lead = self.est.get_lead(card_id)
        nota = (f"[INFORMAÇÃO DA COORDENAÇÃO — fonte autorizada para esta resposta] "
                f"Sua consulta: '{d['pergunta']}'. Resposta da coordenação: {resposta}. "
                f"Elabore AGORA a resposta ao lead usando essa informação (retome a conversa "
                f"com naturalidade; não mencione processos internos).")
        dec = self.brain(lead, nota, self.est.historico(card_id))
        msg = dec.mensagem_ao_lead or resposta
        rev = revisar(lead["curso"], msg, self.ont, fonte_extra=resposta)
        if not rev["aprovado"]:
            msg, rev = resposta, revisar(lead["curso"], resposta, self.ont, fonte_extra=resposta)
        self.est.responder_duvida(did, resposta)
        self.est.set_lead(card_id, estado_conversa="aguardando_resposta")
        self.est.add_decisao(card_id, agente="coordenacao", acao="responder_duvida",
                             mensagem=msg, motivo=f"dúvida #{did} respondida",
                             ancoras=rev.get("ancoras", []),
                             aprovado_revisor=1 if rev["aprovado"] else 0,
                             bloqueios=rev.get("bloqueios", []),
                             status="proposta" if rev["aprovado"] else "bloqueada")
        return {"card_id": card_id, "mensagem": msg, "revisor": rev}

    GANCHO_REATIVACAO = (
        "[GANCHO DE REATIVAÇÃO — o lead ficou em silêncio; NÃO chegou mensagem nova dele. "
        "Escreva UMA mensagem curta, calorosa e natural para retomar o contato: relembre o "
        "curso de interesse, ofereça ajuda com um próximo passo concreto (tirar dúvida, "
        "inscrição, prazo) e convide-o a responder. NÃO repita mensagens anteriores; NÃO "
        "invente preço/data/prazo. Se não houver o que dizer sem repetir, use em_espera com "
        "mensagem vazia.]")

    def propor_reativacao(self, card_id):
        """Nudge proativo (texto livre) na janela de 24h -> PROPOSTA, reusando cérebro+Revisor.
        Não loga mensagem falsa do lead. Retorna card_id se criou proposta aprovada, senão None."""
        lead = self.est.get_lead(card_id)
        if not lead or lead.get("optout"):
            return None
        if self.est.decisao_pendente(card_id):      # já há proposta esperando -> não empilha
            return None
        hist = self.est.historico(card_id)
        dec = self.brain(lead, self.GANCHO_REATIVACAO, hist)
        msg = (dec.mensagem_ao_lead or "").strip()
        ultima_ag = next((h["texto"] for h in reversed(hist) if h.get("papel") == "agente"), None)
        if not msg or msg == ultima_ag:             # nada a dizer / repetiria -> desiste
            return None
        rev = revisar(lead["curso"], msg, self.ont)
        self.est.add_decisao(card_id, agente="reativacao", acao=C.CONTINUAR, mensagem=msg,
                             motivo="reativação proativa (janela 24h)",
                             ancoras=rev.get("ancoras", []),
                             aprovado_revisor=1 if rev["aprovado"] else 0,
                             bloqueios=rev.get("bloqueios", []),
                             status="proposta" if rev["aprovado"] else "bloqueada")
        return card_id if rev["aprovado"] else None
