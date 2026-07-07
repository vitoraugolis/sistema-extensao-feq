#!/usr/bin/env python3
"""Checks do pacote de robustez: dedupe, alertas, nutrição, LGPD, FARO noop."""
import os
import sys
import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from atena.ontologia.servico import Ontologia
from atena.core.estado import Estado
from atena.core.orquestrador import Orquestrador
from atena.core.faro import Faro
from atena.transporte.whatsapp import WhatsApp
from atena.agentes.nutricao import leads_para_reengajar, reengajar

ONT = Ontologia()


def _payload(msg_id, texto="tenho superior, quero saber o preço"):
    return {"entry": [{"changes": [{"value": {"messages": [
        {"id": msg_id, "from": "5519999990002", "text": {"body": texto}}]}}]}]}


def test_dedupe_reentrega_nao_duplica():
    est = Estado(":memory:")
    est.upsert_lead("c1", nome="X", telefone="+55 19 99999-0002", curso="FEQ-0480",
                    fase="Lead", estado_conversa="qualificacao")
    wa = WhatsApp(est, Orquestrador(est, ONT))
    wa.receber(_payload("wamid.ABC"))
    wa.receber(_payload("wamid.ABC"))                    # reentrega da Meta
    turnos_lead = [h for h in est.historico("c1") if h["papel"] == "lead"]
    assert len(turnos_lead) == 1                          # processou UMA vez


def test_escalonamento_gera_alerta():
    est = Estado(":memory:")
    est.upsert_lead("c1", nome="X", telefone="+55", curso="FEQ-0480",
                    fase="Lead", estado_conversa="qualificacao")
    Orquestrador(est, ONT).processar_mensagem("c1", "quero um desconto")
    assert any(a["tipo"] == "escalonamento" for a in est.listar_alertas())


def test_nutricao_eligibilidade_e_guardrails():
    est = Estado(":memory:")
    velho = (datetime.datetime.now() - datetime.timedelta(days=5)).isoformat(timespec="seconds")
    for cid, curso, optout, reeng in (("a", "FEQ-0480", 0, 0),   # elegível
                                      ("b", "FEQ-0480", 1, 0),   # opt-out: fora
                                      ("c", "FEQ-0480", 0, 2),   # esgotou: fora
                                      ("d", "FEQ-0655", 0, 0)):  # encerrado: fora
        est.upsert_lead(cid, nome="L", telefone=f"+55 19 9{cid*7}", curso=curso,
                        fase="Lead", estado_conversa="aguardando_resposta",
                        optout=optout, reengajos=reeng)
        est.conn.execute("UPDATE leads SET atualizado=? WHERE card_id=?", (velho, cid))
    est.conn.commit()
    eleg = [l["card_id"] for l in leads_para_reengajar(est, ONT)]
    assert eleg == ["a"]
    os.environ.pop("WHATSAPP_TOKEN", None)                # dry-run
    assert reengajar(est, ONT, WhatsApp(est, None)) == 1
    assert est.get_lead("a")["reengajos"] == 1
    assert leads_para_reengajar(est, ONT) == []           # silêncio zerado pelo envio? não:
    # (reengajo enviado agora -> 'atualizado' recente não conta; elegibilidade usa última msg do LEAD,
    #  mas o guard de reengajos/atualizado evita repetição imediata)


def test_lgpd_anonimiza_perdidos_antigos():
    est = Estado(":memory:")
    est.upsert_lead("p1", nome="Fulano", telefone="+55 19 98888-7777", curso="FEQ-0480",
                    fase="Perdido", estado_conversa="descartado")
    est.add_conversa("p1", "lead", "dados pessoais aqui")
    antigo = (datetime.datetime.now() - datetime.timedelta(days=200)).isoformat(timespec="seconds")
    est.conn.execute("UPDATE leads SET atualizado=? WHERE card_id='p1'", (antigo,))
    est.conn.commit()
    assert est.anonimizar_antigos(dias=180) == 1
    l = est.get_lead("p1")
    assert l["nome"] == "(anonimizado)" and l["telefone"] == ""
    assert est.historico("p1") == []


def test_faro_noop_sem_chave():
    os.environ.pop("FARO_KEY", None)
    f = Faro()
    assert f.ativo is False
    assert f.criar_lead("X", "+55", "FEQ-0480") is None   # silencioso, sem rede
    f.mover("qualquer", "Prospect")                        # não explode


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
