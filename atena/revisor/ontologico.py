#!/usr/bin/env python3
"""
Atena · Revisor ontológico (anti-alucinação, FAIL-CLOSED)
=========================================================
Substitui o Revisor regex da v1. Extrai asserções factuais TIPADAS da mensagem
candidata e as resolve contra as âncoras do curso no grafo (via servico).

Decisões de design (ver specs/11):
  • FAIL-CLOSED: afirmação de preço/data/carga/parcelas que não resolve a uma
    âncora BLOQUEIA (a v1 deixava passar quando inconclusiva).
  • Tipos distintos: hora-de-relógio (≤24h, ex.: "aulas das 9h às 12h") NUNCA é
    comparada com carga horária (>24h). Mata o falso-positivo da v1.
  • Plano de parcelas é coerente: nº e valor têm de bater com a base
    ("30x R$ 866,09" é bloqueado mesmo com o valor existindo no catálogo).
  • Passada semântica (Claude) é OPCIONAL e só ADICIONA bloqueios — nunca resgata
    o que a passada estrutural bloqueou, nem aprova por omissão.
"""
import os
import re

MONEY_RE = re.compile(r"R?\$?\s*\d{1,3}(?:[.\s]\d{3})*,\d{2}")
PARC_RE = re.compile(r"(\d{1,3})\s*x\s*(?:de\s*)?R?\$?\s*(\d{1,3}(?:[.\s]\d{3})*,\d{2})", re.I)
DATE_RE = re.compile(r"\b(\d{2})/(\d{2})/(\d{4})\b")
HOUR_RE = re.compile(r"(\d{1,4})\s*(?:h\b|horas\b)", re.I)
CODE_RE = re.compile(r"FEQ[-\s]?(\d{3,4})", re.I)
LIMIAR_RELOGIO = 24  # ≤ 24h = hora-de-relógio; > 24h = carga horária


def _ancoras_escopo(codigo, texto, ont):
    """União das âncoras do curso do card + cursos CITADOS na mensagem.
    Sem isso, um fato verdadeiro de outro curso do portfólio seria bloqueado."""
    codigos, vistos, ancs = [codigo] + [f"FEQ-{m}" for m in CODE_RE.findall(texto or "")], set(), []
    for c in codigos:
        a = ont.ancoras(c)
        if a and a["codigo"] not in vistos:
            vistos.add(a["codigo"])
            ancs.append(a)
    return ancs


