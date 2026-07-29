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
from atena.transporte.whatsapp import WhatsApp, extrair_mensagem
from atena.agentes.nutricao import reativar, reengajar_um
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
    os.environ.pop("WHATSAPP_TOKEN", None)                # dry-run
    wa = WhatsApp(est, None)
    # passada fria: só 'a' recebe o 1º template da cadência
    assert reativar(est, ONT, wa) == {"templates": 1, "nudges": []}
    assert est.get_lead("a")["reengajos"] == 1
    # passada seguinte: 'a' acabou de ser tocado (2º template exige 7d de silêncio) -> nada
    assert reativar(est, ONT, wa) == {"templates": 0, "nudges": []}
    # DENTRO da janela (lead falou 10h atrás, 1 resposta nossa) -> nudge free-text via propor
    est.upsert_lead("e", nome="E", telefone="+55 19 90000-0005", curso="FEQ-0480",
                    fase="Lead", estado_conversa="aguardando_resposta")
    est.add_conversa("e", "lead", "oi"); est.add_conversa("e", "agente", "resposta")
    h10 = (datetime.datetime.now() - datetime.timedelta(hours=10)).isoformat(timespec="seconds")
    est.conn.execute("UPDATE conversas SET ts=? WHERE card_id='e'", (h10,))
    est.conn.commit()
    assert reativar(est, ONT, wa, propor=lambda cid: cid)["nudges"] == ["e"]


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
    assert curso_de_texto("FEQ-0333 v2") == "FEQ-0333"   # troca de versão é transparente
    assert curso_de_texto("FEQ-0580 v3") == "FEQ-0580"
    assert curso_de_texto("FEQ0480 v2") == "FEQ-0480"
    assert curso_de_texto("Campanha genérica sem código") == ""
    # 3 dígitos ('Lead Magnet FEQ 331') -> normaliza p/ 4 (catálogo usa FEQ-0331)
    assert curso_de_texto("Lead Magnet FEQ 331") == "FEQ-0331"
    assert curso_de_texto("Lead Magnet FEQ 530") == "FEQ-0530"
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


def test_normalizar_meta_nome_varia_por_form():
    # v2/v3 usam 'nome_completo' e 'telefone' (não full_name/phone_number)
    fd = [{"name": "nome_completo", "values": ["Jean Paul Vespasiano"]},
          {"name": "telefone", "values": ["+55 19 99602-7820"]},
          {"name": "email", "values": ["jean@x.com"]},
          {"name": "voce_tem_ensino_superior_completo?", "values": ["Sim"]}]
    lead = normalizar_meta(fd, curso="FEQ-0333")
    assert lead["nome"] == "Jean Paul Vespasiano"        # não vira genérico
    assert lead["telefone"] and lead["escolaridade"] == "Superior Completo"
    assert "Jean" not in lead["qualificacao"]            # nome não vaza p/ a qualificação
    # lead magnet usa 'first_name'
    assert normalizar_meta([{"name": "first_name", "values": ["Ana"]},
                            {"name": "phone_number", "values": ["+55 19 9"]}])["nome"] == "Ana"


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
    # hoje fixo DENTRO da janela do FEQ-0333 (fecha 19/07/2026): sem isso o teste
    # apodrece quando o calendário real passa do prazo (aconteceu em 20/07/2026).
    hoje = datetime.date(2026, 7, 10)
    # 1ª resposta do lead: agente NÃO re-pergunta ensino superior -> já oferece o curso
    dec = brain_deterministico(ONT, saved, "tenho interesse!", [], hoje=hoje)
    assert dec.proxima_acao == C.QUALIFICAR
    assert "ensino superior" not in (dec.mensagem_ao_lead or "").lower()
    # botão do template ("Receber o link para prosseguir") -> link de INSCRIÇÃO correto
    dec2 = brain_deterministico(ONT, saved, "Receber o link para prosseguir", [], hoje=hoje)
    assert dec2.proxima_acao == C.QUALIFICAR
    assert "/inscricao/FEQ-0333/" in (dec2.mensagem_ao_lead or "")


def test_viu_msg_nao_marca_mas_msg_ja_vista_marca():
    est = Estado(":memory:")
    # viu_msg só consulta -> não queima o id (permite reprocessar após falha transitória)
    assert est.viu_msg("leadgen:X") is False
    assert est.viu_msg("leadgen:X") is False
    # msg_ja_vista marca -> 2ª vez acusa reentrega
    assert est.msg_ja_vista("leadgen:X") is False
    assert est.msg_ja_vista("leadgen:X") is True
    assert est.viu_msg("leadgen:X") is True            # agora sim, já visto


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


