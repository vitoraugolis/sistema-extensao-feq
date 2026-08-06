#!/usr/bin/env python3
"""
Promoção de campanha (agosto/2026) — FEQ-0480 e FEQ-0331.

O risco aqui é dinheiro dito errado a lead real. Os testes travam três coisas:
  1. a aritmética anunciada (o 15% é sobre a TABELA, não sobre o preço promocional);
  2. o Revisor aceita o preço promocional e continua barrando valor inventado;
  3. a promoção MORRE sozinha no fim da janela — não fica anunciada para sempre.
"""
import sys
import pathlib
import datetime

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from atena.ontologia.servico import Ontologia
from atena.revisor.ontologico import revisar_estrutural
from atena.growth.funil import valor_curso
from atena.growth.ponte import _investimento_itens
from atena.agentes.cerebro import _bloco_promo

ONT = Ontologia()
ok = 0


def check(c, msg):
    global ok
    assert c, "FALHOU: " + msg
    ok += 1
    print("  ok:", msg)


# ------------------------------------------------------- 1) aritmética anunciada
TABELA = {"FEQ-0480": (19, 866.09, 16455.75), "FEQ-0331": (5, 1163.19, 5815.95)}

pr = ONT.promocao("FEQ-0480")
check(pr and len(pr["ofertas"]) == 2, "FEQ-0480 tem as duas ofertas da campanha")
a, b = pr["ofertas"]
check((a["n"], a["valor_parcela"]) == (19, "799.00"), "oferta individual: 19x de R$ 799,00")
check((b["n"], b["valor_parcela"], b["a_vista"]) == (19, "736.18", "13987.39"),
      "oferta grupo/à vista: 19x de R$ 736,18 ou R$ 13.987,39")

# o 15% é sobre a TABELA — se fosse sobre os 799 daria R$ 679,15, e o anúncio mentiria
n, vp, av = TABELA["FEQ-0480"]
check(abs(float(b["valor_parcela"]) - vp * 0.85) < 0.01, "parcela do grupo = 15% off da TABELA")
check(abs(float(b["a_vista"]) - av * 0.85) < 0.01, "à vista do grupo = 15% off da TABELA")
check(abs(float(b["valor_parcela"]) - 799 * 0.85) > 50, "o 15% NÃO é sobre o preço promocional")

o331 = ONT.promocao("FEQ-0331")["ofertas"][0]
n3, vp3, av3 = TABELA["FEQ-0331"]
check((o331["n"], o331["valor_parcela"]) == (6, "872.39"), "FEQ-0331 parcela em 6x de R$ 872,39")
check(abs(float(o331["a_vista"]) - av3 * 0.90) < 0.01, "FEQ-0331 à vista = 10% off da tabela")
check(abs(o331["n"] * float(o331["valor_parcela"]) - float(o331["a_vista"])) < 0.11,
      "6 × parcela fecha com o à vista (mesma trava do SHACL)")

# condição obrigatória: anunciar R$ 736,18 sem dizer que exige grupo seria enganoso
check(all(x["condicao"] for x in pr["ofertas"]), "toda oferta declara sua condição")

# ------------------------------------------------------- 2) Revisor
APROVA = [("FEQ-0480", "Na promoção sai por 19x de R$ 799,00."),
          ("FEQ-0480", "Em grupo de 3 ou mais, a parcela cai para R$ 736,18."),
          ("FEQ-0480", "À vista fica R$ 13.987,39."),
          ("FEQ-0480", "O valor de tabela é 19x de R$ 866,09."),
          ("FEQ-0331", "São 6x de R$ 872,39, ou R$ 5.234,36 à vista.")]
BLOQUEIA = [("FEQ-0480", "Faço por 19x de R$ 650,00."),
            ("FEQ-0480", "À vista sai R$ 12.000,00."),
            ("FEQ-0480", "Com mais 10% de ex-aluno, a parcela vai a R$ 662,56."),
            ("FEQ-0331", "Parcelo em 12x de R$ 400,00."),
            ("FEQ-1000", "Tem promoção: 19x de R$ 799,00.")]
for cod, txt in APROVA:
    check(revisar_estrutural(cod, txt, ONT)["aprovado"], f"Revisor aprova: {txt}")
for cod, txt in BLOQUEIA:
    check(not revisar_estrutural(cod, txt, ONT)["aprovado"], f"Revisor bloqueia: {txt}")

# ------------------------------------------------------- 3) a promoção expira sozinha
fim = datetime.date.fromisoformat(ONT.promocao("FEQ-0480")["fim"])
check(ONT.promocao("FEQ-0480", fim) is not None, "no último dia ainda vale")
check(ONT.promocao("FEQ-0480", fim + datetime.timedelta(days=1)) is None,
      "no dia seguinte a promoção some da ontologia")
check(ONT.promocao("FEQ-0480", datetime.date(2026, 8, 1)) is None,
      "antes de começar também não existe")
check(ONT.promocao("FEQ-1000") is None, "curso sem campanha não ganha promoção")