def revisar_estrutural(codigo, texto, ont, fonte_extra=None):
    """Passada determinística, sem rede. Retorna {aprovado, bloqueios, ancoras}.
    `fonte_extra`: texto autorizado por humano (ex.: resposta da coordenação) cujos
    fatos viram âncoras válidas para ESTA mensagem (proveniência humana)."""
    ancs = _ancoras_escopo(codigo, texto, ont)
    if not ancs:
        return {"aprovado": False, "bloqueios": [f"Curso '{codigo}' não está na base."], "ancoras": []}
    escopo = "/".join(a["codigo"] for a in ancs)
    money = set().union(*(a["money"] for a in ancs))
    datas = set().union(*(a["datas"] for a in ancs))
    cargas = {a["carga_horas"] for a in ancs if a["carga_horas"] is not None}
    planos = {(a["plano"]["n"], a["plano"]["valor_digits"]) for a in ancs if a["plano"]["n"]}
    if fonte_extra:
        escopo += "+coordenação"
        money |= {re.sub(r"\D", "", m) for m in MONEY_RE.findall(fonte_extra)}
        datas |= {y + mn + d for d, mn, y in DATE_RE.findall(fonte_extra)}
        cargas |= {int(h) for h in HOUR_RE.findall(fonte_extra) if int(h) > LIMIAR_RELOGIO}
        planos |= {(int(n), re.sub(r"\D", "", v)) for n, v in PARC_RE.findall(fonte_extra)}
    bloq, usadas = [], []
    texto = texto or ""

    # 1) Parcelas (consome seus trechos para o passo de dinheiro não recontar).
    parc_spans = []
    for m in PARC_RE.finditer(texto):
        parc_spans.append(m.span())
        n, vd = int(m.group(1)), re.sub(r"\D", "", m.group(2))
        if (n, vd) in planos:
            usadas.append(f"parcelamento {n}x")
        else:
            bloq.append(f"Plano de parcelas incoerente: '{n}x de {m.group(2).strip()}' "
                        f"não corresponde a nenhum curso citado ({escopo}).")

    # 2) Valores monetários (fora dos trechos de parcela).
    for m in MONEY_RE.finditer(texto):
        if any(a <= m.start() < b for a, b in parc_spans):
            continue
        d = re.sub(r"\D", "", m.group(0))
        if d in money:
            usadas.append(f"valor {m.group(0).strip()}")
        else:
            bloq.append(f"Valor monetário fora da base ({escopo}): {m.group(0).strip()}.")

    # 3) Datas.
    for d, mn, y in DATE_RE.findall(texto):
        if (y + mn + d) in datas:
            usadas.append(f"data {d}/{mn}/{y}")
        else:
            bloq.append(f"Data fora da base ({escopo}): {d}/{mn}/{y}.")

    # 4) Carga horária × hora-de-relógio (fail-closed p/ carga desconhecida).
    for m in HOUR_RE.finditer(texto):
        v = int(m.group(1))
        if v <= LIMIAR_RELOGIO:
            continue  # hora-de-relógio — não é carga; não validar aqui
        if v in cargas:
            usadas.append(f"carga {v}h")
        else:
            bloq.append(f"Carga horária fora da base ({escopo}): {v}h.")

    return {"aprovado": not bloq, "bloqueios": bloq, "ancoras": usadas}


def revisar_semantico(codigo, texto, ont):
    """Opcional (Claude). Best-effort: confere requisito/horário/modalidade.
    Só ADICIONA bloqueios. Sem API key -> não opina (a estrutural já é fail-closed
    para os fatos verificáveis por máquina)."""
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        return {"bloqueios": [], "nota": "passada semântica pulada (sem API key)"}
    try:
        import json
        import requests
        c = ont.curso(codigo)
        sysp = ("Você é um auditor factual. Recebe DADOS OFICIAIS de um curso (JSON) e uma "
                "MENSAGEM. Aponte SOMENTE afirmações que contradigam ou vão além dos dados "
                '(requisito, horário, modalidade, certificação). Responda só JSON: '
                '{"problemas": [strings]}.')
        r = requests.post(
            "https://api.anthropic.com/v1/messages", timeout=30,
            headers={"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
            json={"model": os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6"), "max_tokens": 400,
                  "system": sysp,
                  "messages": [{"role": "user", "content": f"DADOS OFICIAIS:\n{json.dumps(c, ensure_ascii=False)}\n\nMENSAGEM:\n{texto}"}]})
        if r.status_code != 200:
            return {"bloqueios": [], "nota": f"semântica indisponível (HTTP {r.status_code})"}
        m = re.search(r"\{.*\}", r.json()["content"][0]["text"], re.S)
        data = json.loads(m.group(0)) if m else {}
        return {"bloqueios": list(data.get("problemas", []))}
    except Exception as e:
        return {"bloqueios": [], "nota": f"semântica falhou: {e}"}


def revisar(codigo, texto, ont, usar_semantico=False, fonte_extra=None):
    r1 = revisar_estrutural(codigo, texto, ont, fonte_extra=fonte_extra)
    bloq = list(r1["bloqueios"])
    nota = None
    if usar_semantico:
        r2 = revisar_semantico(codigo, texto, ont)
        bloq += r2.get("bloqueios", [])
        nota = r2.get("nota")
    return {"aprovado": not bloq, "bloqueios": bloq, "ancoras": r1["ancoras"], "nota_semantica": nota}
