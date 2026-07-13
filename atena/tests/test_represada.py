#!/usr/bin/env python3
"""Resposta de dúvida represada (respondida fora da janela de 24h) -> entregue ao reabrir."""
import sys, pathlib, tempfile
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
from atena.core.estado import Estado

ok = 0
def check(c, msg):
    global ok
    assert c, "FALHOU: " + msg
    ok += 1
    print("  ok:", msg)

est = Estado(tempfile.mktemp(suffix=".db"))
est.upsert_lead("c1", nome="Fefa", telefone="+5519998247333", curso="FEQ-0333",
                fase="Prospect", estado_conversa="aguardando_resposta")

# nada represado ainda
check(est.coordenacao_represada("c1") is None, "sem proposta -> nada represado")

# resposta do cérebro pendente NÃO conta (só coordenação)
est.add_decisao("c1", agente="orquestrador", acao="continuar", mensagem="oi", status="proposta")
check(est.coordenacao_represada("c1") is None, "proposta do cérebro não é resposta de dúvida")

# resposta de coordenação represada (fora da janela)
est.add_decisao("c1", agente="coordenacao", acao="responder_duvida",
                mensagem="A ementa do CCUS é...", status="proposta")
r = est.coordenacao_represada("c1")
check(r and r["mensagem"] == "A ementa do CCUS é...", "acha a resposta de coordenação represada")

# uma 2ª represada: entrega a MAIS ANTIGA primeiro
est.add_decisao("c1", agente="coordenacao", acao="responder_duvida",
                mensagem="resposta mais nova", status="proposta")
check(est.coordenacao_represada("c1")["mensagem"] == "A ementa do CCUS é...", "entrega a mais antiga 1º")

# entregue -> some da fila
est.set_decisao_status(r["id"], "enviada")
check(est.coordenacao_represada("c1")["mensagem"] == "resposta mais nova", "após entregar, sobra a próxima")
est.set_decisao_status(est.coordenacao_represada("c1")["id"], "enviada")
check(est.coordenacao_represada("c1") is None, "fila vazia após entregar tudo")

print(f"\n{ok}/{ok} passaram.")
