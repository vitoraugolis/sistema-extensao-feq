#!/usr/bin/env python3
"""Checks dos módulos novos: cérebro (fallback), ingestão, whatsapp, auth, curador."""
import datetime
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from flask import Flask
from atena.ontologia.servico import Ontologia
from atena.core.estado import Estado
from atena.core.orquestrador import Orquestrador
from atena.core import guardrails as G
from atena.core import auth
from atena.agentes.cerebro import cerebro_llm
from atena.agentes.ingestao import Ingestao
from atena.agentes.curador import Curador
from atena.transporte.whatsapp import WhatsApp

ONT = Ontologia()
AGORA = datetime.datetime(2026, 7, 13, 10, 0)   # segunda, 10h — dentro do horário comercial


def test_cerebro_fallback_sem_key():
    os.environ.pop("ANTHROPIC_API_KEY", None)  # força fallback determinístico
    lead = {"curso": "FEQ-0480", "escolaridade": None}
    d = cerebro_llm(ONT, lead, "tenho superior em engenharia, quero o curso")
    assert d.proxima_acao == "qualificar"


def test_dispatch_multiprovider_sem_chave_cai_no_deterministico():
    os.environ.pop("OPENAI_API_KEY", None)
    os.environ["ATENA_MODELO"] = "openai:gpt-4o-mini"     # provedor sem chave
    try:
        d = cerebro_llm(ONT, {"curso": "FEQ-0480", "escolaridade": None},
                        "tenho superior em engenharia, quero o curso")
        assert d.proxima_acao == "qualificar"              # fallback funcionou
    finally:
        os.environ.pop("ATENA_MODELO", None)
    from atena.agentes.cerebro import modelo_atual
    assert modelo_atual()[0] == "claude"                   # default volta ao Claude
    assert modelo_atual()[1]                               # com um id não-vazio


def test_config_persistencia():
    est = Estado(":memory:")
    est.set_config("modelo", "claude:claude-haiku-4-5")
    assert est.get_config("modelo") == "claude:claude-haiku-4-5"
    est.set_config("modelo", "claude:claude-sonnet-4-6")   # sobrescreve
    assert est.get_config("modelo") == "claude:claude-sonnet-4-6"


def test_ingestao_dedupe_e_gate():
    est = Estado(":memory:")
    ing = Ingestao(est, ONT)
    r1 = ing.ingest({"telefone": "+55 (19) 99999-0001", "curso": "FEQ-0480", "nome": "A"})
    assert r1["acao"] == "criado" and r1["captavel"] is True
    r2 = ing.ingest({"telefone": "19 99999 0001", "curso": "FEQ-0480"})
    assert r2["acao"] == "atualizado"            # mesmo telefone -> não duplica
    r3 = ing.ingest({"telefone": "+55 19 98888-0002", "curso": "FEQ-0655"})  # encerrado
    assert r3["estado_conversa"] == "aguardando_revisao"


def test_whatsapp_assinatura_e_envio():
    assert G.verificar_assinatura("seg", b"corpo",
        "sha256=" + __import__("hmac").new(b"seg", b"corpo", __import__("hashlib").sha256).hexdigest())
    assert not G.verificar_assinatura("seg", b"corpo", "sha256=errado")
    os.environ.pop("WHATSAPP_TOKEN", None)
    est = Estado(":memory:")
    wa = WhatsApp(est, Orquestrador(est, ONT))
    r = wa.enviar("5519999990001", "oi", agora=AGORA)
    assert r["ok"] and r["status"] in ("dry_run", "enfileirado")  # nunca envia sem token


def test_whatsapp_receber_encaminha():
    est = Estado(":memory:")
    est.upsert_lead("c1", nome="X", telefone="+55 19 99999-0002", curso="FEQ-0480",
                    fase="Lead", estado_conversa="qualificacao")
    wa = WhatsApp(est, Orquestrador(est, ONT))
    out = wa.receber({"entry": [{"changes": [{"value": {"messages": [
        {"from": "5519999990002", "text": {"body": "tenho superior, quero saber o preço"}}]}}]}]})
    assert out and "decisao" in out[0]


