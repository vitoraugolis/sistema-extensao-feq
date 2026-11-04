#!/usr/bin/env python3
"""
Template feq_continuidade_inscricao: manda a cada inscrito o SEU link da Extecamp
(ok-inscricao_taxa.php?id1=…) para seguir com a inscrição.

  python3 -m atena.ops.continuidade_inscricao criar            # submete à Meta (PENDING)
  python3 -m atena.ops.continuidade_inscricao enviar envios.json [--valer]

envios.json: [{"card_id": "...", "link": "https://ins.extecamp..."}]. Sem --valer só
mostra o que sairia. Os links ficam fora do repositório (são individuais).
"""
import sys
import json
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

import atena.api.app as A  # carrega o .env com o parser do app (`source .env` trunca valores)
from atena.transporte.whatsapp import CORPO_CONTINUIDADE_INSCRICAO, montar_params

TPL = "feq_continuidade_inscricao"
EXTECAMP = "https://ins.extecamp.unicamp.br/"


def enviar(envios, valer):
    st = A.WA.templates_status(forcar=True)
    if valer and (st or {}).get(TPL) != "APPROVED":
        sys.exit(f"{TPL} ainda não aprovado na Meta: {(st or {}).get(TPL)}")
    for e in envios:
        lead = A.EST.get_lead(e["card_id"])
        if not lead or not e.get("link", "").startswith(EXTECAMP):
            print("PULADO (lead inexistente ou link fora da Extecamp):", e["card_id"]); continue
        if lead.get("optout"):
            print("PULADO (opt-out):", e["card_id"]); continue
        primeiro = ((lead.get("nome") or "").split() or [""])[0].capitalize()
        params = montar_params(TPL, {**lead, "nome": primeiro, "link": e["link"]}, A.ONTO.ont)
        if params is None:
            print("PULADO (campo vazio):", e["card_id"]); continue
        if not valer:
            print("SAIRIA:", lead["telefone"], params); continue
        r = A.WA.enviar_template(lead["telefone"], TPL, params=params)
        if r.get("ok"):
            A.EST.add_conversa(e["card_id"], "agente", f"[template {TPL}] link de continuidade",
                               wamid=r.get("wamid"), status="enviado", autor="template",
                               destino=lead["telefone"])
        A.EST.log_acao("sistema", f"link de continuidade da inscrição: "
                                  f"{'enviado' if r.get('ok') else 'falhou'}", e["card_id"])
        print("ENVIADO" if r.get("ok") else "FALHOU", e["card_id"], r.get("wamid") or r.get("resp"))


if __name__ == "__main__":
    if sys.argv[1:2] == ["criar"]:
        r = A.WA.criar_template(TPL, "UTILITY", CORPO_CONTINUIDADE_INSCRICAO, exemplos=[
            "Mariana", "Engenharia de Processos Químicos (FEQ-1000)",
            "https://ins.extecamp.unicamp.br/inscricao/ok-inscricao_taxa.php?id1=abc123"])
        print(json.dumps(r, ensure_ascii=False, indent=2))
    elif sys.argv[1:2] == ["enviar"]:
        enviar(json.loads(pathlib.Path(sys.argv[2]).read_text()), "--valer" in sys.argv)
    else:
        sys.exit(__doc__)
