#!/usr/bin/env python3
"""
Atena · Central de Operação (API + SPA glass).
Agora com DADOS REAIS: estado em SQLite (orquestrador) + Injetor (Ontologista).
Captabilidade e Revisor consultam a ontologia ao vivo.

Rodar:
  pip install flask requests rdflib pyshacl
  python3 atena/api/app.py        # http://localhost:8095
"""
import os
import sys
import datetime
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))


def _load_env(p=pathlib.Path(__file__).resolve().parents[2] / ".env"):
    # ponytail: dotenv-lite stdlib — evita `source .env` frágil; não sobrescreve env já setado
    if not p.exists():
        return
    for ln in p.read_text().splitlines():
        ln = ln.strip()
        if ln and not ln.startswith("#") and "=" in ln:
            k, v = ln.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_env()  # antes dos imports que leem env no import (auth lê ATENA_USERS)

from flask import Flask, jsonify, request, send_from_directory

from atena.revisor.ontologico import revisar
from atena.core.estado import Estado
from atena.core.orquestrador import Orquestrador
from atena.core import guardrails as G
from atena.core import auth
from atena.agentes.ontologista import Ontologista
from atena.agentes.ingestao import Ingestao, normalizar_meta, fetch_meta_lead
from atena.agentes import nutricao
from atena.agentes.curador import Curador
from atena.agentes.cerebro import cerebro_factory, modelo_atual, testar_modelo, PROVEDORES, chave_do
from atena.transporte.whatsapp import WhatsApp

EST = Estado()
EST.seed_demo()
_modelo_salvo = EST.get_config("modelo")
if _modelo_salvo:
    os.environ["ATENA_MODELO"] = _modelo_salvo   # escolha da Central sobrevive a restart
ONTO = Ontologista(EST)              # detém o grafo (.ont) — fonte da verdade
ORQ = Orquestrador(estado=EST, ont=ONTO.ont, brain=cerebro_factory(ONTO.ont))
ING = Ingestao(EST, ONTO.ont, faro=ORQ.faro)
WA = WhatsApp(EST, ORQ)
CUR = Curador(EST, ONTO.ont)
WEB = pathlib.Path(__file__).resolve().parents[1] / "web"
app = Flask(__name__)
from werkzeug.middleware.proxy_fix import ProxyFix
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)   # IP real atrás do Caddy
auth.init_app(app, EST)             # login/RBAC/auditoria — /api/* exige sessão


def _ont():
    return ONTO.ont


def _iniciais(nome):
    ps = (nome or "?").split()
    return (ps[0][:1] + (ps[1][:1] if len(ps) > 1 else "")).upper()


def _ha(ts):
    try:
        d = (datetime.datetime.now() - datetime.datetime.fromisoformat(ts)).total_seconds()
    except Exception:
        return ""
    if d < 3600:
        return f"{int(d // 60)} min"
    if d < 86400:
        return f"{int(d // 3600)} h"
    return f"{int(d // 86400)} d"


def _pct(a, b):
    return f"{round(100 * a / b)}%" if b else "0%"


@app.route("/")
def index():
    return send_from_directory(WEB, "index.html")


@app.route("/api/stats")
def stats():
    f = EST.stats_funil()
    total = sum(f.values())
    funil = [
        {"fase": "Leads", "n": total, "nota": f"{f['Lead']} em Lead"},
        {"fase": "Prospects", "n": f["Prospect"], "nota": _pct(f["Prospect"], total) + " do total"},
        {"fase": "Inscritos", "n": f["Inscrito"], "nota": _pct(f["Inscrito"], max(f["Prospect"], 1)) + " dos prospects"},
        {"fase": "Matriculados", "n": f["Matriculado"], "nota": _pct(f["Matriculado"], max(f["Inscrito"], 1)) + " dos inscritos"},
    ]
    escal = sum(1 for l in EST.listar_leads() if l["estado_conversa"] == "escalado_humano")
    return jsonify({"funil": funil, "escalonamentos_abertos": escal,
                    "em_espera": f["Em espera"], "perdidos": f["Perdido"]})


@app.route("/api/captabilidade")
def captabilidade():
    hoje = datetime.date.today()
    cursos = []
    for c in _ont().cursos():
        cap = _ont().captavel(c, hoje)
        info = _ont().curso(c)
        cursos.append({**cap, "nome": info["nome"], "alerta": info.get("alerta")})
    return jsonify({"hoje": hoje.isoformat(), "cursos": cursos})


