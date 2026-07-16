#!/usr/bin/env python3
"""
Atena · Growth — conversões offline de matrícula (Pilar 2, spec 16 §3).
O pagamento acontece na Extecamp; quem sabe quem matriculou é o operador.
Este CLI fecha o loop: telefone/e-mail do matriculado -> hash SHA-256 ->
CAPI Purchase (com valor) -> a campanha aprende quem realmente converte.

  python3 -m atena.growth.matriculas add "+5519988880001" --curso FEQ-0480 --valor 3900
  python3 -m atena.growth.matriculas add aluno@email.com --curso FEQ-1000 --valor 4200
  python3 -m atena.growth.matriculas csv matriculados.csv
       # colunas aceitas: telefone|phone, email, curso, valor (telefone OU email obrigatório)
"""
import sys
import csv
import json
import pathlib
import argparse

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from atena.growth import funil


def processar_matricula(g, ads, est, contato, curso=None, valor=None, agora=None):
    """Uma matrícula: casa o lead (telefone, tolerante ao 9º dígito), promove a
    fase Matriculado e dispara Purchase. E-mail sem lead local ainda envia a
    conversão (match pela Meta) — o hash acontece em meta_ads._user_data."""
    contato = (contato or "").strip()
    email = contato if "@" in contato else None
    telefone = None if email else contato
    card_id = est.lead_por_telefone(telefone) if telefone else None
    lead = est.get_lead(card_id) if card_id else None
    curso = curso or (lead or {}).get("curso") or ""
    if not curso:
        return {"ok": False, "motivo": f"sem curso para {contato} — use --curso"}
    if not (telefone or email):
        return {"ok": False, "motivo": "informe telefone ou e-mail"}
    if lead:
        est.set_lead(card_id, fase="Matriculado")
        est.log_acao("operador", f"matrícula confirmada (offline) · {curso} · R${valor or 0}", card_id)
    r = funil.registrar(g, ads, est, card_id or f"offline-{(telefone or email)[-8:]}",
                        "matricula_confirmada", curso=curso, agora=agora,
                        valor=valor, telefone=telefone, email=email)
    return {"ok": r.get("ok", False), "card_id": card_id, "curso": curso,
            "capi": r.get("capi"), "casou_lead": bool(lead)}


def _linha_csv(row):
    r = {(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}
    contato = r.get("telefone") or r.get("phone") or r.get("email") or ""
    valor = r.get("valor")
    return contato, (r.get("curso") or None), (float(valor) if valor else None)


def main(argv):
    ap = argparse.ArgumentParser(prog="matriculas", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_add = sub.add_parser("add", help="uma matrícula (telefone ou e-mail)")
    p_add.add_argument("contato")
    p_add.add_argument("--curso")
    p_add.add_argument("--valor", type=float)
    p_csv = sub.add_parser("csv", help="lote via CSV (telefone|email[,curso][,valor])")
    p_csv.add_argument("arquivo")
    args = ap.parse_args(argv)

    from atena.core.estado import Estado
    from atena.growth.db import Growth
    from atena.growth.meta_ads import MetaAds
    est, g, ads = Estado(), Growth(), MetaAds()

    if args.cmd == "add":
        return [processar_matricula(g, ads, est, args.contato, args.curso, args.valor)]
    out = []
    with open(args.arquivo, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            contato, curso, valor = _linha_csv(row)
            out.append(processar_matricula(g, ads, est, contato, curso, valor))
    return out


if __name__ == "__main__":
    for r in main(sys.argv[1:]):
        print(json.dumps(r, ensure_ascii=False))
