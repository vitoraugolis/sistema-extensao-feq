#!/usr/bin/env python3
"""
Atena · Growth — funil de conversão (spec 15 §5).
Eventos de checkout do Extecamp → CAPI (pixel mais inteligente → CPL menor),
lead scoring computado do histórico e régua de recuperação de inscrição
abandonada (24h), com urgência legítima: o prazo de inscrição é real.
Envio sempre pelo transporte guardado (opt-out, dry-run sem token).
"""
import re
import sys
import datetime
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

EVENTO_CAPI = {"inicio_inscricao": "InitiateCheckout",
               "boleto_gerado": "InitiateCheckout",
               "inscricao_confirmada": "CompleteRegistration",
               "matricula_confirmada": "Purchase"}   # offline, com valor real (spec 16)
RECUPERAR_APOS_H = {"inicio_inscricao": 2,   # ponte: 2h sem converter -> "posso ajudar?" (spec 17 §7)
                    "boleto_gerado": 24}     # boleto: prazo de compensação é maior
MAX_POR_PASSADA = 25      # mesmo teto anti-blast da nutrição
# Cadência de 5 toques (spec 17 §7): ajuda -> lembrete -> valor -> urgência -> última
# chamada. (template aprovado, horas de espera desde o toque anterior). Texto livre
# exigiria template novo aprovado na Meta (business-initiated fora da janela de 24h).
CADENCIA = [("feq_lead", None),                 # espera do 1º toque vem de RECUPERAR_APOS_H
            ("feq_lembrete_inscricao", 24),
            ("feq_reengajamento", 48),
            ("feq_lembrete_inscricao", 72),
            ("feq_lead", 96)]
MAX_TOQUES = len(CADENCIA)
INTERESSE_RE = re.compile(r"cronograma|inscri|valor|investimento|pre[çc]o|pagamento|boleto|parcela", re.I)


def registrar(g, ads, est, card_id, tipo, curso=None, agora=None, event_id=None,
              contexto=None, valor=None, telefone=None, email=None):
    """Registra evento de funil e dispara o evento CAPI correspondente.
    event_id: default tipo-card_id (idempotente em reenvio); vindo do NAVEGADOR
    (ponte) garante a desduplicação Pixel×CAPI. contexto={fbp,fbc,ip,ua,url} ->
    action_source=website. telefone/email cobrem conversão offline sem card local."""
    if tipo not in EVENTO_CAPI and tipo != "recuperacao_enviada":
        return {"ok": False, "motivo": f"tipo desconhecido: {tipo}"}
    lead = est.get_lead(card_id) if est else None
    curso = curso or (lead or {}).get("curso", "")
    tel = telefone or (lead or {}).get("telefone")
    ts = (agora or datetime.datetime.now()).isoformat(timespec="seconds")
    capi_id = None
    if tipo in EVENTO_CAPI and (tel or email):
        eid = event_id or f"{tipo}-{card_id}"
        custom = {"content_ids": [curso], "content_name": curso}
        if EVENTO_CAPI[tipo] == "Purchase":
            custom.update(currency="BRL", value=valor or 0)
        r = ads.capi(EVENTO_CAPI[tipo], tel, eid, custom, email=email,
                     action_source="website" if contexto else None, contexto=contexto)
        if r.get("ok"):
            capi_id = eid
    g.add_evento(card_id, curso, tipo, ts=ts, capi=capi_id, valor=valor)
    if est:
        est.log_acao("growth", f"evento {tipo} ({curso}) capi={capi_id or 'não'}", card_id)
    return {"ok": True, "capi": capi_id}


def status_funil(g, est, card_id):
    """Status derivado do lead no funil (spec 16 §5): lead | initiated_checkout | enrolled.
    Derivado de fase+eventos — não é coluna (estado duplicado seria drift na certa)."""
    lead = est.get_lead(card_id) if est else None
    tipos = {e["tipo"] for e in g.eventos(card_id=card_id)}
    if "matricula_confirmada" in tipos or (lead or {}).get("fase") == "Matriculado":
        return "enrolled"
    if tipos & {"inicio_inscricao", "boleto_gerado", "inscricao_confirmada"}:
        return "initiated_checkout"
    return "lead"


