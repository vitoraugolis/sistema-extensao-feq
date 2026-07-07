#!/usr/bin/env python3
"""
Atena · Fase 2 — Cérebro de Qualificação + Revisor anti-alucinação
==================================================================
Conduz a conversa de qualificação de um lead (do estágio Lead ao Prospect/Perdido),
de forma autônoma e com guardrails, e valida cada mensagem contra o catálogo FEQ
ANTES de "enviar". Testável offline, sem WhatsApp, no mesmo padrão do /simulate.

Camadas:
  • Cérebro (brain): Claude raciocina com o contexto do curso + guardrails e decide
    a próxima ação (continuar, qualificar, descartar, em espera, escalar, opt-out).
  • Revisor: passada 1 estruturada (preços/datas/horas via regex) + passada 2
    semântica (Claude) — bloqueia qualquer afirmação fora da base do curso.
  • FARO: aplica a decisão movendo/atualizando o card (se card_id for informado).

Endpoints:
  POST /converse   -> conversa completa (cérebro + revisor + FARO opcional)
  POST /reviewer   -> testa só o Revisor sobre um texto qualquer
  GET  /health

Execução:
  pip install flask requests
  # .env deve conter: ANTHROPIC_API_KEY, FARO_KEY  (e, p/ produção, WHATSAPP_TOKEN, PHONE_NUMBER_ID)
  set -a && source .env && set +a
  python3 feq_cerebro_revisor.py
"""

import os
import re
import sys
import json
import datetime
import logging
import unicodedata

try:
    import requests
    from flask import Flask, request, jsonify
except ImportError:
    print("Faltam dependências. Rode: pip install flask requests")
    sys.exit(1)

# ------------------------------------------------------------------ config ----
ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY")
ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6")
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"

FARO_BASE = "https://ecinffgoycqvktmrmvaz.supabase.co/functions/v1"
FARO_KEY = os.environ.get("FARO_KEY")
PIPELINE_ID = os.environ.get("FARO_PIPELINE_ID", "49d65f66-ec1d-4c10-a5ff-71aef07f9279")
STAGES = {
    "prospect": "343b826f-12a6-4dd2-a9fc-04c50739a7bb",
    "perdido": "5a16e79b-dd4a-471d-ae50-ddbce03ddd13",
    "em_espera": "295a20bc-c84d-4159-859b-23ccea643747",
}

CATALOGO_PATH = os.environ.get("CATALOGO_PATH", "cursos_feq.json")
GATE_ACADEMICO = "ensino superior completo"  # vale para todos os 8 cursos da FEQ

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("fase2")
app = Flask(__name__)

# Catálogo (fonte da verdade do Revisor)
try:
    with open(CATALOGO_PATH, encoding="utf-8") as f:
        CATALOGO = {c["codigo"].replace("-", "").upper(): c for c in json.load(f)["cursos"]}
except Exception as e:
    log.error("Não consegui carregar %s: %s", CATALOGO_PATH, e)
    CATALOGO = {}