@app.route("/api/reviewer", methods=["POST"])
def reviewer():
    b = request.get_json(silent=True) or {}
    return jsonify(revisar(b.get("curso", ""), b.get("texto", ""), _ont(),
                           usar_semantico=bool(b.get("semantico"))))


@app.route("/api/conversas")
def conversas():
    out = []
    for l in EST.listar_leads():
        prop = EST.decisao_pendente(l["card_id"])
        ult = EST.ultima_lead_msg(l["card_id"])
        if not prop and not ult:
            continue
        out.append({"id": l["card_id"], "lead": l["nome"], "iniciais": _iniciais(l["nome"]),
                    "curso": l["curso"], "estado": l["estado_conversa"], "ha": _ha(l["atualizado"]),
                    "ultima_lead": ult, "proposta": prop["mensagem"] if prop else None,
                    "thread": EST.historico(l["card_id"])[-8:]})
        if len(out) >= 12:
            break
    return jsonify(out)


@app.route("/api/conversas/<card_id>/responder", methods=["POST"])
def responder(card_id):
    b = request.get_json(silent=True) or {}
    try:
        return jsonify(ORQ.processar_mensagem(card_id, b.get("texto", "")))
    except ValueError as e:
        return jsonify({"erro": str(e)}), 404


@app.route("/api/decisoes/<card_id>")
def decisoes(card_id):
    return jsonify(EST.listar_decisoes(card_id))


def _primeiro_contato(card_id):
    """Caminho único do 1º contato: dispara feq_abertura_curso e põe o lead em
    aguardando_resposta. Usado pelo operador e pela ingestão Meta automática."""
    lead = EST.get_lead(card_id)
    if not lead:
        return {"ok": False, "erro": "lead inexistente"}
    curso = lead.get("curso", "")
    info = ONTO.ont.curso(curso) or {}
    primeiro_nome = (lead.get("nome") or "").split()[0] if lead.get("nome") else "olá"
    r = WA.enviar_template(lead["telefone"], "feq_abertura_curso",
                           params=[primeiro_nome, f"{info.get('nome', curso)} ({curso})"])
    if r.get("ok"):
        EST.add_conversa(card_id, "agente", f"[template feq_abertura_curso] {curso}")
        EST.set_lead(card_id, estado_conversa="aguardando_resposta")
    return r


@app.route("/api/conversas/iniciar", methods=["POST"])
def conversa_iniciar():
    """1º contato (operador): cria/acha o lead e dispara o template de abertura."""
    b = request.get_json(silent=True) or {}
    tel, curso = b.get("telefone", ""), b.get("curso", "")
    nome = b.get("nome", "")
    if not tel or not curso:
        return jsonify({"erro": "telefone e curso são obrigatórios"}), 400
    cap = ONTO.ont.captavel(curso)
    if not cap.get("captavel"):
        return jsonify({"erro": f"curso {curso} não captável ({cap.get('status')})"}), 409
    card_id = EST.lead_por_telefone(tel) or f"op-{''.join(c for c in tel if c.isdigit())}"
    EST.upsert_lead(card_id, nome=nome, telefone=tel, curso=curso,
                    fase="Lead", estado_conversa="aguardando_resposta")
    return jsonify({"card_id": card_id, "envio": _primeiro_contato(card_id)})


CFG = {"auto_aprovar": EST.get_config("auto_aprovar") == "1"}   # persistido: deploys reiniciam o serviço


def _aprovar_e_enviar(card_id, texto=None, ator="humano"):
    """Caminho único de aprovação: usado pelo endpoint e pelo modo automático."""
    prop = EST.decisao_pendente(card_id)
    if not prop:
        return {"erro": "sem proposta pendente"}, 404
    lead = EST.get_lead(card_id)
    texto = (texto or "").strip() or prop["mensagem"]
    if texto != prop["mensagem"]:  # edição humana também passa pelo Revisor (spec 14)
        rev = revisar(lead["curso"], texto, ONTO.ont)
        if not rev["aprovado"]:
            return {"erro": "edição bloqueada pelo Revisor", "bloqueios": rev["bloqueios"]}, 422
    r = WA.enviar(lead["telefone"], texto)
    if r.get("ok"):
        EST.add_conversa(card_id, "agente", texto)
        EST.set_decisao_status(prop["id"], "enviada")
        EST.log_acao(ator, "aprovar_enviar", card_id)
    return {"envio": r}, 200


@app.route("/api/conversas/<card_id>/aprovar", methods=["POST"])
def conversa_aprovar(card_id):
    b = request.get_json(silent=True) or {}
    corpo, code = _aprovar_e_enviar(card_id, b.get("texto"))
    return jsonify(corpo), code


