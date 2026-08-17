#!/usr/bin/env python3
"""
Pacote de atualizações: criar card manualmente no Funil + nome do perfil do WhatsApp.

O fio comum entre os dois recursos é a IDEMPOTÊNCIA por telefone — a mesma regra que
já vale para toda ingestão do sistema (CLAUDE.md §7): número que já tem card nunca
ganha um segundo, e nome só é preenchido quando ainda está vazio (nunca sobrescreve
o que o operador já leu na conversa e corrigiu). E os dois fecham o mesmo buraco: o
card_id ("wa-554199515077") é chave interna — nunca é o que aparece como nome na tela.
"""
import os
import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
os.environ["ATENA_DEV"] = "1"
os.environ["WHATSAPP_TOKEN"] = ""

from atena.core.estado import Estado                    # noqa: E402
from atena.core.orquestrador import Orquestrador        # noqa: E402
from atena.growth.db import Growth                      # noqa: E402
from atena.ontologia.servico import Ontologia            # noqa: E402
from atena.transporte.whatsapp import WhatsApp           # noqa: E402
import atena.api.app as A                                 # noqa: E402

ONT = Ontologia()
# `Estado()`/`Growth()` sem args caem no banco de dev real (atena/data/atena.db).
# app.py lê EST/GRW como globais do MÓDULO a cada chamada de rota, então reatribuir
# depois do import isola /api/kanban/criar sem tocar no arquivo de verdade — sem
# isto, rodar este arquivo 2x seguidas faria a 2ª rodada achar os telefones da 1ª
# já cadastrados e os testes de "criado=True" quebrariam (idempotência de verdade,
# mas contra o banco errado).
A.EST = Estado(":memory:")
A.GRW = Growth(":memory:")


def _cliente():
    c = A.app.test_client()
    with c.session_transaction() as s:
        s["usuario"], s["papel"] = "tester", "supervisor"
    return c


# ---------------------------------------------------------------- criar card no Funil
def test_criar_card_manual_novo():
    c = _cliente()
    r = c.post("/api/kanban/criar", json={"nome": "Pedro Lima", "telefone": "19999990020",
                                          "curso": "FEQ-0480"}).get_json()
    assert r["ok"] and r["criado"] is True
    assert r["card_id"].startswith("op-")
    lead = A.EST.get_lead(r["card_id"])
    assert lead["nome"] == "Pedro Lima" and lead["curso"] == "FEQ-0480"
    assert lead["fase"] == "Lead"


def test_criar_card_sem_curso_fica_a_descobrir():
    """Curso não é obrigatório: o operador pode saber só o contato ainda."""
    c = _cliente()
    r = c.post("/api/kanban/criar", json={"nome": "Sem Curso Ainda",
                                          "telefone": "19999990022"}).get_json()
    assert r["ok"] and r["criado"] is True
    assert A.EST.get_lead(r["card_id"])["curso"] == ""


def test_criar_card_exige_telefone():
    c = _cliente()
    r = c.post("/api/kanban/criar", json={"nome": "Sem Telefone"})
    assert r.status_code == 400
    r2 = c.post("/api/kanban/criar", json={"nome": "Tel Curto", "telefone": "123"})
    assert r2.status_code == 400


def test_criar_card_rejeita_curso_fora_do_catalogo():
    c = _cliente()
    r = c.post("/api/kanban/criar", json={"nome": "X", "telefone": "19999990023",
                                          "curso": "FEQ-9999"})
    assert r.status_code == 400


def test_criar_card_e_idempotente_por_telefone():
    """Chamar 2x com o MESMO telefone não duplica: a 2ª devolve o card já existente,
    com criado=False — a tela nunca mostra duas linhas para a mesma pessoa."""
    c = _cliente()
    r1 = c.post("/api/kanban/criar", json={"nome": "Ana Souza", "telefone": "19999990024",
                                           "curso": "FEQ-0480"}).get_json()
    r2 = c.post("/api/kanban/criar", json={"nome": "Outro Nome Tentando Sobrescrever",
                                           "telefone": "19999990024", "curso": "FEQ-0331"}).get_json()
    assert r1["criado"] is True
    assert r2["criado"] is False
    assert r1["card_id"] == r2["card_id"]
    # nome do 1º clique NÃO foi trocado pelo nome do 2º clique (já não estava vazio)
    assert A.EST.get_lead(r1["card_id"])["nome"] == "Ana Souza"
    # mas o curso novo entrou como interesse adicional, sem apagar o ativo
    cursos = A.EST.cursos_interesse(A.EST.get_lead(r1["card_id"]))
    assert "FEQ-0480" in cursos and "FEQ-0331" in cursos


def test_criar_card_sobre_telefone_ja_conhecido_preenche_nome_vazio():
    """O caminho automático (webhook) pode ter criado o card sem nome; o operador
    completando o cadastro na mesma tela deve preencher, não recusar por já existir."""
    A.EST.upsert_lead("wa-19999990025", nome="", telefone="19999990025",
                      curso="FEQ-0480", fase="Lead", estado_conversa="qualificacao")
    c = _cliente()
    r = c.post("/api/kanban/criar", json={"nome": "Bruna Alves",
                                          "telefone": "19999990025"}).get_json()
    assert r["ok"] and r["criado"] is False and r["card_id"] == "wa-19999990025"
    assert A.EST.get_lead("wa-19999990025")["nome"] == "Bruna Alves"


