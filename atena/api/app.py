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
import json
import hmac
import time
import secrets
import datetime
import pathlib
import tempfile
from urllib.parse import quote

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

from flask import Flask, jsonify, request, send_from_directory, session

from atena.revisor.ontologico import revisar
from atena.core.estado import Estado, FASES_PROFESSOR
from atena.core.orquestrador import Orquestrador
from atena.core import guardrails as G
from atena.core import auth
from atena.agentes.ontologista import Ontologista
from atena.agentes.ingestao import Ingestao, normalizar_meta, fetch_meta_lead
from atena.agentes import nutricao, reconciliacao
from atena.transporte import reenvio
from atena.agentes.curador import Curador
from atena.agentes.cerebro import cerebro_factory, modelo_atual, testar_modelo, PROVEDORES, chave_do
from atena.transporte.whatsapp import (WhatsApp, TEMPLATES_ROL as WhatsApp_TEMPLATES,
                                       CATEGORIAS_TEMPLATE as WhatsApp_CATEGORIAS,
                                       _n_variaveis)
from atena.growth.db import Growth
from atena.growth.meta_ads import MetaAds
from atena.growth import ponte
from atena.coleta import extecamp as coleta_extecamp
from atena.coleta import sincronizacao as coleta_sinc
from atena.growth import funil as growth_funil
from atena.growth import pdf_guia

EST = Estado()
if os.environ.get("ATENA_SEED_DEMO") == "1":   # produção fica só com leads reais do sistema
    EST.seed_demo()
_modelo_salvo = EST.get_config("modelo")
if _modelo_salvo:
    os.environ["ATENA_MODELO"] = _modelo_salvo   # escolha da Central sobrevive a restart
ONTO = Ontologista(EST)              # detém o grafo (.ont) — fonte da verdade
ORQ = Orquestrador(estado=EST, ont=ONTO.ont, brain=cerebro_factory(ONTO.ont))
ING = Ingestao(EST, ONTO.ont, faro=ORQ.faro)
WA = WhatsApp(EST, ORQ)
CUR = Curador(EST, ONTO.ont)
GRW = Growth()                       # growth engine (specs 15/16) — mesmo SQLite, WAL
ADS = MetaAds()
WEB = pathlib.Path(__file__).resolve().parents[1] / "web"
app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 105 * 1024 * 1024   # teto de upload (doc da Meta = 100 MB)
from werkzeug.middleware.proxy_fix import ProxyFix
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)   # IP real atrás do Caddy
auth.init_app(app, EST)             # login/RBAC/auditoria — /api/* exige sessão


def _ont():
    return ONTO.ont


def _iniciais(nome):
    ps = (nome or "?").split()
    return (ps[0][:1] + (ps[1][:1] if len(ps) > 1 else "")).upper()


def _so_digitos(s):
    return "".join(c for c in (s or "") if c.isdigit())


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


@app.route("/assets/<path:arq>")
def assets(arq):
    """Logos e estáticos da Central (públicos — a tela de login também usa)."""
    return send_from_directory(str(WEB / "assets"), arq)


@app.route("/api/stats")
def stats():
    from atena.core.estado import ESTEIRA, alcancou
    f = EST.stats_funil()
    total = sum(f.values())
    insc = sum(n for fase, n in f.items() if alcancou(fase, "Inscrito"))
    funil = [
        {"fase": "Leads", "n": total, "nota": f"{f['Lead']} em Lead"},
        {"fase": "Prospects", "n": f["Prospect"], "nota": _pct(f["Prospect"], total) + " do total"},
        # CUMULATIVO: quem está em "Taxa paga" é inscrito também. Contar só a coluna
        # "Inscrito" faria a conversão despencar sempre que alguém avançasse.
        {"fase": "Inscritos", "n": insc, "nota": _pct(insc, max(f["Prospect"], 1)) + " dos prospects"},
        {"fase": "Matriculados", "n": f["Matriculado"], "nota": _pct(f["Matriculado"], max(insc, 1)) + " dos inscritos"},
    ]
    escal = sum(1 for l in EST.listar_leads() if l["estado_conversa"] == "escalado_humano")
    return jsonify({"funil": funil, "escalonamentos_abertos": escal,
                    "em_espera": f["Em espera"],
                    "perdidos": f["Perdido"] + f.get("Cancelado", 0)})


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


_MIDIA_ROTULO = {"audio": "🎤 áudio", "image": "🖼️ imagem", "video": "🎥 vídeo",
                 "document": "📎 documento", "sticker": "🌟 figurinha"}


def _preview(turno):
    """Texto de preview da lista; se o último turno for mídia, mostra o rótulo."""
    if not turno:
        return ""
    if turno.get("media_id") and turno.get("tipo", "texto") != "texto":
        rot = _MIDIA_ROTULO.get(turno["tipo"], f"[{turno['tipo']}]")
        return f"{rot} · {turno['texto']}" if turno.get("texto") else rot
    return turno.get("texto") or ""


@app.route("/api/conversas")
def conversas():
    """Lista leve (estilo WhatsApp: nome, preview, hora) — a thread completa só
    carrega quando a conversa é aberta (/api/conversas/<id>)."""
    out = []
    nao_lidas = EST.nao_lidas()          # uma consulta só, não uma por conversa
    for l in EST.listar_leads():
        hist = EST.historico(l["card_id"])
        prop = EST.decisao_pendente(l["card_id"])
        if not hist and not prop:
            continue   # nunca houve contato nem proposta -> não é uma "conversa" ainda
        ult = hist[-1] if hist else None
        out.append({"id": l["card_id"], "lead": l["nome"], "iniciais": _iniciais(l["nome"]),
                    "telefone": l.get("telefone") or "",   # fallback de exibição sem nome
                    "curso": l["curso"], "cursos": EST.cursos_interesse(l),  # filtro por curso (aba Conversas)
                    "estado": l["estado_conversa"], "ha": _ha(l["atualizado"]),
                    "optout": bool(l.get("optout")), "modo": l.get("modo") or "auto",
                    "preview": _preview(ult)[:80],
                    "preview_de_lead": bool(ult and ult["papel"] == "lead"),
                    "aguardando": bool(ult and ult["papel"] == "lead"),   # último turno foi do lead
                    "nao_lidas": nao_lidas.get(l["card_id"], 0),
                    "pendente": bool(prop)})
    return jsonify(out)


@app.route("/api/conversas/<card_id>")
def conversa_detalhe(card_id):
    """Thread completa de UM lead — carregada ao abrir a conversa."""
    lead = EST.get_lead(card_id)
    if not lead:
        return jsonify({"erro": "lead inexistente"}), 404
    prop = EST.decisao_pendente(card_id)
    # abrir a conversa É a leitura. Devolve a marca d'água ANTERIOR para a UI desenhar
    # a linha de "não lidas" no ponto certo — depois de marcada, ela se perderia.
    lido_ate = EST.marcar_lido(card_id)
    return jsonify({"id": card_id, "lead": lead["nome"], "iniciais": _iniciais(lead["nome"]),
                    "lido_ate": lido_ate,
                    "curso": lead["curso"], "cursos": EST.cursos_interesse(lead),
                    "estado": lead["estado_conversa"], "fase": lead["fase"],
                    "telefone": lead["telefone"], "optout": bool(lead.get("optout")),
                    "email": lead.get("email") or "", "notas": lead.get("notas") or "",
                    "utm": EST.utm_de(lead), "criado": lead.get("criado"),
                    "modo": lead.get("modo") or "auto",
                    "thread": EST.historico(card_id),
                    # Ficha da etapa 2 junto do card: era o dado que o agente mais
                    # precisava ter à mão durante a conversa (e antes só existia numa
                    # aba separada, obrigando a sair do atendimento para consultar).
                    # Chave `inscricao` e não `ficha`: a UI já chama de "ficha" a faixa
                    # de contato do CRM (telefone/e-mail/notas) — dois nomes iguais para
                    # coisas diferentes na mesma tela é confusão garantida.
                    "inscricao": _ficha_do_card(card_id),
                    "proposta": prop["mensagem"] if prop else None})


def _ficha_do_card(card_id):
    i = GRW.get_inscricao(card_id)
    if not i:
        return None
    return {"id": i["id"], "curso": i["curso"], "turma": i["turma"],
            "status": i["status"], "criado": i["criado"],
            "grupos": _ficha_para_humano(i["dados_json"])}


@app.route("/api/leads/<card_id>/notas", methods=["POST"])
def lead_notas(card_id):
    """Notas do operador sobre o lead — memória de CRM que a conversa não guarda."""
    if not EST.get_lead(card_id):
        return jsonify({"erro": "lead inexistente"}), 404
    EST.set_notas(card_id, (request.get_json(silent=True) or {}).get("notas", ""))
    EST.log_acao(session.get("usuario", "operador"), "notas do lead", card_id)
    return jsonify({"ok": True})


@app.route("/api/leads/<card_id>/contato", methods=["POST"])
def lead_contato(card_id):
    """Completa contato à mão (ex.: e-mail que o lead passou na conversa)."""
    if not EST.get_lead(card_id):
        return jsonify({"erro": "lead inexistente"}), 404
    b = request.get_json(silent=True) or {}
    campos = {}
    if "email" in b:
        email = (b.get("email") or "").strip()[:180]
        if email and "@" not in email:
            return jsonify({"erro": "e-mail inválido"}), 400
        campos["email"] = email
    if not campos:
        return jsonify({"erro": "nada para atualizar"}), 400
    EST.set_lead(card_id, **campos)
    EST.log_acao(session.get("usuario", "operador"), f"contato atualizado: {list(campos)}", card_id)
    return jsonify({"ok": True, **campos})


@app.route("/api/conversas/<card_id>/modo", methods=["POST"])
def conversa_modo(card_id):
    """Chave auto/manual por conversa. Manual: o agente para de gerar respostas;
    o operador conduz pelo compositor. Auto: o agente volta a propor respostas."""
    if not EST.get_lead(card_id):
        return jsonify({"erro": "lead inexistente"}), 404
    modo = "manual" if (request.get_json(silent=True) or {}).get("modo") == "manual" else "auto"
    EST.set_lead(card_id, modo=modo)
    EST.log_acao(session.get("usuario", "operador"), f"modo={modo}", card_id)
    return jsonify({"ok": True, "modo": modo})


from atena.core.estado import FASES          # esteira + saídas, fonte única


@app.route("/api/kanban")
def kanban():
    """Funil de leads por fase — cartões com sinais de 'precisa de você' para o
    operador triar; o motor autônomo move a maioria sozinho."""
    cols = {f: [] for f in FASES}
    for l in EST.listar_leads():
        hist = EST.historico(l["card_id"])
        ult = hist[-1] if hist else None
        fase = l["fase"] if l["fase"] in cols else "Lead"
        cols[fase].append({
            # Sem nome, o card_id ("wa-554199515077") NÃO entra aqui — é chave interna,
            # não é para o operador ler. O front cai para o telefone formatado; se nem
            # telefone houver, mostra "(sem nome)". Ver perfil do WhatsApp em receber().
            "id": l["card_id"], "nome": l["nome"] or "",
            "telefone": l.get("telefone") or "",     # busca do funil aceita número
            "email": l.get("email") or "",
            "utm": EST.utm_de(l), "tem_notas": bool((l.get("notas") or "").strip()),
            "iniciais": _iniciais(l["nome"]), "curso": l["curso"],
            "cursos": EST.cursos_interesse(l),
            "estado": l["estado_conversa"], "ha": _ha(l["atualizado"]),
            "optout": bool(l.get("optout")),
            "precisa_voce": l["estado_conversa"] == "escalado_humano",
            "proposta": bool(EST.decisao_pendente(l["card_id"])),
            "aguardando": bool(ult and ult["papel"] == "lead"),
            "curso_encerrado": not ONTO.ont.captavel(l["curso"]).get("captavel"),
        })
    return jsonify({"colunas": [{"fase": f, "leads": cols[f]} for f in FASES]})


