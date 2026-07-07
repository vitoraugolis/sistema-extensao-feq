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


def brain_deterministico(ont, lead, mensagem_lead):
    """Cérebro mínimo, offline. Substituível por um cérebro LLM (mesmo contrato)."""
    cod = lead["curso"]
    if G.detectar_optout(mensagem_lead):
        return C.DecisaoAgente(C.OPTOUT, "lead pediu opt-out",
                               mensagem_ao_lead="Tudo bem! Não vou mais enviar mensagens. Se mudar de ideia, é só chamar.")
    if G.gatilho_sensivel(mensagem_lead):
        return C.DecisaoAgente(C.ESCALAR, "gatilho sensível",
                               escalonamento_motivo="desconto/reclamação/intenção de fechar")
    cap = ont.captavel(cod)
    if not cap.get("captavel"):
        return C.DecisaoAgente(C.ESCALAR, f"curso {cod} não captável ({cap.get('status')})",
                               escalonamento_motivo=f"captabilidade: {cap.get('status')}")
    esc = _inferir_escolaridade(mensagem_lead) or lead.get("escolaridade")
    if esc:
        eleg = ont.elegivel(esc, cod)
        if eleg is False:
            return C.DecisaoAgente(C.DESCARTAR, "inelegível (gate acadêmico)",
                                   mensagem_ao_lead="Obrigado pelo interesse! Este curso exige ensino superior completo, então não seria possível agora. Posso te avisar sobre opções futuras?",
                                   dados={"escolaridade": esc})
        if eleg is True:
            c = ont.curso(cod)
            p = c["plano"]
            msg = (f"Que ótimo! O {cod} são {p['n']}x de R$ {_fmt_ptbr(p['valor_parcela'])}, "
                   f"ou R$ {_fmt_ptbr(p['a_vista'])} à vista. Posso te enviar o link de inscrição?")
            return C.DecisaoAgente(C.QUALIFICAR, "elegível + interesse",
                                   mensagem_ao_lead=msg, dados={"escolaridade": esc})
    return C.DecisaoAgente(C.CONTINUAR, "coletando escolaridade",
                           mensagem_ao_lead="Perfeito! Para confirmar: você já concluiu o ensino superior? É o requisito deste curso.")


class Orquestrador:
    def __init__(self, estado=None, ont=None, brain=None):
        self.est = estado or Estado()
        self.ont = ont or Ontologia()
        self.brain = brain or (lambda lead, msg, hist=None: brain_deterministico(self.ont, lead, msg))

    def processar_mensagem(self, card_id, mensagem_lead):
        lead = self.est.get_lead(card_id)
        if not lead:
            raise ValueError(f"lead {card_id} não existe")
        if lead.get("optout"):
            return {"ignorado": "lead em opt-out"}
        historico = self.est.historico(card_id)   # antes de gravar a msg atual
        self.est.add_conversa(card_id, "lead", mensagem_lead)

        dec = self.brain(lead, mensagem_lead, historico)
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
        campos["estado_conversa"] = C.ESTADO_POR_ACAO.get(acao, "aguardando_resposta")
        if acao == C.OPTOUT:
            campos["optout"] = 1
        elif acao == C.ESCALAR:
            campos["motivo"] = dec.escalonamento_motivo or dec.motivo
        elif acao in C.FASE_POR_ACAO:
            campos["fase"] = C.FASE_POR_ACAO[acao]
            if acao == C.DESCARTAR:
                campos["motivo"] = dec.motivo
        self.est.set_lead(card_id, **campos)

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
