#!/usr/bin/env python3
"""
Veredito automático de "não inscreveu" na aba Verificação.

O risco desta automação não é técnico, é de negócio: decidir cedo demais manda
"você não terminou" para quem ACABOU de se inscrever (a Extecamp leva horas para
refletir boleto/pagamento). Por isso o limiar de tempo é a trava principal e é o
que estes testes protegem.
"""
import os
import sys
import datetime
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

os.environ.pop("ATENA_USERS", None)
os.environ["WHATSAPP_TOKEN"] = ""        # dry-run: nenhum envio real
os.environ["WHATSAPP_WABA_ID"] = ""
# Sem isto o agendador de _operacoes sobe junto com o app e decide pendências por
# conta própria no meio do teste (além de ser a trava que impede envio real em dev).
os.environ["ATENA_DEV"] = "1"

import atena.core.estado as _E
_E.DEFAULT_DB = tempfile.mktemp(suffix=".db")
import atena.api.app as A

AGORA = datetime.datetime.now()


def _seed(cid, horas, curso="FEQ-0480"):
    ts = (AGORA - datetime.timedelta(hours=horas)).isoformat(timespec="seconds")
    A.EST.upsert_lead(cid, nome=cid, telefone="+5519" + cid, curso=curso,
                      fase="Lead", estado_conversa="aguardando_resposta")
    A.GRW.add_evento(cid, curso, "inicio_inscricao", ts=ts)


def _cliente():
    c = A.app.test_client()
    c.post("/api/login", json={"usuario": "operador", "senha": "operador"})
    return c


def test_limiar_protege_quem_acabou_de_iniciar():
    for cid, h in [("p_recente", 1), ("p_meio", 20), ("p_velho", 60)]:
        _seed(cid, h)
    eleg = lambda h: {x["card_id"] for x in A._verif_elegiveis(horas=h, agora=AGORA)}
    assert "p_recente" not in eleg(6)          # 1h nunca entra
    assert "p_meio" in eleg(6) and "p_meio" not in eleg(24)
    assert "p_velho" in eleg(48)


def test_piso_do_limiar_nao_pode_ser_furado():
    """6h é o mínimo que a Extecamp leva para refletir pagamento — abaixo disso a
    decisão automática seria chute contra o lead."""
    c = _cliente()
    assert c.post("/api/config", json={"verif_auto_horas": 1}
                  ).get_json()["verif_auto_horas"] == A.VERIF_AUTO_MIN_H
    assert c.post("/api/config", json={"verif_auto_horas": 999}
                  ).get_json()["verif_auto_horas"] == 720          # teto de 30 dias
    assert c.post("/api/config", json={"verif_auto_horas": "abc"}).status_code == 400


def test_desligada_nao_decide_nada():
    c = _cliente()
    c.post("/api/config", json={"verif_auto": False})
    assert c.post("/api/verificacao/auto").status_code == 409       # não roda escondido
    antes = len(A.GRW.verificacoes_pendentes())
    assert len(A.GRW.verificacoes_pendentes()) == antes


def test_ligada_decide_so_os_vencidos_e_tira_da_fila():
    _seed("d_novo", 2)
    _seed("d_antigo", 100)
    c = _cliente()
    c.post("/api/config", json={"verif_auto": True, "verif_auto_horas": 48})
    decididos = {a["card_id"] for a in c.post("/api/verificacao/auto").get_json()["aplicados"]}
    assert "d_antigo" in decididos and "d_novo" not in decididos
    restantes = {p["card_id"] for p in A.GRW.verificacoes_pendentes()}
    assert "d_antigo" not in restantes and "d_novo" in restantes    # saiu só quem foi decidido


def test_teto_por_passada_evita_disparo_em_massa():
    """Ligar a chave com fila grande não pode virar rajada de mensagens."""
    for i in range(20):
        _seed(f"massa{i}", 100)
    c = _cliente()
    c.post("/api/config", json={"verif_auto": True, "verif_auto_horas": 48})
    assert len(c.post("/api/verificacao/auto").get_json()["aplicados"]) <= 15


def test_payload_da_aba_traz_o_estado_da_chave():
    c = _cliente()
    c.post("/api/config", json={"verif_auto": True, "verif_auto_horas": 72})
    v = c.get("/api/verificacao").get_json()
    assert isinstance(v["pendentes"], list)          # a lista continua lá p/ o operador
    assert v["auto"] is True and v["horas"] == 72 and v["min_horas"] == A.VERIF_AUTO_MIN_H
    assert isinstance(v["elegiveis"], int)


def test_decisao_automatica_fica_auditada():
    _seed("aud1", 100)
    c = _cliente()
    c.post("/api/config", json={"verif_auto": True, "verif_auto_horas": 48})
    c.post("/api/verificacao/auto")
    linhas = [r for r in A.EST.conn.execute(
        "SELECT ator, acao FROM auditoria WHERE alvo='aud1'").fetchall()]
    assert any(r["ator"] == "auto" and "verificação automática" in r["acao"] for r in linhas)


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