@app.route("/api/kanban/criar", methods=["POST"])
def kanban_criar():
    """Cria um card manualmente no Funil (lead que não veio por ingestão automática
    — indicação, contato feito fora do WhatsApp, prospecção do operador).

    IDEMPOTENTE por telefone, mesma regra de sempre (CLAUDE.md §7 — dedupe de lead é
    SEMPRE por telefone): número que já tem card não cria um segundo. Em vez disso
    atualiza o existente — preenche o nome só se estava vazio (nunca sobrescreve o
    que já foi corrigido) e soma o curso ao interesse (não troca o ativo à força) —
    e devolve `criado:false` com o card_id de verdade, para a tela nunca duplicar."""
    b = request.get_json(silent=True) or {}
    tel = (b.get("telefone") or "").strip()
    nome = (b.get("nome") or "").strip()
    curso = (b.get("curso") or "").strip().upper()
    if not tel:
        return jsonify({"erro": "telefone é obrigatório"}), 400
    if len(_so_digitos(tel)) < 10:
        return jsonify({"erro": "telefone inválido (informe DDD + número)"}), 400
    if curso and not ONTO.ont.curso(curso):
        return jsonify({"erro": f"curso '{curso}' não está no catálogo"}), 400
    usuario = session.get("usuario", "operador")
    existente = EST.lead_por_telefone(tel)
    if existente:
        lead = EST.get_lead(existente) or {}
        if nome and not (lead.get("nome") or "").strip():
            EST.upsert_lead(existente, nome=nome)
        if curso and curso not in EST.cursos_interesse(lead):
            EST.add_curso_interesse(existente, curso)
        EST.log_acao(usuario, "tentativa de criar card novo — telefone já cadastrado, "
                              "card existente atualizado em vez de duplicado", existente)
        return jsonify({"ok": True, "card_id": existente, "criado": False,
                        "nome": (EST.get_lead(existente) or {}).get("nome")})
    card_id = f"op-{_so_digitos(tel)}"
    EST.upsert_lead(card_id, nome=nome, telefone=tel, curso=curso,
                    cursos_interesse=curso, fase="Lead", estado_conversa="qualificacao")
    EST.log_acao(usuario, f"card criado manualmente no Funil{f' ({curso})' if curso else ''}",
                 card_id)
    return jsonify({"ok": True, "card_id": card_id, "criado": True})


def _mover_fase(lead, card_id, fase):
    """Núcleo do move de fase (kanban E aba Verificação): estado + espelho FARO +
    conversão offline idempotente (spec 17 §8). Retorna o event_id CAPI ou None."""
    EST.set_lead(card_id, fase=fase)
    if lead.get("faro_id") and ORQ.faro.ativo:
        try:
            ORQ.faro.mover(lead["faro_id"], fase)
        except Exception as e:
            print(f"[faro] mover falhou: {e}", flush=True)
    # Conversão offline (spec 17 §8): o gesto do operador É o reporte à Meta.
    tipo = {"Inscrito": "inscricao_confirmada", "Matriculado": "matricula_confirmada"}.get(fase)
    if not tipo or any(e["tipo"] == tipo for e in GRW.eventos(card_id=card_id)):
        return None
    try:
        c = ONTO.ont.curso(lead.get("curso") or "")
    except Exception:
        c = None
    valor = growth_funil.valor_curso(c) if tipo == "matricula_confirmada" else None
    return growth_funil.registrar(GRW, ADS, EST, card_id, tipo, valor=valor).get("capi")


@app.route("/api/leads/<card_id>/mover", methods=["POST"])
def lead_mover(card_id):
    """Move manual de fase (override humano). Espelha no FARO se ativo."""
    lead = EST.get_lead(card_id)
    if not lead:
        return jsonify({"erro": "lead inexistente"}), 404
    fase = (request.get_json(silent=True) or {}).get("fase", "")
    if fase not in FASES:
        return jsonify({"erro": "fase inválida"}), 400
    capi = _mover_fase(lead, card_id, fase)
    EST.log_acao(session.get("usuario", "operador"), f"mover fase -> {fase}", card_id)
    return jsonify({"ok": True, "fase": fase, "conversao_offline": capi})


@app.route("/api/verificacao")
def verificacao_lista():
    """Fila da aba Verificação (spec 17 §8b): leads da LP que seguiram pra
    Extecamp e ainda não têm desfecho — nem conversão, nem veredito humano."""
    out = []
    for p in GRW.verificacoes_pendentes():
        l = EST.get_lead(p["card_id"]) or {}
        out.append({**p, "nome": l.get("nome"), "telefone": l.get("telefone"),
                    "fase": l.get("fase")})
    out.sort(key=lambda x: x.get("ultimo_checkout") or "", reverse=True)
    return jsonify({"pendentes": out, "auto": CFG["verif_auto"],
                    "horas": CFG["verif_auto_horas"], "min_horas": VERIF_AUTO_MIN_H,
                    "elegiveis": len(_verif_elegiveis())})


def _verif_elegiveis(horas=None, agora=None):
    """Pendências antigas o bastante para o veredito automático de 'não inscreveu'.

    Quem entregou a ficha da etapa 2 e ainda não foi transcrito NÃO entra: a bola está
    com a gente, não com o lead. Declarar 'não inscreveu' e disparar 'teve dificuldade?'
    para alguém que acabou de nos mandar CPF e endereço seria constrangedor — e
    envenenaria o rótulo verificacao_negativa, que é o que mede o funil (spec 18)."""
    horas = horas if horas is not None else CFG["verif_auto_horas"]
    agora = agora or datetime.datetime.now()
    fora = []
    for p in GRW.verificacoes_pendentes():
        insc = GRW.get_inscricao(p["card_id"])
        if insc and insc["status"] == "recebida":
            continue
        try:
            idade = (agora - datetime.datetime.fromisoformat(p["ultimo_checkout"])).total_seconds() / 3600
        except (TypeError, ValueError):
            continue
        if idade >= horas:
            fora.append({**p, "horas": round(idade, 1)})
    return fora


def _falou_agora_ha_pouco(card_id, agora=None):
    """Mandamos mensagem a este lead nos últimos minutos? Evita repetir o mesmo
    template em segundos quando o lead veio do Meta (já recebeu a abertura) e logo
    depois preenche o formulário da LP."""
    agora = agora or datetime.datetime.now()
    ult = None
    for h in EST.historico(card_id):
        if h.get("papel") == "agente" and h.get("ts"):
            ult = h["ts"]
    if not ult:
        return False
    try:
        return (agora - datetime.datetime.fromisoformat(ult)).total_seconds() < VERIF_AUTO_SILENCIO_S
    except (TypeError, ValueError):
        return False


def _verificacao_auto(limite=15, agora=None, apenas=None):
    """Aplica 'não inscreveu' nas pendências vencidas — o MESMO caminho do botão do
    operador (grava verificacao_negativa e dispara o próximo toque da cadência, que
    tem guardas próprias: teto de toques, opt-out, lead que já respondeu, curso
    captável). Teto por passada para não virar disparo em massa ao ligar a chave.
    `apenas`: restringe a um card (modo imediato, chamado pelo webhook da LP)."""
    feitos = []
    alvos = _verif_elegiveis(agora=agora)
    if apenas:
        alvos = [p for p in alvos if p["card_id"] == apenas]
    for p in alvos[:limite]:
        cid = p["card_id"]
        lead = EST.get_lead(cid)
        if not lead:
            continue
        if _falou_agora_ha_pouco(cid, agora):
            EST.log_acao("auto", "verificação automática adiada: já falamos com o lead "
                                 "há poucos minutos (evita template repetido)", cid)
            continue
        GRW.add_evento(cid, lead.get("curso") or "", "verificacao_negativa")
        r = growth_funil.ativar_um(GRW, EST, ONTO.ont, WA, cid)
        EST.log_acao("auto", f"verificação automática: NÃO inscrito após "
                             f"{p['horas']}h ({'toque enviado' if r.get('ok') else r.get('motivo', '-')})", cid)
        feitos.append({"card_id": cid, "horas": p["horas"], "toque": r.get("ok", False)})
    return feitos


@app.route("/api/verificacao/auto", methods=["POST"])
def verificacao_auto_agora():
    """Roda a passada agora (o operador não precisa esperar o agendador)."""
    if not CFG["verif_auto"]:
        return jsonify({"erro": "a decisão automática está desligada"}), 409
    return jsonify({"aplicados": _verificacao_auto()})


@app.route("/api/leads/<card_id>/verificar", methods=["POST"])
def lead_verificar(card_id):
    """Veredito do operador após conferir no sistema da Extecamp (spec 17 §8b):
    inscrito=true -> fase Inscrito (+CAPI offline, mesmo caminho do kanban);
    inscrito=false -> continua Lead, sai da fila (verificacao_negativa) e recebe
    o próximo toque da cadência AGORA. Fecha o loop da LP."""
    lead = EST.get_lead(card_id)
    if not lead:
        return jsonify({"erro": "lead inexistente"}), 404
    b = request.get_json(silent=True) or {}
    if not isinstance(b.get("inscrito"), bool):
        return jsonify({"erro": "faltou 'inscrito' (true/false)"}), 400
    usuario = session.get("usuario", "operador")
    if b["inscrito"]:
        capi = _mover_fase(lead, card_id, "Inscrito")
        EST.log_acao(usuario, "verificação Extecamp: inscrito ✓", card_id)
        return jsonify({"ok": True, "fase": "Inscrito", "conversao_offline": capi})
    GRW.add_evento(card_id, lead.get("curso") or "", "verificacao_negativa")
    r = growth_funil.ativar_um(GRW, EST, ONTO.ont, WA, card_id)
    EST.log_acao(usuario, "verificação Extecamp: NÃO inscrito -> ativação imediata "
                          f"({'toque enviado' if r.get('ok') else r.get('motivo', '?')})", card_id)
    return jsonify({"ok": True, "fase": lead["fase"], "ativacao": r})


# ------------------------------------------------- Inscrições (fichas da etapa 2)
# Substituiu a aba Verificação. A pergunta mudou: não é mais "ele se inscreveu na
# Extecamp?" (que exigia o operador ir conferir lá), é "já transcrevi a ficha que ele
# entregou aqui?". O dado que antes faltava agora está no sistema.
_ROTULO_FICHA = {
    "cpf": "CPF", "nascimento": "Nascimento", "doc_tipo": "Documento",
    "doc_numero": "Nº do documento", "doc_emissor": "Emissor", "sexo": "Sexo",
    "estado_civil": "Estado civil", "cidade_nascimento": "Cidade natal",
    "cep": "CEP", "rua": "Rua", "numero": "Número", "complemento": "Complemento",
    "bairro": "Bairro", "cidade": "Cidade", "estado": "UF",
    "escolaridade": "Escolaridade", "instituicao": "Instituição",
    "curso_concluido": "Curso concluído", "ano_conclusao": "Ano de conclusão",
    "situacao": "Situação", "empresa": "Empresa", "cargo": "Cargo",
    "experiencia_anos": "Experiência (anos)",
    "forma_pagamento": "Forma de pagamento", "comprovante_em": "Comprovante em nome de",
}
# Ordem de leitura para quem vai TRANSCREVER: a mesma do formulário da Extecamp.
# Um dict solto obrigaria o agente a caçar campo por campo na tela.
_GRUPOS_FICHA = (
    ("Dados pessoais", ("cpf", "nascimento", "doc_tipo", "doc_numero", "doc_emissor",
                        "sexo", "estado_civil", "cidade_nascimento")),
    ("Endereço", ("cep", "rua", "numero", "complemento", "bairro", "cidade", "estado")),
    ("Formação e atuação", ("escolaridade", "instituicao", "curso_concluido",
                            "ano_conclusao", "situacao", "empresa", "cargo",
                            "experiencia_anos")),
    ("Pagamento", ("forma_pagamento", "comprovante_em")),
)


def _ficha_para_humano(dados_json):
    """A ficha em grupos rotulados, pronta para leitura/transcrição."""
    try:
        d = json.loads(dados_json or "{}")
    except ValueError:
        d = {}
    grupos = []
    for titulo, campos in _GRUPOS_FICHA:
        itens = [{"campo": c, "rotulo": _ROTULO_FICHA.get(c, c), "valor": d[c]}
                 for c in campos if d.get(c)]
        if itens:
            grupos.append({"titulo": titulo, "itens": itens})
    # campo novo no formulário que ainda não tem rótulo aqui não pode sumir da tela
    conhecidos = {c for _, campos in _GRUPOS_FICHA for c in campos}
    extras = [{"campo": k, "rotulo": _ROTULO_FICHA.get(k, k), "valor": v}
              for k, v in d.items() if k not in conhecidos and v]
    if extras:
        grupos.append({"titulo": "Outros", "itens": extras})
    return grupos