# --------------------------------------------------------------- utilidades ----
def _norm(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    return s.lower().strip()


def curso_record(curso):
    key = re.sub(r"\D", "", curso or "")
    for k, c in CATALOGO.items():
        if re.sub(r"\D", "", k) == key:
            return c
    return None


def _money_digits(text):
    """Extrai valores monetários como dígitos. 'R$ 866,09' -> '86609'."""
    out = set()
    for m in re.findall(r"R?\$?\s*\d{1,3}(?:[.\s]\d{3})*(?:,\d{2})", text or ""):
        out.add(re.sub(r"\D", "", m))
    return out


def _all_money_in_record(c):
    blob = " ".join(str(c.get(k, "")) for k in ("taxa", "parcelado", "a_vista"))
    return _money_digits(blob)


def _dates_yyyymmdd(text):
    """Datas dd/mm/aaaa no texto -> set de 'aaaammdd'."""
    out = set()
    for d, mth, y in re.findall(r"(\d{2})/(\d{2})/(\d{4})", text or ""):
        out.add(y + mth + d)
    return out


def _record_dates(c):
    out = set()
    for k in ("inscricoes_inicio", "inscricoes_fim", "oferecimento_inicio", "oferecimento_fim"):
        v = c.get(k)
        if v and re.match(r"\d{4}-\d{2}-\d{2}", v):
            y, mth, d = v[:10].split("-")
            out.add(y + mth + d)
    return out


def _hours(text):
    out = set()
    for n in re.findall(r"(\d{1,4})\s*(?:h\b|horas)", text or "", re.I):
        out.add(int(n))
    return out


# ----------------------------------------------------------------- Revisor ----
def revisor_estrutural(curso_cod, texto):
    """Passada 1: confere preços, datas e cargas contra o registro do curso."""
    c = curso_record(curso_cod)
    if not c:
        return {"aprovado": False, "bloqueios": [f"Curso '{curso_cod}' não está na base."]}
    bloqueios = []

    permitido_money = _all_money_in_record(c)
    for m in _money_digits(texto):
        if m not in permitido_money:
            bloqueios.append(f"Valor monetário não consta na base do curso: {m} (dígitos).")

    permitido_datas = _record_dates(c)
    for d in _dates_yyyymmdd(texto):
        if d not in permitido_datas:
            bloqueios.append(f"Data não consta na base do curso: {d[6:]}/{d[4:6]}/{d[:4]}.")

    carga = c.get("carga_horaria")
    if carga:
        permitido_h = set(int(x) for x in re.findall(r"\d{1,4}", carga))
        for h in _hours(texto):
            if h not in permitido_h:
                bloqueios.append(f"Carga horária não consta na base do curso: {h}h.")
    else:
        if _hours(texto):
            bloqueios.append("Mensagem cita carga horária, mas a base não tem esse dado (campo nulo).")

    return {"aprovado": not bloqueios, "bloqueios": bloqueios}


def revisor_semantico(curso_cod, texto):
    """Passada 2 (opcional): Claude confere afirmações de requisito/horário/modalidade."""
    if not ANTHROPIC_KEY:
        return {"aprovado": True, "bloqueios": [], "nota": "passada semântica pulada (sem API key)"}
    c = curso_record(curso_cod)
    sys_prompt = (
        "Você é um auditor factual. Recebe os DADOS OFICIAIS de um curso (JSON) e uma "
        "MENSAGEM. Sua tarefa: encontrar qualquer afirmação factual na mensagem (requisito, "
        "horário, modalidade, certificação, vagas, prazos, preço) que CONTRADIGA ou vá ALÉM "
        "dos dados oficiais. Não invente; se a mensagem só usa dados presentes, está ok. "
        'Responda SOMENTE em JSON: {"ok": bool, "problemas": [strings]}.'
    )
    user = f"DADOS OFICIAIS:\n{json.dumps(c, ensure_ascii=False)}\n\nMENSAGEM:\n{texto}"
    out = _claude(sys_prompt, [{"role": "user", "content": user}], max_tokens=400)
    data = _json_from(out)
    if not data:
        return {"aprovado": True, "bloqueios": [], "nota": "passada semântica inconclusiva"}
    return {"aprovado": bool(data.get("ok")), "bloqueios": data.get("problemas", [])}


def revisar(curso_cod, texto):
    r1 = revisor_estrutural(curso_cod, texto)
    r2 = revisor_semantico(curso_cod, texto)
    bloqueios = r1["bloqueios"] + r2.get("bloqueios", [])
    return {"aprovado": r1["aprovado"] and r2["aprovado"], "bloqueios": bloqueios,
            "estrutural": r1, "semantico": r2}


# ------------------------------------------------------------------- Claude ----
def _claude(system, messages, max_tokens=700):
    if not ANTHROPIC_KEY:
        return ""
    try:
        r = requests.post(ANTHROPIC_URL, timeout=60,
            headers={"x-api-key": ANTHROPIC_KEY, "anthropic-version": "2023-06-01",
                     "content-type": "application/json"},
            json={"model": ANTHROPIC_MODEL, "max_tokens": max_tokens,
                  "system": system, "messages": messages})
        if r.status_code != 200:
            log.error("Claude API %s: %s", r.status_code, r.text[:300])
            return ""
        return r.json()["content"][0]["text"]
    except Exception as e:
        log.error("Claude API erro: %s", e)
        return ""


def _json_from(text):
    if not text:
        return None
    m = re.search(r"\{.*\}", text, re.S)
    try:
        return json.loads(m.group(0)) if m else None
    except Exception:
        return None


# -------------------------------------------------------------------- Brain ----
OPTOUT_RE = re.compile(r"\b(sair|parar|pare|n[aã]o quero|descadastr|remover|stop|cancelar)\b", re.I)


def brain_system_prompt(c):
    insight = c.get("insight_venda", "")
    return f"""Você é a Atena, assistente da Extensão FEQ Unicamp, qualificando um lead via WhatsApp.

FONTE DA VERDADE (só use dados daqui; NUNCA invente preço, data, requisito, carga, horário):
{json.dumps(c, ensure_ascii=False)}

REGRAS:
- Gate acadêmico: TODOS os cursos exigem {GATE_ACADEMICO}. Sem isso, o lead não é elegível.
- Se faltar um dado na base (valor null), diga que vai confirmar — não invente.
- Tom: cordial, direto, sem prometer o que não há. Não bajule.
- Minimização (LGPD): nesta fase, NÃO peça CPF, endereço ou documentos. Só o necessário para qualificar.
- Esclareça proativamente o que costuma confundir neste curso: {insight}
- Encerre se o lead pedir para sair (opt-out).
- Escale para humano se: pedido de desconto/condição especial, reclamação, intenção de fechar matrícula, ou dúvida fora da base.

OBJETIVO: confirmar interesse, checar escolaridade (gate) e disponibilidade; então decidir.

Responda SOMENTE em JSON:
{{"mensagem_ao_lead": "texto curto e natural",
  "proxima_acao": "continuar|qualificar|descartar|em_espera|escalar_humano|encerrar_optout",
  "motivo": "por que essa ação",
  "escalonamento_motivo": null,
  "dados": {{"escolaridade": null, "disponibilidade": null}}}}"""


def brain(curso_cod, historico, mensagem_lead):
    c = curso_record(curso_cod)
    if not c:
        return {"erro": f"curso '{curso_cod}' não encontrado na base"}
    # Opt-out determinístico (não confia só no LLM para isso).
    if OPTOUT_RE.search(mensagem_lead or ""):
        return {"mensagem_ao_lead": "Tudo bem! Não vou mais enviar mensagens. Se mudar de ideia, é só chamar. 🙂",
                "proxima_acao": "encerrar_optout", "motivo": "lead pediu opt-out",
                "escalonamento_motivo": None, "dados": {}}
    msgs = []
    for h in (historico or []):
        role = "assistant" if h.get("de") == "agente" else "user"
        msgs.append({"role": role, "content": h.get("texto", "")})
    msgs.append({"role": "user", "content": mensagem_lead or "(início da conversa)"})
    out = _claude(brain_system_prompt(c), msgs, max_tokens=700)
    data = _json_from(out)
    if not data:
        return {"mensagem_ao_lead": "Pode me contar um pouco mais sobre seu interesse no curso?",
                "proxima_acao": "continuar", "motivo": "fallback (cérebro sem resposta estruturada)",
                "escalonamento_motivo": None, "dados": {}}
    return data


# -------------------------------------------------------------------- FARO ----
def faro_post(path, body):
    if not FARO_KEY:
        return 0, {"error": "FARO_KEY ausente"}
    try:
        r = requests.post(f"{FARO_BASE}/{path}", timeout=30,
            headers={"Authorization": f"Bearer {FARO_KEY}", "Content-Type": "application/json"},
            json=body)
        return r.status_code, (r.json() if r.text else {})
    except Exception as e:
        return 0, {"error": str(e)}


def aplicar_no_faro(card_id, acao, data, motivo):
    hoje = datetime.date.today().strftime("%d/%m/%Y")
    if acao == "qualificar":
        faro_post("api-cards-update", {"card_id": card_id, "fields": {
            "Status Qualificação": "Qualificado", "Última Interação": hoje,
            **({"Escolaridade": data["escolaridade"]} if data.get("escolaridade") else {})}})
        faro_post("api-cards-move", {"card_id": card_id, "stage_id": STAGES["prospect"]})
        return "movido para Prospect"
    if acao == "descartar":
        faro_post("api-cards-update", {"card_id": card_id, "fields": {
            "Status Qualificação": "Descartado", "Motivo Descarte": motivo, "Última Interação": hoje}})
        faro_post("api-cards-move", {"card_id": card_id, "stage_id": STAGES["perdido"]})
        return "movido para Perdido"
    if acao == "em_espera":
        faro_post("api-cards-move", {"card_id": card_id, "stage_id": STAGES["em_espera"]})
        return "movido para Em espera"
    if acao == "encerrar_optout":
        faro_post("api-cards-update", {"card_id": card_id, "fields": {"Opt-out": "true", "Última Interação": hoje}})
        return "Opt-out marcado"
    faro_post("api-cards-update", {"card_id": card_id, "fields": {"Última Interação": hoje}})
    return "interação registrada"


# --------------------------------------------------------------- endpoints ----
@app.route("/converse", methods=["POST"])
def converse():
    body = request.get_json(silent=True) or {}
    curso = body.get("curso", "")
    historico = body.get("historico", [])
    mensagem_lead = body.get("mensagem_lead", "")
    card_id = body.get("card_id")

    decisao = brain(curso, historico, mensagem_lead)
    if decisao.get("erro"):
        return jsonify(decisao), 400

    # Revisor sobre a mensagem proposta; 1 tentativa de reescrita se bloquear.
    msg = decisao.get("mensagem_ao_lead", "")
    rev = revisar(curso, msg)
    if not rev["aprovado"]:
        log.warning("Revisor bloqueou. Reescrevendo. Motivos: %s", rev["bloqueios"])
        historico2 = (historico or []) + [{"de": "lead", "texto": mensagem_lead}]
        retry = brain(curso, historico2,
                      "[REVISOR bloqueou sua mensagem por: " + "; ".join(rev["bloqueios"]) +
                      ". Reescreva sem afirmar dados que não estejam na base; se não souber, diga que vai confirmar.]")
        msg = retry.get("mensagem_ao_lead", msg)
        decisao = retry
        rev = revisar(curso, msg)

    faro_result = None
    if card_id and rev["aprovado"]:
        faro_result = aplicar_no_faro(card_id, decisao.get("proxima_acao", "continuar"),
                                      decisao.get("dados", {}), decisao.get("motivo", ""))

    return jsonify({
        "mensagem_ao_lead": msg if rev["aprovado"] else "(bloqueada pelo Revisor — não enviada)",
        "proxima_acao": decisao.get("proxima_acao"),
        "motivo": decisao.get("motivo"),
        "escalonamento_motivo": decisao.get("escalonamento_motivo"),
        "dados_coletados": decisao.get("dados", {}),
        "revisor": {"aprovado": rev["aprovado"], "bloqueios": rev["bloqueios"]},
        "faro": faro_result,
    }), 200


@app.route("/reviewer", methods=["POST"])
def reviewer_only():
    body = request.get_json(silent=True) or {}
    return jsonify(revisar(body.get("curso", ""), body.get("texto", ""))), 200


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"ok": True, "cursos_carregados": len(CATALOGO),
                    "claude": bool(ANTHROPIC_KEY), "faro": bool(FARO_KEY)}), 200


if __name__ == "__main__":
    if not CATALOGO:
        print("AVISO: catálogo vazio — confira CATALOGO_PATH (cursos_feq.json).")
    if not ANTHROPIC_KEY:
        print("AVISO: ANTHROPIC_API_KEY ausente — cérebro e passada semântica ficam offline.")
    port = int(os.environ.get("PORT", "8090"))
    log.info("Cérebro+Revisor FEQ na porta %s · %s cursos · Claude=%s · FARO=%s",
             port, len(CATALOGO), bool(ANTHROPIC_KEY), bool(FARO_KEY))
    app.run(host="0.0.0.0", port=port)
