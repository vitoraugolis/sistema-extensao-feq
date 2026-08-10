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
               # lista de espera (curso sem turma aberta, spec 17 §2b): é um Lead
               # de verdade pra Meta, mas NÃO é checkout — não entra na régua de
               # recuperação nem na fila de Verificação da Central.
               "lista_espera": "Lead",
               "inscricao_confirmada": "CompleteRegistration",
               "matricula_confirmada": "Purchase"}   # offline, com valor real (spec 16)
RECUPERAR_APOS_H = {"etapa1": 2,             # parou na etapa 1 da LP: 2h -> "posso ajudar?" (spec 17 §7)
                    "inicio_inscricao": 2,   # concluiu a etapa 2 e não converteu
                    "boleto_gerado": 24}     # boleto: prazo de compensação é maior
MAX_POR_PASSADA = 25      # mesmo teto anti-blast da nutrição
# Ociosidade máxima: cadência parada há mais que isto não é retomada. MAX_POR_PASSADA
# limita por PASSADA, não no total — sem este corte, ligar a régua no agendador faria
# os pendentes acumulados (140 em produção, muitos de semanas atrás) virarem uma
# rajada de templates. "Você abandonou a inscrição" três semanas depois é ruído.
# ponytail: teto fixo de 7 dias; se um dia precisar variar por curso, vira config.
IDADE_MAX_H = 24 * 7
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
    à vista quando existe; senão parcelas×valor; senão 0.

    Com promoção vigente o valor é o PROMOCIONAL — reportar o preço de tabela numa
    venda com desconto infla o ROAS e faz o Growth Engine investir errado. Usa a
    oferta mais barata (a mais provável de fechar) para não superestimar."""
    p = (c or {}).get("plano") or {}
    promo = (c or {}).get("promocao")
    if promo and promo.get("ofertas"):
        vals = []
        for o in promo["ofertas"]:
            try:
                if o.get("a_vista"):
                    vals.append(round(float(o["a_vista"]), 2))
                elif o.get("n") and o.get("valor_parcela"):
                    vals.append(round(int(o["n"]) * float(o["valor_parcela"]), 2))
            except (TypeError, ValueError):
                pass
        if vals:
            return min(vals)
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


def _toque(g, est, ont, wa, p, agora):
    """Envia o PRÓXIMO toque da cadência pro pendente p, sem checar tempos —
    o gatilho é de quem chama (relógio na régua, gesto do operador na aba
    Verificação). Guardas de conteúdo valem sempre: teto de toques, opt-out,
    lead que respondeu (cérebro cuida) e captabilidade. Retorna card_id se
    enviou; None caso contrário."""
    if p["toques"] >= MAX_TOQUES:
        return None
    lead = est.get_lead(p["card_id"])
    if not lead or lead.get("optout"):
        return None
    falou = est.ultima_lead_ts(p["card_id"])
    if falou and falou > p["ultimo_checkout"]:
        return None   # respondeu no WhatsApp após o checkout: cérebro cuida, régua não
    if not ont.captavel(p["curso"]).get("captavel"):
        return None
    tpl, _ = CADENCIA[p["toques"]]
    c = ont.curso(p["curso"])
    # parâmetro vazio a Meta rejeita -> fallback "breve" quando não há prazo na ontologia
    prazo = "/".join(reversed((c.get("inscricoes_fim") or "").split("-"))) or "breve"
    nome_curso = f"{c['nome']} ({c['codigo']})"
    params = [lead.get("nome") or "olá", nome_curso] if tpl == "feq_lead" \
        else [lead.get("nome") or "olá", nome_curso, prazo]
    r = wa.enviar_template(lead["telefone"], tpl, params=params)
    if not r.get("ok"):
        return None
    g.add_evento(p["card_id"], p["curso"], "recuperacao_enviada",
                 ts=agora.isoformat(timespec="seconds"))
    est.add_conversa(p["card_id"], "agente",
                     f"[recuperação de inscrição — template {tpl}] prazo {prazo}",
                     wamid=r.get("wamid"), status="enviado", autor="template")
    return p["card_id"]


def recuperar(g, est, ont, wa, agora=None):
    """Uma passada da régua de recuperação: inscrição iniciada e não concluída ->
    template aprovado. Espera por tipo (etapa1 2h, inicio_inscricao 2h, boleto 24h) e
    piso de idade (IDADE_MAX_H). Lead que RESPONDEU depois do checkout sai da régua
    (a conversa é do cérebro). Máx. 5 toques."""
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
        # Cadência DORMENTE não é retomada. O corte é sobre a última atividade (último
        # toque, ou o checkout se nunca houve toque), não sobre a idade do checkout:
        #   • nunca tocado e checkout velho  -> backlog frio, não acordar
        #   • tocado, mas parado há >7 dias  -> retomar agora é um toque do nada
        #   • cadência ativa (intervalos de 1h a 96h) -> segue até o 5º toque
        # Medir só a idade do checkout errava dos dois lados: truncava a cadência ativa
        # (que leva ~10 dias) e deixava passar backlog que já tinha 1 toque antigo — o
        # ensaio contra a base real mostrou 50 desses prestes a receber um "toque 2"
        # 12 a 15 dias depois do primeiro.
        ocioso = _horas(p["ultimo_toque"], agora) if p["ultimo_toque"] else h
        if ocioso is not None and ocioso > IDADE_MAX_H:
            continue
        espera_toque = CADENCIA[p["toques"]][1]
        ht = _horas(p["ultimo_toque"], agora)
        if espera_toque is not None and ht is not None and ht < espera_toque:
            continue
        if _toque(g, est, ont, wa, p, agora):
            enviados.append(p["card_id"])
    if enviados:
        est.log_acao("growth", f"recuperacao: {len(enviados)} toque(s) — {', '.join(enviados)}")
    return {"enviados": enviados}


def ativar_um(g, est, ont, wa, card_id, agora=None):
    """Ativação imediata (aba Verificação, spec 17 §8b): o operador conferiu na
    Extecamp que o lead NÃO se inscreveu -> próximo toque da cadência AGORA,
    pulando só os gatilhos de tempo (as guardas de conteúdo continuam)."""
    agora = agora or datetime.datetime.now()
    p = next((x for x in g.checkouts_pendentes() if x["card_id"] == card_id), None)
    if not p:
        return {"ok": False, "motivo": "sem checkout pendente (já converteu ou nunca iniciou)"}
    if _toque(g, est, ont, wa, p, agora):
        return {"ok": True, "toque": p["toques"] + 1}
    return {"ok": False,
            "motivo": "não enviado (teto de toques, opt-out, lead já respondeu ou curso encerrado)"}