def score(est, g, card_id):
    """Pontuação 0-100 pelo comportamento. Computada, nada a sincronizar."""
    lead = est.get_lead(card_id)
    if not lead or lead.get("optout"):
        return 0
    tipos = {e["tipo"] for e in g.eventos(card_id=card_id)}
    if tipos & {"inscricao_confirmada", "matricula_confirmada"}:
        return 100
    falas = [h for h in est.historico(card_id) if h.get("papel") == "lead"]
    pts = 0
    if falas:
        pts += 30
    if len(falas) >= 3:
        pts += 10
    if any(INTERESSE_RE.search(h.get("texto") or "") for h in falas):
        pts += 20
    if "inicio_inscricao" in tipos:
        pts += 40
    if "boleto_gerado" in tipos:
        pts += 60
    return min(100, pts)


def classificar(pontos):
    return "quente" if pontos >= 70 else ("morno" if pontos >= 40 else "frio")


def valor_curso(c):
    """Valor da conversão (Purchase) a partir do plano da ontologia:
    à vista quando existe; senão parcelas×valor; senão 0."""
    p = (c or {}).get("plano") or {}
    try:
        if p.get("a_vista"):
            return round(float(p["a_vista"]), 2)
        if p.get("n") and p.get("valor_parcela"):
            return round(int(p["n"]) * float(p["valor_parcela"]), 2)
    except (TypeError, ValueError):
        pass
    return 0


def _horas(ts, agora):
    try:
        return (agora - datetime.datetime.fromisoformat(ts)).total_seconds() / 3600
    except (TypeError, ValueError):
        return None


def recuperar(g, est, ont, wa, agora=None):
    """Uma passada da régua de recuperação: checkout sem conversão -> template
    aprovado (espera por tipo: ponte 2h, boleto 24h). Lead que RESPONDEU depois
    do checkout sai da régua (a conversa é do cérebro). Máx. 3 toques, >=48h."""
    agora = agora or datetime.datetime.now()
    enviados = []
    for p in g.checkouts_pendentes():
        if len(enviados) >= MAX_POR_PASSADA:
            est.log_acao("growth", f"recuperacao: teto de {MAX_POR_PASSADA} — resto na próxima passada")
            break
        h = _horas(p["ultimo_checkout"], agora)
        espera = RECUPERAR_APOS_H.get(p.get("tipo_checkout"), 24)
        if h is None or h < espera or p["toques"] >= MAX_TOQUES:
            continue
        tpl, espera_toque = CADENCIA[p["toques"]]
        ht = _horas(p["ultimo_toque"], agora)
        if espera_toque is not None and ht is not None and ht < espera_toque:
            continue
        lead = est.get_lead(p["card_id"])
        if not lead or lead.get("optout"):
            continue
        falou = est.ultima_lead_ts(p["card_id"])
        if falou and falou > p["ultimo_checkout"]:
            continue   # respondeu no WhatsApp após o checkout: cérebro cuida, régua não
        if not ont.captavel(p["curso"]).get("captavel"):
            continue
        c = ont.curso(p["curso"])
        # parâmetro vazio a Meta rejeita -> fallback "breve" quando não há prazo na ontologia
        prazo = "/".join(reversed((c.get("inscricoes_fim") or "").split("-"))) or "breve"
        nome_curso = f"{c['nome']} ({c['codigo']})"
        params = [lead.get("nome") or "olá", nome_curso] if tpl == "feq_lead" \
            else [lead.get("nome") or "olá", nome_curso, prazo]
        r = wa.enviar_template(lead["telefone"], tpl, params=params)
        if r.get("ok"):
            g.add_evento(p["card_id"], p["curso"], "recuperacao_enviada",
                         ts=agora.isoformat(timespec="seconds"))
            est.add_conversa(p["card_id"], "agente",
                             f"[recuperação de inscrição — template {tpl}] prazo {prazo}",
                             wamid=r.get("wamid"), status="enviado")
            enviados.append(p["card_id"])
    if enviados:
        est.log_acao("growth", f"recuperacao: {len(enviados)} toque(s) — {', '.join(enviados)}")
    return {"enviados": enviados}
