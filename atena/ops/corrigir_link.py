#!/usr/bin/env python3
"""
Rastreia leads que receberam link de inscrição ERRADO e reenvia o correto por template.

Origem: 6 leads do FEQ-0480 receberam .../curso/inscricao/FEQ-0480/001 em vez de /009.
O agente recebia a URL /curso/dados/ e tinha de convertê-la à mão — errou a turma. O link
manda o lead ao formulário de OUTRA turma, então a inscrição simplesmente não acontece.

A varredura é GENÉRICA: acusa toda URL da Extecamp que aponte para uma combinação
curso+turma INEXISTENTE no catálogo. Não basta divergir do curso do card — o agente faz
cross-sell e o curso do card muda quando o lead troca de interesse (2 de 8 'afetados' na
primeira versão eram falso positivo, incluindo um link enviado à mão pelo operador).

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


def _urls_validas(ont):
    """TODAS as URLs reais do catálogo. O link errado é o que aponta para uma combinação
    curso+turma que NÃO EXISTE — não basta divergir do curso atual do card: o agente pode
    ter linkado outro curso do portfólio legitimamente (cross-sell), e o curso do card
    muda quando o lead troca de interesse. Comparar só com o curso atual acusaria links
    corretos (conferido em produção: 2 dos 8 'afetados' eram falso positivo)."""
    return {u for c in ont.cursos() for u in (ont.ancoras(c) or {}).get("urls", ())}


def afetados(est, ont):
    """[{card_id, lead, curso, errados, correto}] — quem recebeu link inexistente."""
    validas = _urls_validas(ont)
    out = []
    for l in est.listar_leads():
        errados, ja_corrigido = [], False
        for m in est.historico(l["card_id"]):
            if m.get("papel") != "agente":
                continue
            txt = m.get("texto") or ""
            if MARCA in txt:
                ja_corrigido = True
            for u in URL_EXTECAMP_RE.finditer(txt):
                url = u.group(0).rstrip(").,;*]")
                if _url_norm(url) not in validas:
                    errados.append(url)
        if not errados or ja_corrigido:
            continue
        # o link certo é o do curso que o link QUEBRADO tentava alcançar; sem código
        # reconhecível, cai no curso do card
        alvo_cod = next((c for e in errados for c in ont.cursos()
                         if c.lower() in e.lower()), l.get("curso") or "")
        info = ont.curso(alvo_cod) or {}
        out.append({"card_id": l["card_id"], "lead": l.get("nome") or l["card_id"],
                    "telefone": l.get("telefone"), "curso": alvo_cod,
                    "errados": sorted(set(errados)),
                    "correto": info.get("url_inscricao") or "",
                    "optout": bool(l.get("optout"))})
    return out


def main(aplicar=False, db=None, est=None, ont=None):
    est = est or (Estado(db) if db else Estado())
    ont = ont or Ontologia()
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
        # o curso do template é o do LINK quebrado, não o do card (podem divergir)
        params = montar_params(TEMPLATE, {**lead, "curso": a["curso"]}, ont)
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
