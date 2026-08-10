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


def test_limites_do_prazo():
    """0 = imediato (decisão do operador, ciente de que perde a janela de conferir na
    Extecamp). Negativo vira 0 e o teto é 30 dias."""
    c = _cliente()
    assert c.post("/api/config", json={"verif_auto_horas": 0}).get_json()["verif_auto_horas"] == 0
    assert c.post("/api/config", json={"verif_auto_horas": -5}).get_json()["verif_auto_horas"] == 0
    assert c.post("/api/config", json={"verif_auto_horas": 999}
                  ).get_json()["verif_auto_horas"] == 720          # teto de 30 dias
    assert c.post("/api/config", json={"verif_auto_horas": "abc"}).status_code == 400


def test_imediato_torna_tudo_elegivel():
    """Com prazo 0, até quem acabou de chegar entra — é o que 'imediatamente' significa."""
    _seed("i_agora", 0)
    _seed("i_pouco", 0.2)
    eleg = {x["card_id"] for x in A._verif_elegiveis(horas=0, agora=AGORA)}
    assert {"i_agora", "i_pouco"} <= eleg


def test_nao_repete_template_para_quem_acabou_de_receber():
    """Lead do Meta já recebe feq_lead na ingestão. Se ele preencher a LP em seguida,
    o modo imediato mandaria o MESMO template em segundos — a guarda de silêncio
    impede isso e registra o adiamento."""
    _seed("dup1", 0)
    A.EST.add_conversa("dup1", "agente", "[template feq_lead] FEQ-0480", status="enviado")
    c = _cliente()
    c.post("/api/config", json={"verif_auto": True, "verif_auto_horas": 0})
    decididos = {a["card_id"] for a in c.post("/api/verificacao/auto").get_json()["aplicados"]}
    assert "dup1" not in decididos                    # pulado, não decidido
    assert "dup1" in {p["card_id"] for p in A.GRW.verificacoes_pendentes()}   # segue na fila
    linhas = A.EST.conn.execute("SELECT acao FROM auditoria WHERE alvo='dup1'").fetchall()
    assert any("adiada" in r["acao"] for r in linhas)  # fica rastreável por que não decidiu


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


def test_etapa1_nao_entra_na_fila_de_transcricao():
    """A etapa 1 (só contato) NÃO é um checkout: o lead não chegou à Extecamp e não
    há ficha para transcrever. Ele fica na régua de recuperação (2h) e fora da fila
    de Verificação. Antes o webhook da LP decidia 'não inscreveu' na hora — isso saiu
    quando a régua de 2h passou a rodar de verdade no agendador."""
    c = _cliente()
    c.post("/api/config", json={"verif_auto": True, "verif_auto_horas": 0})
    r = c.post("/webhook/ponte", json={"nome": "Etapa Um", "whatsapp": "19997770001",
                                       "curso": "FEQ-0480", "event_id": "ev-etapa1-1"})
    assert r.status_code == 200
    cid = r.get_json()["card_id"]
    tipos = [x[0] for x in A.GRW.conn.execute(
        "SELECT tipo FROM eventos_funil WHERE card_id=?", (cid,))]
    assert tipos == ["etapa1"], tipos                       # sem checkout, sem CAPI
    assert cid not in {p["card_id"] for p in A.GRW.verificacoes_pendentes()}
    assert cid in {p["card_id"] for p in A.GRW.checkouts_pendentes()}   # está na régua


def test_etapa2_coloca_na_fila_de_transcricao():
    """Concluída a etapa 2, a ficha existe e o agente precisa transcrevê-la na
    Extecamp — é aí que o lead aparece na Verificação."""
    c = _cliente()
    r = c.post("/webhook/ponte", json={"nome": "Etapa Dois", "whatsapp": "19997770002",
                                       "curso": "FEQ-0480", "event_id": "ev-etapa2-1"})
    cid = r.get_json()["card_id"]
    assert cid not in {p["card_id"] for p in A.GRW.verificacoes_pendentes()}
    r2 = c.post("/webhook/inscricao", json={
        "whatsapp": "19997770002", "curso_id": "FEQ-0480", "event_id": "ev-etapa2-1",
        "cpf": "123.456.789-00", "nascimento": "1990-01-01",
        "escolaridade": "Superior Completo", "forma_pagamento": "19 PARCELA(S)"})
    assert r2.status_code == 200, r2.get_json()
    tipos = [x[0] for x in A.GRW.conn.execute(
        "SELECT tipo FROM eventos_funil WHERE card_id=?", (cid,))]
    assert "inicio_inscricao" in tipos
    assert cid in {p["card_id"] for p in A.GRW.verificacoes_pendentes()}
    assert A.GRW.get_inscricao(cid)["status"] == "recebida"


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
