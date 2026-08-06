#!/usr/bin/env python3
"""
Não lidas na Central (estilo WhatsApp).

A regra que importa: a marca d'água é do OPERADOR. O agente de IA responder é ação
nossa e NÃO pode zerar o contador — senão o humano perde de vista o que chegou
justamente nas conversas que a IA está conduzindo sozinha, que são a maioria.
"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

os.environ.pop("ATENA_USERS", None)
os.environ["WHATSAPP_TOKEN"] = ""
os.environ["ATENA_DEV"] = "1"

import atena.core.estado as _E
_E.DEFAULT_DB = tempfile.mktemp(suffix=".db")

from atena.core.estado import Estado
import atena.api.app as A

ok = 0


def check(c, msg):
    global ok
    assert c, "FALHOU: " + msg
    ok += 1
    print("  ok:", msg)


def _cliente():
    c = A.app.test_client()
    c.post("/api/login", json={"usuario": "operador", "senha": "operador"})
    return c


# ------------------------------------------------------------------ unidade
est = Estado(":memory:")
est.upsert_lead("c1", nome="Ana", telefone="5519999990001", curso="FEQ-0480")

check(est.nao_lidas() == {}, "conversa vazia não tem não lidas")

est.add_conversa("c1", "lead", "oi, quero saber do curso")
check(est.nao_lidas() == {"c1": 1}, "mensagem do lead conta como não lida")

est.add_conversa("c1", "agente", "Olá! Claro.", autor="llm")
check(est.nao_lidas() == {"c1": 1},
      "o AGENTE responder não zera: é ação nossa, não leitura do operador")

est.add_conversa("c1", "lead", "e o preço?")
est.add_conversa("c1", "lead", "tem desconto?")
check(est.nao_lidas() == {"c1": 3}, "acumula as mensagens do lead")

antes = est.marcar_lido("c1")
check(antes == 0, "marca d'água anterior de conversa nunca aberta é 0")
check(est.nao_lidas() == {}, "abrir a conversa zera as não lidas")

est.add_conversa("c1", "agente", "resposta automática", autor="llm")
est.add_conversa("c1", "agente", "[template feq_lead] x", autor="template")
check(est.nao_lidas() == {}, "mensagem nossa (llm ou template) nunca vira não lida")

est.add_conversa("c1", "lead", "obrigada!")
check(est.nao_lidas() == {"c1": 1}, "nova fala do lead volta a acender o contador")

antes2 = est.marcar_lido("c1")
check(antes2 > 0, "a marca d'água avança a cada leitura")

# conversa que o operador nunca abriu: tudo do lead conta
est.upsert_lead("c2", nome="Bia", telefone="5519999990002", curso="FEQ-0480")
est.add_conversa("c2", "lead", "oi")
est.add_conversa("c2", "lead", "alguém aí?")
check(est.nao_lidas() == {"c2": 2}, "conversa nunca aberta conta tudo do lead")

# isolamento entre conversas
est.marcar_lido("c2")
est.add_conversa("c1", "lead", "mais uma")
check(est.nao_lidas() == {"c1": 1}, "ler uma conversa não afeta a outra")

# ------------------------------------------------------------------ fim a fim (API)
A.EST.upsert_lead("api1", nome="Carlos", telefone="5519988880001", curso="FEQ-0480",
                  fase="Lead", estado_conversa="aguardando_resposta")
A.EST.add_conversa("api1", "lead", "quanto custa?")
A.EST.add_conversa("api1", "agente", "Está em campanha!", autor="llm")

cli = _cliente()
lista = {c["id"]: c for c in cli.get("/api/conversas").get_json()}
check(lista["api1"]["nao_lidas"] == 1,
      "a lista entrega o contador mesmo com o agente já tendo respondido")

det = cli.get("/api/conversas/api1").get_json()
check(det["lido_ate"] == 0, "detalhe devolve a marca d'água ANTERIOR (p/ a linha de corte)")
check(all("id" in m for m in det["thread"]), "thread traz o id de cada mensagem")

lista = {c["id"]: c for c in cli.get("/api/conversas").get_json()}
check(lista["api1"]["nao_lidas"] == 0, "abrir pela API marca como lida")

A.EST.add_conversa("api1", "agente", "mais uma do agente", autor="llm")
lista = {c["id"]: c for c in cli.get("/api/conversas").get_json()}
check(lista["api1"]["nao_lidas"] == 0, "agente falando de novo não reacende o contador")

A.EST.add_conversa("api1", "lead", "e o parcelamento?")
lista = {c["id"]: c for c in cli.get("/api/conversas").get_json()}
check(lista["api1"]["nao_lidas"] == 1, "nova fala do lead reacende")

det = cli.get("/api/conversas/api1").get_json()
check(det["lido_ate"] > 0, "segunda leitura devolve marca d'água já avançada")

print(f"\n{ok}/{ok} passaram.")
