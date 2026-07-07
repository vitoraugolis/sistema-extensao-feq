#!/usr/bin/env python3
"""Testes do orquestrador + estado (offline, SQLite :memory:)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from atena.ontologia.servico import Ontologia
from atena.core.estado import Estado
from atena.core.orquestrador import Orquestrador

ONT = Ontologia()


def _novo(curso="FEQ-0480", escolaridade=None):
    est = Estado(":memory:")
    est.upsert_lead("c1", nome="Teste", telefone="+55 19 99999", curso=curso,
                    fase="Lead", estado_conversa="qualificacao", escolaridade=escolaridade)
    return est, Orquestrador(estado=est, ont=ONT)


def test_optout_marca_e_encerra():
    est, orq = _novo()
    orq.processar_mensagem("c1", "quero sair, parar de receber")
    assert est.get_lead("c1")["optout"] == 1


def test_inelegivel_descarta():
    est, orq = _novo()
    orq.processar_mensagem("c1", "tenho só o ensino médio, posso fazer?")
    assert est.get_lead("c1")["fase"] == "Perdido"


def test_elegivel_qualifica_com_proposta_aprovada():
    est, orq = _novo()
    r = orq.processar_mensagem("c1", "tenho superior em engenharia química, quero o curso")
    assert est.get_lead("c1")["fase"] == "Prospect"
    assert r["revisor"]["aprovado"] is True
    assert est.decisao_pendente("c1") is not None  # mensagem virou proposta p/ humano


def test_curso_nao_captavel_escala():
    est, orq = _novo(curso="FEQ-0655")  # encerrado hoje
    orq.processar_mensagem("c1", "tenho superior completo, quero me informar")
    assert est.get_lead("c1")["estado_conversa"] == "escalado_humano"


def test_gatilho_sensivel_escala():
    est, orq = _novo(escolaridade="Superior Completo")
    orq.processar_mensagem("c1", "consigo um desconto no valor?")
    assert est.get_lead("c1")["estado_conversa"] == "escalado_humano"


def test_persistencia_conversa_e_decisao():
    est, orq = _novo()
    orq.processar_mensagem("c1", "tenho superior, quero saber o preço")
    assert len(est.historico("c1")) >= 1
    assert len(est.listar_decisoes("c1")) >= 1


def test_regra_de_ouro_consultar():
    from atena.core import contratos as C
    from atena.core.orquestrador import MSG_COORDENACAO
    est, _ = _novo()
    brain = lambda lead, msg, hist=None: C.DecisaoAgente(
        C.CONSULTAR, "sem ementa na base", dados={"duvida": "Lead pede a ementa do FEQ-0480"})
    Orquestrador(est, ONT, brain).processar_mensagem("c1", "qual a ementa?")
    assert est.get_lead("c1")["estado_conversa"] == "aguardando_coordenacao"
    dvs = est.listar_duvidas("aberta")
    assert len(dvs) == 1 and "ementa" in dvs[0]["pergunta"]
    assert est.decisao_pendente("c1")["mensagem"] == MSG_COORDENACAO  # template FIXO


def test_responder_duvida_fecha_ciclo():
    from atena.core import contratos as C
    est, _ = _novo()
    brain1 = lambda lead, msg, hist=None: C.DecisaoAgente(
        C.CONSULTAR, "x", dados={"duvida": "ementa?"})
    orq = Orquestrador(est, ONT, brain1)
    orq.processar_mensagem("c1", "qual a ementa?")
    did = est.listar_duvidas("aberta")[0]["id"]
    est.set_decisao_status(est.decisao_pendente("c1")["id"], "enviada")  # espera já foi ao lead
    orq.brain = lambda lead, msg, hist=None: C.DecisaoAgente(
        C.CONTINUAR, "coordenação respondeu",
        mensagem_ao_lead="A ementa cobre Python, otimização e simulação de processos!")
    r = orq.responder_duvida(did, "Ementa: Python, otimização e simulação de processos.")
    assert r["revisor"]["aprovado"] is True
    assert est.get_duvida(did)["status"] == "respondida"
    assert est.get_lead("c1")["estado_conversa"] == "aguardando_resposta"
    assert est.decisao_pendente("c1")["agente"] == "coordenacao"       # proposta pronta p/ envio


def test_afirmacao_pos_gate_qualifica():
    # "Sim, tenho" logo após a pergunta do gate = superior confirmado (fallback com memória)
    est, orq = _novo()
    est.add_conversa("c1", "agente", "você já concluiu o ensino superior? É o pré-requisito.")
    orq.processar_mensagem("c1", "Sim, tenho")
    assert est.get_lead("c1")["fase"] == "Prospect"


def test_gate_nao_repete_a_mesma_frase():
    from atena.core.orquestrador import MSG_GATE, MSG_GATE_2, brain_deterministico
    est, _ = _novo()
    lead = est.get_lead("c1")
    d1 = brain_deterministico(ONT, lead, "quero saber mais", historico=[])
    assert d1.mensagem_ao_lead == MSG_GATE
    d2 = brain_deterministico(ONT, lead, "legal",
                              historico=[{"papel": "agente", "texto": MSG_GATE}])
    assert d2.mensagem_ao_lead == MSG_GATE_2          # variou, não papagaiou


def test_pedido_de_link_envia_link():
    est, orq = _novo(escolaridade="Superior Completo")
    est.add_conversa("c1", "agente", "Posso te enviar o link de inscrição?")
    orq.processar_mensagem("c1", "Pode sim!")
    d = est.listar_decisoes("c1")[0]
    assert "ins.extecamp.unicamp.br" in (d["mensagem"] or "")     # mandou o link de verdade
    assert "16/08/2026" in d["mensagem"]                          # com o prazo certo


def test_anti_loop_escala_para_humano():
    from atena.core import contratos as C
    est, _ = _novo()
    est.add_conversa("c1", "agente", "MESMA RESPOSTA")
    brain_teimoso = lambda lead, msg, hist=None: C.DecisaoAgente(
        C.CONTINUAR, "x", mensagem_ao_lead="MESMA RESPOSTA")
    Orquestrador(est, ONT, brain_teimoso).processar_mensagem("c1", "e aí?")
    assert est.get_lead("c1")["estado_conversa"] == "escalado_humano"
    assert est.decisao_pendente("c1")["mensagem"] != "MESMA RESPOSTA"  # saída elegante, não papagaio


def test_funil_conta_fases():
    est = Estado(":memory:")
    est.seed_demo()
    s = est.stats_funil()
    assert s["Prospect"] >= 1 and s["Matriculado"] >= 1


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
