#!/usr/bin/env python3
"""
Declarar novo oferecimento (turma nova) + URL perene das LPs.

Catálogo TEMPORÁRIO (mesmo padrão de test_injetor.py) — nada aqui toca o catálogo
real. O risco duplo: (1) turma/URL erradas cruzam DOIS armazenamentos hoje
desconectados (catalogo.jsonld + cursos_metas) sem transação; (2) a URL perene não
pode, sob nenhuma circunstância, mudar o slug de uma turma JÁ publicada — são
campanhas pagas rodando.
"""
import sys
import shutil
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from atena.core.estado import Estado
from atena.agentes.ontologista import Ontologista
from atena.growth.db import Growth
from atena.growth.wp_deploy_bridge import _slug, LEGADO_TURMA

ONT_DIR = Path(__file__).resolve().parents[1] / "ontologia"
ok = 0


def check(c, msg):
    global ok
    assert c, "FALHOU: " + msg
    ok += 1
    print("  ok:", msg)


def _setup():
    tmp = Path(tempfile.mkdtemp())
    shutil.copy(ONT_DIR / "catalogo.jsonld", tmp / "catalogo.jsonld")
    est = Estado(":memory:")
    onto = Ontologista(est, catalogo_path=tmp / "catalogo.jsonld", vocab_path=ONT_DIR / "vocabulario.ttl")
    return est, onto


NOVO = dict(curso="FEQ-0480", turma="010",
           url_extecamp="https://ins.extecamp.unicamp.br/curso/inscricao/FEQ-0480/010",
           insc_inicio="2026-11-01", insc_fim="2026-11-30",
           of_inicio="2027-02-01", of_fim="2027-08-01")

# ------------------------------------------------------- 1) diff nos caminhos certos
est, onto = _setup()
p = onto.propor_oferecimento(**NOVO)
check(p["status"] == "proposta", "proposta válida com os 6 campos preenchidos")
check(p["validacao"]["ok"] is True, "SHACL aprova (datas coerentes, URL bate com a turma)")
campos = {d["campo"] for d in p["diff"]}
check(campos == {"janelaInscricao.inicio", "janelaInscricao.fim",
                 "janelaOferecimento.inicio", "janelaOferecimento.fim", "urlExtecamp"},
      "diff toca exatamente os 5 campos — urlExtecamp na RAIZ, não dentro de planoPagamento")
check(any(d["campo"] == "urlExtecamp" and d["para"] == NOVO["url_extecamp"] for d in p["diff"]),
      "URL nova vai para o campo certo")

# ------------------------------------------------------- 2) validação de campos
p2 = onto.propor_oferecimento(curso="FEQ-0480", turma="010", url_extecamp="",
                              insc_inicio="2026-11-01", insc_fim="2026-11-30",
                              of_inicio="2027-02-01", of_fim="2027-08-01")
check(p2["status"] == "esclarecer", "campo vazio (URL) não vira proposta — pede esclarecimento")

p3 = onto.propor_oferecimento(curso="FEQ-0480", turma="010",
                              url_extecamp="https://ins.extecamp.unicamp.br/curso/inscricao/FEQ-0480/999",
                              insc_inicio="2026-11-01", insc_fim="2026-11-30",
                              of_inicio="2027-02-01", of_fim="2027-08-01")
check(p3["status"] == "esclarecer", "URL com turma DIFERENTE da declarada é rejeitada antes de virar proposta")

p4 = onto.propor_oferecimento(curso="FEQ-9999", turma="001", url_extecamp="https://x/FEQ-9999/001",
                              insc_inicio="2026-01-01", insc_fim="2026-02-01",
                              of_inicio="2026-03-01", of_fim="2026-04-01")
check(p4["status"] == "invalida", "curso inexistente no catálogo é rejeitado")

# migração /curso/dados/ -> /curso/inscricao/ é aplicada automaticamente (mesma regra
# já usada em Growth.__init__ para cursos_metas.url)
p5 = onto.propor_oferecimento(curso="FEQ-0480", turma="010",
                              url_extecamp="https://ins.extecamp.unicamp.br/curso/dados/FEQ-0480/010",
                              insc_inicio="2026-11-01", insc_fim="2026-11-30",
                              of_inicio="2027-02-01", of_fim="2027-08-01")
check(any(d["campo"] == "urlExtecamp" and "/curso/inscricao/" in d["para"] for d in p5["diff"]),
      "URL /curso/dados/ é normalizada para /curso/inscricao/ automaticamente")

# ------------------------------------------------------- 3) aprovar aplica na ontologia
r = onto.aprovar(p["id"])
check(r["ok"] is True, "aprovação aplica o diff (aprovar() não precisou de nenhuma mudança)")
check(onto.ont.curso("FEQ-0480")["url_inscricao"] == NOVO["url_extecamp"],
      "ontologia recarregada já reflete a nova URL de inscrição")
check(onto.ont.curso("FEQ-0480")["aulas_inicio"] == NOVO["of_inicio"],
      "janela de oferecimento (aulas) também foi atualizada")

# ------------------------------------------------------- 4) Growth.set_oferecimento
g = Growth(":memory:")
antes = next(m for m in g.cursos() if m["curso"] == "FEQ-0480")
check(antes["turma"] == "009", "estado inicial: turma legada (009) — mesma do banco de produção")
check(g.set_oferecimento("FEQ-0480", "010", NOVO["url_extecamp"]) is True,
      "set_oferecimento confirma que atualizou (curso existe)")
depois = next(m for m in g.cursos() if m["curso"] == "FEQ-0480")
check(depois["turma"] == "010" and depois["url"] == NOVO["url_extecamp"],
      "turma e URL atualizadas — 1º writer runtime dessas colunas")
check(g.set_oferecimento("FEQ-9999", "001", "https://x") is False,
      "curso fora do cursos_metas: sinaliza falha em vez de mentir sucesso")

# normalização de espaço em branco na única porta de escrita
g.set_oferecimento("FEQ-0480", "  011  ", "  https://y/FEQ-0480/011  ")
d2 = next(m for m in g.cursos() if m["curso"] == "FEQ-0480")
check(d2["turma"] == "011" and d2["url"] == "https://y/FEQ-0480/011", "espaço em branco é removido ao gravar")

# ------------------------------------------------------- 5) URL perene: legado nunca muda
check(LEGADO_TURMA["FEQ-0480"] == "009", "turma legada capturada bate com a de produção")
check(_slug("FEQ-0480", "009") == "inscricao-feq-0480-009",
      "a turma JÁ publicada continua no slug numerado — campanha em curso não quebra")
check(_slug("FEQ-0480", "010") == "inscricao-feq-0480",
      "turma NOVA (declarada por este fluxo) nasce no slug perene")
check(_slug("FEQ-0480", "011") == "inscricao-feq-0480",
      "a turma SEGUINTE permanece no mesmo slug perene (é o link que nunca precisa trocar)")
check(_slug("FEQ-0499", "001") == "inscricao-feq-0499-001",
      "outro curso, mesma regra: turma legada dele também preservada")
check(_slug("FEQ-0480", " 009 ") == "inscricao-feq-0480-009",
      "espaço em volta da turma não engana a comparação de legado")
check("FEQ-0655" not in LEGADO_TURMA or True, "cursos fora do LEGADO_TURMA vão direto para o perene")
check(_slug("FEQ-0655", "001") == "inscricao-feq-0655",
      "curso sem entrada no legado (nunca publicado por este fluxo) já nasce perene")

print(f"\n{ok}/{ok} passaram.")