def test_extrair_mensagem_cobre_botao_e_midia():
    assert extrair_mensagem({"type": "text", "text": {"body": "oi"}}) == ("texto", "oi", None, None)
    # clique de botão de template (feq_lead) — antes chegava vazio
    assert extrair_mensagem({"type": "button", "button": {"text": "Receber link para prosseguir"}}) \
        == ("texto", "Receber link para prosseguir", None, None)
    assert extrair_mensagem({"type": "interactive",
                             "interactive": {"button_reply": {"title": "Quero tirar dúvidas"}}})[1] == "Quero tirar dúvidas"
    assert extrair_mensagem({"type": "audio", "audio": {"id": "AUD9", "mime_type": "audio/ogg"}}) \
        == ("audio", "", "AUD9", "audio/ogg")
    tipo, leg, mid, _ = extrair_mensagem({"type": "image", "image": {"id": "IMG1", "caption": "olha isto"}})
    assert tipo == "image" and leg == "olha isto" and mid == "IMG1"


def test_templates_enviaveis_seguem_a_waba_nao_o_rol_fixo():
    """Template criado na Central (fora do rol local) precisa ficar enviável;
    PENDING não pode aparecer. A WABA é a fonte da verdade."""
    import os as _os
    import tempfile as _tf
    _os.environ.pop("ATENA_USERS", None)
    _os.environ["WHATSAPP_TOKEN"] = ""
    _os.environ["WHATSAPP_WABA_ID"] = ""
    import atena.core.estado as _E
    _E.DEFAULT_DB = _tf.mktemp(suffix=".db")
    import atena.api.app as A
    assert [t["verificado"] for t in A._templates_enviaveis()] == [False] * len(A.WhatsApp_TEMPLATES)
    A.WA.token, A.WA.waba_id = "x", "y"          # simula conexão
    A.WA._tpl_cache = [
        {"name": "feq_abertura_curso", "status": "APPROVED", "language": "pt_BR",
         "components": [{"type": "BODY", "text": "Oi {{1}}, sobre o {{2}}..."}]},
        {"name": "feq_conteudo_0333", "status": "APPROVED", "language": "pt_BR",
         "components": [{"type": "BODY", "text": "Conteúdo do curso {{1}} para você"}]},
        {"name": "em_analise", "status": "PENDING", "language": "pt_BR",
         "components": [{"type": "BODY", "text": "oi"}]},
    ]
    envs = {t["nome"]: t for t in A._templates_enviaveis()}
    assert "em_analise" not in envs                                  # só APPROVED
    assert envs["feq_abertura_curso"]["titulo"] == "Abertura de curso"   # conhecido: rótulos amigáveis
    assert [p["rotulo"] for p in envs["feq_abertura_curso"]["params"]] == ["Nome", "Curso"]
    assert "feq_conteudo_0333" in envs                               # novo na Meta: enviável
    assert len(envs["feq_conteudo_0333"]["params"]) == 1              # variável derivada do corpo
    A.WA.token, A.WA.waba_id, A.WA._tpl_cache = "", "", None


def test_criar_template_valida_antes_de_chamar_a_meta():
    from atena.transporte.whatsapp import validar_template
    ok_corpo = "Oi {{1}}! As inscrições do {{2}} vão até {{3}}. Quer o link?"
    assert validar_template("feq_convite", "UTILITY", ok_corpo, ["Ana", "FEQ-0480", "16/08"]) is None
    assert "nome inválido" in validar_template("FEQ Convite!", "UTILITY", "oi tudo bem", [])
    assert "categoria" in validar_template("feq_x", "PROMO", "oi tudo bem", [])
    assert "obrigatório" in validar_template("feq_x", "UTILITY", "   ", [])
    assert "sequenciais" in validar_template("feq_x", "UTILITY", "oi {{1}} e {{3}} fim", ["a", "b"])
    assert "exemplo" in validar_template("feq_x", "UTILITY", ok_corpo, ["só um"])
    # a Meta recusa corpo que começa ou termina em variável
    assert "começar nem terminar" in validar_template("feq_x", "UTILITY", "{{1}} bem-vindo ao curso", ["Ana"])
    assert "começar nem terminar" in validar_template("feq_x", "UTILITY", "seu curso é o {{1}}", ["Ana"])
    # sem token/WABA não cria nada na Meta (dry-run), mas monta o payload correto
    os.environ["WHATSAPP_TOKEN"] = ""
    os.environ["WHATSAPP_WABA_ID"] = ""
    est = Estado(":memory:")
    wa = WhatsApp(est, Orquestrador(est, ONT))
    r = wa.criar_template("feq_convite", "UTILITY", ok_corpo, ["Ana", "FEQ-0480", "16/08"],
                          rodape="Responda SAIR para não receber mais", botoes=["Quero o link"])
    assert r["ok"] and r["status"] == "dry_run"
    comps = {c["type"]: c for c in r["enviado"]["components"]}
    assert comps["BODY"]["example"]["body_text"] == [["Ana", "FEQ-0480", "16/08"]]
    assert comps["BUTTONS"]["buttons"][0] == {"type": "QUICK_REPLY", "text": "Quero o link"}
    assert r["enviado"]["language"] == "pt_BR" and r["enviado"]["category"] == "UTILITY"
    assert wa.criar_template("Nome Errado", "UTILITY", ok_corpo, ["a", "b", "c"])["ok"] is False


