#!/usr/bin/env python3
"""
Inscrição fora do prazo: prazo vencido MAS a turma ainda não começou.

Antes existiam só dois mundos — "aberta" e "encerrada" — e o segundo mandava o lead
para a lista de espera. Só que enquanto as aulas não começam ainda sobram vagas
remanescentes e a coordenação consegue gerar um link de inscrição fora do prazo:
tratar esse caso como "encerrado" joga fora uma matrícula que ainda era possível.
"""
import sys
import pathlib
import datetime

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from atena.ontologia.servico import Ontologia
from atena.agentes.bibliotecario import Bibliotecario
from atena.agentes.cerebro import _bloco_janela
from atena.core.orquestrador import brain_deterministico
from atena.revisor.ontologico import revisar_estrutural
from atena.growth.ponte import render_tokens
from atena.growth.db import Growth

ONT = Ontologia()
ok = 0


def check(c, msg):
    global ok
    assert c, "FALHOU: " + msg
    ok += 1
    print("  ok:", msg)


# ------------------------------------------------------- 1) o estado novo
# FEQ-1000: prorrogado em 28/08/2026 — inscrições até 03/09/2026, aulas começam
# 05/09/2026. A janela de recuperação encolheu para 1 dia só (04/09): datas fixas
# de um curso real são frágeis por natureza (já quebrou 2x nesta suíte por causa
# disso — test_promocao.py e test_growth.py) mas o comportamento sob teste é o da
# MÁQUINA DE ESTADOS, não do calendário do FEQ-1000 em si.
D = datetime.date
cap = ONT.captavel("FEQ-1000", D(2026, 9, 4))
check(cap["status"] == "fora_do_prazo", "prazo vencido + turma por começar = fora_do_prazo")
check(cap["recuperavel"] is True, "o estado se declara recuperável")
check(cap["captavel"] is False,
      "mas NÃO vira captável: inscrição pública segue fechada (nada mais muda sozinho)")
check(cap["aulas_inicio"] == "2026-09-05", "expõe quando as aulas começam")

check(ONT.captavel("FEQ-1000", D(2026, 9, 3))["status"] == "aberta",
      "no último dia do prazo ainda é inscrição normal")
check(ONT.captavel("FEQ-1000", D(2026, 9, 4))["status"] == "fora_do_prazo",
      "véspera da 1ª aula ainda é recuperável")
check(ONT.captavel("FEQ-1000", D(2026, 9, 5))["status"] == "encerrada",
      "começou a aula: acabou a janela de recuperação")
check(ONT.captavel("FEQ-1000", D(2026, 12, 1))["recuperavel"] is False,
      "curso em andamento não é recuperável")

# curso que já começou há meses continua simplesmente encerrado. O código sai do
# catálogo, não fixo: o FEQ-0499 era o exemplo até reabrir (11/08/2026) — teste de
# janela temporal não pode depender de qual curso está fechado hoje.
_ENCERRADO = next(c for c in sorted(ONT.cursos())
                  if ONT.captavel(c, D(2026, 8, 7))["status"] == "encerrada")
check(ONT.captavel(_ENCERRADO, D(2026, 8, 7))["status"] == "encerrada",
      f"{_ENCERRADO} (aulas no passado) segue encerrado")

# Idem para "fora do prazo", agora com DATA FIXA. Tornar só o curso dinâmico não
# bastava: o teste continuava dependendo de existir, naquele dia, ALGUM curso nessa
# janela — e em 09/09/2026 não existia mais nenhum, então a suíte quebrou sozinha,
# sem ninguém mudar código. As seções 3 e 4 recebem a mesma data por parâmetro.
_HOJE = D(2026, 8, 30)
_FORA_DO_PRAZO = next(c for c in sorted(ONT.cursos())
                      if ONT.captavel(c, _HOJE)["status"] == "fora_do_prazo")

