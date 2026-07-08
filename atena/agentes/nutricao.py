#!/usr/bin/env python3
"""
Atena · Nutrição/cadência (reengajamento de leads parados) — spec 01/roster #4.
Guardrails: só curso captável, sem opt-out, máx. 2 reengajamentos por lead,
somente após N dias de silêncio, via template aprovado feq_reengajamento.
"""
import datetime


def leads_para_reengajar(est, ont, agora=None, dias=3, max_reengajos=2):
    """Lista pura (testável) de leads elegíveis a reengajamento."""
    agora = agora or datetime.datetime.now()
    out = []
    for l in est.listar_leads():
        if l["fase"] not in ("Lead", "Prospect") or l.get("optout"):
            continue
        if l["estado_conversa"] not in ("aguardando_resposta", "aguardando_1o_contato"):
            continue
        if (l.get("reengajos") or 0) >= max_reengajos:
            continue
        if not ont.captavel(l["curso"]).get("captavel"):
            continue
        ts = est.ultima_lead_ts(l["card_id"]) or l["atualizado"] or l["criado"]
        try:
            dt = datetime.datetime.fromisoformat(ts)
        except (TypeError, ValueError):
            continue
        if agora - dt >= datetime.timedelta(days=dias):
            out.append(l)
    return out


def reengajar_um(est, ont, wa, card_id):
    """Reativação manual de UM lead (botão do kanban). Trava dura: opt-out e
    captabilidade. Sem o limite de 2× nem o silêncio de 3 dias — é ato deliberado
    do operador. Retorna o resultado do envio."""
    l = est.get_lead(card_id)
    if not l:
        return {"ok": False, "motivo": "lead inexistente"}
    if l.get("optout"):
        return {"ok": False, "motivo": "lead em opt-out"}
    if not ont.captavel(l["curso"]).get("captavel"):
        return {"ok": False, "motivo": "curso não captável (inscrições encerradas)"}
    c = ont.curso(l["curso"])
    prazo = "/".join(reversed((c.get("inscricoes_fim") or "").split("-")))
    r = wa.enviar_template(l["telefone"], "feq_reengajamento",
                           params=[l.get("nome") or "olá", f"{c['nome']} ({c['codigo']})", prazo])
    if r.get("ok"):
        est.set_lead(card_id, reengajos=(l.get("reengajos") or 0) + 1)
        est.add_conversa(card_id, "agente", f"[template feq_reengajamento] prazo {prazo}")
        est.log_acao("operador", f"reativação manual: {card_id}")
    return r


def reengajar(est, ont, wa):
    """Envia feq_reengajamento aos elegíveis. Retorna quantos foram enviados."""
    enviados = 0
    for l in leads_para_reengajar(est, ont):
        c = ont.curso(l["curso"])
        prazo = "/".join(reversed((c.get("inscricoes_fim") or "").split("-")))
        r = wa.enviar_template(l["telefone"], "feq_reengajamento",
                               params=[l.get("nome") or "olá",
                                       f"{c['nome']} ({c['codigo']})", prazo])
        if r.get("ok"):
            est.set_lead(l["card_id"], reengajos=(l.get("reengajos") or 0) + 1)
            est.add_conversa(l["card_id"], "agente",
                             f"[template feq_reengajamento] prazo {prazo}")
            enviados += 1
    if enviados:
        est.log_acao("sistema", f"nutricao: {enviados} reengajado(s)")
    return enviados
