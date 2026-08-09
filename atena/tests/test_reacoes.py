#!/usr/bin/env python3
"""
Reações do WhatsApp: emoji sobre uma mensagem existente, não uma mensagem nova.

O risco central é a reação virar uma "fala" para o cérebro responder — um lead
reagindo 👍 a uma pergunta de dias atrás não pode reabrir aquele assunto como se
fosse texto novo. `receber()` precisa interceptar ANTES de extrair_mensagem/
processar_mensagem.
"""
import os
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

os.environ["ATENA_DEV"] = "1"

from atena.core.estado import Estado
from atena.transporte.whatsapp import WhatsApp

ok = 0


def check(c, msg):
    global ok
    assert c, "FALHOU: " + msg
    ok += 1
    print("  ok:", msg)


class OrqFake:
    """Se processar_mensagem for chamado, o teste precisa saber — é exatamente
    o que uma reação NUNCA pode disparar."""
    def __init__(self):
        self.chamado = False

    def processar_mensagem(self, *a, **k):
        self.chamado = True
        return {}


# ------------------------------------------------------------- 1) marcar_reacao
est = Estado(":memory:")
est.upsert_lead("c1", nome="Ana", telefone="5519999990001", curso="FEQ-0480")
est.add_conversa("c1", "agente", "Oi! Tudo bem?", wamid="wamid.1", status="entregue", autor="llm")

check(est.marcar_reacao("wamid.1", "👍") == "c1", "anexa a reação e devolve o card_id certo")
check(est.historico("c1")[0]["reacao"] == "👍", "historico() expõe a reação")
check(len(est.historico("c1")) == 1, "reação NÃO cria linha nova na thread")

check(est.marcar_reacao("wamid.1", "") == "c1", "remover a reação (emoji vazio) também funciona")
check(est.historico("c1")[0]["reacao"] is None, "campo volta a None quando removida")

check(est.marcar_reacao("wamid.inexistente", "👍") is None, "wamid desconhecido não quebra, devolve None")
check(est.marcar_reacao(None, "👍") is None, "wamid vazio não quebra")

# ------------------------------------------------------------- 2) webhook: reação
os.environ["WHATSAPP_TOKEN"] = ""
orq = OrqFake()
wa = WhatsApp(est, orq)

payload = {"entry": [{"changes": [{"value": {"messages": [
    {"id": "wamid.reacao1", "from": "5519999990001", "type": "reaction",
     "reaction": {"message_id": "wamid.1", "emoji": "❤️"}}
]}}]}]}
out = wa.receber(payload)

check(orq.chamado is False, "reação NUNCA chega ao cérebro (não é 'nova fala')")
check(est.historico("c1")[0]["reacao"] == "❤️", "webhook de reação anexa à mensagem certa")
check(out == [{"card_id": "c1", "acao": "reacao", "emoji": "❤️"}],
      "resultado do receber() identifica a ação para quem chama (app.py)")

# reentrega da Meta (mesmo id de evento) não duplica processamento
out2 = wa.receber(payload)
check(out2 == [], "reentrega do MESMO evento de reação é ignorada (dedupe por msg_ja_vista)")

# reação a wamid que não existe no nosso histórico: não quebra, só não aparece no out
payload_orfao = {"entry": [{"changes": [{"value": {"messages": [
    {"id": "wamid.reacao2", "from": "5519999990001", "type": "reaction",
     "reaction": {"message_id": "wamid.fantasma", "emoji": "😮"}}
]}}]}]}
out3 = wa.receber(payload_orfao)
check(out3 == [], "reação a mensagem desconhecida não gera card_id nem erro")

# ------------------------------------------------------------- 3) envio (operador)
est.upsert_lead("c2", nome="Bia", telefone="5519999990002", curso="FEQ-0480")
est.add_conversa("c2", "lead", "quero saber o valor", wamid="wamid.lead1", status=None)

r = wa.enviar_reacao("5519999990002", "wamid.lead1", "👍")
check(r["ok"] and r["status"] == "dry_run", "dry-run sem token: não chama a Meta de verdade")
check(est.historico("c2")[0]["reacao"] == "👍", "mesmo em dry-run, a reação fica registrada localmente")

est.upsert_lead("c3", nome="Cid", telefone="5519999990003", curso="FEQ-0480", optout=1)
est.add_conversa("c3", "lead", "para de mandar msg", wamid="wamid.lead2", status=None)
r = wa.enviar_reacao("5519999990003", "wamid.lead2", "👍")
check(r["ok"] is False and r["status"] == "bloqueado", "opt-out bloqueia reação igual a qualquer envio")

print(f"\n{ok}/{ok} passaram.")
