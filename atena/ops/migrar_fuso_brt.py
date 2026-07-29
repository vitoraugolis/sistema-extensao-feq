#!/usr/bin/env python3
"""
Migração ÚNICA: timestamps gravados em UTC -> BRT (America/Sao_Paulo).

CONTEXTO
--------
O VPS roda com o relógio em Etc/UTC. Como o código usava `datetime.now()` ingênuo,
tudo que ele gravou ficou 3h adiantado (prova: a cadência automática, que só dispara
dentro do horário comercial BRT, aparece no banco às 11:00 = 08:00 BRT).

A partir de agora `atena/__init__.py` fixa o fuso do processo em BRT, então os
registros NOVOS já nascem certos. Este script conserta os ANTIGOS.

FRONTEIRA (importante)
----------------------
O banco tem registros de duas eras:
  • até 2026-07-07T12:23:22  -> escritos na máquina local (macOS, já em BRT) => NÃO mexer
  • a partir daí             -> escritos no VPS em UTC                       => -3h
12:23:22 de 07/07 é quando o systemd subiu o serviço no VPS pela 1ª vez
(`journalctl -u atena | head`), e ele rodou contínuo daí em diante.

SEGURANÇA
---------
• Sem `--aplicar` é DRY-RUN: só relata o que mudaria.
• Só mexe em colunas TEXT cujos valores casam com ISO 'AAAA-MM-DDTHH:MM:SS'.
  Colunas só-data ('AAAA-MM-DD') ficam de fora de propósito — deslocar 3h mudaria o dia.
• Faça backup do .db antes e rode com o serviço parado (evita escrita concorrente).
"""
import re
import sys
import sqlite3
import pathlib

CORTE = "2026-07-07T12:23:22"      # 1º start do serviço no VPS; antes disso é BRT local
HORAS = -3                          # UTC -> BRT
ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")
DB = pathlib.Path(__file__).resolve().parents[1] / "data" / "atena.db"


def colunas_de_timestamp(con):
    """Descobre sozinho as colunas com data-hora ISO — não confia em lista escrita à mão."""
    achadas = []
    tabelas = [r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
    for t in tabelas:
        for col in [r[1] for r in con.execute(f"PRAGMA table_info({t})")]:
            amostra = con.execute(
                f'SELECT "{col}" FROM "{t}" WHERE "{col}" IS NOT NULL AND "{col}" != "" LIMIT 30'
            ).fetchall()
            vals = [r[0] for r in amostra if isinstance(r[0], str)]
            if vals and all(ISO_RE.match(v) for v in vals):
                achadas.append((t, col))
    return achadas


def main(aplicar=False, db=DB):
    con = sqlite3.connect(str(db))
    con.row_factory = sqlite3.Row
    alvos = colunas_de_timestamp(con)
    print(f"banco: {db}\ncorte: >= {CORTE}  (antes disso é BRT local — preservado)\n")
    total = 0
    for tab, col in alvos:
        n = con.execute(f'SELECT COUNT(*) c FROM "{tab}" WHERE "{col}" >= ?', (CORTE,)).fetchone()["c"]
        antes = con.execute(f'SELECT COUNT(*) c FROM "{tab}" WHERE "{col}" < ?', (CORTE,)).fetchone()["c"]
        total += n
        print(f"  {tab}.{col}: {n} a ajustar, {antes} preservado(s)")
        if aplicar and n:
            # datetime() devolve com espaço; recolocamos o 'T' para manter o formato ISO
            con.execute(
                f'UPDATE "{tab}" SET "{col}" = replace(datetime("{col}", ?), " ", "T") '
                f'WHERE "{col}" >= ?', (f"{HORAS} hours", CORTE))
    if aplicar:
        con.commit()
        print(f"\nAPLICADO: {total} valores deslocados em {HORAS}h.")
    else:
        print(f"\nDRY-RUN: {total} valores seriam deslocados em {HORAS}h. Use --aplicar para valer.")
    con.close()
    return total


if __name__ == "__main__":
    main(aplicar="--aplicar" in sys.argv)
