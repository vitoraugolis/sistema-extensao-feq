#!/usr/bin/env python3
"""
Compartilhar contatos no WhatsApp.

Contato recebido virava o rótulo genérico "[contacts]" — nome e telefone eram
perdidos. E o risco novo que isso introduz: o JSON estruturado que a Central usa
para desenhar o cartão NÃO pode vazar cru para o cérebro como se fosse fala do lead.
"""
import os
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

os.environ["ATENA_DEV"] = "1"
os.environ["WHATSAPP_TOKEN"] = ""

from atena.core.estado import Estado
from atena.core.contatos import CONTATOS_OFICIAIS, payload_contato
from atena.transporte.whatsapp import WhatsApp, extrair_mensagem
from atena.core.orquestrador import Orquestrador

ok = 0


def check(c, msg):
    global ok
    assert c, "FALHOU: " + msg
    ok += 1
    print("  ok:", msg)


# ------------------------------------------------------------- 1) inbound
m = {"type": "contacts", "contacts": [
    {"name": {"formatted_name": "Ana Souza"}, "phones": [{"phone": "+55 19 99999-0001"}]}]}
tipo, texto, media_id, mime = extrair_mensagem(m)
check(tipo == "contatos", "tipo reconhecido (antes caía no fallback genérico)")
check("Ana Souza" in texto and "99999-0001" in texto, "nome e telefone preservados (não vira '[contacts]')")
check(media_id is None, "contato não é mídia")

m_vazio = {"type": "contacts", "contacts": []}
check(extrair_mensagem(m_vazio)[1] == "[]", "lista vazia não quebra (JSON válido, mesmo vazio)")

# ------------------------------------------------------------- 2) não vaza ao cérebro
class BrainEspiao:
    """Registra o que o cérebro recebeu como 'fala do lead' — é o que não pode ser JSON cru."""
    def __init__(self):
        self.recebido = None

    def __call__(self, lead, mensagem, historico):
        self.recebido = mensagem
        from atena.core import contratos as C
        return C.DecisaoAgente(C.CONTINUAR, "ok")


est = Estado(":memory:")
est.upsert_lead("c1", nome="Bia", telefone="5519999990001", curso="FEQ-0480",
                fase="Lead", estado_conversa="qualificacao")
espiao = BrainEspiao()
orq = Orquestrador(estado=est, brain=espiao)
orq.processar_mensagem("c1", texto, tipo="contatos")

check(espiao.recebido == "[o lead compartilhou um contato]",
      "cérebro recebe um RÓTULO, nunca o JSON cru do cartão")
check(est.historico("c1")[-1]["texto"] == texto,
      "mas o BANCO guarda o JSON estruturado — é o que a Central usa para o cartão")
check(est.historico("c1")[-1]["tipo"] == "contatos", "tipo persistido corretamente")

# ------------------------------------------------------------- 3) outbound
p = payload_contato("secretaria")
check(p["name"]["formatted_name"] == CONTATOS_OFICIAIS["secretaria"]["rotulo"],
      "payload usa o rótulo oficial")
check(p["phones"][0]["phone"] == CONTATOS_OFICIAIS["secretaria"]["telefone_exibicao"],
      "telefone de exibição vai no cartão")
check("emails" in p, "secretaria tem e-mail — entra no payload")
check("emails" not in payload_contato("whatsapp"), "contato sem e-mail não inventa o campo")
check(payload_contato("chave_inexistente") is None, "chave desconhecida devolve None, não quebra")

wa = WhatsApp(est, orq)
r = wa.enviar_contato("5519999990001", "secretaria")
check(r["ok"] and r["status"] == "dry_run", "dry-run sem token: não chama a Meta de verdade")
r2 = wa.enviar_contato("5519999990001", "chave_inexistente")
check(r2["ok"] is False, "tentar enviar contato inexistente falha explicitamente (não manda nada)")

est.upsert_lead("c2", nome="Cid", telefone="5519999990002", curso="FEQ-0480", optout=1)
r3 = wa.enviar_contato("5519999990002", "secretaria")
check(r3["ok"] is False and r3["status"] == "bloqueado", "opt-out bloqueia envio de contato também")

print(f"\n{ok}/{ok} passaram.")