def test_auth_multiusuario():
    os.environ.pop("ATENA_USERS", None)
    auth._FALHAS.clear()
    est = Estado(":memory:")
    app = Flask(__name__)
    auth.init_app(app, est)
    c = app.test_client()
    assert c.get("/api/me").status_code == 401                       # sem login
    # bootstrap DEV (supervisor) vale enquanto não há usuários reais
    assert c.post("/api/login", json={"usuario": "operador", "senha": "operador"}).status_code == 200
    assert c.post("/api/usuarios", json={"usuario": "ana", "senha": "segredo1",
                                         "papel": "operador"}).status_code == 200
    # criado o 1º usuário real, o bootstrap MORRE
    c2 = app.test_client()
    assert c2.post("/api/login", json={"usuario": "operador", "senha": "operador"}).status_code == 401
    # usuário real loga; papel operador não gerencia usuários
    assert c2.post("/api/login", json={"usuario": "ana", "senha": "segredo1"}).status_code == 200
    assert c2.get("/api/usuarios").status_code == 403
    # senha errada nega; 5 falhas seguidas -> rate limit 429
    c3 = app.test_client()
    assert c3.post("/api/login", json={"usuario": "ana", "senha": "errada"}).status_code == 401
    for _ in range(4):
        c3.post("/api/login", json={"usuario": "x", "senha": "y"})
    assert c3.post("/api/login", json={"usuario": "x", "senha": "y"}).status_code == 429
    auth._FALHAS.clear()


def test_enviar_respeita_24h_e_optout():
    os.environ.pop("WHATSAPP_TOKEN", None)
    est = Estado(":memory:")
    est.upsert_lead("c9", nome="Y", telefone="+55 19 97777-0009", curso="FEQ-0480",
                    fase="Lead", estado_conversa="qualificacao")
    wa = WhatsApp(est, None)
    r = wa.enviar("5519977770009", "oi", agora=AGORA)       # sem msg do lead nas 24h
    assert r["ok"] is False and r["status"] == "fora_da_janela_24h"
    est.add_conversa("c9", "lead", "olá")                    # abre a janela
    r = wa.enviar("5519977770009", "oi", agora=AGORA)
    assert r["ok"] and r["status"] in ("dry_run", "enfileirado")
    est.set_lead("c9", optout=1)                             # opt-out bloqueia tudo
    assert wa.enviar("5519977770009", "oi", agora=AGORA)["status"] == "bloqueado"
    assert wa.enviar_template("5519977770009", "feq_abertura_curso")["status"] == "bloqueado"


def test_bibliotecario_portfolio_e_citados():
    from atena.agentes.bibliotecario import Bibliotecario
    ctx = Bibliotecario(ONT).contexto("FEQ-0480", "Superior Completo",
                                      mensagem="qual a ementa do FEQ-0580?")
    assert len(ctx["portfolio"]) == 10                      # conhece o catálogo inteiro
    assert ctx["cursos_citados"][0]["curso"]["codigo"] == "FEQ-0580"
    assert ctx["curso"]["insight_venda"]                    # registro enriquecido
    assert ctx["curso"]["aulas_inicio"]                     # sabe quando começa


def test_orquestrador_troca_de_curso():
    from atena.core import contratos as C
    est = Estado(":memory:")
    est.upsert_lead("c1", nome="T", telefone="+55 19 90000", curso="FEQ-0480",
                    fase="Lead", estado_conversa="qualificacao")
    brain = lambda lead, msg, hist=None: C.DecisaoAgente(
        C.CONTINUAR, "troca de interesse", mensagem_ao_lead="Claro! Vamos falar do 0580.",
        dados={"curso": "FEQ-0580"})
    Orquestrador(est, ONT, brain).processar_mensagem("c1", "na verdade quero o FEQ-0580")
    assert est.get_lead("c1")["curso"] == "FEQ-0580"
    brain2 = lambda lead, msg, hist=None: C.DecisaoAgente(
        C.CONTINUAR, "x", dados={"curso": "FEQ-9999"})       # inexistente -> ignora
    Orquestrador(est, ONT, brain2).processar_mensagem("c1", "e o FEQ-9999?")
    assert est.get_lead("c1")["curso"] == "FEQ-0580"


def test_curador_alerta():
    est = Estado(":memory:"); est.seed_demo()
    alertas = Curador(est, ONT).varrer()
    assert any(a["tipo"] == "captabilidade" for a in alertas)
    assert any(a["tipo"] == "alerta_catalogo" for a in alertas)      # FEQ-0499


if __name__ == "__main__":
    casos = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    falhas = 0
    for fn in casos:
        try:
            fn(); print(f"PASS  {fn.__name__}")
        except AssertionError as e:
            falhas += 1; print(f"FAIL  {fn.__name__}  {e}")
        except Exception as e:
            falhas += 1; print(f"ERRO  {fn.__name__}  {type(e).__name__}: {e}")
    print(f"\n{len(casos) - falhas}/{len(casos)} passaram.")
    sys.exit(1 if falhas else 0)
