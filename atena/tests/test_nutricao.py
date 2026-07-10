#!/usr/bin/env python3
"""Reativação comercial: janela 24h -> nudge free-text; fora -> cadência de templates."""
import sys, pathlib, tempfile, os, datetime
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
from atena.core.estado import Estado
from atena.agentes import nutricao

ok = 0
def check(c, msg):
    global ok
    assert c, "FALHOU: " + msg
    ok += 1
    print("  ok:", msg)


class FakeOnt:
    def __init__(self, captavel=True): self._cap = captavel
    def captavel(self, curso): return {"captavel": self._cap}
    def curso(self, curso): return {"codigo": curso, "nome": "Curso X", "inscricoes_fim": "2026-12-31"}


class FakeWA:
    def __init__(self): self.enviados = []
    def enviar_template(self, tel, nome, params=None):
        self.enviados.append(nome)
        return {"ok": True, "wamid": "w." + str(len(self.enviados))}


def novo_est():
    est = Estado(tempfile.mktemp(suffix=".db"))
    return est


def lead(est, cid, **kw):
    base = dict(nome="L", telefone="+5519" + cid[-8:].rjust(8, "9"), curso="FEQ-0480",
                fase="Prospect", estado_conversa="aguardando_resposta", reengajos=0)
    base.update(kw)
    est.upsert_lead(cid, **base)


# --- FORA da janela: cadência de templates escalonados -------------------------
est = novo_est(); ont = FakeOnt(); wa = FakeWA()
lead(est, "c-frio")
est.add_conversa("c-frio", "lead", "oi")                       # última fala do lead = agora
d4 = datetime.datetime.now() + datetime.timedelta(days=4)      # 4 dias depois: janela fechada
r = nutricao.reativar(est, ont, wa, agora=d4)
check(r["templates"] == 1 and wa.enviados == ["feq_reengajamento"], "toque 1 = feq_reengajamento (>=3d)")
check(est.get_lead("c-frio")["reengajos"] == 1, "reengajos incrementou p/ 1")

d8 = datetime.datetime.now() + datetime.timedelta(days=8)      # 8d depois do último contato (o template)
r = nutricao.reativar(est, ont, wa, agora=d8)
check(wa.enviados[-1] == "feq_lembrete_inscricao", "toque 2 = feq_lembrete_inscricao (>=7d)")
check(est.get_lead("c-frio")["reengajos"] == 2, "reengajos incrementou p/ 2")

d30 = datetime.datetime.now() + datetime.timedelta(days=30)
r = nutricao.reativar(est, ont, wa, agora=d30)
check(r["templates"] == 0, "esgotou os toques frios (máx 2) — não envia mais")

# --- DENTRO da janela: nudge free-text (proposta), sem template ----------------
est = novo_est(); ont = FakeOnt(); wa = FakeWA()
lead(est, "c-stall")
est.add_conversa("c-stall", "lead", "tenho interesse")         # lead falou
est.add_conversa("c-stall", "agente", "que ótimo! me conta...")  # respondemos 1x (gate agentes==1)
chamados = []
d9h = datetime.datetime.now() + datetime.timedelta(hours=9)    # 9h: dentro da janela, travado
r = nutricao.reativar(est, ont, wa, propor=lambda cid: (chamados.append(cid) or cid), agora=d9h)
check(chamados == ["c-stall"], "travado na janela -> propor() chamado (nudge free-text)")
check(r["nudges"] == ["c-stall"] and wa.enviados == [], "nudge registrado, nenhum template gasto")

# se já respondemos 2x depois do lead (nudge já enviado), NÃO repete
est.add_conversa("c-stall", "agente", "ainda por aí?")
chamados2 = []
r = nutricao.reativar(est, ont, wa, propor=lambda cid: (chamados2.append(cid) or cid), agora=d9h)
check(chamados2 == [], "1 nudge por janela — não re-dispara")

# --- Guardrails: opt-out e curso não captável são pulados ----------------------
est = novo_est(); wa = FakeWA()
lead(est, "c-out", optout=1); est.add_conversa("c-out", "lead", "oi")
lead(est, "c-nocap"); est.add_conversa("c-nocap", "lead", "oi")
r = nutricao.reativar(est, FakeOnt(captavel=False), wa, agora=d30)
check(r["templates"] == 0 and wa.enviados == [], "opt-out + não-captável nunca reativam")

# --- Teto de segurança por passada (evita blast no 1º deploy) ------------------
est = novo_est(); wa = FakeWA()
for i in range(nutricao.MAX_POR_PASSADA + 5):
    cid = f"c-massa-{i:03d}"; lead(est, cid); est.add_conversa(cid, "lead", "oi")
r = nutricao.reativar(est, FakeOnt(), wa, agora=d30)
check(r["templates"] == nutricao.MAX_POR_PASSADA, f"teto: no máx {nutricao.MAX_POR_PASSADA} envios/passada")

print(f"\n{ok}/{ok} passaram.")
