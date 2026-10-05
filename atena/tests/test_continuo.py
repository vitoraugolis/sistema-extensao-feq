#!/usr/bin/env python3
"""
Curso de DIFUSÃO de oferecimento CONTÍNUO (FEQ-1150, 05/10/2026).

Primeiro curso da FEQ que foge de três premissas embutidas na LP e no guia:
  1. emite DECLARAÇÃO de frequência, não certificado (Extecamp: difusão a distância);
  2. pagamento em 1 parcela — "em até 1 parcelas" / "1x de" é texto quebrado;
  3. não tem turma a formar nem início de aulas: a inscrição vai até 2031 e o
     passo "aguarde a abertura da turma ao encerramento das inscrições" mandaria o
     lead esperar cinco anos.

`hoje` fixo: o estado depende do calendário (ver test_captacao_antecipada.py).
"""
import sys
import pathlib
import datetime

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from atena.ontologia.servico import Ontologia
from atena.growth.db import Growth
from atena.growth import ponte, pdf_guia

ONT = Ontologia()
GRW = Growth(":memory:")
HOJE = datetime.date(2026, 10, 5)
COD = "FEQ-1150"
ok = 0


def check(c, msg):
    global ok
    assert c, "FALHOU: " + msg
    ok += 1


c = ONT.curso(COD)
check(c and c["tipo"] == "Curso de Difusão" and c["continuo"], "FEQ-1150 é difusão de oferecimento contínuo")
check(c["requisito"] == "nenhum" and ONT.elegivel("Ensino Médio Completo", COD), "sem gate de escolaridade")
check(ONT.captavel(COD, HOJE)["status"] == "aberta", "inscrição aberta em 05/10/2026")
check(c["url_inscricao"] == "https://ins.extecamp.unicamp.br/curso/inscricao/FEQ-1150/001", "link de inscrição da turma 001")
check(not ONT.curso("FEQ-0295")["continuo"], "curso com turma (FEQ-0295) não é contínuo")

lp = ponte.render(GRW, ONT, COD, hoje=HOJE)
check(lp and "{{" not in lp.split("<script")[0], "LP renderiza sem token sobrando")
check("Certificado Unicamp" not in lp and "Declaração de frequência Unicamp" in lp,
      "LP promete declaração de frequência, não certificado")
check("1x de" not in lp and "1 parcelas" not in lp, "1 parcela é mostrada como à vista")
check("R$ 499,16</strong> à vista" in lp, "preço à vista no topo")
check("abertura da turma" not in lp and "turma confirmada" not in lp,
      "sem passo de 'aguardar a turma' num curso contínuo")
check("Quando começam e terminam as aulas?" not in lp and "oferecimento é contínuo" in lp,
      "período vira disponibilidade na plataforma, não início de aulas")
meta = next(m for m in GRW.cursos() if m["curso"] == COD)
check(ponte.url_inscricao(meta, c).endswith("/curso/inscricao/FEQ-1150/001") and meta["url"].endswith("/FEQ-1150/001"),
      "destino da inscrição é o formulário da turma 001 (ontologia e cursos_metas concordam)")

# os demais cursos não mudam de documento nem de passos
lp_0295 = ponte.render(GRW, ONT, "FEQ-0295", hoje=HOJE)
check("Certificado Unicamp" in lp_0295 and "abertura da turma" in lp_0295 and "4x de" in lp_0295,
      "curso com turma mantém certificado, parcelas e os 5 passos")
check("taxa de inscrição de R$ 24,00" in lp_0295, "FEQ-0295 mostra a taxa de inscrição publicada na Extecamp")

home = ponte.home_cursos_html(GRW, ONT, hoje=HOJE)
check('href="/inscricao-feq-1150/"' in home and 'href="/inscricao-feq-0295/"' in home,
      "os dois cursos têm card na home, no slug perene")
card = home[home.index('href="/inscricao-feq-1150/"'):].split("</a>")[0]
check("feq-capa" in card and "Inscrições abertas" in card and "à vista" in card and "1x de" not in card,
      "card do FEQ-1150 com capa, selo de aberto e preço à vista")

guia = pdf_guia._render_html(c)
check("ao vivo" not in guia and "Turmas reduzidas" not in guia, "guia não promete aula ao vivo nem turma reduzida")
check("Início em" not in guia and "Início das aulas" not in guia, "guia não inventa início de aulas")
check("Declaração de frequência" in guia, "guia fala em declaração de frequência")
check("ao vivo" in pdf_guia._render_html(ONT.curso("FEQ-0480")), "guia dos demais cursos FEQ segue igual")

print(f"\n{ok} checks OK — oferecimento contínuo")
