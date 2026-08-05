#!/usr/bin/env python3
"""
Migração ÚNICA: reconstrói `conversas.erro_codigo` e `destino` das falhas antigas.

Sem o código do erro, a retentativa (transporte/reenvio.py) trata toda falha como
"desconhecida" e retenta o MESMO número com outro template — que é exatamente o que
não funciona nos 131026, onde o problema é o número, não o texto.

O código não era gravado até 05/08/2026, mas o alerta guardou o título do erro da Meta,
que mapeia 1:1 para o código. `destino` recebe o telefone atual do lead: é a mesma linha
telefônica do envio original (só muda a notação, que a migração de E.164 normalizou).

Sem --aplicar é DRY-RUN.
"""
import sys
import sqlite3
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

DB = pathlib.Path(__file__).resolve().parents[1] / "data" / "atena.db"

# título que a Meta manda no webhook de status -> código
TITULOS = [("undeliverable", 131026),
           ("healthy ecosystem", 131049),
           ("experiment", 130472)]


def main(aplicar=False, db=None):
    con = sqlite3.connect(str(db or DB))
    con.row_factory = sqlite3.Row
    for mig in ("ALTER TABLE conversas ADD COLUMN erro_codigo INTEGER",
                "ALTER TABLE conversas ADD COLUMN destino TEXT"):
        try:
            con.execute(mig)      # o app também cria; aqui garante rodar antes dele
        except sqlite3.OperationalError:
            pass

    # código por lead: o alerta mais recente daquele card manda
    codigo_de = {}
    for a in con.execute("SELECT texto FROM alertas WHERE tipo='envio_falhou' ORDER BY id"):
        txt = a["texto"] or ""
        cid = txt.split(" lead ", 1)[-1].split(" ", 1)[0] if " lead " in txt else None
        if not cid:
            continue
        for chave, cod in TITULOS:
            if chave in txt:
                codigo_de[cid] = cod
                break

    plano = []
    for r in con.execute(
            "SELECT c.id, c.card_id, l.telefone FROM conversas c JOIN leads l ON l.card_id=c.card_id"
            " WHERE c.papel='agente' AND c.status='falhou' AND c.erro_codigo IS NULL"):
        plano.append((codigo_de.get(r["card_id"]), r["telefone"], r["id"]))

    achados = sum(1 for c, _, _ in plano if c)
    print(f"banco: {db or DB}\nfalhas sem código: {len(plano)}  ·  com alerta correspondente: {achados}")
    for cod in (131026, 131049, 130472, None):
        n = sum(1 for c, _, _ in plano if c == cod)
        if n:
            print(f"  {cod or '(sem alerta — ficam desconhecidas)'}: {n}")

    if aplicar:
        con.executemany("UPDATE conversas SET erro_codigo=?, destino=? WHERE id=?", plano)
        con.commit()
        print(f"\nAPLICADO: {len(plano)} falhas anotadas.")
    else:
        print("\nDRY-RUN. Use --aplicar para gravar.")
    con.close()
    return plano


if __name__ == "__main__":
    import atena  # fuso BRT
    main(aplicar="--aplicar" in sys.argv,
         db=next((a for a in sys.argv[1:] if not a.startswith("--")), None))
