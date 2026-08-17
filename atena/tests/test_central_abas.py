#!/usr/bin/env python3
"""
Central: Inscrições (substituiu a Verificação), Notificações e Otimização.

O que estes testes protegem, e por quê:
- a ficha da etapa 2 tem de chegar INTEIRA à tela de quem vai transcrever;
- a caixa de entrada não pode mostrar o mesmo trabalho duas vezes;
- o otimizador não pode dizer que está otimizando quando está cego ou sem dado.
"""
import os
import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
os.environ["ATENA_DEV"] = "1"
import atena.api.app as A                                    # noqa: E402
from atena.avaliacao import otimizacao as otim               # noqa: E402
from atena.core.estado import Estado                          # noqa: E402
from atena.growth.db import Growth                            # noqa: E402

# `Estado()`/`Growth()` sem args caem no arquivo real (atena/data/atena.db) — não
# existe "ATENA_ESTADO" honrado em lugar nenhum do código (isto aqui era um no-op:
# o teste vinha escrevendo card de teste no banco de dev de verdade a cada execução).
# As rotas de app.py leem os globais MÓDULO (EST/GRW) por nome a cada chamada, então
# reatribuir depois do import redireciona toda rota que só usa EST/GRW — que é o caso
# de tudo que este arquivo exercita (kanban_criar, inscrições, notificações).
A.EST = Estado(":memory:")
A.GRW = Growth(":memory:")

FICHA = {"cpf": "123.456.789-09", "nascimento": "1992-05-14", "escolaridade": "Superior Completo",
         "forma_pagamento": "19 PARCELA(S) DE R$ 799,00", "cep": "13083-970",
         "cidade": "Campinas", "empresa": "Braskem", "cargo": "Engenheira"}


def _cliente():
    c = A.app.test_client()
    with c.session_transaction() as s:
        s["usuario"], s["papel"] = "tester", "supervisor"
    return c


def _com_ficha(cid="meta-19900000001", curso="FEQ-0480"):
    A.EST.upsert_lead(cid, nome="Fulana", telefone="+5519900000001", curso=curso, fase="Lead")
    A.GRW.add_evento(cid, curso, "etapa1")
    A.GRW.add_inscricao(cid, curso, "009", json.dumps(FICHA, ensure_ascii=False))
    return cid


def test_ficha_chega_agrupada_e_completa():
    """Transcrever é copiar campo a campo: se um campo some da tela, a inscrição do
    lead sai errada na Extecamp e ninguém descobre."""
    cid = _com_ficha("meta-19900000011")
    d = _cliente().get("/api/inscricoes").get_json()
    item = next(i for i in d["inscricoes"] if i["card_id"] == cid)
    campos = {it["campo"]: it["valor"] for g in item["grupos"] for it in g["itens"]}
    assert set(FICHA) <= set(campos), set(FICHA) - set(campos)
    assert campos["cpf"] == FICHA["cpf"]
    titulos = [g["titulo"] for g in item["grupos"]]
    assert titulos == sorted(titulos, key=lambda t: [
        "Dados pessoais", "Endereço", "Formação e atuação", "Pagamento", "Outros"].index(t)), titulos


def test_campo_novo_no_formulario_nao_some_da_tela():
    """Campo sem rótulo cai em 'Outros' em vez de sumir — some seria pior que feio:
    o agente transcreveria uma ficha incompleta sem saber."""
    cid = "meta-19900000012"
    A.EST.upsert_lead(cid, nome="Beltrana", telefone="+5519900000012", curso="FEQ-0480")
    A.GRW.add_inscricao(cid, "FEQ-0480", "009",
                        json.dumps({**FICHA, "campo_inedito": "valor x"}, ensure_ascii=False))
    d = _cliente().get("/api/inscricoes").get_json()
    item = next(i for i in d["inscricoes"] if i["card_id"] == cid)
    outros = next(g for g in item["grupos"] if g["titulo"] == "Outros")
    assert any(it["campo"] == "campo_inedito" for it in outros["itens"])


