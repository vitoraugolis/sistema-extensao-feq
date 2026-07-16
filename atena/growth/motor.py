#!/usr/bin/env python3
"""
Atena · Growth — ponto de entrada do motor (cron, spec 15 §8).
  python3 -m atena.growth.motor status                 # ritmo por curso (sem efeito colateral)
  python3 -m atena.growth.motor pacing                 # 1×/dia: insights + ritmo + CPL + realocação
  python3 -m atena.growth.motor recuperar              # a cada 30 min: régua de checkout abandonado
  python3 -m atena.growth.motor evento <card_id> <tipo>  # inicio_inscricao|boleto_gerado|inscricao_confirmada
Sem tokens no ambiente tudo roda em dry-run. Cron sugerido:
  30 7 * * *   pacing      ·      */30 * * * *   recuperar
"""
import sys
import json
import pathlib
import datetime

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from atena.core.estado import Estado
from atena.ontologia.servico import Ontologia
from atena.transporte.whatsapp import WhatsApp
from atena.growth.db import Growth
from atena.growth.meta_ads import MetaAds
from atena.growth import pacing, funil


def _status(g, ont):
    out = {}
    hoje = datetime.date.today()
    for c in g.cursos():
        try:
            fim = ont.curso(c["curso"]).get("inscricoes_fim") or c.get("prazo")
        except Exception:
            fim = c.get("prazo")
        prazo = datetime.date.fromisoformat(fim) if fim else None
        out[f"{c['curso']}/{c['turma']}"] = pacing.calcular(
            c["meta_seg"], g.inscritos(c["curso"]), prazo, g.vel_7d(c["curso"]), hoje)
    return {"ritmos": out, "checkouts_pendentes": len(g.checkouts_pendentes()),
            "acoes_recentes": g.listar_acoes(5)}


def main(argv):
    cmd = argv[0] if argv else "status"
    est, g, ads, ont = Estado(), Growth(), MetaAds(), Ontologia()
    if cmd == "status":
        return _status(g, ont)
    if cmd == "pacing":
        return pacing.passada(g, ads, ont=ont, est=est)
    if cmd == "recuperar":
        return funil.recuperar(g, est, ont, WhatsApp(est, None))
    if cmd == "evento" and len(argv) >= 3:
        return funil.registrar(g, ads, est, argv[1], argv[2])
    return {"erro": f"uso: motor.py status|pacing|recuperar|evento <card_id> <tipo> (recebi: {argv})"}


if __name__ == "__main__":
    print(json.dumps(main(sys.argv[1:]), ensure_ascii=False, indent=2, default=str))
