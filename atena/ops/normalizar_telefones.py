#!/usr/bin/env python3
"""
Migração ÚNICA: telefones de lead em formato internacional (E.164 só com dígitos).

Até 05/08/2026 o telefone era gravado como veio do formulário Meta ("(19) 99999-0001",
"12997631036"). A Meta ACEITA enviar assim — 416 mensagens foram entregues a números
sem o código do país —, mas o link `wa.me` da Central não abre a conversa sem ele.

A regra é por COMPRIMENTO, nunca por prefixo: o DDD 55 existe (Santa Maria/RS), então
"5599999999" é um fixo brasileiro SEM país, não um número já normalizado. Números que
não caem em 10/11 dígitos ficam intocados — há ESTRANGEIRO na base (+351, Portugal) e
prefixar 55 nele destruiria o número.

A dedupe (`_tel_key`) reduz a DDD+8 dígitos, então nada aqui muda quem casa com quem.

Sem --aplicar é DRY-RUN.
"""
import sys
import sqlite3
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from atena.core.estado import normalizar_e164

DB = pathlib.Path(__file__).resolve().parents[1] / "data" / "atena.db"


def main(aplicar=False, db=None):
    con = sqlite3.connect(str(db or DB))
    con.row_factory = sqlite3.Row
    plano, intocados = [], []

    for r in con.execute("SELECT card_id, nome, telefone FROM leads WHERE telefone<>''"):
        novo = normalizar_e164(r["telefone"])
        if novo != (r["telefone"] or ""):
            plano.append((novo, r["card_id"], r["telefone"]))
        else:
            intocados.append(r["telefone"])

    print(f"banco: {db or DB}\nleads com telefone: {len(plano) + len(intocados)}")
    print(f"a normalizar: {len(plano)}")
    for novo, card, velho in plano[:8]:
        print(f"  {velho:<22} -> {novo}   ({card})")
    if len(plano) > 8:
        print(f"  … e mais {len(plano) - 8}")

    # o que não muda: já internacional, ou comprimento fora do padrão BR (estrangeiro/lixo)
    estranhos = [t for t in intocados if not (len(t) in (12, 13) and t.startswith("55"))]
    if estranhos:
        print(f"\nintocados por não serem BR (confira): {estranhos}")

    if aplicar:
        con.executemany("UPDATE leads SET telefone=? WHERE card_id=?",
                        [(n, c) for n, c, _ in plano])
        con.commit()
        print(f"\nAPLICADO: {len(plano)} telefones normalizados.")
    else:
        print("\nDRY-RUN. Use --aplicar para gravar.")
    con.close()
    return plano


if __name__ == "__main__":
    import atena  # fuso BRT
    main(aplicar="--aplicar" in sys.argv,
         db=next((a for a in sys.argv[1:] if not a.startswith("--")), None))