@app.route("/api/inscricoes")
def inscricoes_lista():
    """Fila de fichas da etapa 2: o que o agente comercial precisa transcrever."""
    status = (request.args.get("status") or "").strip() or None
    out = []
    for i in GRW.inscricoes(status=status):
        lead = EST.get_lead(i["card_id"]) or {}
        out.append({
            "id": i["id"], "card_id": i["card_id"], "curso": i["curso"],
            "turma": i["turma"], "status": i["status"], "criado": i["criado"],
            "nome": lead.get("nome"), "telefone": lead.get("telefone"),
            "email": lead.get("email"), "fase": lead.get("fase"),
            "grupos": _ficha_para_humano(i["dados_json"]),
        })
    pendentes = sum(1 for i in out if i["status"] == "recebida")
    return jsonify({"inscricoes": out, "pendentes": pendentes})


@app.route("/api/inscricoes/<card_id>/transcrita", methods=["POST"])
def inscricao_transcrita(card_id):
    """O agente lançou a ficha na Extecamp. É ESTE gesto que confirma a conversão:
    marca a ficha e reporta `inscricao_confirmada` (CAPI offline), do mesmo jeito que
    mover o card para Inscrito no kanban — sem duplicar se já foi reportado."""
    lead = EST.get_lead(card_id)
    ficha = GRW.get_inscricao(card_id)
    if not lead or not ficha:
        return jsonify({"erro": "ficha ou lead inexistente"}), 404
    usuario = session.get("usuario", "operador")
    GRW.set_inscricao_status(ficha["id"], "transcrita")
    capi = _mover_fase(lead, card_id, "Inscrito")
    EST.log_acao(usuario, f"ficha transcrita na Extecamp ({ficha['curso']})", card_id)
    return jsonify({"ok": True, "status": "transcrita", "conversao_offline": capi})


# ------------------------------------------------------ Central de Notificações
@app.route("/api/notificacoes")
def notificacoes():
    """Uma caixa de entrada só, em vez de o agente varrer 5 abas atrás de trabalho.

    O feed é COMPOSTO na leitura a partir das filas que já existem — não há um
    segundo caminho de escrita para desincronizar. `alertas` entra porque é a única
    fila com estado próprio de leitura."""
    itens = []
    for a in EST.alertas_nao_lidos():
        # Alerta de tipo 'inscricao' fica FORA: a fila de fichas logo abaixo já
        # representa o mesmo fato, e com estado melhor (some quando o trabalho é
        # feito, em vez de depender de alguém marcar como lido). O alerta continua
        # existindo para o disparo externo ao operador (`alertas_nao_notificados`) —
        # o que não pode é a mesma ficha ocupar duas linhas da caixa de entrada.
        if a["tipo"] == "inscricao":
            continue
        itens.append({"fonte": "alerta", "id": a["id"], "tipo": a["tipo"],
                      "texto": a["texto"], "card_id": a.get("card_id"),
                      "ts": a["criado"], "lido": False})
    for i in GRW.inscricoes(status="recebida"):
        lead = EST.get_lead(i["card_id"]) or {}
        itens.append({"fonte": "inscricao", "id": i["id"], "tipo": "ficha_nova",
                      "texto": f"Ficha completa de {lead.get('nome') or i['card_id']} "
                               f"({i['curso']}) aguardando transcrição na Extecamp",
                      "card_id": i["card_id"], "ts": i["criado"], "lido": False,
                      "acao": "inscricoes"})
    agora = datetime.datetime.now()
    # A fila de aprovações mede idade em HORAS; alertas e fichas têm timestamp. Para
    # ordenar tudo junto, converto horas -> ts (não o contrário): timestamp é o dado
    # mais preciso e o único que sobrevive a um reload da página.
    ABAS = {"abrir": "conversas", "duvidas": "duvidas", "injetor": "injetor"}
    try:
        # só o que EXIGE decisão humana (prioridade <=3); captabilidade e afins são
        # informativos e já têm lugar próprio — repetir aqui vira ruído na caixa.
        for p in _aprovacoes_payload()["itens"]:
            if p.get("prioridade", 9) > 3:
                continue
            h = p.get("horas")
            ts = ((agora - datetime.timedelta(hours=h)).isoformat()
                  if isinstance(h, (int, float)) else agora.isoformat())
            itens.append({"fonte": "aprovacao", "id": p.get("card_id") or p.get("pid"),
                          "tipo": p.get("tipo") or "aprovacao",
                          "texto": f"{p.get('nome') or p.get('curso') or ''}: {p.get('motivo') or ''}".strip(": "),
                          "card_id": p.get("card_id"), "ts": ts, "lido": False,
                          "acao": ABAS.get(p.get("acao"), "aprovacoes")})
    except Exception as e:      # a caixa de entrada nunca pode cair por causa de uma fonte
        print(f"[notificacoes] aprovações indisponíveis: {e}", flush=True)
    itens.sort(key=lambda x: x.get("ts") or "", reverse=True)
    por_tipo = {}
    for i in itens:
        por_tipo[i["fonte"]] = por_tipo.get(i["fonte"], 0) + 1
    return jsonify({"itens": itens, "total": len(itens), "por_fonte": por_tipo})


@app.route("/api/notificacoes/ler", methods=["POST"])
def notificacoes_ler():
    """Marca alertas como lidos. As outras fontes somem sozinhas quando o trabalho
    é feito (ficha transcrita, aprovação decidida) — marcar 'lido' nelas seria criar
    um estado que mente sobre a fila real."""
    b = request.get_json(silent=True) or {}
    usuario = session.get("usuario", "operador")
    if b.get("id"):
        return jsonify({"ok": EST.marcar_alerta_lido(int(b["id"]), usuario)})
    return jsonify({"ok": True, "lidos": EST.marcar_alertas_lidos(usuario, b.get("tipo"))})


# ------------------------------------------------------------ Otimização (spec 18)
@app.route("/api/otimizacao")
def otimizacao():
    """Objetivo de controle, bandit e o gate — em modo sombra até haver evidência."""
    from atena.avaliacao import otimizacao as otim
    return jsonify(otim.painel(GRW, _ont()))


@app.route("/api/leads/<card_id>/reativar", methods=["POST"])
def lead_reativar(card_id):
    """Reativação manual: dispara feq_reengajamento (opt-out/captabilidade travam)."""
    return jsonify({"envio": nutricao.reengajar_um(EST, ONTO.ont, WA, card_id)})


@app.route("/api/leads/<card_id>/interesse", methods=["POST"])
def lead_interesse(card_id):
    """Adiciona um curso de interesse ao lead. ativar=True torna-o o curso ativo
    (o que o agente conversa). Curso precisa existir no catálogo."""
    b = request.get_json(silent=True) or {}
    curso = (b.get("curso") or "").strip()
    if not EST.get_lead(card_id):
        return jsonify({"erro": "lead inexistente"}), 404
    if not ONTO.ont.curso(curso):
        return jsonify({"erro": f"curso '{curso}' não está no catálogo"}), 400
    EST.add_curso_interesse(card_id, curso, ativar=bool(b.get("ativar")))
    EST.log_acao(session.get("usuario", "operador"),
                 f"interesse +{curso}{' (ativo)' if b.get('ativar') else ''}", card_id)
    return jsonify({"ok": True, "cursos": EST.cursos_interesse(EST.get_lead(card_id))})


@app.route("/api/meta/reconciliar", methods=["POST"])
def meta_reconciliar():
    """Força uma reconciliação Meta agora (busca+ativa leads que o webhook não trouxe)."""
    return jsonify({"novos_ativados": reconciliacao.poll_meta(EST, ONTO.ont, ING, _primeiro_contato)})


MEDIA_DIR = pathlib.Path(__file__).resolve().parents[1] / "data" / "media"


@app.route("/api/media/<media_id>")
def media(media_id):
    """Serve a mídia recebida do lead (áudio/imagem/vídeo/doc). Guardada pela auth."""
    safe = "".join(c for c in media_id if c.isalnum() or c in "-_")   # sanitiza (path traversal)
    if not safe or not (MEDIA_DIR / safe).exists():
        return jsonify({"erro": "mídia não encontrada"}), 404
    return send_from_directory(str(MEDIA_DIR), safe,
                               mimetype=EST.midia_mime(media_id) or "application/octet-stream")


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


# preferido primeiro; cai para o próximo se o de cima ainda não estiver aprovado na Meta.
# feq_lead (com botões link/dúvidas) é o alvo; enquanto PENDING, usa feq_abertura_curso.
TEMPLATES_ABERTURA = ("feq_lead", "feq_abertura_curso")


def _primeiro_contato(card_id):
    """Caminho único do 1º contato: dispara o template de abertura e põe o lead em
    aguardando_resposta. Usado pelo operador e pela ingestão Meta automática."""
    lead = EST.get_lead(card_id)
    if not lead:
        return {"ok": False, "erro": "lead inexistente"}
    curso = lead.get("curso", "")
    info = ONTO.ont.curso(curso) or {}
    primeiro_nome = (lead.get("nome") or "").split()[0] if lead.get("nome") else "olá"
    params = [primeiro_nome, f"{info.get('nome', curso)} ({curso})"]
    r = {"ok": False, "erro": "sem template"}
    for tpl in TEMPLATES_ABERTURA:      # feq_lead quando aprovado; senão feq_abertura_curso
        r = WA.enviar_template(lead["telefone"], tpl, params=params)
        if r.get("ok"):
            EST.add_conversa(card_id, "agente", f"[template {tpl}] {curso}",
                             wamid=r.get("wamid"), status="enviado", autor="template",
                             destino=lead["telefone"])
            EST.set_lead(card_id, estado_conversa="aguardando_resposta")
            return r
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
    card_id = EST.lead_por_telefone(tel) or f"op-{_so_digitos(tel)}"
    EST.upsert_lead(card_id, nome=nome, telefone=tel, curso=curso,
                    fase="Lead", estado_conversa="aguardando_resposta")
    return jsonify({"card_id": card_id, "envio": _primeiro_contato(card_id)})


@app.route("/api/conversas/<card_id>/nova", methods=["POST"])
def conversa_nova(card_id):
    """Inicia (ou reinicia) a conversa de um card que JÁ existe — mesmo template de
    abertura do 1º contato automático (feq_lead/feq_abertura_curso). Cobre os dois
    casos pedidos: (1) o lead que nunca recebeu mensagem nenhuma — ex.: só entregou
    a ficha da etapa 2 pela LP e nunca passou pelo WhatsApp (spec 17 §7b) — e (2) o
    pedido explícito do operador de recomeçar o atendimento a qualquer momento,
    mesmo havendo histórico. Não exige histórico prévio nem checa captabilidade do
    curso (ato deliberado do operador — mesmo raciocínio de conversa_enviar_template);
    opt-out continua bloqueando, checado dentro de enviar_template."""
    if not EST.get_lead(card_id):
        return jsonify({"erro": "lead inexistente"}), 404
    r = _primeiro_contato(card_id)
    if r.get("ok"):
        EST.log_acao(session.get("usuario", "operador"), "iniciar/nova conversa", card_id)
    return jsonify({"envio": r})


# Limiar da verificação automática. 0 = IMEDIATO: decide assim que o lead chega da LP
# (não espera o tick). O que torna o imediato aceitável é o conteúdo do 1º toque: ele é
# o `feq_lead`, template de ABERTURA com os botões de link/dúvidas — não uma cobrança
# de "você não terminou". Na prática o lead preenche o formulário, é levado à Extecamp
# e recebe o link no WhatsApp. O custo é perder a chance de conferir na Extecamp antes.
VERIF_AUTO_MIN_H = 0
# Segundos mínimos desde a última mensagem nossa antes de disparar o toque automático.
# Sem isto, lead vindo do Meta (que já recebeu feq_lead na ingestão) e que depois
# preenche a LP receberia o MESMO template duas vezes em segundos.
VERIF_AUTO_SILENCIO_S = 900

