#!/usr/bin/env python3
"""
Migração ÚNICA: marca como LIDO todo alerta anterior à Central de Notificações.

`alertas.lido` nasceu com `DEFAULT 0` (spec 14, 11/08/2026) — correto para dado NOVO,
mas a coluna se aplicou retroativamente a toda linha já existente. `alertas` era, até
aqui, um log só de escrita (lido só por `alertas_nao_notificados`, que dispara o toque
externo ao operador e nunca marcava nada como visto) — meses de `envio_falhou`,
`escalonamento`, `manual` etc. viraram, de uma hora para outra, "não lido" na caixa de
entrada nova. O operador abriu a Central e viu 223 notificações no primeiro dia; a
maioria fala de um lead cujo estado já mudou há semanas (conferido: os `escalonamento`
mais antigos não têm nem `card_id`, coluna que também só passou a existir agora).

Corte: tudo criado ANTES do primeiro deploy da Central de Notificações vira lido. Alerta
gerado depois disso é, de fato, novo trabalho — e continua chegando normalmente.

Sem --aplicar é DRY-RUN.
"""
import sys
import sqlite3
import pathlib
import datetime

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

DB = pathlib.Path(__file__).resolve().parents[1] / "data" / "atena.db"
# Deploy da Central de Notificações (spec 14 §6/7) — não "agora": um valor fixo, para
# que rodar a migração de novo (ex.: outro ambiente) sempre corte no mesmo lugar.
CORTE = "2026-08-11T18:00:00"


def main(aplicar=False, db=None):
    con = sqlite3.connect(str(db or DB))
    con.row_factory = sqlite3.Row
    for mig in ("ALTER TABLE alertas ADD COLUMN lido INTEGER DEFAULT 0",
                "ALTER TABLE alertas ADD COLUMN lido_por TEXT",
                "ALTER TABLE alertas ADD COLUMN lido_ts TEXT",
                "ALTER TABLE alertas ADD COLUMN card_id TEXT"):
        try:
            con.execute(mig)
        except sqlite3.OperationalError:
            pass

    alvo = con.execute(
        "SELECT id, tipo, criado FROM alertas WHERE lido=0 AND criado<?", (CORTE,)).fetchall()
    from collections import Counter
    print(f"banco: {db or DB}\ncorte: {CORTE}")
    print(f"alertas anteriores ao corte, hoje 'não lido': {len(alvo)}")
    for tipo, n in Counter(a["tipo"] for a in alvo).most_common():
        print(f"  {tipo:18} {n}")
    depois = con.execute(
        "SELECT COUNT(*) FROM alertas WHERE lido=0 AND criado>=?", (CORTE,)).fetchone()[0]
    print(f"ficam não lidos (posteriores ao corte, trabalho real): {depois}")

    if aplicar:
        agora = datetime.datetime.now().isoformat()
        con.execute("UPDATE alertas SET lido=1, lido_por='backfill', lido_ts=?"
                    " WHERE lido=0 AND criado<?", (agora, CORTE))
        con.commit()
        print(f"\nAPLICADO: {len(alvo)} alertas marcados como lidos.")
    else:
        print("\nDRY-RUN. Use --aplicar para gravar.")
    con.close()
    return alvo


if __name__ == "__main__":
    import atena  # fuso BRT
    main(aplicar="--aplicar" in sys.argv,
         db=next((a for a in sys.argv[1:] if not a.startswith("--")), None))
