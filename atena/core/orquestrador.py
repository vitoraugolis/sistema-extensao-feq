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


ESC_RE = re.compile(r"(superior incompleto|incompleto|p[óo]s|mestrad|doutorad|superior|graduad|gradua[çc]|diploma|m[ée]dio|fundamental)", re.I)

# Regra de ouro (anti-alucinação): mensagem de espera FIXA — nunca redigida pelo LLM.
MSG_COORDENACAO = ("Certo! Essa informação eu não tenho de pronto para você. "
                   "Um momento, por favor — vou consultar a coordenação e já retorno! 😊")


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
        return ("Desculpe insistir! 🙏 É que essa informação define os próximos passos: "
                f"você já concluiu {alvo}?")
    return ("Que ótimo seu interesse! 😊 Para eu te orientar da melhor forma, só preciso "
            f"confirmar uma coisa: você já concluiu {alvo}? É o pré-requisito deste curso.")


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
            msg = ("Oi! Que bom falar com você 😊 Sou o Vinícius, da Extensão FEQ Unicamp. "
                   "Sobre qual curso você quer informações? "
                   + (f"Alguns com inscrições abertas agora: {lista}. " if lista else "")
                   + "Se estiver em dúvida, me conta sua formação e o que você quer "
                     "melhorar que eu te indico o mais adequado!")
            return C.DecisaoAgente(C.CONTINUAR, "lead sem curso definido — descoberta",
                                   mensagem_ao_lead=msg)
    cap = ont.captavel(cod, hoje)
    if not cap.get("captavel"):
        return C.DecisaoAgente(C.ESCALAR, f"curso {cod} não captável ({cap.get('status')})",
                               escalonamento_motivo=f"captabilidade: {cap.get('status')}")
    esc = _inferir_escolaridade(mensagem_lead) or lead.get("escolaridade")
    # memória mínima: "sim/tenho" logo após a pergunta do gate confirma o nível QUE
    # FOI PERGUNTADO (varia por curso — não assumir superior).
    ultima_agente = next((h["texto"] for h in reversed(historico or []) if h.get("papel") == "agente"), "")
    nivel_curso = ((ont.curso(cod) or {}).get("requisito")) or "superiorCompleto"
    if not esc and AFIRMA_RE.search(mensagem_lead or "") and (
            "ensino superior" in ultima_agente.lower() or "ensino médio" in ultima_agente.lower()
            or "pós-graduação" in ultima_agente.lower()):
        esc = {"medio": "Ensino Médio Completo", "superiorIncompleto": "Superior Incompleto",
               "superiorCompleto": "Superior Completo", "pos": "Pós-graduação"}[nivel_curso]
    if esc:
        eleg = ont.elegivel(esc, cod)
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
            if quer_link and c.get("url_extecamp"):
                prazo = "/".join(reversed((c.get("inscricoes_fim") or "").split("-")))
                msg = (f"Perfeito! 🎉 Aqui está o link de inscrição do {cod}: "
                       f"{c['url_extecamp']} — as inscrições vão até {prazo}. "
                       f"Qualquer dúvida no preenchimento, me chama por aqui!")
                return C.DecisaoAgente(C.QUALIFICAR, "elegível — link de inscrição enviado",
                                       mensagem_ao_lead=msg, dados={"escolaridade": esc})
            p = c["plano"]
            msg = (f"Que ótimo! O {cod} são {p['n']}x de R$ {_fmt_ptbr(p['valor_parcela'])}, "
                   f"ou R$ {_fmt_ptbr(p['a_vista'])} à vista. Posso te enviar o link de inscrição?")
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

    def processar_mensagem(self, card_id, mensagem_lead, tipo="texto", media_id=None):
        lead = self.est.get_lead(card_id)
        if not lead:
            raise ValueError(f"lead {card_id} não existe")
        if lead.get("optout"):
            return {"ignorado": "lead em opt-out"}
        historico = self.est.historico(card_id)   # antes de gravar a msg atual
        self.est.add_conversa(card_id, "lead", mensagem_lead, tipo=tipo, media_id=media_id)

        # mídia sem legenda: o cérebro não "ouve"/"vê" -> recebe um rótulo p/ não misfire
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
                                  mensagem_ao_lead="Boa pergunta! Vou pedir para alguém da equipe te responder pessoalmente — só um instante! 😊",
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
        self.est.add_decisao(card_id, agente="orquestrador", acao=acao,
                             mensagem=dec.mensagem_ao_lead, motivo=dec.motivo,
                             ancoras=rev.get("ancoras", []),
                             aprovado_revisor=1 if rev["aprovado"] else 0,
                             bloqueios=rev.get("bloqueios", []), status=status)

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