@app.route("/api/config", methods=["GET", "POST"])
def config_ep():
    if request.method == "POST":
        b = request.get_json(silent=True) or {}
        if "auto_aprovar" in b:
            CFG["auto_aprovar"] = bool(b.get("auto_aprovar"))
            EST.set_config("auto_aprovar", "1" if CFG["auto_aprovar"] else "0")
            EST.log_acao("operador", f"auto_aprovar={CFG['auto_aprovar']}")
        if b.get("modelo"):
            prov = b["modelo"].partition(":")[0]
            if prov not in PROVEDORES or ":" not in b["modelo"]:
                return jsonify({"erro": "modelo deve ser 'claude:<id>', 'openai:<id>' ou 'gemini:<id>'"}), 400
            os.environ["ATENA_MODELO"] = b["modelo"]
            EST.set_config("modelo", b["modelo"])
            EST.log_acao("operador", f"modelo={b['modelo']}")
    prov, mid = modelo_atual()
    return jsonify({**CFG, "modelo": f"{prov}:{mid}",
                    "provedores": {p: bool(chave_do(p)) for p in PROVEDORES}})


@app.route("/api/config/testar", methods=["POST"])
def config_testar():
    b = request.get_json(silent=True) or {}
    m = b.get("modelo", "")
    if ":" not in m or m.partition(":")[0] not in PROVEDORES:
        return jsonify({"ok": False, "erro": "formato: provedor:id_do_modelo"}), 400
    return jsonify({"modelo": m, **testar_modelo(m)})


@app.route("/api/conversas/<card_id>/recusar", methods=["POST"])
def conversa_recusar(card_id):
    prop = EST.decisao_pendente(card_id)
    if not prop:
        return jsonify({"erro": "sem proposta pendente"}), 404
    EST.set_decisao_status(prop["id"], "recusada")
    return jsonify({"ok": True})


@app.route("/api/aprovacoes")
def aprovacoes():
    out = []
    for l in EST.listar_leads():
        if l["estado_conversa"] == "escalado_humano":
            out.append({"tipo": "escalonamento", "icone": "ti-alert-triangle",
                        "texto": f"{l.get('motivo') or 'escalonamento'} · {l['curso']} ({l['nome']})"})
    ativos = {}
    for l in EST.listar_leads():
        if l["fase"] in ("Lead", "Prospect"):
            ativos[l["curso"]] = ativos.get(l["curso"], 0) + 1
    for cod, n in sorted(ativos.items()):
        cap = _ont().captavel(cod)
        if cap.get("existe") and not cap.get("captavel"):
            out.append({"tipo": "captabilidade", "icone": "ti-calendar-x",
                        "texto": f"{cod} {cap['status']} · {n} leads ativos"})
    for p in EST.listar_propostas("proposta"):
        out.append({"tipo": "injetor", "icone": "ti-database-import",
                    "texto": f"{p['tipo']} · {p['alvo']}", "pid": p["id"]})
    for a in EST.listar_alertas(5):
        out.append({"tipo": "alerta", "icone": "ti-alert-circle",
                    "texto": f"[{a['tipo']}] {a['texto']} · {(a['criado'] or '')[5:16]}"})
    return jsonify(out)


@app.route("/api/injetor", methods=["POST"])
def injetor_propor():
    b = request.get_json(silent=True) or {}
    return jsonify(ONTO.propor(b.get("texto", ""), autor=b.get("autor", "operador")))


@app.route("/api/injetor/<pid>")
def injetor_get(pid):
    p = EST.get_proposta(pid)
    return (jsonify(p), 200) if p else (jsonify({"erro": "não encontrada"}), 404)


@app.route("/api/injetor/<pid>/aprovar", methods=["POST"])
def injetor_aprovar(pid):
    r = ONTO.aprovar(pid)
    ORQ.ont = ING.ont = CUR.ont = ONTO.ont   # todos passam a usar o grafo recarregado
    return jsonify(r)


@app.route("/api/ingestao/simulate", methods=["POST"])
def ingestao_simulate():
    return jsonify(ING.ingest(request.get_json(silent=True) or {}))


@app.route("/webhook/meta", methods=["GET", "POST"])
def webhook_meta():
    if request.method == "GET":
        if request.args.get("hub.verify_token") == os.environ.get("META_VERIFY_TOKEN", "troque-este-token"):
            return request.args.get("hub.challenge", ""), 200
        return "forbidden", 403
    if not G.verificar_assinatura(os.environ.get("META_APP_SECRET"), request.get_data(),
                                  request.headers.get("X-Hub-Signature-256")):
        return jsonify({"erro": "assinatura inválida"}), 403
    payload = request.get_json(silent=True) or {}
    # ACK imediato (a Meta reentrega se demorar); fetch na Graph + envio rodam fora do request
    threading.Thread(target=_processar_meta, args=(payload,), daemon=True).start()
    return jsonify({"ok": True})


