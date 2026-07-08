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
from atena.agentes.nutricao import leads_para_reengajar, reengajar, reengajar_um
from atena.agentes.ingestao import Ingestao, normalizar_meta, curso_de_texto
from atena.core.orquestrador import brain_deterministico
from atena.core import contratos as C

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


def test_curso_do_nome_do_formulario():
    # padrão acordado: nome do formulário = curso + versão
    assert curso_de_texto("FEQ-0333 v1") == "FEQ-0333"
    assert curso_de_texto("FEQ0480 v2") == "FEQ-0480"
    assert curso_de_texto("Campanha genérica sem código") == ""
    # form vazio -> cai no fallback de campanha
    assert curso_de_texto("", "Leads FEQ-0499 verão") == "FEQ-0499"


def test_normalizar_meta_so_nome_tel_email():
    # formulário coleta só Nome/Telefone/E-mail; curso vem do nome do form
    fd = [{"name": "full_name", "values": ["Maria Silva"]},
          {"name": "phone_number", "values": ["+55 19 99888-7766"]},
          {"name": "email", "values": ["maria@x.com"]}]
    lead = normalizar_meta(fd, campaign_name="qualquer", leadgen_id="777", curso="FEQ-0333")
    assert lead["nome"] == "Maria Silva" and lead["curso"] == "FEQ-0333"
    assert lead["telefone"] and lead["email"] == "maria@x.com"


def test_ingest_gate_dispara_1o_contato_so_se_captavel():
    est = Estado(":memory:")
    ing = Ingestao(est, ONT)
    cap = ing.ingest(normalizar_meta(
        [{"name": "full_name", "values": ["A"]}, {"name": "phone_number", "values": ["+55 19 90000-0001"]}],
        curso="FEQ-0480"))                                   # captável hoje
    assert cap["captavel"] and cap["estado_conversa"] == "aguardando_1o_contato"
    nao = ing.ingest(normalizar_meta(
        [{"name": "full_name", "values": ["B"]}, {"name": "phone_number", "values": ["+55 19 90000-0002"]}],
        curso="FEQ-0655"))                                   # encerrado
    assert nao["captavel"] is False and nao["estado_conversa"] == "aguardando_revisao"


def test_prequalificacao_marca_escolaridade():
    # form qualificatório: superior=sim + disponibilidade=sim -> escolaridade marcada
    fd = [{"name": "full_name", "values": ["Ana"]},
          {"name": "phone_number", "values": ["+55 19 90000-0009"]},
          {"name": "voce_ja_concluiu_o_ensino_superior?", "values": ["Sim"]},
          {"name": "disponibilidade_aula_presencial_01_08_2026", "values": ["Sim"]}]
    lead = normalizar_meta(fd, curso="FEQ-0333")
    assert lead["escolaridade"] == "Superior Completo"
    assert "ensino superior" in lead["qualificacao"] and "01 08 2026" in lead["qualificacao"]
    # defensivo: se a resposta ao superior for negativa, NÃO marca (não pula o gate)
    fd_neg = fd[:2] + [{"name": "tem_ensino_superior?", "values": ["Não"]}]
    assert normalizar_meta(fd_neg, curso="FEQ-0333")["escolaridade"] is None
    # form simples (só contato) -> nada muda
    assert normalizar_meta(fd[:2], curso="FEQ-0333")["escolaridade"] is None


def test_prequalificado_pula_gate_academico():
    est = Estado(":memory:")
    lead = normalizar_meta(
        [{"name": "full_name", "values": ["Ana"]},
         {"name": "phone_number", "values": ["+55 19 90000-0009"]},
         {"name": "concluiu_ensino_superior?", "values": ["Sim"]}], curso="FEQ-0333")
    r = Ingestao(est, ONT).ingest(lead)
    saved = est.get_lead(r["card_id"])
    assert saved["escolaridade"] == "Superior Completo"      # persistido
    # 1ª resposta do lead: agente NÃO re-pergunta ensino superior -> já oferece o curso
    dec = brain_deterministico(ONT, saved, "tenho interesse!", [])
    assert dec.proxima_acao == C.QUALIFICAR
    assert "ensino superior" not in (dec.mensagem_ao_lead or "").lower()
    # botão do template ("Receber o link para prosseguir") -> link de INSCRIÇÃO correto
    dec2 = brain_deterministico(ONT, saved, "Receber o link para prosseguir", [])
    assert dec2.proxima_acao == C.QUALIFICAR
    assert "/inscricao/FEQ-0333/" in (dec2.mensagem_ao_lead or "")


def test_purgar_demo_deixa_so_reais():
    est = Estado(":memory:")
    est.seed_demo()                                    # cria seed-*/demo-*
    est.upsert_lead("meta-5519999", nome="Real", telefone="+55 19 99999-9999",
                    curso="FEQ-0333", fase="Lead", estado_conversa="aguardando_resposta")
    est.add_conversa("demo-marina", "lead", "oi")      # ligado a um demo
    antes = len(est.listar_leads())
    removidos = est.purgar_demo()
    restantes = est.listar_leads()
    assert removidos >= 30 and antes - removidos == len(restantes)
    assert [l["card_id"] for l in restantes] == ["meta-5519999"]   # só o real sobra
    assert est.historico("demo-marina") == []          # conversas do demo também sumiram


def test_reativar_um_respeita_travas():
    est = Estado(":memory:")
    os.environ.pop("WHATSAPP_TOKEN", None)             # dry-run
    wa = WhatsApp(est, None)
    # captável + sem opt-out -> reengaja (dry-run) e incrementa contador
    est.upsert_lead("r1", nome="Ana", telefone="+55 19 90000-1111", curso="FEQ-0480",
                    fase="Lead", estado_conversa="aguardando_resposta")
    assert reengajar_um(est, ONT, wa, "r1")["ok"] is True
    assert est.get_lead("r1")["reengajos"] == 1
    # opt-out -> trava dura
    est.upsert_lead("r2", nome="B", telefone="+55 19 90000-2222", curso="FEQ-0480",
                    fase="Lead", estado_conversa="aguardando_resposta", optout=1)
    assert reengajar_um(est, ONT, wa, "r2")["ok"] is False
    # curso encerrado -> trava dura
    est.upsert_lead("r3", nome="C", telefone="+55 19 90000-3333", curso="FEQ-0655",
                    fase="Lead", estado_conversa="aguardando_resposta")
    assert reengajar_um(est, ONT, wa, "r3")["ok"] is False


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
