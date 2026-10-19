#!/usr/bin/env python3
"""
Disparo de template em lote (Campanhas): público filtrado, envio em lotes pacientes,
contagem de status sempre ao vivo (nunca contador denormalizado) e clique de botão
quick-reply correlacionado por context.id — sem nunca sobrescrever `status` (achado real
do desenho: clique não é estágio de entrega, é anotação separada em `clicado_ts`).
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

# Mesma armadilha já documentada no projeto: sem isto, `_load_env()` do app.py carrega
# o .env real (setdefault não sobrescreve string vazia) e um teste local dispara chamada
# de verdade à Meta. Zera ANTES do import.
os.environ["ATENA_DEV"] = "1"
os.environ["WHATSAPP_TOKEN"] = ""
os.environ["META_ADS_TOKEN"] = ""
os.environ["META_CAPI_TOKEN"] = ""

import atena.api.app as A                                     # noqa: E402
from atena.core.estado import Estado                            # noqa: E402
from atena.growth.db import Growth                              # noqa: E402

# Redireciona os globais de módulo (rotas leem EST/GRW pelo nome a cada chamada — ver
# test_central_abas.py) para um banco isolado, em vez do atena/data/atena.db real.
A.EST = Estado(":memory:")
A.GRW = Growth(":memory:")


def _cliente():
    c = A.app.test_client()
    with c.session_transaction() as s:
        s["usuario"], s["papel"] = "tester", "operador"
    return c


def _lead(cid, **kw):
    base = dict(nome="L", telefone="+5519" + cid[-8:].rjust(8, "9"), curso="FEQ-0480",
                fase="Lead", estado_conversa="aguardando_1o_contato", optout=0)
    base.update(kw)
    A.EST.upsert_lead(cid, **base)


def _tpl(nome="feq_teste", idioma="pt_BR", n_params=0):
    return {"nome": nome, "idioma": idioma, "titulo": nome,
            "params": [{"k": f"var{i+1}", "rotulo": f"Variável {i+1}"} for i in range(n_params)]}


# ------------------------------------------------------------- filtrar_leads
def test_filtrar_leads_por_curso_casa_ativo_e_interesse():
    est = Estado(":memory:")
    est.upsert_lead("c1", telefone="1", curso="FEQ-0480", fase="Lead")
    est.add_curso_interesse("c1", "FEQ-0295")           # interesse, não é o ativo
    est.upsert_lead("c2", telefone="2", curso="FEQ-0295", fase="Lead")
    est.upsert_lead("c3", telefone="3", curso="FEQ-0331", fase="Lead")
    achados = {l["card_id"] for l in est.filtrar_leads(curso="FEQ-0295")}
    assert achados == {"c1", "c2"}, achados


def test_filtrar_leads_por_fase_estado_conversa_e_optout():
    est = Estado(":memory:")
    est.upsert_lead("c1", telefone="1", curso="FEQ-0480", fase="Lead",
                    estado_conversa="aguardando_resposta")
    est.upsert_lead("c2", telefone="2", curso="FEQ-0480", fase="Prospect",
                    estado_conversa="aguardando_resposta")
    est.upsert_lead("c3", telefone="3", curso="FEQ-0480", fase="Lead",
                    estado_conversa="aguardando_resposta", optout=1)
    assert {l["card_id"] for l in est.filtrar_leads(fase="Lead")} == {"c1", "c3"}
    assert {l["card_id"] for l in est.filtrar_leads(estado_conversa="aguardando_resposta")} == {"c1", "c2", "c3"}
    assert {l["card_id"] for l in est.filtrar_leads(optout=0)} == {"c1", "c2"}
    assert {l["card_id"] for l in est.filtrar_leads(optout=1)} == {"c3"}


# ------------------------------------------------------------- clique
def test_registrar_clique_idempotente_e_nao_mexe_status():
    est = Estado(":memory:")
    est.upsert_lead("c1", telefone="1")
    est.add_conversa("c1", "agente", "[campanha x]", wamid="w1", status="lido", campanha_id=7)
    cid = est.registrar_clique("w1")
    assert cid == "c1"
    hist = est.historico("c1")[0]
    assert hist["status"] == "lido", "clique NÃO pode sobrescrever o status de entrega"
    ts1 = est.conn.execute("SELECT clicado_ts FROM conversas WHERE wamid='w1'").fetchone()["clicado_ts"]
    assert ts1
    est.registrar_clique("w1")   # segundo clique no mesmo botão
    ts2 = est.conn.execute("SELECT clicado_ts FROM conversas WHERE wamid='w1'").fetchone()["clicado_ts"]
    assert ts1 == ts2, "segundo clique não deve reescrever o timestamp (idempotente)"


def test_registrar_clique_silencioso_sem_campanha_ou_wamid_desconhecido():
    est = Estado(":memory:")
    est.upsert_lead("c1", telefone="1")
    est.add_conversa("c1", "agente", "template avulso", wamid="w2", status="lido")  # sem campanha_id
    assert est.registrar_clique("w2") is None, "envio fora de campanha não registra clique"
    assert est.registrar_clique("inexistente") is None
    assert est.registrar_clique(None) is None
    assert est.registrar_clique("") is None


# ------------------------------------------------------------- contagens
def test_campanha_contagens_sempre_ao_vivo():
    est = Estado(":memory:")
    cid = est.criar_campanha("Camp", "feq_teste", "pt_BR", {}, 3, "operador")
    est.upsert_lead("a", telefone="1"); est.upsert_lead("b", telefone="2"); est.upsert_lead("c", telefone="3")
    est.add_conversa("a", "agente", "x", wamid="wa", status="entregue", campanha_id=cid)
    est.add_conversa("b", "agente", "x", wamid="wb", status="lido", campanha_id=cid)
    est.add_conversa("c", "agente", "x", wamid="wc", status="falhou", campanha_id=cid)
    est.registrar_clique("wb")
    g = est.campanha_contagens(cid)
    assert g == {"enviado": 0, "entregue": 1, "lido": 1, "falhou": 1, "clicado": 1}, g
    # status muda depois (webhook atrasado) -> contagem reflete na hora, sem cache
    est.atualizar_status_msg("wa", "lido")
    assert est.campanha_contagens(cid)["lido"] == 2


def test_cancelar_campanha_flag_cooperativo():
    est = Estado(":memory:")
    cid = est.criar_campanha("Camp", "feq_teste", "pt_BR", {}, 1, "operador")
    assert est.campanha_cancelada(cid) is False
    est.cancelar_campanha(cid)
    assert est.campanha_cancelada(cid) is True
    est.concluir_campanha(cid, "cancelada")
    assert est.get_campanha(cid)["status"] == "cancelada"


# ------------------------------------------------------------- preview (rota)
def test_preview_conta_publico_e_avisa_tier():
    for i in range(3):
        _lead(f"pv{i}", curso="FEQ-0480")
    r = _cliente().get("/api/campanhas/preview?curso=FEQ-0480").get_json()
    assert r["total"] == 3, r
    assert len(r["amostra"]) == 3
    assert r["aviso_tier"] is None, "público pequeno não aciona aviso de tier"


# ------------------------------------------------------------- disparo (função direta)
def test_disparar_campanha_envia_registra_e_conclui():
    for i in range(4):
        _lead(f"d{i}", curso="FEQ-0331")
    leads = A.EST.filtrar_leads(curso="FEQ-0331")
    cid = A.EST.criar_campanha("Camp direta", "feq_teste", "pt_BR", {"curso": "FEQ-0331"},
                               len(leads), "operador")
    antigo = A.WA.enviar_template
    A.WA.enviar_template = lambda tel, nome, idioma="pt_BR", params=None, componentes=None: {"ok": True, "wamid": "w-" + tel}
    try:
        A._disparar_campanha(cid, leads, _tpl())
    finally:
        A.WA.enviar_template = antigo
    g = A.EST.campanha_contagens(cid)
    assert g["enviado"] == 4, g
    assert A.EST.get_campanha(cid)["status"] == "concluida"


def test_disparar_campanha_registra_falha_sem_derrubar_o_lote():
    for i in range(3):
        _lead(f"f{i}", curso="FEQ-0333")
    leads = A.EST.filtrar_leads(curso="FEQ-0333")
    cid = A.EST.criar_campanha("Camp falha", "feq_teste", "pt_BR", {}, len(leads), "operador")
    antigo = A.WA.enviar_template
    A.WA.enviar_template = lambda tel, nome, idioma="pt_BR", params=None, componentes=None: {"ok": False, "motivo": "bloqueado"}
    try:
        A._disparar_campanha(cid, leads, _tpl())
    finally:
        A.WA.enviar_template = antigo
    g = A.EST.campanha_contagens(cid)
    assert g["falhou"] == 3, g
    assert A.EST.get_campanha(cid)["status"] == "concluida"


def test_cancelamento_interrompe_lotes_futuros():
    for i in range(5):
        _lead(f"cc{i}", curso="FEQ-1000")
    leads = A.EST.filtrar_leads(curso="FEQ-1000")
    cid = A.EST.criar_campanha("Camp cancel", "feq_teste", "pt_BR", {}, len(leads), "operador")
    A.EST.cancelar_campanha(cid)          # já cancelada ANTES de disparar
    antigo = A.WA.enviar_template
    A.WA.enviar_template = lambda tel, nome, idioma="pt_BR", params=None, componentes=None: {"ok": True, "wamid": "w-" + tel}
    try:
        A._disparar_campanha(cid, leads, _tpl())
    finally:
        A.WA.enviar_template = antigo
    g = A.EST.campanha_contagens(cid)
    assert g["enviado"] == 0, "cancelada antes do 1º lote não deve enviar nada"
    assert A.EST.get_campanha(cid)["status"] == "cancelada"


def test_lote_e_pausa_respeitam_CAMPANHA_LOTE():
    for i in range(5):
        _lead(f"lt{i}", curso="FEQ-0499")
    leads = A.EST.filtrar_leads(curso="FEQ-0499")
    cid = A.EST.criar_campanha("Camp lote", "feq_teste", "pt_BR", {}, len(leads), "operador")
    lote_antigo, pausa_antiga = A.CAMPANHA_LOTE, A.CAMPANHA_PAUSA_S
    sleep_original = A.time.sleep
    sleeps = []
    A.CAMPANHA_LOTE = 2
    A.time.sleep = lambda s: sleeps.append(s)        # não espera de verdade (restaurado no finally)
    antigo = A.WA.enviar_template
    A.WA.enviar_template = lambda tel, nome, idioma="pt_BR", params=None, componentes=None: {"ok": True, "wamid": "w-" + tel}
    try:
        A._disparar_campanha(cid, leads, _tpl())
    finally:
        A.WA.enviar_template = antigo
        A.CAMPANHA_LOTE, A.CAMPANHA_PAUSA_S = lote_antigo, pausa_antiga
        A.time.sleep = sleep_original
    # 5 leads, lote de 2 -> pausa antes do 3º e do 5º (índices 2 e 4) = 2 pausas
    assert len(sleeps) == 2, sleeps
    assert A.EST.campanha_contagens(cid)["enviado"] == 5


# ------------------------------------------------------------- clique via webhook
def test_webhook_button_correlaciona_clique_pelo_context_id():
    from atena.transporte.whatsapp import WhatsApp
    est = Estado(":memory:")
    est.upsert_lead("c1", telefone="5519999990001")
    est.add_conversa("c1", "agente", "[campanha]", wamid="wamid-original", status="lido", campanha_id=1)

    class FakeOrq:
        def processar_mensagem(self, *a, **kw):
            return {}
    wa = WhatsApp(est, FakeOrq())
    payload = {"entry": [{"changes": [{"value": {"messages": [
        {"id": "m1", "from": "5519999990001", "type": "button",
         "button": {"text": "Quero saber mais"},
         "context": {"id": "wamid-original"}}]}}]}]}
    out = wa.receber(payload)
    assert any(o.get("acao") == "campanha_clique" and o["card_id"] == "c1" for o in out), out
    assert est.campanha_contagens(1)["clicado"] == 1


# ------------------------------------------------- template completo (19/10/2026)
def _tpl_completo():
    from atena.transporte.whatsapp import estrutura_template, montar_componentes
    comps = montar_componentes(
        "Oi {{1}}, as inscrições do {{2}} estão abertas!", ["Ana", "FEQ-0480"],
        {"formato": "IMAGE", "handle": "h"}, "Extensão FEQ",
        ["Tenho dúvidas", {"tipo": "URL", "texto": "Inscrever", "url": "https://feq.x/{{1}}",
                           "exemplo": "feq-0480"}, {"tipo": "COPY_CODE", "exemplo": "FEQ15"}])
    return {**_tpl("feq_completo", n_params=2), "estrutura": estrutura_template({"components": comps}),
            "padrao": {"midia": "https://feq.x/tplmidia/feq_completo.png", "botoes": {"2": "FEQ15"}}}


def test_campanha_monta_cabecalho_link_e_cupom_por_lead():
    _lead("tc1", nome="Ana Souza", curso="FEQ-0655")
    lead = A.EST.get_lead("tc1")
    cfg = {"corpo": [{"fonte": "nome"}, {"fonte": "codigo"}],
           "botoes": {"1": {"fonte": "fixo", "valor": "feq-0480"}}}
    comps, corpo, erro = A._componentes_para(_tpl_completo(), lead, cfg)
    assert erro is None and corpo == ["Ana", "FEQ-0655"], (erro, corpo)
    por = {(c["type"], c.get("index")): c for c in comps}
    assert por[("header", None)]["parameters"][0]["image"]["link"].endswith("feq_completo.png")
    assert por[("button", "1")]["parameters"][0]["text"] == "feq-0480"
    assert por[("button", "2")]["parameters"][0]["coupon_code"] == "FEQ15"      # padrão da criação


def test_campanha_recusa_disparo_com_variavel_vazia():
    _lead("tc2", curso="FEQ-0655")
    antigo = A._templates_enviaveis
    A._templates_enviaveis = lambda: [_tpl_completo()]
    try:
        r = _cliente().post("/api/campanhas", json={
            "template_nome": "feq_completo", "filtro": {"curso": "FEQ-0655"},
            "valores": {"corpo": [{"fonte": "nome"}, {"fonte": "fixo", "valor": ""}]}})
    finally:
        A._templates_enviaveis = antigo
    assert r.status_code == 400 and "vazia" in r.get_json()["erro"], r.get_json()


def test_criar_template_com_imagem_guarda_midia_e_padrao():
    import base64
    png = base64.b64encode(b"\x89PNG\r\n\x1a\nfake").decode()
    r = _cliente().post("/api/templates", json={
        "nome": "feq_teste_imagem", "categoria": "MARKETING", "corpo": "Oi {{1}}, novidade!",
        "exemplos": ["Ana"], "cabecalho": {"formato": "IMAGE", "midia": {"mime": "image/png", "base64": png}},
        "botoes": [{"tipo": "URL", "texto": "Ver", "url": "https://feq.x/{{1}}", "exemplo": "a"}]}).get_json()
    assert r["ok"] and r["status"] == "dry_run", r
    assert r["enviado"]["components"][0] == {"type": "HEADER", "format": "IMAGE",
                                             "example": {"header_handle": ["dry-run-handle"]}}
    pad = A._tpl_padrao("feq_teste_imagem")
    assert pad["midia"].endswith("/tplmidia/feq_teste_imagem.png") and pad["botoes"] == {"0": "a"}, pad
    assert (A.TPL_MIDIA_DIR / "feq_teste_imagem.png").exists()
    assert A.app.test_client().get("/tplmidia/feq_teste_imagem.png").status_code == 200   # público
    (A.TPL_MIDIA_DIR / "feq_teste_imagem.png").unlink()


def test_midia_da_campanha_vem_do_computador():
    """Arquivo escolhido na campanha vira link público próprio (hash do conteúdo):
    mesmo arquivo -> mesmo link; tipo errado é recusado antes de gravar."""
    import base64
    png = base64.b64encode(b"\x89PNG\r\n\x1a\ncampanha").decode()
    c = _cliente()
    r1 = c.post("/api/campanhas/midia", json={"formato": "IMAGE", "midia": {"mime": "image/png", "base64": png}}).get_json()
    r2 = c.post("/api/campanhas/midia", json={"formato": "IMAGE", "midia": {"mime": "image/png", "base64": png}}).get_json()
    assert r1["ok"] and r1["url"] == r2["url"] and "/tplmidia/camp_" in r1["url"], (r1, r2)
    arq = A.TPL_MIDIA_DIR / r1["url"].rsplit("/", 1)[-1]
    assert A.app.test_client().get("/tplmidia/" + arq.name).status_code == 200   # público p/ a Meta
    ruim = c.post("/api/campanhas/midia", json={"formato": "IMAGE", "midia": {"mime": "application/pdf", "base64": png}})
    assert ruim.status_code == 400 and "não aceito" in ruim.get_json()["erro"]
    arq.unlink()


if __name__ == "__main__":
    casos = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    falhas = 0
    for fn in casos:
        try:
            fn()
            print(f"PASS  {fn.__name__}")
        except AssertionError as e:
            falhas += 1
            print(f"FAIL  {fn.__name__}  {e}")
        except Exception as e:
            falhas += 1
            print(f"ERRO  {fn.__name__}  {type(e).__name__}: {e}")
    print(f"\n{len(casos) - falhas}/{len(casos)} passaram.")
    sys.exit(1 if falhas else 0)