def _processar_meta(payload):
    for entry in payload.get("entry", []):
        for ch in entry.get("changes", []):
            if ch.get("field") != "leadgen":
                continue
            v = ch.get("value", {})
            lid = str(v.get("leadgen_id", ""))
            if lid and EST.msg_ja_vista(f"leadgen:{lid}"):
                continue                    # reentrega da Meta -> já processado
            try:
                if v.get("field_data"):     # payload de teste/simulação com dados inline
                    lead = normalizar_meta(v.get("field_data"), v.get("campaign_name", ""), lid)
                else:                       # webhook real: só o ID -> Graph resolve dados + curso (nome do form)
                    lead = fetch_meta_lead(lid, v.get("form_id"))
                if not lead:
                    EST.add_alerta("ingestao",
                                   f"leadgen {lid} sem fetch (token com leads_retrieval?)")
                    continue
                r = ING.ingest(lead)
                if not r.get("ok") or r.get("acao") != "criado":
                    continue                # dedupe por telefone ou lead sem telefone
                EST.log_acao("meta",
                             f"lead {r['card_id']} · curso {lead.get('curso') or '?'} · consentimento no formulário",
                             r["card_id"])
                if r.get("captavel"):
                    _primeiro_contato(r["card_id"])   # a conversa no WhatsApp continua o processo
                else:
                    EST.add_alerta("ingestao",
                                   f"lead {r['card_id']} — curso '{lead.get('curso') or '?'}' não captável "
                                   f"(form: '{lead.get('form', '')}'), revisar")
            except Exception as e:
                print(f"[webhook-meta] falha ao processar leadgen {lid}: {e}", flush=True)
                EST.add_alerta("ingestao", f"falha ao processar leadgen {lid}: {e}")


@app.route("/webhook/whatsapp", methods=["GET", "POST"])
def webhook_whatsapp():
    if request.method == "GET":
        body, code = WA.handshake(request.args)
        return body, code
    if not G.verificar_assinatura(os.environ.get("META_APP_SECRET"), request.get_data(),
                                  request.headers.get("X-Hub-Signature-256")):
        return jsonify({"erro": "assinatura inválida"}), 403
    payload = request.get_json(silent=True) or {}
    # ACK imediato (a Meta reentrega se demorar); turno de LLM roda fora do request
    threading.Thread(target=_processar_whatsapp, args=(payload,), daemon=True).start()
    return jsonify({"ok": True})


def _processar_whatsapp(payload):
    try:
        res = WA.receber(payload)
        if CFG["auto_aprovar"]:
            for r in res:
                if r.get("card_id") and r.get("revisor", {}).get("aprovado"):
                    _aprovar_e_enviar(r["card_id"], ator="auto")
    except Exception as e:
        print(f"[webhook] processamento falhou: {e}", flush=True)
        EST.add_alerta("webhook", f"falha ao processar mensagem: {e}")


@app.route("/api/duvidas")
def duvidas_listar():
    out = []
    for d in EST.listar_duvidas("aberta"):
        l = EST.get_lead(d["card_id"]) or {}
        out.append({**d, "lead": l.get("nome") or d["card_id"], "curso": l.get("curso", "")})
    return jsonify(out)


@app.route("/api/duvidas/<int:did>/responder", methods=["POST"])
def duvidas_responder(did):
    b = request.get_json(silent=True) or {}
    resposta = (b.get("resposta") or "").strip()
    if not resposta:
        return jsonify({"erro": "resposta vazia"}), 400
    r = ORQ.responder_duvida(did, resposta)
    if r.get("erro"):
        return jsonify(r), 404
    if CFG["auto_aprovar"] and r["revisor"]["aprovado"]:
        corpo, _ = _aprovar_e_enviar(r["card_id"], ator="coordenacao")
        r["envio"] = corpo.get("envio") or corpo.get("erro")
    return jsonify(r)


@app.route("/api/curador")
def curador_ep():
    return jsonify(CUR.varrer())


@app.route("/api/auditoria")
def auditoria_ep():
    return jsonify(EST.listar_auditoria())


@app.route("/api/injetor/<pid>/rejeitar", methods=["POST"])
def injetor_rejeitar(pid):
    return jsonify(ONTO.rejeitar(pid))