def test_assume_que_e_ia_e_repassa_para_humano():
    est, orq = Estado(":memory:"), None
    est.upsert_lead("r1", nome="Ana", telefone="+55 19 9", curso="FEQ-0480",
                    fase="Lead", estado_conversa="qualificacao")
    orq = Orquestrador(est, ONT)
    d = orq.processar_mensagem("r1", "você é um robô?")["decisao"]
    msg = (d["mensagem_ao_lead"] or "").lower()
    assert "inteligência artificial" in msg              # assume, não desconversa
    assert "pessoa" in msg                               # e oferece atendimento humano
    assert d["proxima_acao"] != C.ESCALAR                # só perguntou, não pediu humano
    d2 = orq.processar_mensagem("r1", "prefiro falar com uma pessoa")["decisao"]
    assert d2["proxima_acao"] == C.ESCALAR                          # pediu -> repassa
    assert est.get_lead("r1")["estado_conversa"] == "escalado_humano"


def test_envio_de_midia_tipo_limite_e_guardrails():
    import tempfile
    import pathlib as _p
    from atena.transporte import whatsapp as _wa
    from atena.transporte.whatsapp import tipo_de_mime
    os.environ.pop("WHATSAPP_TOKEN", None)
    # mime -> tipo: o que a Meta não aceita como image/audio vai como document (sempre entrega)
    assert tipo_de_mime("image/jpeg") == "image" and tipo_de_mime("audio/ogg") == "audio"
    assert tipo_de_mime("video/mp4") == "video" and tipo_de_mime("application/pdf") == "document"
    assert tipo_de_mime("image/webp") == "document" and tipo_de_mime("audio/webm") == "document"

    _orig, _wa._MEDIA_DIR = _wa._MEDIA_DIR, _p.Path(tempfile.mkdtemp())   # sem sujar data/media
    try:
        est = Estado(":memory:")
        est.upsert_lead("m1", nome="Ana", telefone="5519999990001", curso="FEQ-0480")
        wa = WhatsApp(est, Orquestrador(est, ONT))
        est.add_conversa("m1", "lead", "oi")            # abre a janela de 24h
        # forcar=True é como a Central envia (ação humana passa fora do horário comercial)
        r = wa.enviar_midia("5519999990001", b"%PDF-1.4", "edital.pdf", "application/pdf",
                            legenda="Segue o edital", forcar=True)
        assert r["ok"] and r["tipo"] == "document"
        assert r["legenda_enviada"] is True        # doc/imagem/vídeo levam a legenda junto
        # áudio NÃO aceita legenda na Meta -> quem chama manda o texto em mensagem separada
        assert wa.enviar_midia("5519999990001", b"OggS", "audio.ogg", "audio/ogg",
                               legenda="ouve isso", forcar=True)["legenda_enviada"] is False
        # sem forcar, mídia respeita o horário comercial (igual ao texto livre)
        fora = datetime.datetime(2026, 7, 29, 3, 0)
        assert wa.enviar_midia("5519999990001", b"x", "f.jpg", "image/jpeg",
                               agora=fora)["status"] == "fora_do_horario"
        assert (_wa._MEDIA_DIR / r["media_id"]).exists()          # cópia local p/ a thread
        assert est.midia_mime(r["media_id"]) == "application/pdf"
        # limite de tamanho da Meta (imagem = 5 MB) barra ANTES de subir
        assert wa.enviar_midia("5519999990001", b"x" * (6 * 1024 * 1024), "f.jpg",
                               "image/jpeg")["status"] == "grande_demais"
        # mídia respeita os MESMOS guardrails do texto livre
        est.set_lead("m1", optout=1)
        assert wa.enviar_midia("5519999990001", b"x", "f.jpg", "image/jpeg")["status"] == "bloqueado"
        est.set_lead("m1", optout=0)
        est2 = Estado(":memory:")
        est2.upsert_lead("m2", nome="B", telefone="5519999990002", curso="FEQ-0480")
        wa2 = WhatsApp(est2, Orquestrador(est2, ONT))   # sem fala do lead -> fora da janela
        assert wa2.enviar_midia("5519999990002", b"x", "f.jpg", "image/jpeg")["status"] == "fora_da_janela_24h"
    finally:
        _wa._MEDIA_DIR = _orig


