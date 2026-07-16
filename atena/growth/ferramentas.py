#!/usr/bin/env python3
"""
Atena · Growth — ferramentas p/ agentes de IA (Pilar 3, spec 16 §4).
Contrato de tool calling (formato Claude input_schema) + despachante com os
MESMOS guardrails da spec 15: toda escrita sem GROWTH_AUTO_APPLY=1 vira
sugestão em acoes_orcamento (aplicado=0) — o agente propõe, o humano aprova.
Uso futuro: passar FERRAMENTAS ao LLM e rotear tool_use -> executar().
"""
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from atena.growth import pacing

PCT_MAX = 50   # clamp por chamada: agente nenhum dobra verba de uma vez

FERRAMENTAS = [
    {"name": "listar_campanhas",
     "description": "Lista campanhas Meta da conta (id, nome, status, verba diária, objetivo).",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "ritmo_cursos",
     "description": "Ritmo de inscrições por curso vs. meta de segurança: inscritos, faltam, "
                    "velocidade necessária vs. real (7d), status atrasado/no_ritmo/adiantado.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "custo_por_matricula",
     "description": "Por curso: gasto de mídia, matrículas offline confirmadas, custo por "
                    "matrícula e ROAS (valor das matrículas ÷ gasto), na janela de N dias.",
     "input_schema": {"type": "object", "properties": {
         "dias": {"type": "integer", "description": "janela em dias (default 30)"}}}},
    {"name": "analise_lp",
     "description": "Funil de leitura das landing pages: alcance e tempo por seção, ponto de "
                    "maior abandono, taxas CTA->modal->envio. Use para propor mudanças de copy.",
     "input_schema": {"type": "object", "properties": {
         "curso": {"type": "string", "description": "ex.: FEQ-0480 (omitir = todos)"},
         "dias": {"type": "integer", "description": "janela em dias (default 30)"}}}},
    {"name": "aumentar_orcamento",
     "description": "Altera a verba diária de uma campanha/adset em ±N% (clamp ±50%). "
                    "Sem GROWTH_AUTO_APPLY=1 registra a sugestão em vez de aplicar.",
     "input_schema": {"type": "object", "required": ["objeto_id", "percentual"],
                      "properties": {"objeto_id": {"type": "string"},
                                     "percentual": {"type": "number"}}}},
    {"name": "pausar_ad",
     "description": "Pausa um anúncio/adset/campanha. Motivo é obrigatório (auditoria). "
                    "Sem GROWTH_AUTO_APPLY=1 registra a sugestão em vez de aplicar.",
     "input_schema": {"type": "object", "required": ["ad_id", "motivo"],
                      "properties": {"ad_id": {"type": "string"},
                                     "motivo": {"type": "string"}}}},
]


def custo_por_matricula(g, dias=30):
    out = {}
    for c in g.cursos():
        curso = c["curso"]
        gasto = sum(r["gasto"] for r in g.desempenho(dias=dias) if r["curso"] == curso)
        mats = [e for e in g.eventos(tipo="matricula_confirmada") if e["curso"] == curso]
        valor = sum(e["valor"] or 0 for e in mats)
        out[curso] = {"gasto": round(gasto, 2), "matriculas": len(mats),
                      "custo_por_matricula": round(gasto / len(mats), 2) if mats else None,
                      "roas": round(valor / gasto, 2) if gasto and valor else None}
    return out


def _ritmo(g):
    import datetime
    hoje = datetime.date.today()
    out = {}
    for c in g.cursos():   # prazo = cache sincronizado pela passada diária de pacing
        prazo = datetime.date.fromisoformat(c["prazo"]) if c.get("prazo") else None
        out[c["curso"]] = pacing.calcular(c["meta_seg"], g.inscritos(c["curso"]),
                                          prazo, g.vel_7d(c["curso"]), hoje)
    return out


def aumentar_orcamento(g, ads, objeto_id, percentual):
    pct = max(-PCT_MAX, min(PCT_MAX, float(percentual)))
    det = f"verba de {objeto_id}: {pct:+.0f}%" + ("" if pct == percentual else
                                                  f" (pedido {percentual:+.0f}% clampado)")
    if not (pacing.AUTO and ads.ativo):
        g.add_acao("orcamento_agente", objeto_id, "Sugestão — " + det, aplicado=0)
        return {"ok": True, "aplicado": False, "percentual": pct, "detalhe": det}
    atual = ads.orcamento(objeto_id)
    if not atual:
        return {"ok": False, "motivo": "sem verba diária legível (CBO no nível acima?)"}
    novo = max(pacing.ORCAMENTO_MIN, int(atual * (1 + pct / 100)))
    ok = ads.definir_orcamento(objeto_id, novo).get("ok", False)
    g.add_acao("orcamento_agente", objeto_id, f"{det}: {atual} -> {novo} centavos", aplicado=ok)
    return {"ok": ok, "aplicado": ok, "de": atual, "para": novo, "percentual": pct}


def pausar_ad(g, ads, ad_id, motivo):
    if not (motivo or "").strip():
        return {"ok": False, "motivo": "motivo é obrigatório"}
    if not (pacing.AUTO and ads.ativo):
        g.add_acao("pausa_agente", ad_id, "Sugestão — pausar: " + motivo, aplicado=0)
        return {"ok": True, "aplicado": False, "detalhe": motivo}
    ok = ads.pausar(ad_id).get("ok", False)
    g.add_acao("pausa_agente", ad_id, motivo, aplicado=ok)
    return {"ok": ok, "aplicado": ok}


def executar(nome, args, g, ads, est=None):
    """Roteia um tool_use do LLM. Erro vira {ok: False} — nunca exceção ao agente."""
    args = args or {}
    try:
        if nome == "listar_campanhas":
            return {"ok": True, "campanhas": ads.campanhas()}
        if nome == "ritmo_cursos":
            return {"ok": True, "ritmos": _ritmo(g)}
        if nome == "custo_por_matricula":
            return {"ok": True, "cursos": custo_por_matricula(g, int(args.get("dias") or 30))}
        if nome == "analise_lp":
            return {"ok": True, **g.lp_analytics(args.get("curso") or None,
                                                 int(args.get("dias") or 30))}
        if nome == "aumentar_orcamento":
            return aumentar_orcamento(g, ads, args["objeto_id"], args["percentual"])
        if nome == "pausar_ad":
            return pausar_ad(g, ads, args["ad_id"], args.get("motivo", ""))
        return {"ok": False, "motivo": f"ferramenta desconhecida: {nome}"}
    except (KeyError, TypeError, ValueError) as e:
        return {"ok": False, "motivo": f"argumentos inválidos: {e}"}