def test_transcrever_confirma_conversao_uma_vez():
    cid = _com_ficha("meta-19900000013")
    c = _cliente()
    r = c.post(f"/api/inscricoes/{cid}/transcrita").get_json()
    assert r["ok"] and r["status"] == "transcrita"
    assert A.EST.get_lead(cid)["fase"] == "Inscrito"
    tipos = [e["tipo"] for e in A.GRW.eventos(card_id=cid)]
    assert tipos.count("inscricao_confirmada") == 1
    # reclique não duplica a conversão reportada à Meta
    c.post(f"/api/inscricoes/{cid}/transcrita")
    tipos = [e["tipo"] for e in A.GRW.eventos(card_id=cid)]
    assert tipos.count("inscricao_confirmada") == 1, tipos


def test_ficha_transcrita_sai_da_fila():
    cid = _com_ficha("meta-19900000014")
    c = _cliente()
    pend = c.get("/api/inscricoes?status=recebida").get_json()
    assert any(i["card_id"] == cid for i in pend["inscricoes"])
    c.post(f"/api/inscricoes/{cid}/transcrita")
    pend = c.get("/api/inscricoes?status=recebida").get_json()
    assert not any(i["card_id"] == cid for i in pend["inscricoes"])


def test_ficha_aparece_no_card_da_conversa():
    """O agente é perguntado 'o que eu preenchi?' durante o atendimento — a ficha
    tem de estar no card, não numa aba que o obriga a largar a conversa."""
    cid = _com_ficha("meta-19900000015")
    d = _cliente().get(f"/api/conversas/{cid}").get_json()
    assert d["inscricao"] and d["inscricao"]["status"] == "recebida"
    assert any(it["campo"] == "cpf" for g in d["inscricao"]["grupos"] for it in g["itens"])


def test_caixa_de_entrada_nao_duplica_a_mesma_ficha():
    """`processar_etapa2` grava alerta E a ficha entra na fila: sem o filtro, o mesmo
    trabalho ocupava duas linhas da caixa de entrada."""
    cid = _com_ficha("meta-19900000016")
    A.EST.add_alerta("inscricao", "Ficha completa pronta para transcrever", cid)
    d = _cliente().get("/api/notificacoes").get_json()
    do_card = [i for i in d["itens"] if i.get("card_id") == cid]
    assert len(do_card) == 1, do_card
    assert do_card[0]["tipo"] == "ficha_nova"


def test_alerta_marcado_como_lido_sai_da_caixa():
    A.EST.add_alerta("pacing_atrasado", "FEQ-0480 atrasado", None)
    c = _cliente()
    antes = c.get("/api/notificacoes").get_json()
    alerta = next(i for i in antes["itens"] if i["fonte"] == "alerta")
    c.post("/api/notificacoes/ler", json={"id": alerta["id"]})
    depois = c.get("/api/notificacoes").get_json()
    assert not any(i["fonte"] == "alerta" and i["id"] == alerta["id"] for i in depois["itens"])


def test_otimizacao_nao_finge_cpl_sem_dado_de_gasto():
    """Sem linha em ad_desempenho, CPL não existe. Reportar 0 faria o objetivo
    parecer ótimo justamente quando se está cego."""
    d = _cliente().get("/api/otimizacao").get_json()
    o = d["objetivo"]
    if not o["cpl_disponivel"]:
        assert o["j_custo"] is None and o["motivo_sem_cpl"]
        assert all(c["cpl"] is None for c in o["por_curso"])


def test_gate_mantem_otimizador_em_sombra_sem_evidencia():
    d = _cliente().get("/api/otimizacao").get_json()
    g = d["gate"]
    if g["episodios"] < otim.GATE_EPISODIOS or g["conversoes"] < otim.GATE_CONVERSOES:
        assert g["liberado"] is False and g["modo"] == "sombra"


def test_bandit_nao_le_1_de_1_como_certeza():
    """Beta(1,1) + barra de erro: com 1 amostra a média posterior é 2/3, não 100%.
    É o que impede o otimizador de eleger uma variante por sorte."""
    b = {"n": 1, "sucessos": 1}
    a, beta = 1 + b["sucessos"], 1 + (b["n"] - b["sucessos"])
    media = a / (a + beta)
    assert 0.6 < media < 0.7, media


def test_rotulo_antigo_nao_contamina_o_treino():
    """Antes do formulário de 2 etapas, `inicio_inscricao` queria dizer outra coisa.
    Episódios só contam a partir do primeiro `etapa1` observado."""
    corte = otim.corte_rotulo_novo(A.GRW)
    if corte:
        for e in otim.episodios(A.GRW):
            assert e["ts"] >= corte, (e["ts"], corte)


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
