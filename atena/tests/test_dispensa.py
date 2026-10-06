#!/usr/bin/env python3
"""
Central: limpar a fila de intervenção e as notificações (spec 14 § Dispensa).

A dispensa oculta sem mentir: nada é resolvido nem apagado, e o item VOLTA quando o
estado dele muda (o lead fala de novo). Sem isso a fila só crescia — 14 conversas em
modo manual paradas havia semanas não tinham como sair (05/10/2026).
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
os.environ["ATENA_DEV"] = "1"
import atena.api.app as A                                    # noqa: E402
from atena.core.estado import Estado                          # noqa: E402
from atena.growth.db import Growth                            # noqa: E402

A.EST = Estado(":memory:")
A.GRW = Growth(":memory:")
ok = 0


def check(cond, msg):
    global ok
    assert cond, "FALHOU: " + msg
    ok += 1
    print("  ok:", msg)


c = A.app.test_client()
with c.session_transaction() as s:
    s["usuario"], s["papel"] = "tester", "supervisor"

for cid in ("m1", "m2"):
    A.EST.upsert_lead(cid, nome=cid, telefone="55199999000" + cid[-1], curso="FEQ-0480")
    A.EST.set_lead(cid, modo="manual")
    A.EST.add_conversa(cid, "lead", "oi, tem alguém aí?")
A.EST.add_alerta("sistema", "alerta de teste")

chaves = lambda: {i["chave"] for i in c.get("/api/aprovacoes").get_json()["itens"]}
check({"manual:m1", "manual:m2"} <= chaves(), "duas conversas manuais na fila")

r = c.post("/api/aprovacoes/dispensar", json={"chave": "manual:m1"}).get_json()
check(r["dispensados"] == 1 and "manual:m1" not in chaves() and "manual:m2" in chaves(),
      "dispensar UM item tira só ele")
check(A.EST.get_lead("m1")["modo"] == "manual", "dispensar não mexe no lead")
check(all(n.get("chave") != "manual:m1" for n in c.get("/api/notificacoes").get_json()["itens"]),
      "item dispensado sai das notificações também")

import time; time.sleep(1.1)                 # ts da conversa tem resolução de segundo
A.EST.add_conversa("m1", "agente", "oi!")
A.EST.add_conversa("m1", "lead", "voltei com outra pergunta")
check("manual:m1" in chaves(), "lead falou de novo: o item VOLTA à fila")

c.post("/api/aprovacoes/dispensar", json={"todas": True})
check(not chaves() and c.get("/api/aprovacoes").get_json()["agir"] == 0, "limpar tudo esvazia a fila e o contador")
check(c.post("/api/aprovacoes/dispensar", json={}).status_code == 400, "sem chave nem 'todas' é erro, não limpa nada")

check(len(c.get("/api/aprovacoes").get_json()["alertas"]) == 1, "alerta não lido aparece no painel")
c.post("/api/notificacoes/ler", json={})
check(c.get("/api/aprovacoes").get_json()["alertas"] == [], "alerta lido sai do painel de alertas")

print(f"\n{ok}/{ok} passaram.")
