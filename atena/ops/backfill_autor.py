#!/usr/bin/env python3
"""
Migração ÚNICA: reconstrói `conversas.autor` do histórico (spec 18).

Até 04/08/2026 o banco não distinguia texto DIGITADO por humano de texto GERADO
pelo LLM — os dois entravam como papel='agente'. É exatamente a fala humana que
ensina o sistema (são resoluções de problema), então sem essa distinção o corpus
não consegue priorizá-la.

A reconstrução usa a auditoria, que sempre registrou QUEM disparou o envio:
  enviar_livre   -> humano digitou      -> autor = humano:<usuario>
  aprovar_enviar -> texto do LLM aprovado -> autor = llm  (ator 'auto' = sem revisão humana)
Casamento por (alvo == card_id) e diferença de tempo <= 2s, que é a folga entre
gravar a conversa e gravar a auditoria no mesmo request.

Mensagens que começam com "[template" são marcadas como template independentemente
da auditoria — o rótulo já diz o que são.

Sem --aplicar é DRY-RUN.
"""
import sys
import sqlite3
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

TOLERANCIA_S = 2
DB = pathlib.Path(__file__).resolve().parents[1] / "data" / "atena.db"


def main(aplicar=False, db=None):
    con = sqlite3.connect(str(db or DB))
    con.row_factory = sqlite3.Row
    plano = {}          # id da conversa -> autor

    # 1) templates: o próprio texto identifica
    for r in con.execute("SELECT id FROM conversas WHERE papel='agente' AND autor IS NULL "
                         "AND (texto LIKE '[template%' OR texto LIKE '[recuperação%')"):
        plano[r["id"]] = "template"

    # 2) casamento com a auditoria por card + janela de tempo
    for acao, molde in (("enviar_livre", "humano:{}"), ("aprovar_enviar", None)):
        for r in con.execute(
                "SELECT c.id, a.ator FROM conversas c JOIN auditoria a"
                "  ON a.alvo = c.card_id AND a.acao = ?"
                " AND abs(strftime('%s', a.ts) - strftime('%s', c.ts)) <= ?"
                " WHERE c.papel='agente' AND c.autor IS NULL", (acao, TOLERANCIA_S)):
            if r["id"] in plano:
                continue
            plano[r["id"]] = molde.format(r["ator"]) if molde else (
                "coordenacao" if r["ator"] == "coordenacao" else "llm")

    resumo = {}
    for a in plano.values():
        chave = a.split(":")[0] if a.startswith("humano") else a
        resumo[chave] = resumo.get(chave, 0) + 1
    total = con.execute("SELECT COUNT(*) n FROM conversas WHERE papel='agente'").fetchone()["n"]
    print(f"banco: {db or DB}\nmensagens do agente: {total}\na marcar: {len(plano)}")
    for k, v in sorted(resumo.items(), key=lambda x: -x[1]):
        print(f"  {k}: {v}")
    print(f"  (sem correspondência, seguem NULL): {total - len(plano)}")

    if aplicar:
        con.executemany("UPDATE conversas SET autor=? WHERE id=?",
                        [(a, i) for i, a in plano.items()])
        con.commit()
        print(f"\nAPLICADO: {len(plano)} mensagens marcadas.")
    else:
        print("\nDRY-RUN. Use --aplicar para gravar.")
    con.close()
    return plano


if __name__ == "__main__":
    import atena  # fuso BRT
    main(aplicar="--aplicar" in sys.argv,
         db=next((a for a in sys.argv[1:] if not a.startswith("--")), None))