CFG = {"auto_aprovar": EST.get_config("auto_aprovar") == "1",   # persistido: deploys reiniciam o serviço
       "verif_auto": EST.get_config("verif_auto") == "1",
       "verif_auto_horas": int(EST.get_config("verif_auto_horas") or 48)}


def _enviar_guia(card_id, lead, ator="humano"):
    """Anexa o guia em PDF do curso do lead à thread. Best-effort: usada tanto pelo
    fluxo automático (após o texto do agente) quanto pelo botão manual do operador —
    falha aqui nunca desfaz um texto já enviado, só fica registrada no retorno."""
    try:
        pdf = pdf_guia.obter(ONTO.ont, lead["curso"])
    except Exception as e:   # WeasyPrint ausente/quebrado no host não pode derrubar a rota
        return {"ok": False, "motivo": f"geração do guia falhou: {e}"}
    if not pdf:
        return {"ok": False, "motivo": f"sem guia disponível para {lead['curso']}"}
    r = WA.enviar_midia(lead["telefone"], pdf, f"Guia-{lead['curso']}.pdf",
                        "application/pdf", forcar=True)
    if r.get("ok"):
        EST.add_conversa(card_id, "agente", "", tipo=r.get("tipo"), media_id=r.get("media_id"),
                         wamid=r.get("wamid"), status="enviado", autor=f"guia:{ator}")
        EST.log_acao(ator, "enviar_guia_pdf", card_id)
    return r


def _aprovar_e_enviar(card_id, texto=None, ator="humano"):
    """Caminho único de aprovação: usado pelo endpoint e pelo modo automático."""
    prop = EST.decisao_pendente(card_id)
    if not prop:
        return {"erro": "sem proposta pendente"}, 404
    lead = EST.get_lead(card_id)
    texto = (texto or "").strip() or prop["mensagem"]
    if texto != prop["mensagem"]:
        # edição humana também passa pelo Revisor (spec 14), mas confiando no operador
        # para preço/parcela (negociação real) — mesmo raciocínio de conversa_enviar.
        # Se o texto NÃO foi editado (aprovou o rascunho do LLM tal como veio), esse
        # ramo nem roda: o preço já foi checado ESTRITO quando a proposta nasceu.
        rev = revisar(lead["curso"], texto, ONTO.ont, confia_valor_humano=True)
        if not rev["aprovado"]:
            return {"erro": "edição bloqueada pelo Revisor", "bloqueios": rev["bloqueios"]}, 422
    r = WA.enviar(lead["telefone"], texto, forcar=True)   # resposta humana: não trava por horário
    if r.get("ok"):
        # texto do LLM; se o humano editou antes de aprovar, a correção é sinal p/ o corpus
        autoria = "llm" if texto == prop["mensagem"] else f"llm+edicao:{ator}"
        EST.add_conversa(card_id, "agente", texto, wamid=r.get("wamid"), status="enviado",
                         autor=autoria)
        EST.set_decisao_status(prop["id"], "enviada")
        EST.log_acao(ator, "aprovar_enviar", card_id)
        if prop.get("anexo") == "guia_curso":
            r["guia"] = _enviar_guia(card_id, lead, ator=ator)
    return {"envio": r}, 200


def _entregar_represada(card_id):
    """Lead reabriu a conversa e havia resposta de coordenação represada (dúvida
    respondida fora da janela de 24h): entrega agora (a janela acabou de reabrir).
    Retorna True se entregou."""
    prop = EST.coordenacao_represada(card_id)
    if not prop:
        return False
    lead = EST.get_lead(card_id)
    r = WA.enviar(lead["telefone"], prop["mensagem"], forcar=True)
    if r.get("ok"):
        EST.add_conversa(card_id, "agente", prop["mensagem"], wamid=r.get("wamid"),
                         status="enviado", autor="coordenacao")
        EST.set_decisao_status(prop["id"], "enviada")
        EST.log_acao("sistema", "resposta_duvida_represada_entregue", card_id)
        return True
    return False


def _reabrir_conversa(card_id):
    """Fora da janela de 24h: envia o template de retomada para o lead reabrir; a
    resposta represada é entregue quando ele responder (_entregar_represada)."""
    lead = EST.get_lead(card_id)
    r = WA.enviar_template(lead["telefone"], "retomar_atendimento")
    if r.get("ok"):
        EST.add_conversa(card_id, "agente", "[template retomar_atendimento]", autor="template",
                         wamid=r.get("wamid"), status="enviado", destino=lead["telefone"])
        EST.log_acao("sistema", "retomada_enviada_duvida_represada", card_id)
    return r


@app.route("/api/conversas/<card_id>/aprovar", methods=["POST"])
def conversa_aprovar(card_id):
    b = request.get_json(silent=True) or {}
    corpo, code = _aprovar_e_enviar(card_id, b.get("texto"))
    return jsonify(corpo), code


@app.route("/api/conversas/<card_id>/enviar", methods=["POST"])
def conversa_enviar(card_id):
    """Intervenção humana: operador escreve e envia agora, sem proposta prévia
    do agente (estilo WhatsApp). Passa pelo Revisor (spec 07/CLAUDE.md §7), mas
    com confia_valor_humano=True: preço/parcela que o operador digitou são
    negociação de verdade com o lead, não alucinação de LLM — o Revisor deixa
    passar (e registra como negociado, não como "bateu com a base"). Data, carga
    horária e link da Extecamp continuam bloqueados mesmo digitados por humano:
    são fatos estruturais do curso; a correção correta é editar a ontologia."""
    lead = EST.get_lead(card_id)
    if not lead:
        return jsonify({"erro": "lead inexistente"}), 404
    texto = (request.get_json(silent=True) or {}).get("texto", "").strip()
    if not texto:
        return jsonify({"erro": "texto vazio"}), 400
    rev = revisar(lead["curso"], texto, ONTO.ont, confia_valor_humano=True)
    if not rev["aprovado"]:
        return jsonify({"erro": "mensagem bloqueada pelo Revisor", "bloqueios": rev["bloqueios"]}), 422
    r = WA.enviar(lead["telefone"], texto, forcar=True)   # envio humano: passa fora do horário
    if r.get("ok"):
        EST.add_conversa(card_id, "agente", texto, wamid=r.get("wamid"), status="enviado",
                         autor=f"humano:{session.get('usuario','humano')}")
        EST.set_lead(card_id, estado_conversa="aguardando_resposta")
        prop = EST.decisao_pendente(card_id)
        if prop:   # humano respondeu por fora -> a proposta do agente fica obsoleta
            EST.set_decisao_status(prop["id"], "recusada")
        EST.log_acao(session.get("usuario", "humano"), "enviar_livre", card_id)
    return jsonify({"envio": r})


@app.route("/api/conversas/<card_id>/reagir", methods=["POST"])
def conversa_reagir(card_id):
    """Operador reage a UMA mensagem da thread (nossa ou do lead) — um jeito rápido de
    confirmar/reconhecer sem escrever nada. Não passa pelo Revisor: emoji não afirma
    preço/data/carga, é o mesmo tipo de conteúdo que já não passa por lá."""
    lead = EST.get_lead(card_id)
    if not lead:
        return jsonify({"erro": "lead inexistente"}), 404
    b = request.get_json(silent=True) or {}
    wamid = (b.get("wamid") or "").strip()
    if not wamid:
        return jsonify({"erro": "wamid da mensagem-alvo é obrigatório"}), 400
    r = WA.enviar_reacao(lead["telefone"], wamid, (b.get("emoji") or "").strip())
    return jsonify(r)


@app.route("/api/conversas/<card_id>/contato", methods=["POST"])
def conversa_enviar_contato(card_id):
    """Operador compartilha um contato oficial (secretaria/WhatsApp) como cartão."""
    lead = EST.get_lead(card_id)
    if not lead:
        return jsonify({"erro": "lead inexistente"}), 404
    chave = (request.get_json(silent=True) or {}).get("chave", "")
    from atena.core.contatos import CONTATOS_OFICIAIS
    info = CONTATOS_OFICIAIS.get(chave)
    if not info:
        return jsonify({"erro": "contato desconhecido"}), 400
    r = WA.enviar_contato(lead["telefone"], chave)
    if r.get("ok"):
        texto = json.dumps([{"nome": info["rotulo"], "telefone": info["telefone_exibicao"]}],
                           ensure_ascii=False)
        EST.add_conversa(card_id, "agente", texto, tipo="contatos", wamid=r.get("wamid"),
                         status="enviado", autor=f"humano:{session.get('usuario','humano')}")
        EST.log_acao(session.get("usuario", "humano"), f"compartilhou contato {chave}", card_id)
    return jsonify(r)


# ============================ Funil comercial de professores (módulo restrito) ===
# O acesso é barrado no guard de auth.py (MODULOS_RESTRITOS), não aqui — rota nova
# sob /api/professores já nasce protegida.

@app.route("/api/professores")
def professores_listar():
    profs = EST.listar_professores()
    colunas = [{"fase": f, "professores": [p for p in profs if (p["fase"] or "Lead") == f]}
               for f in FASES_PROFESSOR]
    # "cursos" NÃO vem da ontologia: são professores de fora da FEQ, prospectados para
    # cursos que muitas vezes ainda nem existem no catálogo — o campo é texto livre.
    return jsonify({"colunas": colunas, "fases": FASES_PROFESSOR, "total": len(profs)})


@app.route("/api/professores", methods=["POST"])
def professores_criar():
    b = request.get_json(silent=True) or {}
    if not (b.get("nome") or "").strip():
        return jsonify({"erro": "nome é obrigatório"}), 400
    if b.get("fase") and b["fase"] not in FASES_PROFESSOR:
        return jsonify({"erro": "fase inválida"}), 400
    pid = EST.add_professor(**b)
    EST.log_acao(session.get("usuario", "?"), f"professor criado: {b['nome']}")
    return jsonify({"ok": True, "id": pid})


@app.route("/api/professores/<int:pid>", methods=["POST"])
def professores_editar(pid):
    if not EST.get_professor(pid):
        return jsonify({"erro": "professor inexistente"}), 404
    b = request.get_json(silent=True) or {}
    if b.get("fase") and b["fase"] not in FASES_PROFESSOR:
        return jsonify({"erro": "fase inválida"}), 400
    EST.set_professor(pid, **b)
    return jsonify({"ok": True, "professor": EST.get_professor(pid)})


@app.route("/api/professores/<int:pid>/excluir", methods=["POST"])
def professores_excluir(pid):
    p = EST.get_professor(pid)
    if not p:
        return jsonify({"erro": "professor inexistente"}), 404
    EST.del_professor(pid)
    EST.log_acao(session.get("usuario", "?"), f"professor excluído: {p['nome']}")
    return jsonify({"ok": True})


@app.route("/api/modulo-acesso")
def modulo_acesso_listar():
    """Quem tem acesso ao módulo. Só o master gerencia — os demais nem veem a tela."""
    if not auth.eh_master(session.get("usuario")):
        return jsonify({"erro": "só o usuário master gerencia acessos"}), 403
    concedidos = EST.listar_acesso_modulo("professores")
    com_acesso = {c["usuario"] for c in concedidos}
    disponiveis = [u["usuario"] for u in EST.listar_usuarios()
                   if u["ativo"] and u["usuario"] not in com_acesso
                   and not auth.eh_master(u["usuario"])]
    return jsonify({"master": auth.master(), "concedidos": concedidos,
                    "disponiveis": disponiveis})


@app.route("/api/modulo-acesso", methods=["POST"])
def modulo_acesso_conceder():
    if not auth.eh_master(session.get("usuario")):
        return jsonify({"erro": "só o usuário master gerencia acessos"}), 403
    b = request.get_json(silent=True) or {}
    usuario, revogar = (b.get("usuario") or "").strip(), bool(b.get("revogar"))
    if not usuario:
        return jsonify({"erro": "usuário obrigatório"}), 400
    if revogar:
        EST.revogar_modulo(usuario, "professores")
    else:
        if not EST.get_usuario(usuario):
            return jsonify({"erro": "usuário não existe"}), 404
        EST.conceder_modulo(usuario, "professores", session["usuario"])
    EST.log_acao(session["usuario"],
                 f"{'revogou' if revogar else 'concedeu'} acesso ao funil de professores: {usuario}")
    return jsonify({"ok": True})


