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
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from atena.ontologia.servico import CODE_RE, normalizar_codigo

MONEY_RE = re.compile(r"R?\$?\s*\d{1,3}(?:[.\s]\d{3})*,\d{2}")
PARC_RE = re.compile(r"(\d{1,3})\s*x\s*(?:de\s*)?R?\$?\s*(\d{1,3}(?:[.\s]\d{3})*,\d{2})", re.I)
DATE_RE = re.compile(r"\b(\d{2})/(\d{2})/(\d{4})\b")
# Data SEM ano ("as inscrições vão até 07/08"). Precisou existir quando o corpus
# passou a alimentar o cérebro com precedentes: eles carregam datas de turmas
# antigas, e nesse formato escapavam do DATE_RE e chegavam ao lead.
DATE_CURTA_RE = re.compile(r"\b(\d{2})/(\d{2})\b(?!\s*/)")
HOUR_RE = re.compile(r"(\d{1,4})\s*(?:h\b|horas\b)", re.I)
# Link da Extecamp. Precisou existir quando 6 leads do FEQ-0480 receberam a turma
# ERRADA (/001 em vez de /009): o Revisor barrava preço e data inventados, mas deixava
# passar qualquer URL — e link quebrado mata a conversão tão bem quanto preço errado.
URL_EXTECAMP_RE = re.compile(r"https?://[^\s<>\])]*extecamp\.unicamp\.br/curso/[^\s<>\])]*", re.I)
LIMIAR_RELOGIO = 24  # ≤ 24h = hora-de-relógio; > 24h = carga horária


def _url_norm(u):
    """Compara links ignorando o que não muda o destino: esquema, www, barra final e
    /curso/dados/ vs /curso/inscricao/ (as duas são páginas legítimas do MESMO curso)."""
    u = (u or "").strip().lower().rstrip("/")
    u = re.sub(r"^https?://(www\.)?", "", u)
    return u.replace("/curso/dados/", "/curso/inscricao/")


def _ancoras_escopo(codigo, texto, ont):
    """União das âncoras do curso do card + cursos CITADOS na mensagem.
    Sem isso, um fato verdadeiro de outro curso do portfólio seria bloqueado."""
    codigos, vistos, ancs = [codigo] + [normalizar_codigo(p, n) for p, n in CODE_RE.findall(texto or "")], set(), []
    for c in codigos:
        a = ont.ancoras(c)
        if a and a["codigo"] not in vistos:
            vistos.add(a["codigo"])
            ancs.append(a)
    return ancs


def revisar_estrutural(codigo, texto, ont, fonte_extra=None, confia_valor_humano=False):
    """Passada determinística, sem rede. Retorna {aprovado, bloqueios, ancoras}.
    `fonte_extra`: texto autorizado por humano (ex.: resposta da coordenação) cujos
    fatos viram âncoras válidas para ESTA mensagem (proveniência humana).
    Sem curso associado (lead fora da base — ex.: número desconhecido que chegou
    pelo botão de WhatsApp sem campanha): NÃO bloqueia de cara. Aprova mensagens
    sem afirmação factual (preço/data/carga/parcela) — não há o que verificar,
    então não há o que alucinar. Qualquer afirmação desse tipo, porém, segue
    bloqueada: sem âncora pra checar, o valor cai fora dos conjuntos abaixo e os
    passos 1-4 barram normalmente.

    `confia_valor_humano`: mensagem digitada ou editada DIRETAMENTE por um operador
    (não gerada pelo LLM) — preço e parcela negociados de verdade com o lead/
    coordenação não têm por que existir na ontologia (spec 15: preço muda por
    campanha; negociação pontual é ainda mais específica). Afrouxa SÓ os passos 1/2
    (parcelas e valor monetário). Data, carga horária e link da Extecamp continuam
    travados mesmo com um humano digitando: são fatos estruturais do curso, não
    termos de negociação — datado errado ou link da turma errada já causaram
    incidente real neste projeto (ver comentário de URL_EXTECAMP_RE) e a correção
    correta pra eles é editar a ontologia (Injetor), não digitar por cima."""
    ancs = _ancoras_escopo(codigo, texto, ont)
    escopo = "/".join(a["codigo"] for a in ancs) or (
        f"curso '{codigo}' desconhecido" if codigo else "nenhum curso associado")
    money = set().union(*(a["money"] for a in ancs))
    datas = set().union(*(a["datas"] for a in ancs))
    cargas = {a["carga_horas"] for a in ancs if a["carga_horas"] is not None}
    # lista, não um só: com promoção vigente o curso passa a ter mais de um plano
    # válido (tabela + ofertas), e o Revisor tem de aceitar qualquer um deles.
    planos = {(pl["n"], pl["valor_digits"]) for a in ancs for pl in a["planos"] if pl["n"]}
    urls = {u for a in ancs for u in a.get("urls", ())}
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
        elif confia_valor_humano:
            # NÃO finge que bateu com a base — fica honesto no trace de auditoria/corpus
            # (spec 18) que este valor veio de negociação humana, não da ontologia.
            usadas.append(f"parcelamento {n}x (negociado por humano, fora da base)")
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
        elif confia_valor_humano:
            usadas.append(f"valor {m.group(0).strip()} (negociado por humano, fora da base)")
        else:
            bloq.append(f"Valor monetário fora da base ({escopo}): {m.group(0).strip()}.")

    # 3) Datas completas (DD/MM/AAAA).
    data_spans = []
    for m in DATE_RE.finditer(texto):
        data_spans.append(m.span())
        d, mn, y = m.groups()
        if (y + mn + d) in datas:
            usadas.append(f"data {d}/{mn}/{y}")
        else:
            bloq.append(f"Data fora da base ({escopo}): {d}/{mn}/{y}.")

    # 3b) Datas sem ano (DD/MM): valida dia+mês contra as âncoras, em qualquer ano.
    for m in DATE_CURTA_RE.finditer(texto):
        if any(a <= m.start() < b for a, b in data_spans):
            continue                      # já contada como data completa
        d, mn = m.group(1), m.group(2)
        if not (1 <= int(mn) <= 12 and 1 <= int(d) <= 31):
            continue                      # não é data (ex.: "20/26 vagas")
        if any(x[4:] == mn + d for x in datas):
            usadas.append(f"data {d}/{mn}")
        else:
            bloq.append(f"Data fora da base ({escopo}): {d}/{mn}.")

    # 3c) Links da Extecamp: turma errada leva o lead a um formulário que não é o dele.
    for m in URL_EXTECAMP_RE.finditer(texto):
        u = m.group(0).rstrip(").,;*]")
        if _url_norm(u) in urls:
            usadas.append("link da Extecamp")
        else:
            bloq.append(f"Link da Extecamp fora da base ({escopo}): {u}. "
                        f"Use exatamente a URL de inscrição que está nos dados do curso.")

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


def revisar(codigo, texto, ont, usar_semantico=False, fonte_extra=None, confia_valor_humano=False):
    r1 = revisar_estrutural(codigo, texto, ont, fonte_extra=fonte_extra,
                            confia_valor_humano=confia_valor_humano)
    bloq = list(r1["bloqueios"])
    nota = None
    if usar_semantico:
        r2 = revisar_semantico(codigo, texto, ont)
        bloq += r2.get("bloqueios", [])
        nota = r2.get("nota")
    return {"aprovado": not bloq, "bloqueios": bloq, "ancoras": r1["ancoras"], "nota_semantica": nota}
