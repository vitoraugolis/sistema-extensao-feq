#!/usr/bin/env python3
"""
Card de CRM: contato completo, notas e origem de campanha (UTM).

O ponto sensível é a ATRIBUIÇÃO: o UTM do PRIMEIRO contato é o que diz qual anúncio
trouxe o lead. Se uma visita posterior sobrescrevesse, a inteligência de campanha
apontaria sempre para o último clique. Estes testes fixam isso.
"""
import os
import sys
import json
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

os.environ.pop("ATENA_USERS", None)
os.environ["WHATSAPP_TOKEN"] = ""
os.environ["ATENA_DEV"] = "1"          # sem agendador durante o teste

import atena.core.estado as _E
_E.DEFAULT_DB = tempfile.mktemp(suffix=".db")

from atena.core.estado import Estado
from atena.ontologia.servico import Ontologia
from atena.agentes.ingestao import Ingestao, extrair_utm
import atena.api.app as A

ONT = Ontologia()


def _cliente():
    c = A.app.test_client()
    c.post("/api/login", json={"usuario": "operador", "senha": "operador"})
    return c


def test_extrair_utm_do_payload_e_da_url():
    assert extrair_utm({"utm_source": "meta", "utm_campaign": "ago"}) == \
        {"utm_source": "meta", "utm_campaign": "ago"}
    # fallback: integração antiga mandava só a url
    u = extrair_utm({"url": "https://x.com/lp?utm_source=google&utm_medium=cpc&gclid=abc"})
    assert u == {"utm_source": "google", "utm_medium": "cpc", "gclid": "abc"}
    assert extrair_utm({"url": "https://x.com/lp"}) == {}
    assert extrair_utm({}) == {}


def test_ingestao_grava_email_e_utm():
    """O e-mail já vinha do formulário Meta e era DESCARTADO antes desta mudança."""
    est = Estado(":memory:")
    ing = Ingestao(est, ONT)
    r = ing.ingest({"telefone": "+55 19 99999-1111", "curso": "FEQ-0480", "nome": "Ana",
                    "email": "ana@exemplo.com", "utm_source": "meta",
                    "utm_campaign": "feq0480_agosto"})
    lead = est.get_lead(r["card_id"])
    assert lead["email"] == "ana@exemplo.com"
    assert est.utm_de(lead) == {"utm_source": "meta", "utm_campaign": "feq0480_agosto"}


def test_primeiro_utm_vence_o_ultimo():
    """Atribuição é do primeiro toque: o anúncio que TROUXE o lead."""
    est = Estado(":memory:")
    ing = Ingestao(est, ONT)
    r = ing.ingest({"telefone": "+55 19 99999-2222", "curso": "FEQ-0480",
                    "utm_source": "meta", "utm_campaign": "primeira"})
    ing.ingest({"telefone": "19999992222", "curso": "FEQ-1000",
                "utm_source": "google", "utm_campaign": "segunda"})
    lead = est.get_lead(r["card_id"])
    assert est.utm_de(lead)["utm_campaign"] == "primeira"


def test_segundo_contato_completa_o_que_faltava():
    """Se o 1º toque não tinha e-mail e o 2º tem, preenche — sem apagar nada."""
    est = Estado(":memory:")
    ing = Ingestao(est, ONT)
    r = ing.ingest({"telefone": "+55 19 99999-3333", "curso": "FEQ-0480", "nome": "Bia"})
    assert not (est.get_lead(r["card_id"])["email"] or "")
    ing.ingest({"telefone": "19999993333", "curso": "FEQ-0480", "email": "bia@exemplo.com"})
    lead = est.get_lead(r["card_id"])
    assert lead["email"] == "bia@exemplo.com" and lead["nome"] == "Bia"


def test_card_do_funil_traz_contato_e_origem():
    A.EST.upsert_lead("crm1", nome="Carlos", telefone="+55 19 98888-0001",
                      email="carlos@exemplo.com", curso="FEQ-0480", fase="Lead",
                      estado_conversa="aguardando_resposta",
                      utm=json.dumps({"utm_source": "meta", "utm_campaign": "x"}))
    c = _cliente()
    cards = [l for col in c.get("/api/kanban").get_json()["colunas"]
             for l in col["leads"] if l["id"] == "crm1"]
    assert cards and cards[0]["email"] == "carlos@exemplo.com"
    # guardado em formato internacional (o link wa.me depende disso); a máscara
    # de exibição é aplicada na tela, não no banco
    assert cards[0]["telefone"] == "5519988880001"
    assert cards[0]["utm"]["utm_source"] == "meta"
    assert cards[0]["tem_notas"] is False


def test_notas_e_email_pela_central():
    A.EST.upsert_lead("crm2", nome="Dora", telefone="+55 19 98888-0002", curso="FEQ-0480",
                      fase="Lead", estado_conversa="aguardando_resposta")
    c = _cliente()
    assert c.post("/api/leads/crm2/notas", json={"notas": "Prefere contato à noite."}).status_code == 200
    assert A.EST.get_lead("crm2")["notas"] == "Prefere contato à noite."
    d = c.get("/api/conversas/crm2").get_json()
    assert d["notas"] == "Prefere contato à noite."
    # e-mail preenchido à mão, com validação mínima
    assert c.post("/api/leads/crm2/contato", json={"email": "sem-arroba"}).status_code == 400
    assert c.post("/api/leads/crm2/contato", json={"email": "dora@exemplo.com"}).status_code == 200
    assert A.EST.get_lead("crm2")["email"] == "dora@exemplo.com"
    assert c.post("/api/leads/inexistente/notas", json={"notas": "x"}).status_code == 404


def test_lgpd_anonimiza_email_e_notas():
    """PII nova precisa sumir junto com nome/telefone na anonimização."""
    import datetime
    est = Estado(":memory:")
    antigo = (datetime.datetime.now() - datetime.timedelta(days=400)).isoformat()
    est.upsert_lead("velho", nome="X", telefone="+5519999", email="x@y.com",
                    notas="anotação com dado pessoal", curso="FEQ-0480", fase="Perdido",
                    utm=json.dumps({"utm_source": "meta"}))
    est.conn.execute("UPDATE leads SET atualizado=? WHERE card_id='velho'", (antigo,))
    est.conn.commit()
    assert est.anonimizar_antigos(dias=180) == 1
    l = est.get_lead("velho")
    assert l["email"] == "" and l["notas"] == "" and l["telefone"] == ""
    assert est.utm_de(l) == {"utm_source": "meta"}   # campanha não é dado pessoal: fica


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