@app.route("/api/conversas/transcrever/<int:conversa_id>", methods=["POST"])
def conversa_transcrever(conversa_id):
    """Lê uma mídia sob demanda: áudio -> transcrição, imagem -> descrição.

    A percepção já roda sozinha quando a mídia chega (whatsapp._perceber), mas isto
    cobre os dois casos em que ela não existe: o áudio que chegou ANTES desta função
    existir, e o que falhou na hora (API fora do ar, timeout). Só relê o que ainda não
    foi lido — não gasta chamada de API à toa."""
    conv = EST.get_conversa(conversa_id)
    if not conv or not conv.get("media_id"):
        return jsonify({"ok": False, "motivo": "mensagem sem mídia"}), 404
    if conv.get("transcricao"):
        return jsonify({"ok": True, "transcricao": conv["transcricao"], "cache": True})
    mime = EST.midia_mime(conv["media_id"])
    texto = WA._perceber(conv["tipo"], conv["media_id"], mime)
    if not texto:
        return jsonify({"ok": False,
                        "motivo": "não foi possível ler esta mídia (formato não suportado, "
                                  "arquivo indisponível ou chave de API ausente)"}), 422
    EST.set_transcricao(conversa_id, texto)
    EST.log_acao(session.get("usuario", "humano"), f"leitura automática de mídia ({conv['tipo']})",
                 conv["card_id"])
    return jsonify({"ok": True, "transcricao": texto})


@app.route("/api/conversas/<card_id>/guia", methods=["POST"])
def conversa_enviar_guia(card_id):
    """Operador manda o guia em PDF do curso do lead — mesmo material que o agente
    anexa automaticamente, disparado por ação humana explícita (sem passar pelo
    cérebro/Revisor: é um documento fixo gerado da ontologia, não texto livre)."""
    lead = EST.get_lead(card_id)
    if not lead:
        return jsonify({"erro": "lead inexistente"}), 404
    return jsonify(_enviar_guia(card_id, lead, ator=f"humano:{session.get('usuario','humano')}"))


@app.route("/api/conversas/<card_id>/midia", methods=["POST"])
def conversa_enviar_midia(card_id):
    """Operador envia arquivo/áudio/foto ao lead (multipart: arquivo + legenda opcional).
    A legenda é texto digitado pelo operador -> mesmo tratamento de conversa_enviar
    (confia_valor_humano=True): passa pelo Revisor, mas preço/parcela negociado não
    bloqueia; data/carga/link seguem travados."""
    lead = EST.get_lead(card_id)
    if not lead:
        return jsonify({"erro": "lead inexistente"}), 404
    arq = request.files.get("arquivo")
    if not arq or not arq.filename:
        return jsonify({"erro": "nenhum arquivo enviado"}), 400
    legenda = (request.form.get("legenda") or "").strip()
    if legenda:
        rev = revisar(lead["curso"], legenda, ONTO.ont, confia_valor_humano=True)
        if not rev["aprovado"]:
            return jsonify({"erro": "legenda bloqueada pelo Revisor", "bloqueios": rev["bloqueios"]}), 422
    conteudo = arq.read()
    r = WA.enviar_midia(lead["telefone"], conteudo, arq.filename,
                        arq.mimetype or "application/octet-stream", legenda=legenda or None,
                        forcar=True)     # envio humano: passa fora do horário comercial
    if r.get("ok"):
        # áudio não aceita legenda na Meta: se havia texto, vai em mensagem separada
        if legenda and not r.get("legenda_enviada"):
            t = WA.enviar(lead["telefone"], legenda, forcar=True)
            if t.get("ok"):
                EST.add_conversa(card_id, "agente", legenda, wamid=t.get("wamid"), status="enviado",
                                 autor=f"humano:{session.get('usuario','humano')}")
        EST.add_conversa(card_id, "agente", legenda if r.get("legenda_enviada") else "",
                         tipo=r.get("tipo"), media_id=r.get("media_id"),
                         wamid=r.get("wamid"), status="enviado")
        EST.set_lead(card_id, estado_conversa="aguardando_resposta")
        EST.log_acao(session.get("usuario", "humano"), f"enviar_midia {r.get('tipo')}", card_id)
    return jsonify({"envio": r})


@app.route("/api/medicao")
def medicao():
    """Medição do funil por coorte (spec 18 §3.1). Só mede — otimização está atrás
    do gate de §3.4 (hoje o sinal de conversão é 1 em 253)."""
    from atena.avaliacao.medicao import medir
    try:
        dias = max(1, min(365, int(request.args.get("dias", 30))))
    except ValueError:
        dias = 30
    curso = (request.args.get("curso") or "").strip().upper() or None
    return jsonify(medir(dias=dias, curso=curso))


# ----------------------------------------------------------------- Gestão de LPs
def _lp_slug(curso, turma):
    from atena.growth.wp_deploy_bridge import _slug
    return _slug(curso, turma)


def _lp_gerado(curso):
    """HTML que a ontologia produziria AGORA (sem considerar edição à mão)."""
    from atena.growth.wp_deploy_bridge import _webhook_url
    return ponte.render(GRW, _ont(), curso, webhook_url=_webhook_url())


@app.route("/api/lps")
def lps_listar():
    """Uma linha por LP: link no ar, status da inscrição e se está editada à mão."""
    editadas = EST.cursos_lp_editada()
    base = (os.environ.get("WP_URL") or "").rstrip("/")
    out = []
    for m in GRW.cursos():
        c = _ont().curso(m["curso"]) or {}
        cap = _ont().captavel(m["curso"])
        slug = _lp_slug(m["curso"], m["turma"])
        out.append({"curso": m["curso"], "nome": c.get("nome", m["curso"]), "turma": m["turma"],
                    "slug": slug, "link": f"{base}/{slug}/" if base else "",
                    "previa": f"/ponte?curso={m['curso']}",
                    "inscricao": cap.get("status"), "aberto": bool(cap.get("captavel")),
                    "editada": m["curso"] in editadas, "editada_em": editadas.get(m["curso"]),
                    "drift": EST.get_config(f"lp_drift_{m['curso']}") == "1"})
    return jsonify({"wp_configurado": bool(base), "lps": out})


@app.route("/api/lps/<curso>")
def lp_html(curso):
    curso = curso.upper()
    ed = EST.get_lp_html(curso)
    html = ed["html"] if ed else _lp_gerado(curso)
    if not html:
        return jsonify({"erro": "curso sem LP (fora do cursos_metas)"}), 404
    return jsonify({"curso": curso, "html": html,
                    "origem": "editado" if ed else "gerado",
                    "atualizado": (ed or {}).get("atualizado"), "autor": (ed or {}).get("autor")})


@app.route("/api/lps/<curso>", methods=["POST"])
def lp_salvar(curso):
    """Salva a edição. A partir daqui a página NÃO é mais regenerada pela ontologia
    (preço/data/lista de espera deixam de se atualizar sozinhos) até restaurar."""
    curso = curso.upper()
    html = (request.get_json(silent=True) or {}).get("html") or ""
    if len(html.strip()) < 200:
        return jsonify({"erro": "HTML muito curto — provável engano"}), 400
    gerado = _lp_gerado(curso)
    if not gerado and not EST.get_lp_html(curso):
        return jsonify({"erro": "curso sem LP (fora do cursos_metas)"}), 404
    EST.set_lp_html(curso, html, autor=session.get("usuario", "operador"))
    # baseline de deriva: "o que a ontologia geraria agora" no INSTANTE da edição —
    # o tick automático (lp_auto.rodar) compara contra isto, nunca contra si mesmo.
    from atena.growth.lp_auto import _hash as _lp_hash
    EST.set_config(f"lp_hash_{curso}", _lp_hash(gerado or ""))
    EST.set_config(f"lp_drift_{curso}", "0")
    return jsonify({"ok": True, "curso": curso, "bytes": len(html)})


@app.route("/api/lps/<curso>/restaurar", methods=["POST"])
def lp_restaurar(curso):
    """Descarta a edição: a LP volta a ser gerada pela ontologia."""
    curso = curso.upper()
    if not EST.del_lp_html(curso):
        return jsonify({"erro": "esta LP não estava editada"}), 404
    EST.set_config(f"lp_drift_{curso}", "0")
    EST.log_acao(session.get("usuario", "operador"), f"restaurou LP gerada {curso}")
    return jsonify({"ok": True, "curso": curso})


@app.route("/api/lps/<curso>/publicar", methods=["POST"])
def lp_publicar(curso):
    """Publica ESTA LP no WordPress (ação externa: muda uma página pública)."""
    if session.get("papel") != "supervisor":
        return jsonify({"erro": "publicar exige papel supervisor"}), 403
    curso = curso.upper()
    m = next((x for x in GRW.cursos() if x["curso"] == curso), None)
    if not m:
        return jsonify({"erro": "curso sem LP (fora do cursos_metas)"}), 404
    from atena.growth import wp_deploy_bridge as WP
    paginas = WP.renderizar(GRW, _ont(), [m], pathlib.Path(tempfile.mkdtemp()), est=EST)
    if not paginas:
        return jsonify({"erro": "nada para publicar"}), 400
    r = WP.publicar(paginas)[0]
    EST.log_acao(session.get("usuario", "operador"), f"publicou LP {curso}: {r.get('status')}")
    return jsonify(r)


@app.route("/api/templates")
def templates_listar():
    """Gestão de templates: o que existe na WABA + status na Meta. Sem token/WABA,
    devolve o rol local usado no envio, sinalizando que não deu para consultar."""
    lst = WA.templates_listar(forcar=request.args.get("forcar") == "1")
    if lst is None:
        return jsonify({"conectado": False, "categorias": WhatsApp_CATEGORIAS,
                        "templates": [{"name": t["nome"], "status": None,
                                       "language": t["idioma"], "category": None}
                                      for t in WhatsApp_TEMPLATES]})
    return jsonify({"conectado": True, "categorias": WhatsApp_CATEGORIAS, "templates": lst})


@app.route("/api/templates", methods=["POST"])
def templates_criar():
    """Cria o template na Meta e submete para aprovação (ação externa — o operador
    dispara conscientemente pela Central; fica PENDING até a Meta revisar)."""
    b = request.get_json(silent=True) or {}
    r = WA.criar_template(
        nome=(b.get("nome") or "").strip().lower(), categoria=(b.get("categoria") or "").strip(),
        corpo=b.get("corpo") or "", exemplos=b.get("exemplos") or [],
        cabecalho=(b.get("cabecalho") or "").strip() or None,
        rodape=(b.get("rodape") or "").strip() or None, botoes=b.get("botoes") or [])
    if not r.get("ok"):
        return jsonify(r), 400
    EST.log_acao(session.get("usuario", "operador"),
                 f"criar_template {b.get('nome')} ({b.get('categoria')})")
    return jsonify(r)


def _valor_param(k, lead):
    """Prefill ancorado (nome/curso/prazo) — o operador confere e edita antes de enviar."""
    info = ONTO.ont.curso(lead.get("curso", "")) or {}
    if k == "nome":
        return (lead.get("nome") or "").split()[0] if lead.get("nome") else "olá"
    if k == "curso":
        return f"{info.get('nome', lead.get('curso',''))} ({lead.get('curso','')})"
    if k == "prazo":
        return "/".join(reversed((info.get("inscricoes_fim") or "").split("-")))
    return ""