def fazer_backup():
    """Backup consistente do banco + catálogo (retenção: 14 de cada)."""
    import shutil
    import sqlite3 as sq
    bdir = pathlib.Path(__file__).resolve().parents[1] / "backups"
    bdir.mkdir(exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M")
    with EST._lock:
        dest = sq.connect(str(bdir / f"atena_{ts}.db"))
        EST.conn.backup(dest)
        dest.close()
    shutil.copy(pathlib.Path(__file__).resolve().parents[1] / "ontologia" / "catalogo.jsonld",
                bdir / f"catalogo_{ts}.jsonld")
    for velhos in (sorted(bdir.glob("atena_*.db"))[:-14], sorted(bdir.glob("catalogo_*.jsonld"))[:-14]):
        for f in velhos:
            f.unlink()
    EST.log_acao("sistema", "backup", ts)
    return {"ok": True, "backup": ts, "arquivos": len(list(bdir.iterdir()))}


def _canario():
    """Autoteste diário do pipeline: LLM (contrato JSON), Revisor e ontologia."""
    problemas = []
    r = testar_modelo(":".join(modelo_atual()))
    if not r.get("ok"):
        problemas.append(f"LLM {':'.join(modelo_atual())}: {r.get('erro')}")
    rev = revisar("FEQ-0480", "São 19x de R$ 866,09.", ONTO.ont)
    if not rev["aprovado"]:
        problemas.append("Revisor bloqueou um fato verdadeiro")
    if not ONTO.ont.captavel("FEQ-0480").get("existe"):
        problemas.append("ontologia não carregou o catálogo")
    if problemas:
        EST.add_alerta("canario", "Canário falhou: " + "; ".join(problemas))
    else:
        EST.log_acao("sistema", "canario_ok")


def _despachar_alertas():
    """Alerta novo -> tenta avisar o gestor por WhatsApp (best effort; a Central sempre mostra)."""
    dest = EST.get_config("alerta_whatsapp") or "5519936185086"
    for a in EST.alertas_nao_notificados():
        try:
            WA.enviar(dest, f"⚠️ Atena [{a['tipo']}]: {a['texto']}")
        except Exception:
            pass
        EST.marcar_alerta_notificado(a["id"])


def _rodou_hoje(chave):
    hoje = datetime.date.today().isoformat()
    if EST.get_config(chave) == hoje:
        return True
    EST.set_config(chave, hoje)
    return False


def _operacoes():
    """Agendador único (tick 60s): alertas, degradação, backup, canário, nutrição, LGPD."""
    import time as _t
    from atena.agentes import cerebro as _cer
    while True:
        _t.sleep(60)
        try:
            _despachar_alertas()
            # degradação: 3+ fallbacks determinísticos em 15 min -> alerta (máx. 1 a cada 30 min)
            recentes = [t for t in _cer.FALLBACKS if _t.time() - t < 900]
            ultimo = float(EST.get_config("op_fb_alerta") or 0)
            if len(recentes) >= 3 and _t.time() - ultimo > 1800:
                EST.set_config("op_fb_alerta", str(_t.time()))
                EST.add_alerta("degradacao",
                               f"{len(recentes)} respostas caíram no fallback determinístico em 15 min — verificar journalctl")
            h = datetime.datetime.utcnow().hour
            if h == 6 and not _rodou_hoje("op_backup"):       # 03:30h BRT ~ 06h UTC
                fazer_backup()
            if h == 7 and not _rodou_hoje("op_lgpd"):
                EST.anonimizar_antigos(dias=180)
            if h == 11 and not _rodou_hoje("op_canario"):     # 08h BRT
                _canario()
            if h == 14 and not _rodou_hoje("op_nutricao"):    # 11h BRT
                nutricao.reengajar(EST, ONTO.ont, WA)
        except Exception as e:
            print(f"[operacoes] tick falhou: {e}", flush=True)


import threading
threading.Thread(target=_operacoes, daemon=True).start()


@app.route("/api/backup", methods=["POST"])
def backup_ep():
    try:
        return jsonify(fazer_backup())
    except Exception as e:
        return jsonify({"ok": False, "erro": str(e)}), 500


@app.route("/health")
def health():
    return jsonify({"ok": True, "cursos": len(_ont().cursos()), "leads": len(EST.listar_leads())})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8095"))
    print(f"Central de Operação FEQ em http://localhost:{port}")
    try:
        from waitress import serve       # servidor de produção (24/7)
        serve(app, host="0.0.0.0", port=port, threads=8)
    except ImportError:
        app.run(host="0.0.0.0", port=port)   # fallback dev
