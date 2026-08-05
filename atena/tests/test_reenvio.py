#!/usr/bin/env python3
"""
Retentativa de entrega: recuperar o lead que a Meta recusou.

O que estes testes travam é a DISTINÇÃO entre as causas — porque tratar todas como
"manda outro template" desperdiça tentativa em 72% dos casos (os 131026 são problema
de número, não de texto).
"""
import sys
import pathlib
import datetime

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from atena.core.estado import Estado
from atena.ontologia.servico import Ontologia
from atena.transporte import reenvio as R

ok = 0


def check(c, msg):
    global ok
    assert c, "FALHOU: " + msg
    ok += 1
    print("  ok:", msg)


AGORA = datetime.datetime(2026, 8, 5, 15, 0, 0)


def _env(status="falhou", codigo=131026, destino="5519996027820", tpl="feq_lead", h=1):
    return {"texto": f"[template {tpl}] curso", "status": status, "destino": destino,
            "erro_codigo": codigo, "ts": (AGORA - datetime.timedelta(hours=h)).isoformat()}


# ---------------------------------------------------------------- variante do 9º dígito
check(R.variante_9("5519996027820") == "551996027820", "celular com 9 -> sem o 9")
check(R.variante_9("551996027820") == "5519996027820", "celular sem 9 -> com o 9 (volta igual)")
check(R.variante_9("551621088346") is None, "FIXO não tem variante (não gasta tentativa)")
check(R.variante_9("351910800862") is None, "estrangeiro não entra na regra do 9º dígito")
check(R.variante_9("") is None and R.variante_9(None) is None, "vazio não quebra")

# ---------------------------------------------------------------- escada por causa
lead = {"card_id": "c1", "telefone": "5519996027820", "nome": "Ana", "curso": "FEQ-0480"}

p = R.plano(lead, [_env(codigo=131026)], AGORA)
check(p["acao"] == "reenviar" and p["destino"] == "551996027820",
      "131026 (undeliverable) -> retenta na VARIANTE do número")
check(p["template"] == "feq_lead", "131026 mantém o template: o problema é o número, não o texto")

# a variante já foi tentada e também falhou -> não há terceira forma do número
p = R.plano(lead, [_env(destino="5519996027820"), _env(destino="551996027820")], AGORA)
check(p["acao"] == "humano", "as duas formas do número falharam -> operador")

# fixo: nada a tentar, vai direto para o humano em vez de queimar tentativa
p = R.plano({**lead, "telefone": "551621088346"}, [_env(destino="551621088346")], AGORA)
check(p["acao"] == "humano" and "fixo" in p["motivo"], "fixo -> operador (e-mail/telefone)")

# pacing: é limite de MARKETING; retentar no mesmo minuto falha igual -> espera
p = R.plano(lead, [_env(codigo=131049, h=1)], AGORA)
check(p["acao"] == "esperar" and p["familia"] == "pacing", "131049 dentro da espera -> aguarda")
p = R.plano(lead, [_env(codigo=131049, h=30)], AGORA)
check(p["acao"] == "reenviar" and p["destino"] == lead["telefone"],
      "131049 após a espera -> retenta no MESMO número")
check(p["template"] != "feq_lead", "pacing troca para um template complementar")

p = R.plano(lead, [_env(codigo=130472, h=30)], AGORA)
check(p["acao"] == "reenviar", "130472 (experimento) tem o mesmo tratamento do pacing")

# UTILITY é a única categoria fora do limite de marketing: tem preferência quando existir
meta = [{"name": "feq_lead", "status": "APPROVED", "category": "MARKETING", "language": "pt_BR"},
        {"name": "feq_retorno_solicitacao", "status": "APPROVED", "category": "UTILITY",
         "language": "pt_BR"}]
p = R.plano(lead, [_env(codigo=131049, h=30)], AGORA, templates_meta=meta)
check(p["template"] == "feq_retorno_solicitacao", "havendo UTILITY aprovado, ele vem primeiro")

# teto: nunca insistir para sempre
p = R.plano(lead, [_env(destino="a"), _env(destino="b"), _env(destino="c")], AGORA)
check(p["acao"] == "humano", f"{R.MAX_TENTATIVAS} tentativas -> para e chama o operador")

# quem recebeu não é candidato
check(R.plano(lead, [_env(status="entregue")], AGORA)["acao"] == "nada",
      "última mensagem entregue -> nada a fazer")

# ---------------------------------------------------------------- fim a fim
class WAFake:
    """Entrega só na variante SEM o 9 — o caso real dos 16 celulares perdidos."""
    def __init__(self):
        self.enviados = []

    def templates_listar(self):
        return None

    def enviar_template(self, telefone, nome, idioma="pt_BR", params=None):
        self.enviados.append((telefone, nome))
        return {"ok": True, "wamid": f"wamid.{len(self.enviados)}"}


est = Estado(":memory:")
ont = Ontologia()
est.upsert_lead("perdido", nome="Ana", telefone="5519996027820", curso="FEQ-0480",
                fase="Lead", estado_conversa="aguardando_resposta")
est.add_conversa("perdido", "agente", "[template feq_lead] curso", wamid="w1",
                 status="enviado", autor="template", destino="5519996027820")
est.atualizar_status_msg("w1", "falhou", 131026)

check(len(est.leads_sem_entrega()) == 1, "lead sem nenhuma entrega é candidato a retentativa")
wa = WAFake()
r = R.rodar(est, ont, wa)
check(r["reenviados"] == ["perdido"], "passada retenta o lead perdido")
check(wa.enviados[0][0] == "551996027820", "retentativa saiu para a variante sem o 9")

# a variante entrega -> o cadastro do lead é corrigido sozinho
from atena.transporte.whatsapp import WhatsApp
class OrqFake:
    pass
w = WhatsApp(est, OrqFake())
w._processar_status({"id": "wamid.1", "status": "delivered"}, [])
check(est.get_lead("perdido")["telefone"] == "551996027820",
      "entregou na variante -> número do lead corrigido para os próximos contatos")
check(est.leads_sem_entrega() == [], "com entrega confirmada, sai da fila de retentativa")

# o código do erro fica gravado (é ele que escolhe a estratégia)
check(est.envios_do_lead("perdido")[0]["erro_codigo"] == 131026, "código do erro persistido")

# esgotada a escada, vira alerta em vez de sumir
est.upsert_lead("fixo", nome="Bia", telefone="551621088346", curso="FEQ-0480",
                fase="Lead", estado_conversa="aguardando_resposta")
est.add_conversa("fixo", "agente", "[template feq_lead] curso", wamid="w2",
                 status="enviado", autor="template", destino="551621088346")
est.atualizar_status_msg("w2", "falhou", 131026)
r = R.rodar(est, ont, WAFake())
check(r["humano"] == ["fixo"], "número sem saída vira tarefa do operador")
check(any(a["tipo"] == "entrega_impossivel" for a in est.listar_alertas(50)),
      "operador é avisado na Central (lead não morre em silêncio)")
r2 = R.rodar(est, ont, WAFake())
check(r2["humano"] == [], "não repete o alerta a cada passada")

print(f"\n{ok}/{ok} passaram.")