# ---------------------------------------------------------------- nome do perfil do WhatsApp
def _wa():
    est = Estado(":memory:")
    return est, WhatsApp(est, Orquestrador(est, ONT))


def _payload_perfil(msg_id, tel, nome_perfil, texto="oi"):
    return {"entry": [{"changes": [{"value": {
        "contacts": [{"wa_id": tel, "profile": {"name": nome_perfil}}],
        "messages": [{"id": msg_id, "from": tel, "text": {"body": texto}}]}}]}]}


def test_lead_novo_ganha_o_nome_do_perfil_do_whatsapp():
    est, wa = _wa()
    wa.receber(_payload_perfil("m1", "5519999990010", "Maria da Silva"))
    card_id = est.lead_por_telefone("5519999990010")
    assert card_id == "wa-5519999990010"
    assert est.get_lead(card_id)["nome"] == "Maria da Silva"


def test_card_existente_sem_nome_e_preenchido_na_proxima_mensagem():
    """Card criado por outro caminho (ex.: manual, sem nome) — a mensagem seguinte
    pelo WhatsApp, com o perfil, completa o cadastro sozinha."""
    est, wa = _wa()
    est.upsert_lead("op-5519999990011", nome="", telefone="5519999990011",
                    curso="FEQ-0480", fase="Lead", estado_conversa="qualificacao")
    wa.receber(_payload_perfil("m2", "5519999990011", "João Pereira"))
    assert est.get_lead("op-5519999990011")["nome"] == "João Pereira"


def test_nome_ja_confirmado_pelo_operador_nunca_e_sobrescrito():
    """O outro lado da idempotência: card que JÁ tem nome não é tocado, mesmo que
    uma mensagem chegue com um perfil de WhatsApp diferente (apelido, erro de
    digitação no perfil, etc.) — o operador é a fonte de verdade quando já agiu."""
    est, wa = _wa()
    est.upsert_lead("wa-5519999990012", nome="Nome Confirmado Pelo Operador",
                    telefone="5519999990012", curso="FEQ-0480", fase="Lead",
                    estado_conversa="qualificacao")
    wa.receber(_payload_perfil("m3", "5519999990012", "Apelido Bobo"))
    assert est.get_lead("wa-5519999990012")["nome"] == "Nome Confirmado Pelo Operador"


def test_preenchimento_e_idempotente_em_mensagens_repetidas():
    """Duas mensagens seguidas da mesma pessoa: a 1ª preenche, a 2ª é NO-OP — o
    próprio estado (nome não-vazio) é a marca de 'já processado', sem tabela nova
    de controle. Confirma pela auditoria: só 1 entrada de preenchimento, nunca 2."""
    est, wa = _wa()
    est.upsert_lead("op-5519999990015", nome="", telefone="5519999990015",
                    curso="FEQ-0480", fase="Lead", estado_conversa="qualificacao")
    wa.receber(_payload_perfil("m7", "5519999990015", "Carla Nunes"))
    wa.receber(_payload_perfil("m8", "5519999990015", "Carla Nunes"))
    assert est.get_lead("op-5519999990015")["nome"] == "Carla Nunes"
    n = est.conn.execute(
        "SELECT COUNT(*) FROM auditoria WHERE alvo=? AND acao LIKE 'nome preenchido%'",
        ("op-5519999990015",)).fetchone()[0]
    assert n == 1, f"preencheu {n} vez(es) — devia ser idempotente (só a 1ª conta)"


def test_sem_array_contacts_nao_quebra_e_so_fica_sem_nome():
    """Nem toda entrega da Meta necessariamente traz `contacts` — o sistema não pode
    quebrar, só fica sem o nome (mesmo comportamento de antes, degradação segura)."""
    est, wa = _wa()
    out = est, wa.receber({"entry": [{"changes": [{"value": {"messages": [
        {"id": "m6", "from": "5519999990014", "text": {"body": "oi"}}]}}]}]})
    card_id = est.lead_por_telefone("5519999990014")
    assert card_id and est.get_lead(card_id)["nome"] == ""


def test_reentrega_da_meta_nao_processa_nome_duas_vezes():
    """msg_ja_vista já intercepta reentrega — confirma que o nome também não sofre
    2 escritas quando a MESMA mensagem (mesmo id) chega de novo."""
    est, wa = _wa()
    payload = _payload_perfil("m9", "5519999990016", "Duplicada Fulana")
    wa.receber(payload)
    wa.receber(payload)   # mesma entrega, reprocessada pela Meta
    n = est.conn.execute(
        "SELECT COUNT(*) FROM leads WHERE telefone LIKE '%5519999990016%'").fetchone()[0]
    assert n == 1


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
