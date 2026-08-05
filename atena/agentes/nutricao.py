#!/usr/bin/env python3
"""
Atena · Reativação comercial — jamais perder um lead conquistado pela campanha.
Dois regimes, escolhidos pela janela de 24h do WhatsApp:
  • DENTRO da janela (o lead falou < 24h e travou): nudge em TEXTO LIVRE gerado pelo
    cérebro + Revisor -> vira PROPOSTA (envia se auto-aprovar; senão espera o operador).
  • FORA da janela (lead frio): cadência de TEMPLATES aprovados, escalonando o tom/urgência.
Guardrails: só curso captável, sem opt-out, 1 nudge por janela, máx. len(TEMPLATES_FRIOS)
toques frios por lead. Horário comercial: o agendador só chama isto dentro da janela.
"""
import datetime

STALL_H = 8                       # silêncio (h) DENTRO da janela de 24h -> nudge free-text
TEMPLATES_FRIOS = [               # cadência fria (fora da janela), na ordem de escalonamento
    ("feq_reengajamento", 3),     # (template aprovado, dias_min de silêncio desde o último contato)
    ("feq_lembrete_inscricao", 7),
]
MAX_POR_PASSADA = 25              # teto de segurança: espalha o 1º deploy em vez de blast (inc. 09/07)
# ponytail: assumo params [nome, curso, prazo] p/ os dois templates (ambos são lembretes de
# curso+prazo). Se a var de algum diferir no WABA, o envio falha closed (não conta) e loga.


def _horas(ts, agora):
    try:
        return (agora - datetime.datetime.fromisoformat(ts)).total_seconds() / 3600
    except (TypeError, ValueError):
        return None


def _agentes_apos_ultimo_lead(hist):
    """Msgs do agente DEPOIS da última fala do lead (1 = só respondemos, ainda sem nudge)."""
    n = 0
    for h in reversed(hist):
        if h.get("papel") == "lead":
            break
        if h.get("papel") == "agente":
            n += 1
    return n


def _elegivel(l, ont):
    if l["fase"] not in ("Lead", "Prospect") or l.get("optout"):
        return False
    if l["estado_conversa"] not in ("aguardando_resposta", "aguardando_1o_contato"):
        return False
    return bool(ont.captavel(l["curso"]).get("captavel"))


def _template_frio_devido(l, sil_dias):
    """Toque frio na vez? Retorna o nome do template ou None."""
    toque = l.get("reengajos") or 0
    if toque >= len(TEMPLATES_FRIOS):
        return None
    nome, dias_min = TEMPLATES_FRIOS[toque]
    return nome if sil_dias >= dias_min else None


def _enviar_template(est, ont, wa, l, nome):
    c = ont.curso(l["curso"])
    prazo = "/".join(reversed((c.get("inscricoes_fim") or "").split("-")))
    r = wa.enviar_template(l["telefone"], nome,
                           params=[l.get("nome") or "olá", f"{c['nome']} ({c['codigo']})", prazo])
    if r.get("ok"):
        est.set_lead(l["card_id"], reengajos=(l.get("reengajos") or 0) + 1)
        est.add_conversa(l["card_id"], "agente", f"[template {nome}] prazo {prazo}",
                         wamid=r.get("wamid"), status="enviado", autor="template",
                         destino=l["telefone"])
    return r


def reativar(est, ont, wa, propor=None, agora=None):
    """Uma passada de reativação. `propor(card_id)` (opcional) gera o nudge free-text como
    PROPOSTA e devolve o card_id se criou uma. Retorna {'templates': n, 'nudges': [card_ids]}."""
    agora = agora or datetime.datetime.now()
    templates, nudges = 0, []
    for l in est.listar_leads():
        if templates + len(nudges) >= MAX_POR_PASSADA:
            est.log_acao("sistema", f"reativacao: teto de {MAX_POR_PASSADA} atingido — resto na próxima passada")
            break
        if not _elegivel(l, ont):
            continue
        cid = l["card_id"]
        hist = est.historico(cid)
        janela_h = _horas(est.ultima_lead_ts(cid), agora)              # desde a última fala do lead
        ult_ativ = hist[-1]["ts"] if hist else (l.get("atualizado") or l.get("criado"))
        sil_dias = (_horas(ult_ativ, agora) or 0) / 24                 # desde o último contato (qualquer)
        # DENTRO da janela e travado -> nudge free-text (1x por janela: gate agentes==1)
        if propor and janela_h is not None and STALL_H <= janela_h < 24 \
                and _agentes_apos_ultimo_lead(hist) == 1:
            if propor(cid):
                nudges.append(cid)
            continue
        # FORA da janela -> cadência de templates escalonados
        if janela_h is None or janela_h >= 24:
            nome = _template_frio_devido(l, sil_dias)
            if nome and _enviar_template(est, ont, wa, l, nome).get("ok"):
                templates += 1
    if templates or nudges:
        est.log_acao("sistema", f"reativacao: {templates} template(s), {len(nudges)} nudge(s)")
    return {"templates": templates, "nudges": nudges}


def reengajar_um(est, ont, wa, card_id):
    """Reativação manual de UM lead (botão do kanban). Trava dura: opt-out e captabilidade.
    Sem o limite de toques nem silêncio — é ato deliberado do operador. Retorna o envio."""
    l = est.get_lead(card_id)
    if not l:
        return {"ok": False, "motivo": "lead inexistente"}
    if l.get("optout"):
        return {"ok": False, "motivo": "lead em opt-out"}
    if not ont.captavel(l["curso"]).get("captavel"):
        return {"ok": False, "motivo": "curso não captável (inscrições encerradas)"}
    r = _enviar_template(est, ont, wa, l, "feq_reengajamento")
    if r.get("ok"):
        est.log_acao("operador", f"reativação manual: {card_id}")
    return r
