#!/usr/bin/env python3
"""
Funil comercial de professores (módulo restrito).

O que este teste protege, em ordem de gravidade:
 1. ACESSO. O módulo é do master. Em produção existem OUTROS supervisores
    (Miguel, extfeq) — se acesso viesse de papel, eles veriam a prospecção toda.
    Só a concessão nominal do master abre a porta, e ela é revogável.
 2. Que o guard bloqueie por PREFIXO de rota, não rota a rota: rota nova sob
    /api/professores tem que nascer fechada, senão a próxima que alguém escrever
    vira vazamento silencioso.
 3. Que professor NÃO seja lead: tabelas separadas, sem contaminar o funil de
    captação nem as regras de LGPD/nutrição que valem para leads.
"""
import os
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

os.environ["ATENA_DEV"] = "1"
os.environ["ATENA_MASTER"] = "vitor"

from atena.core.estado import Estado, FASES_PROFESSOR
from atena.core import auth

ok = 0


def check(c, msg):
    global ok
    assert c, "FALHOU: " + msg
    ok += 1
    print("  ok:", msg)


est = Estado(":memory:")
est.add_usuario("vitor", auth.hash_senha("secreta123"), "supervisor")
est.add_usuario("miguel", auth.hash_senha("secreta123"), "supervisor")   # OUTRO supervisor
est.add_usuario("vinicius", auth.hash_senha("secreta123"), "operador")

# ------------------------------------------------------------------ 1) acesso
check(auth.eh_master("vitor"), "vitor é o master")
check(not auth.eh_master("miguel"), "outro supervisor NÃO é master")
check(auth.pode_modulo(est, "vitor", "professores"), "master entra sem precisar de concessão")
check(not auth.pode_modulo(est, "miguel", "professores"),
      "SUPERVISOR sem concessão NÃO entra — acesso não vem de papel (é o ponto do módulo)")
check(not auth.pode_modulo(est, "vinicius", "professores"), "operador sem concessão não entra")

est.conceder_modulo("vinicius", "professores", "vitor")
check(auth.pode_modulo(est, "vinicius", "professores"), "concessão nominal do master abre o acesso")
check(not auth.pode_modulo(est, "miguel", "professores"),
      "conceder a um não abre para os outros")

concedidos = est.listar_acesso_modulo("professores")
check([c["usuario"] for c in concedidos] == ["vinicius"], "concessão fica listada")
check(concedidos[0]["concedido_por"] == "vitor", "fica registrado QUEM concedeu (auditoria)")

check(est.revogar_modulo("vinicius", "professores"), "revogação funciona")
check(not auth.pode_modulo(est, "vinicius", "professores"), "revogado perde o acesso na hora")
check(not est.revogar_modulo("vinicius", "professores"), "revogar de novo é no-op, não erro")

# o guard barra por PREFIXO: qualquer rota futura sob esses caminhos já nasce fechada
check(auth.MODULOS_RESTRITOS.get("/api/professores") == "professores",
      "prefixo /api/professores mapeado no guard")
check(auth.MODULOS_RESTRITOS.get("/api/modulo-acesso") == "professores",
      "a própria gestão de acesso é restrita (senão dava para se autoconceder)")

# ------------------------------------------------------------------ 2) fases
check(FASES_PROFESSOR[0] == "Lead" and FASES_PROFESSOR[-1] == "Inviável",
      "as 8 fases na ordem pedida")
check(len(FASES_PROFESSOR) == 8 and "Em negociação" in FASES_PROFESSOR
      and "Revisitar" in FASES_PROFESSOR, "todas as fases do funil comercial presentes")

# ------------------------------------------------------------------ 3) CRUD
pid = est.add_professor(nome="Ana Ribeiro", telefone="(19) 99999-1234",
                        email="ana@unicamp.br", instituto="FEQ",
                        cursos="FEQ-0480,FEQ-1000", anotacoes="Indicada pelo Flavio.",
                        servicos="Aula gravada + mentoria")
p = est.get_professor(pid)
check(p["nome"] == "Ana Ribeiro" and p["fase"] == "Lead", "nasce na 1ª fase")
check(p["telefone"] == "5519999991234",
      "telefone normalizado para E.164 — é o que faz o link do WhatsApp abrir")
check(p["cursos"] == "FEQ-0480,FEQ-1000", "vários cursos no mesmo professor")

est.set_professor(pid, fase="Em negociação", proposta="R$ 400/hora-aula",
                  ultimo_contato="2026-08-10")
p = est.get_professor(pid)
check(p["fase"] == "Em negociação" and p["proposta"] == "R$ 400/hora-aula",
      "mover de fase e registrar proposta")
check(p["atualizado"] >= p["criado"], "atualizado avança")

check(not est.set_professor(pid, campo_inventado="x"),
      "campo fora da lista branca é ignorado (não injeta coluna via API)")

# professor NÃO é lead: um não aparece na listagem do outro
est.upsert_lead("lead-1", nome="Lead Qualquer", telefone="5519988887777", curso="FEQ-0480")
check(len(est.listar_professores()) == 1, "lead não entra no funil de professores")
check(len(est.listar_leads()) == 1 and est.listar_leads()[0]["card_id"] == "lead-1",
      "professor não entra no funil de leads (nem em captação/nutrição/LGPD)")

check(est.del_professor(pid) and est.get_professor(pid) is None, "exclusão funciona")

print(f"\n{ok}/{ok} passaram.")