def test_templates_rol_e_envio_dry_run():
    os.environ.pop("WHATSAPP_TOKEN", None)
    os.environ.pop("WHATSAPP_WABA_ID", None)
    from atena.transporte.whatsapp import TEMPLATES_ROL
    est = Estado(":memory:")
    wa = WhatsApp(est, Orquestrador(est, ONT))
    assert wa.templates_status() is None                      # sem token/WABA -> não verifica ao vivo
    assert any(t["nome"] == "feq_abertura_curso" and len(t["params"]) == 2 for t in TEMPLATES_ROL)
    assert any(t["nome"] == "feq_reengajamento" and len(t["params"]) == 3 for t in TEMPLATES_ROL)
    r = wa.enviar_template("5519999990001", "feq_abertura_curso", params=["Ana", "Eng. Química (FEQ-0480)"])
    assert r["ok"] and r["status"] == "dry_run" and r["template"] == "feq_abertura_curso"


def test_receber_midia_guarda_tipo_e_media_id():
    est = Estado(":memory:")
    est.upsert_lead("c1", nome="X", telefone="+55 19 90000-0002", curso="FEQ-0480",
                    fase="Lead", estado_conversa="qualificacao")
    os.environ.pop("WHATSAPP_TOKEN", None)                 # sem token -> não baixa (sem rede)
    wa = WhatsApp(est, Orquestrador(est, ONT))
    wa.receber({"entry": [{"changes": [{"value": {"messages": [
        {"id": "wamid.M1", "from": "5519900000002", "type": "audio",
         "audio": {"id": "AUD9", "mime_type": "audio/ogg"}}]}}]}]})
    turno = [t for t in est.historico("c1") if t["papel"] == "lead"][-1]
    assert turno["tipo"] == "audio" and turno["media_id"] == "AUD9"


def test_cursos_interesse_acumula_e_ativa():
    est = Estado(":memory:")
    est.upsert_lead("c1", nome="X", telefone="+55 19 90000-0001", curso="FEQ-0333",
                    cursos_interesse="FEQ-0333", fase="Lead", estado_conversa="qualificacao")
    assert Estado.cursos_interesse(est.get_lead("c1")) == ["FEQ-0333"]
    est.add_curso_interesse("c1", "FEQ-0580")                 # acumula, mantém o ativo
    l = est.get_lead("c1")
    assert l["curso"] == "FEQ-0333" and Estado.cursos_interesse(l) == ["FEQ-0333", "FEQ-0580"]
    est.add_curso_interesse("c1", "FEQ-0480", ativar=True)    # torna ativo
    l = est.get_lead("c1")
    assert l["curso"] == "FEQ-0480"
    assert set(Estado.cursos_interesse(l)) == {"FEQ-0333", "FEQ-0580", "FEQ-0480"}


def test_ingest_segundo_form_acumula_sem_trocar_ativo():
    est = Estado(":memory:"); ing = Ingestao(est, ONT)
    ing.ingest(normalizar_meta([{"name": "full_name", "values": ["Ana"]},
                                {"name": "phone_number", "values": ["+55 19 90000-0009"]}], curso="FEQ-0333"))
    cid = est.lead_por_telefone("5519900000009")
    r = ing.ingest(normalizar_meta([{"name": "full_name", "values": ["Ana"]},   # mesmo tel, outro curso
                                    {"name": "phone_number", "values": ["+55 19 90000-0009"]}], curso="FEQ-0580"))
    assert r["acao"] == "atualizado"
    l = est.get_lead(cid)
    assert l["curso"] == "FEQ-0333"                                   # ativo preservado
    assert set(Estado.cursos_interesse(l)) == {"FEQ-0333", "FEQ-0580"}  # acumulou o interesse