def _templates_enviaveis():
    """Fonte da verdade do que dá para enviar AGORA: os APPROVED da WABA. O rol local
    só enriquece (título amigável, rótulos e prefill) os que ele conhece — assim um
    template novo, criado pela aba Templates, já fica disponível para envio.
    Sem conexão com a Meta (dev), cai no rol local marcado 'não verificado'."""
    lst = WA.templates_listar()
    if lst is None:
        return [dict(t, verificado=False) for t in WhatsApp_TEMPLATES]
    local = {t["nome"]: t for t in WhatsApp_TEMPLATES}
    out = []
    for t in lst:
        if t.get("status") != "APPROVED" or t.get("name") == "hello_world":
            continue           # hello_world é o exemplo em inglês da Meta — nunca vai a um lead
        nome = t.get("name", "")
        corpo = next((c.get("text", "") for c in (t.get("components") or [])
                      if c.get("type") == "BODY"), "")
        n = _n_variaveis(corpo)
        base = local.get(nome)
        if base and len(base["params"]) == n:      # conhecido e com o mesmo nº de variáveis
            titulo, descricao, params = base["titulo"], base["descricao"], base["params"]
        else:                                       # criado na Central ou alterado na Meta
            titulo, descricao = nome, corpo[:160]
            params = [{"k": f"var{i + 1}", "rotulo": f"Variável {{{{{i + 1}}}}}"} for i in range(n)]
        out.append({"nome": nome, "idioma": t.get("language", "pt_BR"), "titulo": titulo,
                    "descricao": descricao, "params": params, "verificado": True})
    return out


def _rol_para_lead(lead):
    """Templates enviáveis com as variáveis já prefillados para ESTE lead."""
    return [{**t, "params": [{"k": p["k"], "rotulo": p["rotulo"],
                              "valor": _valor_param(p["k"], lead)} for p in t["params"]]}
            for t in _templates_enviaveis()]


@app.route("/api/conversas/<card_id>/templates")
def conversa_templates(card_id):
    lead = EST.get_lead(card_id)
    if not lead:
        return jsonify({"erro": "lead inexistente"}), 404
    return jsonify(_rol_para_lead(lead))


@app.route("/api/conversas/<card_id>/template", methods=["POST"])
def conversa_enviar_template(card_id):
    """Operador dispara um template aprovado (rol da Meta) para a conversa. Útil para
    reabrir fora da janela de 24h, onde texto livre não sai. Opt-out é respeitado no envio."""
    lead = EST.get_lead(card_id)
    if not lead:
        return jsonify({"erro": "lead inexistente"}), 404
    b = request.get_json(silent=True) or {}
    tpl = next((t for t in _templates_enviaveis() if t["nome"] == b.get("template")), None)
    if not tpl:
        return jsonify({"erro": "template indisponível (não está aprovado na Meta)"}), 400
    params = [str(p) for p in (b.get("params") or [])]
    if len(params) != len(tpl["params"]):
        return jsonify({"erro": f"{tpl['nome']} espera {len(tpl['params'])} variável(is)"}), 400
    r = WA.enviar_template(lead["telefone"], tpl["nome"], idioma=tpl["idioma"], params=params or None)
    if r.get("ok"):
        rotulo = f"[template {tpl['nome']}]" + (f" {' · '.join(params)}" if params else "")
        EST.add_conversa(card_id, "agente", rotulo, wamid=r.get("wamid"), status="enviado",
                         autor="template", destino=lead["telefone"])
        if lead.get("estado_conversa") in ("aguardando_1o_contato", "escalado_humano", "manual_humano"):
            EST.set_lead(card_id, estado_conversa="aguardando_resposta")
        EST.log_acao(session.get("usuario", "operador"), f"template {tpl['nome']}", card_id)
    return jsonify({"envio": r})


@app.route("/api/config", methods=["GET", "POST"])
def config_ep():
    if request.method == "POST":
        b = request.get_json(silent=True) or {}
        if "auto_aprovar" in b:
            CFG["auto_aprovar"] = bool(b.get("auto_aprovar"))
            EST.set_config("auto_aprovar", "1" if CFG["auto_aprovar"] else "0")
            EST.log_acao("operador", f"auto_aprovar={CFG['auto_aprovar']}")
        if "verif_auto" in b:
            CFG["verif_auto"] = bool(b.get("verif_auto"))
            EST.set_config("verif_auto", "1" if CFG["verif_auto"] else "0")
            EST.log_acao(session.get("usuario", "operador"), f"verif_auto={CFG['verif_auto']}")
        if "verif_auto_horas" in b:
            try:
                h = max(VERIF_AUTO_MIN_H, min(720, int(b["verif_auto_horas"])))
            except (TypeError, ValueError):
                return jsonify({"erro": "verif_auto_horas deve ser inteiro"}), 400
            CFG["verif_auto_horas"] = h
            EST.set_config("verif_auto_horas", str(h))
            EST.log_acao(session.get("usuario", "operador"), f"verif_auto_horas={h}")
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
    return jsonify(_aprovacoes_payload())


def _aprovacoes_payload():
    """Fila de intervenção humana — ACIONÁVEL (spec 18).

    Antes isto era uma lista de texto sem botão: quando o agente desistia e pedia
    humano, virava uma linha perdida no meio de alertas, sem contador no menu e sem
    como agir. A intervenção humana é o sinal mais importante do sistema (é o que o
    curador transforma em lição), então ela precisa ser visível e ter ação.
    """
    agora = datetime.datetime.now()
    def _ha_h(ts):
        try:
            return round((agora - datetime.datetime.fromisoformat(ts)).total_seconds() / 3600, 1)
        except (TypeError, ValueError):
            return None

    itens = []
    for l in EST.listar_leads():
        if l.get("optout"):
            continue
        cid = l["card_id"]
        # 1) o agente desistiu e pediu humano -> prioridade máxima
        if l["estado_conversa"] == "escalado_humano":
            itens.append({"tipo": "escalonamento", "prioridade": 1, "card_id": cid,
                          "nome": l.get("nome") or cid, "curso": l.get("curso"),
                          "motivo": l.get("motivo") or "o agente pediu ajuda",
                          "horas": _ha_h(l.get("atualizado")), "acao": "abrir"})
            continue
        # 2) lead em modo manual com fala nova esperando resposta humana
        if (l.get("modo") or "auto") == "manual":
            hist = EST.historico(cid)
            ult = hist[-1] if hist else None
            if ult and ult.get("papel") == "lead":
                itens.append({"tipo": "manual", "prioridade": 2, "card_id": cid,
                              "nome": l.get("nome") or cid, "curso": l.get("curso"),
                              "motivo": "conversa em modo manual — você conduz",
                              "horas": _ha_h(ult.get("ts")), "acao": "abrir"})
    # 3) proposta do agente aguardando aprovação para sair
    for l in EST.listar_leads():
        if l.get("optout") or l["estado_conversa"] == "escalado_humano":
            continue
        prop = EST.decisao_pendente(l["card_id"])
        if prop:
            itens.append({"tipo": "proposta", "prioridade": 3, "card_id": l["card_id"],
                          "nome": l.get("nome") or l["card_id"], "curso": l.get("curso"),
                          "motivo": (prop.get("mensagem") or "")[:120],
                          "horas": _ha_h(prop.get("ts")), "acao": "abrir"})
    # 4) dúvidas abertas para a coordenação
    for d in EST.listar_duvidas("aberta"):
        l = EST.get_lead(d["card_id"]) or {}
        itens.append({"tipo": "duvida", "prioridade": 2, "card_id": d["card_id"],
                      "nome": l.get("nome") or d["card_id"], "curso": l.get("curso"),
                      "motivo": d.get("pergunta") or "", "horas": _ha_h(d.get("criado")),
                      "acao": "duvidas"})
    # 5) curso encerrado com leads ativos (decisão de negócio, não de conversa)
    ativos = {}
    for l in EST.listar_leads():
        if l["fase"] in ("Lead", "Prospect"):
            ativos[l["curso"]] = ativos.get(l["curso"], 0) + 1
    for cod, n in sorted(ativos.items()):
        cap = _ont().captavel(cod)
        if cap.get("existe") and not cap.get("captavel"):
            itens.append({"tipo": "captabilidade", "prioridade": 4, "curso": cod,
                          "motivo": f"inscrição {cap['status']} · {n} lead(s) ativos",
                          "acao": "injetor"})
    # 6) proposta de ontologia aguardando aprovação
    for p in EST.listar_propostas("proposta"):
        itens.append({"tipo": "injetor", "prioridade": 3, "pid": p["id"],
                      "motivo": f"{p['tipo']} em {p['alvo']}", "acao": "injetor"})

    itens.sort(key=lambda x: (x["prioridade"], -(x.get("horas") or 0)))
    agir = [i for i in itens if i["prioridade"] <= 3]
    alertas = [{"tipo": "alerta", "motivo": f"[{a['tipo']}] {a['texto']}",
                "horas": _ha_h(a.get("criado"))} for a in EST.listar_alertas(8)]
    return {"itens": itens, "alertas": alertas, "agir": len(agir)}


@app.route("/api/leads/<card_id>/resolver", methods=["POST"])
def lead_resolver(card_id):
    """Operador tratou o escalonamento: devolve a conversa ao fluxo normal."""
    lead = EST.get_lead(card_id)
    if not lead:
        return jsonify({"erro": "lead inexistente"}), 404
    EST.set_lead(card_id, estado_conversa="aguardando_resposta", motivo="")
    EST.log_acao(session.get("usuario", "operador"), "escalonamento resolvido", card_id)
    return jsonify({"ok": True})


@app.route("/api/injetor", methods=["POST"])
def injetor_propor():
    b = request.get_json(silent=True) or {}
    return jsonify(ONTO.propor(b.get("texto", ""), autor=b.get("autor", "operador")))


@app.route("/api/injetor/oferecimento", methods=["POST"])
def injetor_propor_oferecimento():
    """Declarar novo oferecimento: estruturado, não texto livre (5 campos atômicos +
    URL exata cruzando catalogo.jsonld e cursos_metas — ver Ontologista.propor_oferecimento)."""
    b = request.get_json(silent=True) or {}
    curso = (b.get("curso") or "").strip().upper()
    # o check de "está no cursos_metas" mora aqui, não dentro do Ontologista: a
    # ontologia e o Growth são deliberadamente desacoplados (spec 13), e essa fronteira
    # só existe na camada da app, que já enxerga os dois.
    if not any(m["curso"] == curso for m in GRW.cursos()):
        return jsonify({"erro": "curso fora do cursos_metas — sem turma/URL para gerenciar"}), 404
    return jsonify(ONTO.propor_oferecimento(
        curso=curso, turma=b.get("turma"), url_extecamp=b.get("url_extecamp"),
        insc_inicio=b.get("insc_inicio"), insc_fim=b.get("insc_fim"),
        of_inicio=b.get("of_inicio"), of_fim=b.get("of_fim"),
        autor=session.get("usuario", "operador")))


@app.route("/api/injetor/<pid>")
def injetor_get(pid):
    p = EST.get_proposta(pid)
    return (jsonify(p), 200) if p else (jsonify({"erro": "não encontrada"}), 404)


@app.route("/api/injetor/<pid>/aprovar", methods=["POST"])
def injetor_aprovar(pid):
    p = EST.get_proposta(pid)
    oferecimento = bool(p) and p.get("tipo") == "novo_oferecimento"
    # muda ontologia governada E publica página pública -> mesmo gate de lp_publicar
    if oferecimento and session.get("papel") != "supervisor":
        return jsonify({"erro": "aprovar novo oferecimento exige papel supervisor"}), 403
    if oferecimento and p["status"] == "aprovada":
        r = {"ok": True, "retomado": True}   # reclique após crash: ontologia já escrita
    else:
        r = ONTO.aprovar(pid)
        ORQ.ont = ING.ont = CUR.ont = ONTO.ont   # todos passam a usar o grafo recarregado
    # Prorrogação: turma e URL continuam as mesmas, então não há set_oferecimento —
    # mas a LP no ar ficou com as datas velhas e precisa ser republicada igual.
    if r.get("ok") and p and p.get("tipo") == "prorrogacao":
        from atena.growth import lp_auto
        r["lp"] = lp_auto.republicar_um(GRW, ONTO.ont, EST, p["alvo"])
        EST.log_acao(session.get("usuario", "operador"),
                     f"prorrogação de datas em {p['alvo']}")
    if r.get("ok") and oferecimento:
        extra = json.loads(p["texto"])
        if not GRW.set_oferecimento(p["alvo"], extra["turma"], extra["url_extecamp"]):
            r["aviso"] = "ontologia atualizada, mas curso não está em cursos_metas — LP não republicada"
        else:
            from atena.growth import lp_auto
            r["lp"] = lp_auto.republicar_um(GRW, ONTO.ont, EST, p["alvo"])
        EST.log_acao(session.get("usuario", "operador"),
                    f"novo oferecimento {p['alvo']}: turma {extra['turma']}")
    return jsonify(r)


