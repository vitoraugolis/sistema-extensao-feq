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
check("emails" not in payload_contato("secretaria_ie") or CONTATOS_OFICIAIS["secretaria_ie"]["email"],
      "e-mail só entra quando existe")
check(payload_contato("chave_inexistente") is None, "chave desconhecida devolve None, não quebra")

# Reteste via API em 28/08/2026 confirmou que a secretaria PASSOU a ter WhatsApp
# (o bug de 10/08/2026 — "este número não está no WhatsApp" — não se repetiu):
# agora o wa_id entra no payload dela também.
check(p["phones"][0].get("wa_id") == "551935213911",
      "secretaria tem WhatsApp confirmado: leva wa_id (clique-para-conversar)")
check(payload_contato("whatsapp") is None and "98262" not in str(CONTATOS_OFICIAIS),
      "o antigo WhatsApp (98262-0072) saiu: só o da secretaria é informado")

wa = WhatsApp(est, orq)
r = wa.enviar_contato("5519999990001", "secretaria")
check(r["ok"] and r["status"] == "dry_run", "dry-run sem token: não chama a Meta de verdade")
r2 = wa.enviar_contato("5519999990001", "chave_inexistente")
check(r2["ok"] is False, "tentar enviar contato inexistente falha explicitamente (não manda nada)")

est.upsert_lead("c2", nome="Cid", telefone="5519999990002", curso="FEQ-0480", optout=1)
r3 = wa.enviar_contato("5519999990002", "secretaria")
check(r3["ok"] is False and r3["status"] == "bloqueado", "opt-out bloqueia envio de contato também")

print(f"\n{ok}/{ok} passaram.")

# 05/10/2026: o cérebro não recebia o contato da secretaria (o lead perguntava e o
# agente não tinha de onde tirar). Agora vem de CONTATOS_OFICIAIS, por instituto.
from atena.core.contatos import secretaria_texto
from atena.agentes.cerebro import _system_prompt
from atena.ontologia.servico import Ontologia
_ont = Ontologia()
_s = secretaria_texto("FEQ-0480")
check("extfeq@unicamp.br" in _s and "WhatsApp (19) 3521-3911" in _s, "secretaria FEQ: e-mail + WhatsApp")
_e = secretaria_texto("ECO-1100")
check("iecoext@unicamp.br" in _e and "WhatsApp" not in _e and "3911" not in _e,
      "secretaria do IE: sem WhatsApp não confirmado e sem dado da FEQ")
check(_s in _system_prompt({"curso": _ont.curso("FEQ-0480"), "captavel": _ont.captavel("FEQ-0480")}), "contato da secretaria está no prompt do cérebro")
check("não são cumulativos" in (_ont.curso("FEQ-0480")["descontos"] or "") and "à vista tem 15%" in (_ont.curso("FEQ-0480")["descontos"] or ""), "FEQ-0480: 15% à vista na ontologia")
check("à vista" not in (_ont.curso("FEQ-0580")["descontos"] or ""), "os 15% à vista são só do FEQ-0480")

# 15/10/2026: lead JÁ matriculado vai para a secretaria do instituto; cancelamento por e-mail.
from atena.core.contatos import orientacao_matriculado
from atena.revisor.ontologico import revisar
_mf, _me = orientacao_matriculado("FEQ-1000"), orientacao_matriculado("ECO-1100")
check("(19) 3521-3911" in _mf and "extfeq@unicamp.br" in _mf and "cancelar" in _mf,
      "matriculado FEQ: secretaria FEQ + e-mail para cancelar")
check("iecoext@unicamp.br" in _me and "3521-5728" in _me and "3911" not in _me and "extfeq" not in _me,
      "matriculado ECO: só contatos do IE")
check(revisar("FEQ-1000", _mf, _ont)["aprovado"] and revisar("ECO-1100", _me, _ont)["aprovado"],
      "orientação passa no Revisor")
_ctx = {"curso": _ont.curso("FEQ-1000"), "captavel": _ont.captavel("FEQ-1000")}
check(_mf in _system_prompt(_ctx, fase="Matriculado"), "prompt de matriculado traz a orientação")
check("LEAD JÁ MATRICULADO" not in _system_prompt(_ctx, fase="Inscrito"),
      "inscrito ainda não matriculado segue o fluxo de captação")
from atena.core import contratos as C
_est = Estado(":memory:")
_est.upsert_lead("m1", nome="Ana", telefone="+55 19 98888", curso="ECO-1100", fase="Matriculado",
                 estado_conversa="aguardando_resposta")
Orquestrador(_est, _ont).processar_mensagem("m1", "quero cancelar minha matrícula")
_d = _est.listar_decisoes("m1")[0]
check(_d["acao"] == C.CONTINUAR and _d["mensagem"] == _me and _d["status"] == "proposta",
      "fallback determinístico: cancelamento de matriculado vira orientação, não escalonamento")
print(f"{ok}/{ok} passaram.")