# a promoção não pode sobreviver à inscrição (o lead chegaria na Extecamp sem o preço)
for cod in ("FEQ-0480", "FEQ-0331"):
    p = ONT.promocao(cod)
    check(p["fim"] <= ONT.curso(cod)["inscricoes_fim"],
          f"{cod}: promoção termina junto ou antes do fim das inscrições")

# ------------------------------------------------------- 4) o que o lead vê
html = _investimento_itens(ONT.curso("FEQ-0480"))
check("799,00" in html and "736,18" in html and "13.987,39" in html, "LP mostra as duas ofertas")
check("866,09" in html and "line-through" in html, "LP mostra a tabela riscada como referência")
check("16/08/2026" in html, "LP mostra o prazo da promoção")
check("3 ou mais" in html, "LP diz a condição do preço de grupo")
sem = _investimento_itens(ONT.curso("FEQ-0670"))
check("line-through" not in sem, "curso sem promoção mantém a LP como era")

b0480 = _bloco_promo(ONT.curso("FEQ-0480"))
check("R$ 799,00" in b0480 and "R$ 13.987,39" in b0480, "prompt do cérebro traz os valores")
check("nunca recalcule" in b0480 and "NÃO some a promoção" in b0480,
      "prompt proíbe recalcular e acumular desconto")
check(_bloco_promo(ONT.curso("FEQ-1000")) == "", "sem promoção, sem bloco no prompt")

# ------------------------------------------------------- 5) valor de conversão
check(abs(valor_curso(ONT.curso("FEQ-0480")) - 13987.39) < 0.01,
      "Purchase usa o valor promocional (não infla o ROAS com o preço de tabela)")
check(abs(valor_curso(ONT.curso("FEQ-0670")) - 1798.18) < 0.01,
      "curso sem promoção continua no valor de tabela")

# ------------------------------------------------------- 6) templates de campanha
from atena.transporte.whatsapp import (CORPOS_PROMO, TEMPLATES_PROMO, validar_template,
                                       template_promocional, montar_params)
from atena.transporte import reenvio as R

for nome, corpo in CORPOS_PROMO.items():
    check(validar_template(nome, "MARKETING", corpo, ["Ana", "16/08/2026"]) is None,
          f"{nome}: corpo passa nas regras da Meta antes de submeter")
# os valores do corpo têm de ser os MESMOS da ontologia — senão o template mente
check("799,00" in CORPOS_PROMO["feq_promo_0480"] and "736,18" in CORPOS_PROMO["feq_promo_0480"]
      and "13.987,39" in CORPOS_PROMO["feq_promo_0480"], "template do 0480 traz os 3 valores certos")
check("872,39" in CORPOS_PROMO["feq_promo_0331"] and "5.234,36" in CORPOS_PROMO["feq_promo_0331"],
      "template do 0331 traz os valores certos")
check("3 ou mais" in CORPOS_PROMO["feq_promo_0480"], "template do 0480 declara a condição de grupo")

check(template_promocional("FEQ-0480", ONT) == "feq_promo_0480", "curso em campanha tem template")
check(template_promocional("FEQ-1000", ONT) is None, "curso sem campanha não tem")


class _OntSemPromo:
    def promocao(self, curso, hoje=None):
        return None

    def curso(self, c):
        return ONT.curso(c)


check(template_promocional("FEQ-0480", _OntSemPromo()) is None,
      "campanha encerrada trava o template (não anuncia preço morto)")

class _WA:
    def __init__(self, st):
        self._st = st

    def templates_status(self, forcar=False):
        return self._st


check(template_promocional("FEQ-0480", ONT, _WA({"feq_promo_0480": "PENDING"})) is None,
      "template ainda em revisão na Meta não é usado (não queima tentativa)")
check(template_promocional("FEQ-0480", ONT, _WA({"feq_promo_0480": "APPROVED"})) == "feq_promo_0480",
      "aprovado na Meta, passa a ser usado")
check(template_promocional("FEQ-0480", ONT, _WA(None)) == "feq_promo_0480",
      "sem conseguir consultar a Meta (dev/dry-run), não bloqueia")

check(montar_params("feq_promo_0480", {"nome": "Ana", "curso": "FEQ-0480"}, ONT)
      == ["Ana", "16/08/2026"], "params do template de campanha: nome + prazo")

# a retentativa usa o template da campanha antes dos genéricos
env = [{"texto": "[template feq_lead] x", "status": "falhou", "destino": "5519996027820",
        "erro_codigo": 131049, "ts": (datetime.datetime.now() - datetime.timedelta(hours=30)).isoformat()}]
p = R.plano({"card_id": "x", "telefone": "5519996027820", "curso": "FEQ-0480"}, env,
            promo_tpl="feq_promo_0480")
check(p["template"] == "feq_promo_0480", "retentativa por pacing usa o template da campanha")
p2 = R.plano({"card_id": "x", "telefone": "5519996027820", "curso": "FEQ-0480"}, env, promo_tpl=None)
check(p2["template"] != "feq_promo_0480", "sem campanha vigente, volta aos templates genéricos")

print(f"\n{ok}/{ok} passaram.")
