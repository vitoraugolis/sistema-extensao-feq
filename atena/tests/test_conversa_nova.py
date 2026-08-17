#!/usr/bin/env python3
"""
Iniciar/reiniciar conversa a qualquer momento (atualização pedida pelo operador).

Dois casos cobertos pelo MESMO endpoint (`POST /api/conversas/<card_id>/nova`),
que reaproveita o caminho único do 1º contato (`_primeiro_contato`, já usado pela
ingestão automática do Meta):

1. Lead que NUNCA recebeu mensagem — ex.: só entregou a ficha da etapa 2 pela LP
   (spec 17 §7b) e nunca passou pelo WhatsApp. Precisa ser possível abrir a
   conversa mesmo sem nenhum histórico prévio.
2. Lead que JÁ tem conversa — o operador quer, quando quiser, recomeçar o
   atendimento (reenviar a mensagem de abertura). Não há conceito de "thread"
   separada no schema (`conversas` é só um log por card_id — specs 12), então
   "nova conversa" aqui é reenviar a abertura na mesma linha do tempo.

Não exige histórico, não checa captabilidade do curso (ato deliberado do
operador — mesmo raciocínio de conversa_enviar_template) e respeita opt-out.
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
os.environ["ATENA_DEV"] = "1"
os.environ["WHATSAPP_TOKEN"] = ""

from atena.core.estado import Estado                     # noqa: E402
from atena.growth.db import Growth                        # noqa: E402
from atena.transporte.whatsapp import WhatsApp             # noqa: E402
import atena.api.app as A                                  # noqa: E402

# Isola do banco de dev real (mesmo padrão de test_funil_manual.py). `WA` também
# precisa ser reconstruído sobre o EST novo: ele guarda `self.est` por referência
# na construção (não por lookup de nome de módulo), então sem isto o gate de
# opt-out do WhatsApp checaria o card_id contra o banco de dev antigo.
A.EST = Estado(":memory:")
A.GRW = Growth(":memory:")
A.WA = WhatsApp(A.EST, None)


def _cliente():
    c = A.app.test_client()
    with c.session_transaction() as s:
        s["usuario"], s["papel"] = "tester", "supervisor"
    return c


def test_iniciar_conversa_sem_historico_nenhum():
    """Lead só com ficha da etapa 2 (nenhuma mensagem ainda) — o botão precisa
    conseguir abrir a conversa."""
    A.EST.upsert_lead("op-19999991001", nome="Fulano Ficha", telefone="19999991001",
                      curso="FEQ-0480", fase="Inscrito", estado_conversa="aguardando_1o_contato")
    assert A.EST.historico("op-19999991001") == []
    c = _cliente()
    r = c.post("/api/conversas/op-19999991001/nova").get_json()
    assert r["envio"]["ok"] is True
    hist = A.EST.historico("op-19999991001")
    assert len(hist) == 1 and hist[0]["papel"] == "agente"
    assert "template" in hist[0]["texto"]
    assert A.EST.get_lead("op-19999991001")["estado_conversa"] == "aguardando_resposta"


def test_nova_conversa_com_historico_reenvia_abertura_sem_apagar_nada():
    """Lead que já conversou antes — 'nova conversa' reenvia a abertura, preservando
    o histórico anterior (não existe apagamento nem thread separada, spec 12)."""
    A.EST.upsert_lead("op-19999991002", nome="Ciclana Antiga", telefone="19999991002",
                      curso="FEQ-0480", fase="Prospect", estado_conversa="escalado_humano")
    A.EST.add_conversa("op-19999991002", "lead", "oi, ainda tenho dúvida")
    A.EST.add_conversa("op-19999991002", "agente", "claro, posso ajudar")
    antes = len(A.EST.historico("op-19999991002"))
    c = _cliente()
    r = c.post("/api/conversas/op-19999991002/nova").get_json()
    assert r["envio"]["ok"] is True
    depois = A.EST.historico("op-19999991002")
    assert len(depois) == antes + 1                       # só ACRESCENTA, nada some
    assert depois[0]["texto"] == "oi, ainda tenho dúvida"  # histórico antigo intacto
    assert "template" in depois[-1]["texto"]
    assert A.EST.get_lead("op-19999991002")["estado_conversa"] == "aguardando_resposta"


def test_endpoint_exige_lead_existente():
    c = _cliente()
    r = c.post("/api/conversas/nao-existe-123/nova")
    assert r.status_code == 404


def test_optout_bloqueia_nova_conversa():
    A.EST.upsert_lead("op-19999991003", nome="Optout Fulano", telefone="19999991003",
                      curso="FEQ-0480", fase="Lead", estado_conversa="aguardando_1o_contato",
                      optout=1)
    c = _cliente()
    r = c.post("/api/conversas/op-19999991003/nova").get_json()
    assert r["envio"]["ok"] is False
    assert "opt-out" in (r["envio"].get("motivo") or "")
    assert A.EST.historico("op-19999991003") == []        # nada foi gravado


def test_nao_exige_curso_captavel():
    """Ato deliberado do operador: mesmo curso encerrado, o botão ainda dispara —
    consistente com conversa_enviar_template, que também não checa captabilidade."""
    A.EST.upsert_lead("op-19999991004", nome="Curso Encerrado", telefone="19999991004",
                      curso="FEQ-0333", fase="Em espera", estado_conversa="aguardando_1o_contato")
    c = _cliente()
    r = c.post("/api/conversas/op-19999991004/nova").get_json()
    assert r["envio"]["ok"] is True


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
