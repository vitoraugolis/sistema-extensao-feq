#!/usr/bin/env python3
"""
Rastreia leads que receberam link de inscrição ERRADO e reenvia o correto por template.

Origem: 6 leads do FEQ-0480 receberam .../curso/inscricao/FEQ-0480/001 em vez de /009.
O agente recebia a URL /curso/dados/ e tinha de convertê-la à mão — errou a turma. O link
manda o lead ao formulário de OUTRA turma, então a inscrição simplesmente não acontece.

A varredura é GENÉRICA: compara toda URL da Extecamp enviada contra a âncora do curso
(mesma normalização do Revisor). Serve para qualquer curso, não só para este incidente.

  python3 -m atena.ops.corrigir_link              # dry-run: só lista quem foi afetado
  python3 -m atena.ops.corrigir_link --aplicar    # envia o template de correção

Sem --aplicar não manda nada. Não reenvia para quem já recebeu a correção.
"""
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

import atena  # fuso BRT
from atena.core.estado import Estado
from atena.ontologia.servico import Ontologia
from atena.revisor.ontologico import URL_EXTECAMP_RE, _url_norm

MARCA = "[template feq_link_corrigido]"
TEMPLATE = "feq_link_corrigido"


def afetados(est, ont):
    """[{card_id, lead, curso, errados, correto}] — quem recebeu link fora da base."""
    out = []
    for l in est.listar_leads():
        curso = l.get("curso") or ""
        alvo = ont.ancoras(curso) if curso else None
        if not alvo or not alvo.get("urls"):
            continue
        correto = (ont.curso(curso) or {}).get("url_inscricao") or ""
        errados, ja_corrigido = [], False
        for m in est.historico(l["card_id"]):
            if m.get("papel") != "agente":
                continue
            txt = m.get("texto") or ""
            if MARCA in txt:
                ja_corrigido = True
            for u in URL_EXTECAMP_RE.finditer(txt):
                url = u.group(0).rstrip(").,;*]")
                if _url_norm(url) not in alvo["urls"]:
                    errados.append(url)
        if errados and not ja_corrigido:
            out.append({"card_id": l["card_id"], "lead": l.get("nome") or l["card_id"],
                        "telefone": l.get("telefone"), "curso": curso,
                        "errados": sorted(set(errados)), "correto": correto,
                        "optout": bool(l.get("optout"))})
    return out


def main(aplicar=False, db=None):
    est = Estado(db) if db else Estado()
    ont = Ontologia()
    from atena.transporte.whatsapp import WhatsApp, montar_params
    wa = WhatsApp(est, None)

    lista = afetados(est, ont)
    print(f"leads que receberam link errado: {len(lista)}")
    for a in lista:
        print(f"  {a['curso']}  {a['lead']} ({a['telefone']})"
              + ("  [OPT-OUT: não recebe]" if a["optout"] else ""))
        for e in a["errados"]:
            print(f"      errado : {e}")
        print(f"      correto: {a['correto']}")

    if not aplicar:
        print("\nDRY-RUN. Use --aplicar para enviar a correção.")
        return lista

    st = wa.templates_status()
    if st is not None and st.get(TEMPLATE) != "APPROVED":
        print(f"\nABORTADO: template '{TEMPLATE}' está {st.get(TEMPLATE) or 'inexistente'} "
              f"na Meta — só envia depois de APPROVED.")
        return lista

    enviados = 0
    for a in lista:
        if a["optout"]:
            continue
        lead = est.get_lead(a["card_id"])
        params = montar_params(TEMPLATE, lead, ont)
        if not params:
            print(f"  ! {a['lead']}: parâmetro vazio, não enviado")
            continue
        r = wa.enviar_template(lead["telefone"], TEMPLATE, params=params)
        if r.get("ok"):
            est.add_conversa(a["card_id"], "agente", f"{MARCA} {a['correto']}",
                             wamid=r.get("wamid"), status="enviado", autor="template",
                             destino=lead["telefone"])
            est.log_acao("correcao_link", f"link corrigido reenviado ({a['curso']})", a["card_id"])
            enviados += 1
        else:
            print(f"  ! {a['lead']}: falhou — {r}")
    print(f"\nAPLICADO: {enviados} correção(ões) enviada(s).")
    return lista


if __name__ == "__main__":
    main(aplicar="--aplicar" in sys.argv,
         db=next((a for a in sys.argv[1:] if not a.startswith("--")), None))