# ------------------------------------------------------- 2) política ancorada
pol = ONT.politica("politicaForaDoPrazo")
check(pol and "vagas remanescentes" in pol and "24 horas" in pol,
      "a política vive na ontologia (o agente não inventa a regra)")
check(ONT.politica("inexistente") is None, "política desconhecida devolve None")

# ------------------------------------------------------- 3) instrução ao agente
bib = Bibliotecario(ONT)
ctx = bib.contexto(_FORA_DO_PRAZO, "Superior completo", hoje=_HOJE)
check(ctx.get("politica_fora_do_prazo"),
      f"contexto carrega a política quando aplicável ({_FORA_DO_PRAZO})")
check(bib.contexto("FEQ-0480", "Superior completo", hoje=_HOJE).get("politica_fora_do_prazo") is None,
      "curso aberto não carrega a política (prompt não é depósito de regra)")

b = _bloco_janela(ctx)
check("PRAZO VENCIDO" in b, "bloco anuncia a situação especial")
check("vagas remanescentes" in b and "fora do prazo" in b, "oferece a saída")
check("E-MAIL" in b and "24 horas" in b, "pede o e-mail e informa a validade do link")
check("escalar_humano" in b, "manda escalar para um humano gerar o link")
check("QUEM GERA O LINK É UMA PESSOA" in b and "não invente URL" in b,
      "proíbe o agente de fingir que gerou o link")
check("NÃO ofereça lista de espera" in b, "proíbe a oferta errada (esperar) neste caso")

b_enc = _bloco_janela(bib.contexto(_ENCERRADO, "Superior completo", hoje=_HOJE))
check("lista de espera" in b_enc and "vagas remanescentes" not in b_enc,
      "curso realmente encerrado mantém a lista de espera")
check(_bloco_janela(bib.contexto("FEQ-0480", "Superior completo", hoje=_HOJE)) == "",
      "curso aberto não recebe bloco nenhum")

# ------------------------------------------------------- 4) fallback determinístico
d = brain_deterministico(ONT, {"card_id": "x", "curso": _FORA_DO_PRAZO, "nome": "Ana",
                               "escolaridade": "Superior completo"},
                         "ainda dá para entrar?", [], hoje=_HOJE)
check(d.proxima_acao == "escalar_humano", "sem LLM, escala para o humano gerar o link")
check(d.mensagem_ao_lead and "vagas remanescentes" in d.mensagem_ao_lead,
      "mas JÁ responde ao lead — antes ele ficava mudo até alguém abrir a Central")
check("e-mail" in d.mensagem_ao_lead.lower(), "o fallback também pede o e-mail")
check(revisar_estrutural(_FORA_DO_PRAZO, d.mensagem_ao_lead, ONT)["aprovado"],
      "a mensagem do fallback passa pelo Revisor (as '24 horas' não são carga horária)")

# ------------------------------------------------------- 5) a LP diz o mesmo
g = Growth(":memory:")
meta = next(m for m in g.cursos() if m["curso"] == "FEQ-1000")
tk_rec = render_tokens(meta, ONT.curso("FEQ-1000"), aberto=False, recuperavel=True)
tk_enc = render_tokens(meta, ONT.curso("FEQ-1000"), aberto=False, recuperavel=False)

check("remanescente" in tk_rec["CTA_LABEL"], "CTA da LP muda para vaga remanescente")
check("fora do prazo" in tk_rec["MODAL_BOTAO"], "botão do modal fala do link fora do prazo")
check("24 horas" in tk_rec["MODAL_TITULO"], "modal avisa a validade do link")
check("remanescentes" in tk_rec["PRAZO_LINHA"], "a linha de prazo não promete 'aviso quando reabrir'")
check("lista de espera" not in tk_rec["MODAL_BOTAO"],
      "a LP recuperável não oferece lista de espera")
check("lista de espera" in tk_enc["MODAL_BOTAO"],
      "curso realmente encerrado mantém a lista de espera na LP")

print(f"\n{ok}/{ok} passaram.")
