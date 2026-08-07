#!/usr/bin/env python3
"""
Link de inscrição: o Revisor barrava preço e data inventados, mas deixava passar
qualquer URL. Seis leads do FEQ-0480 receberam a turma /001 em vez de /009 — o link
existe, abre uma página real, e simplesmente inscreve (ou não) na turma errada.
Link quebrado mata a conversão tão bem quanto preço errado.

Causa: o agente recebia só a URL /curso/dados/ e tinha de convertê-la à mão.
"""
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from atena.core.estado import Estado
from atena.ontologia.servico import Ontologia
from atena.revisor.ontologico import revisar_estrutural
from atena.ops.corrigir_link import afetados, MARCA

ONT = Ontologia()
ok = 0


def check(c, msg):
    global ok
    assert c, "FALHOU: " + msg
    ok += 1
    print("  ok:", msg)


CERTO = "https://ins.extecamp.unicamp.br/curso/inscricao/FEQ-0480/009"
ERRADO = "https://ins.extecamp.unicamp.br/curso/inscricao/FEQ-0480/001"

# ------------------------------------------------------- 1) link pronto na ontologia
c = ONT.curso("FEQ-0480")
check(c["url_inscricao"] == CERTO, "ontologia entrega o link de INSCRIÇÃO pronto")
check("/curso/dados/" in c["url_extecamp"], "a URL de detalhes segue existindo, separada")
check(c["url_inscricao"] != c["url_extecamp"],
      "são campos distintos — o agente não precisa converter nada")

# ------------------------------------------------------- 2) Revisor fail-closed em URL
check(revisar_estrutural("FEQ-0480", f"Inscreva-se: {CERTO}", ONT)["aprovado"],
      "link correto passa")
check(revisar_estrutural("FEQ-0480", f"Detalhes: {c['url_extecamp']}", ONT)["aprovado"],
      "página de detalhes do MESMO curso também é legítima")

r = revisar_estrutural("FEQ-0480", f"Inscreva-se: {ERRADO}", ONT)
check(not r["aprovado"], "TURMA ERRADA é bloqueada (o incidente não se repete)")
check("Link da Extecamp fora da base" in r["bloqueios"][0], "bloqueio explica o motivo")

for variacao, desc in [
    (f"{ERRADO}.", "com ponto final"),
    (f"[clique]({ERRADO})", "dentro de markdown"),
    (f"{ERRADO}*", "com asterisco colado"),
    ("http://ins.extecamp.unicamp.br/curso/inscricao/FEQ-0480/002", "http e outra turma"),
    ("https://ins.extecamp.unicamp.br/curso/inscricao/FEQ-9999/001", "curso inexistente"),
]:
    check(not revisar_estrutural("FEQ-0480", f"Olha: {variacao}", ONT)["aprovado"],
          f"link errado {desc} não escapa")

# curso CITADO na mensagem entra no escopo: linkar outro curso do portfólio é legítimo
check(revisar_estrutural(
    "FEQ-0480", "Veja o FEQ-1000: https://ins.extecamp.unicamp.br/curso/inscricao/FEQ-1000/018",
    ONT)["aprovado"], "link de outro curso citado é aprovado (não é alucinação)")

check(revisar_estrutural("FEQ-0480", "Sem link nenhum aqui.", ONT)["aprovado"],
      "mensagem sem link não é afetada")

# ------------------------------------------------------- 3) rastreamento dos afetados
est = Estado(":memory:")
est.upsert_lead("errado", nome="Gisele", telefone="5531986588835", curso="FEQ-0480")
est.add_conversa("errado", "agente", f"Segue o link: {ERRADO}", autor="llm")
est.upsert_lead("certo", nome="Bia", telefone="5519999990002", curso="FEQ-0480")
est.add_conversa("certo", "agente", f"Segue o link: {CERTO}", autor="llm")
est.upsert_lead("mudo", nome="Cid", telefone="5519999990003", curso="FEQ-0480")
est.add_conversa("mudo", "agente", "Oi! Posso ajudar?", autor="llm")

lst = afetados(est, ONT)
check([a["card_id"] for a in lst] == ["errado"], "acha só quem recebeu link fora da base")
check(lst[0]["correto"] == CERTO, "sabe qual é o link correto para reenviar")
check(lst[0]["errados"] == [ERRADO], "registra o link errado que foi enviado")

# não reenvia para quem já foi corrigido
est.add_conversa("errado", "agente", f"{MARCA} {CERTO}", autor="template")
check(afetados(est, ONT) == [], "quem já recebeu a correção sai da fila")

# FALSO POSITIVO conferido em produção: link de OUTRO curso pode ser legítimo — o agente
# faz cross-sell e o curso do card muda quando o lead troca de interesse. O que define o
# bug é a URL apontar para curso+turma que NÃO EXISTE no catálogo.
est.upsert_lead("crosssell", nome="Isaias", telefone="5519988770794", curso="FEQ-0480")
est.add_conversa("crosssell", "agente",
                 "Segue: https://ins.extecamp.unicamp.br/curso/inscricao/FEQ-0360/007", autor="llm")
check([a["card_id"] for a in afetados(est, ONT)] == [],
      "link real de OUTRO curso não é acusado (era falso positivo em produção)")

# opt-out é sinalizado (o envio pula, mas o operador precisa saber que existe)
est.upsert_lead("saiu", nome="Dina", telefone="5519999990004", curso="FEQ-0480", optout=1)
est.add_conversa("saiu", "agente", f"link: {ERRADO}", autor="llm")
lst = afetados(est, ONT)
check(len(lst) == 1 and lst[0]["optout"] is True, "lead em opt-out aparece marcado")

# o link certo vem do curso que o link QUEBRADO tentava alcançar, não do card
est.upsert_lead("outro", nome="Eva", telefone="5519999990005", curso="FEQ-1000")
est.add_conversa("outro", "agente",
                 "link: https://ins.extecamp.unicamp.br/curso/inscricao/FEQ-0480/001", autor="llm")
alvo = next(a for a in afetados(est, ONT) if a["card_id"] == "outro")
check(alvo["curso"] == "FEQ-0480" and alvo["correto"] == CERTO,
      "corrige para o curso do link quebrado, mesmo se o card estiver em outro")

print(f"\n{ok}/{ok} passaram.")