# ======================================================================= COLETA
# Integração com o ADM Cursos da Extecamp (spec 20).
#
# Por que TOKEN e não a sessão da Central: o login da Extecamp exige humano +
# verificação anti-robô, então a sessão autenticada vive no navegador do
# operador. O coletor precisa rodar DENTRO da origem da Extecamp para enxergar
# essa sessão — e de lá não há como mandar o cookie da Central (outra origem).
# O token é gerado na Central pelo operador logado e viaja no corpo do POST.
_COLETA_TTL = 12 * 3600     # a sessão da Extecamp não dura um dia de trabalho inteiro


def _coleta_token_valido(token):
    guardado = EST.get_config("coleta_token") or ""
    if not token or not guardado or "|" not in guardado:
        return False
    valor, nascido = guardado.rsplit("|", 1)
    if not hmac.compare_digest(valor, str(token)):
        return False
    try:
        return (time.time() - float(nascido)) < _COLETA_TTL
    except ValueError:
        return False


@app.route("/api/coleta/sessao", methods=["POST"])
def coleta_sessao():
    """Abre uma sessão de coleta e devolve o bookmarklet já com o token dentro."""
    token = secrets.token_urlsafe(24)
    EST.set_config("coleta_token", f"{token}|{time.time()}")
    EST.log_acao(session.get("usuario", "operador"), "abriu sessão de coleta da Extecamp")
    base = (os.environ.get("ATENA_PUBLIC_URL") or request.url_root).rstrip("/")
    js = (f"(function(){{var s=document.createElement('script');"
          f"s.src='{base}/coleta/coletor.js?t={token}&_='+Date.now();"
          f"document.body.appendChild(s);}})()")
    return jsonify({"ok": True, "token": token, "base": base,
                    "bookmarklet": "javascript:" + quote(js, safe="") ,
                    "entradas": coleta_extecamp.ENTRADAS,
                    "validade_horas": _COLETA_TTL // 3600})


@app.route("/coleta/coletor.js")
def coleta_coletor_js():
    """O coletor. Servido para ser injetado na página da Extecamp — por isso é
    público (não há sessão nossa naquela origem); quem manda dado precisa do
    token, que é o que realmente protege a escrita."""
    js = (WEB / "coletor-extecamp.js").read_text(encoding="utf-8")
    base = (os.environ.get("ATENA_PUBLIC_URL") or request.url_root).rstrip("/")
    js = js.replace("{{BASE}}", base).replace("{{TOKEN}}", request.args.get("t", ""))
    return js, 200, {"Content-Type": "application/javascript; charset=utf-8",
                     "Access-Control-Allow-Origin": "*"}


@app.route("/api/coleta/pagina", methods=["POST", "OPTIONS"])
def coleta_pagina():
    """Recebe UMA página do ADM Cursos e devolve as PRÓXIMAS a buscar.

    O coletor é uma bomba burra de propósito: quem sabe a estrutura do ADM
    Cursos é este backend, em Python testado contra fixtures reais. Assim a
    lógica frágil (HTML de terceiro) não vira JavaScript sem teste no navegador
    de ninguém."""
    if request.method == "OPTIONS":
        return "", 204, _CORS_LP
    b = request.get_json(silent=True) or {}
    if not _coleta_token_valido(b.get("token")):
        return jsonify({"erro": "sessão de coleta expirada — reabra pela aba Coleta"}), 403, _CORS_LP
    url, pagina = b.get("url") or "", b.get("html") or ""
    tipo = coleta_extecamp.classificar(url)
    resposta = {"tipo": tipo, "url": url, "proximas": [], "resumo": None}
    try:
        if tipo == "unidades":
            resposta["proximas"] = coleta_extecamp.links_unidades(pagina)
        elif tipo == "lista":
            # Só os cursos que ESTE sistema opera. A FEQ tem 49 em inscrição e 220
            # em andamento; varrer tudo seriam ~800 requisições ao servidor da
            # Extecamp para trazer oferecimentos que não estão em campanha nenhuma.
            conhecidos = {m["curso"] for m in GRW.cursos()}
            todos = bool(b.get("todos"))
            for c in coleta_extecamp.parse_lista_cursos(pagina):
                if not (todos or c["curso"] in conhecidos):
                    continue
                ctx = {"curso": c["curso"], "turma": c["turma"]}
                # RELATÓRIOS PRIMEIRO. Eles trazem o telefone, e telefone é a chave
                # de identidade do sistema: sabendo o contato ANTES de criar o card,
                # o lead que já falava conosco pelo WhatsApp é reencontrado em vez de
                # virar um segundo card para fundir depois.
                for aba, u in coleta_extecamp.urls_relatorios(c["chaves"]).items():
                    resposta["proximas"].append({"url": u, "ctx": dict(ctx, aba=aba)})
                for u in coleta_extecamp.urls_das_abas(c["chaves"]).values():
                    resposta["proximas"].append({"url": u, "ctx": ctx})
        elif tipo == "contatos":
            # Guardado por oferecimento até a listagem correspondente chegar. Config
            # em vez de tabela nova: é cache de uma passada de coleta, não histórico.
            ctx = b.get("ctx") or {}
            contatos = coleta_extecamp.parse_relatorio_contatos(pagina)
            chave = f"coleta_contatos:{ctx.get('curso')}/{ctx.get('turma')}"
            guardado = coleta_sinc.indexar_contatos(contatos)
            if guardado:
                anterior = json.loads(EST.get_config(chave) or "{}")
                anterior.update(guardado)
                EST.set_config(chave, json.dumps(anterior, ensure_ascii=False))
            resposta["resumo"] = {"ok": True, "aba": "contatos", "curso": ctx.get("curso"),
                                  "turma": ctx.get("turma"), "vistos": len(contatos),
                                  "criados": [], "movidos": [], "atualizados": [],
                                  "com_telefone": sum(1 for v in guardado.values() if v["telefone"])}
        elif tipo in coleta_extecamp.ABAS:
            parse = {"inscritos": coleta_extecamp.parse_inscritos,
                     "matriculados": coleta_extecamp.parse_matriculados,
                     "cancelados": coleta_extecamp.parse_cancelados}[tipo]
            dados = parse(pagina)
            chave = f"coleta_contatos:{dados.get('curso')}/{dados.get('turma')}"
            contatos = json.loads(EST.get_config(chave) or "{}")
            r = coleta_sinc.sincronizar(EST, dados, contatos=contatos)
            resposta["resumo"] = r
            # a listagem pagina de 20 em 20: enfileira as páginas 2..N
            if dados.get("paginas", 1) > 1 and "pagina_form" not in url:
                for n in range(2, dados["paginas"] + 1):
                    prox = coleta_extecamp.urls_das_abas(
                        (dados.get("itens") or [{}])[0].get("chaves", {}), pagina=n)
                    if prox.get(tipo):
                        resposta["proximas"].append(prox[tipo])
        else:
            resposta["erro"] = "página fora do roteiro da coleta"
    except Exception as e:                      # HTML de terceiro: nunca derruba a coleta
        resposta["erro"] = f"{type(e).__name__}: {e}"
        print(f"[coleta] {url}: {e}", flush=True)
    return jsonify(resposta), 200, _CORS_LP


@app.route("/api/coleta")
def coleta_status():
    """Fila de trabalho gerada pela última coleta, agrupada pela AÇÃO."""
    grupos, cursos, ultima = {}, {}, None
    for l in EST.listar_leads():
        bruto = l.get("extecamp_json")
        if not bruto:
            continue
        try:
            e = json.loads(bruto)
        except ValueError:
            continue
        ultima = max(ultima or "", e.get("coletado_em") or "")
        ck = f"{e.get('curso')}/{e.get('turma')}"
        cursos.setdefault(ck, {"curso": e.get("curso"), "turma": e.get("turma"),
                               "inscritos": 0, "matriculados": 0, "cancelados": 0})
        cursos[ck][e.get("aba", "inscritos")] = cursos[ck].get(e.get("aba"), 0) + 1
        if e.get("aba") != "inscritos":
            continue
        g = grupos.setdefault(e.get("situacao") or "—",
                              {"situacao": e.get("situacao"), "acao": e.get("acao"),
                               "taxa": e.get("taxa"), "leads": []})
        g["leads"].append({"card_id": l["card_id"], "nome": l.get("nome"),
                           "telefone": l.get("telefone") or "", "curso": e.get("curso"),
                           "turma": e.get("turma"), "documentacao": e.get("documentacao"),
                           "desde": e.get("desde")})
    ordem = ["termo_pendente", "taxa_vencida", "taxa_a_pagar", "taxa_paga"]
    fila = sorted(grupos.values(),
                  key=lambda g: (ordem.index(g["taxa"]) if g["taxa"] in ordem else 9))
    return jsonify({"ultima_coleta": ultima, "fila": fila,
                    "cursos": sorted(cursos.values(), key=lambda c: c["curso"] or ""),
                    "sessao_ativa": bool(EST.get_config("coleta_token"))})


@app.route("/api/ingestao/simulate", methods=["POST"])
def ingestao_simulate():
    return jsonify(ING.ingest(request.get_json(silent=True) or {}))


@app.route("/ponte")
def ponte_page():
    """Bridge page pública (spec 16 §2): anúncio -> modal -> Pixel+CAPI -> Extecamp."""
    html = ponte.render(GRW, _ont(), (request.args.get("curso") or "").strip().upper())
    return (html, 200, {"Content-Type": "text/html; charset=utf-8"}) if html \
        else ("curso desconhecido", 404)


_CORS_LP = {"Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Headers": "Content-Type",
            "Access-Control-Allow-Methods": "POST, OPTIONS"}
_RL = {}


def _rate_ok(ip, limite=30, janela=60):
    """Rate limit simples por IP p/ webhooks públicos da LP (in-memory).
    ponytail: por-processo; um WAF/Caddy na frente cobre ataque distribuído."""
    agora = time.time()
    fila = _RL.setdefault(ip or "?", [])
    while fila and agora - fila[0] > janela:
        fila.pop(0)
    if len(fila) >= limite:
        return False
    fila.append(agora)
    return True


@app.route("/webhook/ponte", methods=["POST", "OPTIONS"])
def webhook_ponte():
    """Recebe o modal da ponte: mesmo event_id do Pixel -> CAPI desduplica.
    CORS liberado: a LP publicada no WordPress vive em outra origem (spec 16 §2)."""
    if request.method == "OPTIONS":
        return "", 204, _CORS_LP
    if not _rate_ok(request.remote_addr):
        return jsonify({"ok": False, "motivo": "rate limit"}), 429, _CORS_LP
    dados = {**(request.get_json(silent=True) or {}),
             "ip": request.remote_addr, "ua": request.headers.get("User-Agent", "")}
    r = ponte.processar(dados, EST, GRW, ADS, ING, ont=_ont())
    # O "modo imediato" da verificação automática vivia aqui: com a régua de 2h nunca
    # tendo rodado, ele era o que fazia o 1º toque sair. Agora a régua roda de verdade
    # (op_recuperar no _operacoes), e quem parou na etapa 1 nem está na fila de
    # Verificação — o gancho aqui seria no-op e, pior, declararia "não inscreveu"
    # para alguém que acabou de chegar.
    return jsonify(r), (200 if r.get("ok") else 400), _CORS_LP


@app.route("/webhook/inscricao", methods=["POST", "OPTIONS"])
def webhook_inscricao():
    """Etapa 2 da LP: ficha completa. É o ponto em que a conversão conta — o Pixel
    dispara no navegador com o MESMO event_id que vai daqui à CAPI."""
    if request.method == "OPTIONS":
        return "", 204, _CORS_LP
    if not _rate_ok(request.remote_addr):
        return jsonify({"ok": False, "motivo": "rate limit"}), 429, _CORS_LP
    dados = {**(request.get_json(silent=True) or {}),
             "ip": request.remote_addr, "ua": request.headers.get("User-Agent", "")}
    r = ponte.processar_etapa2(dados, EST, GRW, ADS, ING, ont=_ont())
    return jsonify(r), (200 if r.get("ok") else 400), _CORS_LP


@app.route("/webhook/lp", methods=["POST", "OPTIONS"])
def webhook_lp():
    """Telemetria de sessão da LP (spec 17 §5). sendBeacon manda text/plain."""
    if request.method == "OPTIONS":
        return "", 204, _CORS_LP
    if not _rate_ok(request.remote_addr):
        return jsonify({"ok": False}), 429, _CORS_LP
    import json as _json
    try:
        d = _json.loads(request.get_data(as_text=True) or "{}")
    except ValueError:
        return jsonify({"ok": False}), 400, _CORS_LP
    if not d.get("sessao_id") or not d.get("curso"):
        return jsonify({"ok": False}), 400, _CORS_LP
    GRW.upsert_lp_sessao(d)
    return jsonify({"ok": True}), 200, _CORS_LP


@app.route("/api/lp/analytics")
def lp_analytics():
    """Funil de leitura da LP p/ a Central: alcance/tempo por seção, abandono, taxas."""
    return jsonify(GRW.lp_analytics(request.args.get("curso") or None,
                                    int(request.args.get("dias", 30))))


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
            if lid and EST.viu_msg(f"leadgen:{lid}"):   # read-only: reentrega já processada
                continue
            try:
                if v.get("field_data"):     # payload de teste/simulação com dados inline
                    lead = normalizar_meta(v.get("field_data"), v.get("campaign_name", ""), lid)
                else:                       # webhook real: só o ID -> Graph resolve dados + curso (nome do form)
                    lead = fetch_meta_lead(lid, v.get("form_id"))
                if not lead:
                    # NÃO marca o leadgen: falha transitória (token/permissão) tem de poder
                    # ser reprocessada quando a Meta reentregar. Dedupe real de envio = telefone (ingest).
                    EST.add_alerta("ingestao",
                                   f"leadgen {lid} sem fetch (token com leads_retrieval / Página ligada ao app?)")
                    continue
                if lid:
                    EST.msg_ja_vista(f"leadgen:{lid}")   # fetch OK -> marca (evita re-buscar)
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
        for r in res:
            cid = r.get("card_id")
            if not cid:
                continue
            # lead reabriu -> entrega a resposta de dúvida represada (fora da janela).
            # Se entregou, a proposta nova do cérebro (resposta ao "Quero continuar")
            # fica em Aprovações; não enviamos as duas.
            if _entregar_represada(cid):
                continue
            if CFG["auto_aprovar"] and r.get("revisor", {}).get("aprovado"):
                _aprovar_e_enviar(cid, ator="auto")
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


@app.route("/api/duvidas/<int:did>/descartar", methods=["POST"])
def duvidas_descartar(did):
    """Dúvida que não faz sentido responder sai da fila. ARQUIVA (status='descartada')
    em vez de apagar: o registro e o motivo ficam para auditoria."""
    d = EST.get_duvida(did)
    if not d or d["status"] != "aberta":
        return jsonify({"erro": "dúvida inexistente ou já tratada"}), 404
    motivo = ((request.get_json(silent=True) or {}).get("motivo") or "").strip() or "sem sentido responder"
    EST.responder_duvida(did, f"[descartada] {motivo}", status="descartada")
    EST.log_acao(session.get("usuario", "operador"), f"descartar_duvida {did}: {motivo}", d["card_id"])
    return jsonify({"ok": True, "id": did})


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
        # resposta caiu fora da janela de 24h? fica represada -> reabre com template
        if not (corpo.get("envio") or {}).get("ok"):
            r["reativacao"] = _reabrir_conversa(r["card_id"])
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
    """Alerta novo -> tenta avisar o gestor por WhatsApp (best effort; a Central sempre mostra).
    Nunca despacha para um número que também é um lead rastreado: texto interno (motivo de
    escalonamento, estado do sistema) não pode vazar para dentro de uma conversa de lead."""
    dest = EST.get_config("alerta_whatsapp") or "5519936185086"
    pendentes = EST.alertas_nao_notificados()
    if not pendentes:
        return
    if EST.lead_por_telefone(dest):
        EST.log_acao("sistema", f"alerta_whatsapp={dest} coincide com um lead — dispatch suprimido, só Central")
        for a in pendentes:
            EST.marcar_alerta_notificado(a["id"])
        return
    for a in pendentes:
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
            # reconciliação Meta (rede de segurança do webhook leadgen): varre os
            # formulários e ingere+ativa o que o webhook não entregou. A cada ~5 min.
            if _t.time() - float(EST.get_config("op_meta_poll") or 0) > 300:
                EST.set_config("op_meta_poll", str(_t.time()))
                reconciliacao.poll_meta(EST, ONTO.ont, ING, _primeiro_contato)
            # degradação: 3+ fallbacks determinísticos em 15 min -> alerta (máx. 1 a cada 30 min)
            recentes = [t for t in _cer.FALLBACKS if _t.time() - t < 900]
            ultimo = float(EST.get_config("op_fb_alerta") or 0)
            if len(recentes) >= 3 and _t.time() - ultimo > 1800:
                EST.set_config("op_fb_alerta", str(_t.time()))
                EST.add_alerta("degradacao",
                               f"{len(recentes)} respostas caíram no fallback determinístico em 15 min — verificar journalctl")
            # utcnow() aqui era o bug: o comentário sempre disse "BRT" mas a leitura
            # era UTC — os 5 jobs por hora (backup/LGPD/canário/curador/pacing) rodavam
            # 3h mais cedo que o documentado (ex.: "06h BRT" na prática era 03h BRT).
            # now() já sai em BRT (atena/__init__.py fixa o TZ do processo no import).
            h = datetime.datetime.now().hour
            if h == 6 and not _rodou_hoje("op_backup"):       # 06h BRT (o processo já roda em BRT)
                fazer_backup()
            if h == 7 and not _rodou_hoje("op_lgpd"):
                EST.anonimizar_antigos(dias=180)
            if h == 11 and not _rodou_hoje("op_canario"):     # 11h BRT
                _canario()
            # Pacing do Growth Engine (spec 15 §8): insights + ritmo + CPL + realocação.
            # Documentado como cron externo (30 7 * * * pacing) mas o VPS nunca teve
            # crontab nem timer — a régua de recuperação tinha o mesmo problema até
            # entrar neste loop (ver comentário no bloco op_recuperar); pacing ficou
            # para trás e nunca rodou em produção. 7h BRT ~= 07:30 documentado.
            if h == 7 and not _rodou_hoje("op_pacing"):
                try:
                    from atena.growth import pacing as _pacing
                    rp = _pacing.passada(GRW, ADS, ont=ONTO.ont, est=EST)
                    realoc = rp.get("realocacao")
                    print(f"[operacoes] pacing: {len(rp.get('ritmos', {}))} curso(s)"
                          + (f", realocação {realoc['doador']}->{realoc['receptor']}" if realoc else ""),
                          flush=True)
                except Exception as e:
                    print(f"[operacoes] pacing falhou: {e}", flush=True)
            # aprendizado contínuo (spec 18): ingere o novo e destila as resoluções
            # humanas do dia em lições. 05h BRT — fora do horário de atendimento.
            if h == 5 and not _rodou_hoje("op_curador"):
                try:
                    from atena.corpus import ingestao as _ing, curador as _cur
                    _ing.rodar()
                    r = _cur.curar(limite=40)
                    print(f"[operacoes] curador: {r['licoes']} lição(ões), "
                          f"{r['descartadas']} descartada(s)", flush=True)
                except Exception as e:
                    print(f"[operacoes] curador falhou: {e}", flush=True)
            # reativação comercial: só dentro do horário, a cada ~2h. Nudge em texto livre p/
            # quem travou na janela de 24h; template escalonado p/ quem esfriou fora dela.
            # veredito automático de "não inscreveu" (chave da aba Verificação).
            # Só no horário comercial: o caminho envia template ao lead.
            if CFG["verif_auto"] and G.dentro_janela() \
                    and _t.time() - float(EST.get_config("op_verif_auto") or 0) > 900:
                EST.set_config("op_verif_auto", str(_t.time()))
                feitos = _verificacao_auto()
                if feitos:
                    print(f"[operacoes] verificação automática: {len(feitos)} decidido(s)", flush=True)
            # Correção de link quebrado: varre as conversas atrás de URL da Extecamp que
            # aponte para curso+turma inexistente e reenvia o link certo. Fica ARMADO —
            # se o template ainda estiver em revisão na Meta, não envia e tenta depois;
            # quando não há ninguém para corrigir, é uma consulta e nada mais. Vale para
            # links futuros, não só para o incidente do FEQ-0480/001.
            if G.dentro_janela() and _t.time() - float(EST.get_config("op_link") or 0) > 3600:
                EST.set_config("op_link", str(_t.time()))
                try:
                    from atena.ops import corrigir_link as _cl
                    afet = _cl.afetados(EST, ONTO.ont)
                    if afet:
                        _cl.main(aplicar=True, est=EST, ont=ONTO.ont)
                except Exception as e:
                    print(f"[operacoes] correcao de link falhou: {e}", flush=True)
            # Auto-republicação de LP quando a ontologia deriva (preço/promoção expirando,
            # curso mudando de status). Sem o gate de horário comercial: não é mensagem a
            # lead, é publicação de página — pode rodar de madrugada sem problema.
            if _t.time() - float(EST.get_config("op_lp_auto") or 0) > 3600:
                EST.set_config("op_lp_auto", str(_t.time()))
                try:
                    from atena.growth import lp_auto
                    rl = lp_auto.rodar(GRW, ONTO.ont, EST)
                    if rl["publicados"] or rl["drift"]:
                        print(f"[operacoes] lp_auto: {len(rl['publicados'])} republicada(s), "
                              f"{len(rl['drift'])} editada(s) com deriva sinalizada", flush=True)
                except Exception as e:
                    print(f"[operacoes] lp_auto falhou: {e}", flush=True)
            # retentativa de entrega: recupera quem a Meta recusou (número na variante do
            # 9º dígito; pacing de marketing com espera + template complementar). A cada ~1h,
            # no horário comercial — o caminho manda template ao lead.
            if G.dentro_janela() and _t.time() - float(EST.get_config("op_reenvio") or 0) > 3600:
                EST.set_config("op_reenvio", str(_t.time()))
                rr = reenvio.rodar(EST, ONTO.ont, WA)
                if rr["reenviados"] or rr["humano"]:
                    print(f"[operacoes] reenvio: {len(rr['reenviados'])} retentativa(s), "
                          f"{len(rr['humano'])} para humano", flush=True)
            if G.dentro_janela() and _t.time() - float(EST.get_config("op_reativacao") or 0) > 7200:
                EST.set_config("op_reativacao", str(_t.time()))
                res = nutricao.reativar(EST, ONTO.ont, WA, propor=ORQ.propor_reativacao)
                if CFG["auto_aprovar"]:              # nudges free-text respeitam o toggle (tier externa)
                    for cid in res["nudges"]:
                        _aprovar_e_enviar(cid, ator="reativacao")
            # Régua de recuperação de inscrição (spec 17 §7): quem parou na etapa 1 da LP
            # leva o 1º toque 2h depois. Estava escrita mas NUNCA rodava — só existia no
            # CLI (growth/motor.py) e o VPS não tem cron nem timer, então o "2h" da spec
            # nunca aconteceu. A cada ~15 min, no horário comercial (manda template).
            if G.dentro_janela() and _t.time() - float(EST.get_config("op_recuperar") or 0) > 900:
                EST.set_config("op_recuperar", str(_t.time()))
                rc = growth_funil.recuperar(GRW, EST, ONTO.ont, WA)
                if rc["enviados"]:
                    print(f"[operacoes] recuperacao: {len(rc['enviados'])} toque(s)", flush=True)
        except Exception as e:
            print(f"[operacoes] tick falhou: {e}", flush=True)


import threading
# ATENA_DEV=1 (preview local) NÃO roda o agendador: nada de poller/nutrição/envio real.
# Evita que uma instância de desenvolvimento com tokens reais no .env dispare mensagens
# aos leads (incidente 09/07: preview local reenviou feq_lead a ~28 leads).
if os.environ.get("ATENA_DEV") != "1":
    threading.Thread(target=_operacoes, daemon=True).start()
else:
    print("[dev] ATENA_DEV=1 — agendador (_operacoes) desativado.", flush=True)


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
