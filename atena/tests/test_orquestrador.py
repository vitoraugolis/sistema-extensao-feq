#!/usr/bin/env python3
"""Testes do orquestrador + estado (offline, SQLite :memory:)."""
import sys
import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from atena.ontologia.servico import Ontologia
from atena.core.estado import Estado
from atena.core.orquestrador import Orquestrador, brain_deterministico
from atena.core import contratos as C

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


def test_duas_mensagens_juntas_nao_geram_resposta_duplicada():
    """Caso real (print de 31/07): o lead mandou 2 falas no mesmo minuto, o webhook tratou
    cada uma numa thread e as duas leram o histórico antes de qualquer resposta ser gravada
    -> a MESMA mensagem foi enviada duas vezes. O lock por lead tem de serializar."""
    import threading
    import time
    est, orq = _novo(escolaridade="Superior Completo")

    def brain_lento(lead, msg, hist=None):
        n = sum(1 for h in (hist or []) if h.get("papel") == "agente")
        time.sleep(0.15)                       # janela larga para a corrida acontecer
        return C.DecisaoAgente(C.CONTINUAR, "teste",
                               mensagem_ao_lead=f"resposta vendo {n} msg(s) do agente")
    orq.brain = brain_lento
    ts = [threading.Thread(target=orq.processar_mensagem, args=("c1", m))
          for m in ("Uma área em expansão", "Quando abre novas turmas?")]
    for t in ts: t.start()
    for t in ts: t.join()
    props = [d["mensagem"] for d in est.listar_decisoes("c1")]
    assert len(props) == 2, props
    assert props[0] != props[1], f"resposta duplicada: {props}"   # a 2ª viu a 1ª


def test_modo_manual_nao_gera_resposta():
    est, orq = _novo(escolaridade="Superior Completo")
    est.set_lead("c1", modo="manual")
    r = orq.processar_mensagem("c1", "tenho superior, quero saber o preço")
    assert r == {"modo_manual": True}                 # cérebro não rodou
    assert est.decisao_pendente("c1") is None          # nenhuma proposta gerada
    assert any(h["papel"] == "lead" for h in est.historico("c1"))  # msg do lead registrada
    # voltar a auto: agente responde normalmente
    est.set_lead("c1", modo="auto")
    r2 = orq.processar_mensagem("c1", "tenho superior, quero saber o preço")
    assert r2.get("decisao") and est.decisao_pendente("c1") is not None


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
    from atena.core.orquestrador import _msg_gate, brain_deterministico
    est, _ = _novo()
    lead = est.get_lead("c1")
    d1 = brain_deterministico(ONT, lead, "quero saber mais", historico=[])
    assert d1.mensagem_ao_lead == _msg_gate(ONT, "FEQ-0480")
    d2 = brain_deterministico(ONT, lead, "legal",
                              historico=[{"papel": "agente", "texto": d1.mensagem_ao_lead}])
    assert d2.mensagem_ao_lead == _msg_gate(ONT, "FEQ-0480", insistindo=True)
    assert d2.mensagem_ao_lead != d1.mensagem_ao_lead          # variou, não papagaiou


def test_gate_pergunta_o_nivel_DO_CURSO():
    """FEQ-0299/0670 pedem só ensino médio: perguntar 'concluiu o ensino superior?'
    faria o lead elegível se autoexcluir (e seria informação falsa)."""
    from atena.core.orquestrador import _msg_gate
    assert "ensino médio" in _msg_gate(ONT, "FEQ-0299")
    assert "ensino superior" not in _msg_gate(ONT, "FEQ-0299")
    assert "ensino superior" in _msg_gate(ONT, "FEQ-0480")


def test_lead_com_medio_e_elegivel_no_curso_de_medio():
    """O gate não pode descartar quem tem exatamente o nível exigido pelo curso."""
    est, _ = _novo()
    est.set_lead("c1", curso="FEQ-0299", escolaridade="Ensino Médio Completo")
    d = brain_deterministico(ONT, est.get_lead("c1"), "tenho interesse",
                             historico=[], hoje=datetime.date(2026, 7, 21))
    assert d.proxima_acao != C.DESCARTAR


def test_sem_curso_pergunta_qual_em_vez_de_escalar():
    """Lead do botão de WhatsApp da LP sem curso: agente descobre, não escala."""
    est, _ = _novo()
    est.set_lead("c1", curso="")
    d = brain_deterministico(ONT, est.get_lead("c1"), "oi, quero informações", historico=[])
    assert d.proxima_acao == C.CONTINUAR
    assert "qual curso" in (d.mensagem_ao_lead or "").lower()


def test_sem_curso_mas_citou_codigo_adota_o_curso():
    est, _ = _novo()
    est.set_lead("c1", curso="")
    d = brain_deterministico(ONT, est.get_lead("c1"),
                             "Olá! Vim da página do curso FEQ-1000 e quero saber mais.",
                             historico=[], hoje=datetime.date(2026, 7, 21))
    assert d.dados.get("curso") == "FEQ-1000"      # persiste no card
    assert "qual curso" not in (d.mensagem_ao_lead or "").lower()


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


def test_optout_do_cerebro_sem_o_lead_pedir_e_ignorado():
    """INCIDENTE 21/07/2026: o LLM devolveu 'encerrar_optout' com motivo "Link
    enviado para inscrição" logo depois do lead clicar "Receber link para
    prosseguir" — confundiu ENCERRAR o atendimento com descadastro e silenciou
    para sempre o lead de MAIOR intenção. Opt-out só vale com as palavras do lead."""
    est, orq = _novo()
    orq.brain = lambda lead, msg, hist=None: C.DecisaoAgente(
        C.OPTOUT, "Link enviado para inscrição no curso.",
        mensagem_ao_lead="Claro! Aqui está o link de inscrição.")
    orq.processar_mensagem("c1", "Receber link para prosseguir")
    assert est.get_lead("c1")["optout"] == 0          # NÃO descadastrou
    assert est.get_lead("c1")["estado_conversa"] != "encerrado"


def test_optout_de_verdade_continua_valendo():
    """O caminho legítimo (LGPD) não pode ser enfraquecido pela trava acima."""
    est, orq = _novo()
    orq.processar_mensagem("c1", "não quero mais receber, pode me tirar da lista")
    assert est.get_lead("c1")["optout"] == 1


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