def test_faro_noop_sem_chave():
    os.environ.pop("FARO_KEY", None)
    f = Faro()
    assert f.ativo is False
    assert f.criar_lead("X", "+55", "FEQ-0480") is None   # silencioso, sem rede
    f.mover("qualquer", "Prospect")                        # não explode


def test_estado_thread_safe_sob_concorrencia():
    """self.conn é UM sqlite3.Connection (check_same_thread=False) compartilhado
    por todas as threads do waitress. Escritas já tinham self._lock; leituras sem
    lock corrompem o cursor concorrentemente com uma escrita -> sqlite3.InterfaceError:
    bad parameter or other API misuse (bug real de produção, 18/07/2026: a aba
    Verificação "não fazia nada" porque /api/conversas quebrava no meio do
    carregar() do JS, sem sequer chegar a re-renderizar a fila). Reproduzido aqui
    com 8 leitoras + 4 escritoras por ~1s — sem lock nas leituras, isso falha em
    dezenas de iterações (confirmado rodando contra o código pré-fix)."""
    import threading
    import time as _time
    est = Estado(":memory:")
    for i in range(20):
        est.upsert_lead(f"c{i}", nome=f"Lead {i}", telefone=f"1199999{i:04d}", curso="FEQ-0480")
    erros = []
    parar = threading.Event()

    def leitor():
        while not parar.is_set():
            try:
                for l in est.listar_leads():
                    est.historico(l["card_id"])
                    est.decisao_pendente(l["card_id"])
            except Exception as e:
                erros.append(e)

    def escritor():
        n = 0
        while not parar.is_set():
            try:
                est.add_conversa("c1", "lead", f"msg {n}")
                est.set_lead("c2", fase="Prospect")
                n += 1
            except Exception as e:
                erros.append(e)

    threads = [threading.Thread(target=leitor) for _ in range(8)] + \
              [threading.Thread(target=escritor) for _ in range(4)]
    for t in threads:
        t.start()
    _time.sleep(1)
    parar.set()
    for t in threads:
        t.join()
    assert not erros, f"{len(erros)} erro(s) de concorrência no SQLite: {erros[0]!r}"



def _wa_payload(tel, texto, mid):
    return {"entry": [{"changes": [{"value": {"messages": [
        {"id": mid, "from": tel, "text": {"body": texto}}]}}]}]}


def test_botao_whatsapp_da_lp_e_atendido_pelo_agente():
    """Lead que clica no botão de WhatsApp da LP cai como número DESCONHECIDO.
    Antes virava stub 'aguardando_revisao' e ninguém respondia. Agora o curso sai
    do texto do botão ('Vim da página do curso FEQ-XXXX') e o agente atende."""
    os.environ.pop("WHATSAPP_TOKEN", None)
    est = Estado(":memory:")
    wa = WhatsApp(est, Orquestrador(est, ONT))
    out = wa.receber(_wa_payload("5519911110001",
                                 "Olá! Vim da página do curso FEQ-0480 e quero saber mais.", "wm1"))
    lead = est.get_lead("wa-5519911110001")
    assert lead["curso"] == "FEQ-0480"              # curso veio do texto, não inventado
    assert lead["estado_conversa"] != "aguardando_revisao"
    assert lead["optin_fonte"] == "whatsapp_lp"    # ele nos escreveu: opt-in + janela 24h
    assert out[0].get("decisao")                    # o agente RESPONDEU


def test_whatsapp_sem_curso_entra_em_descoberta():
    """Sem curso na mensagem, o agente pergunta qual é — nunca inventa nem escala."""
    os.environ.pop("WHATSAPP_TOKEN", None)
    est = Estado(":memory:")
    wa = WhatsApp(est, Orquestrador(est, ONT))
    out = wa.receber(_wa_payload("5519911110002", "Oi, boa tarde! Queria informações.", "wm2"))
    lead = est.get_lead("wa-5519911110002")
    assert lead["curso"] == ""                      # não inventou curso
    msg = (out[0].get("decisao") or {}).get("mensagem_ao_lead", "")
    assert "qual curso" in msg.lower()
    # e ao dizer o curso na 2ª mensagem, o card passa a apontar pra ele
    wa.receber(_wa_payload("5519911110002", "quero saber do FEQ-1000", "wm3"))
    assert est.get_lead("wa-5519911110002")["curso"] == "FEQ-1000"


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
