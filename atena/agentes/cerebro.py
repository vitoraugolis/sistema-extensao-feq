#!/usr/bin/env python3
"""
Atena · Cérebro de conversa (LLM, mesmo contrato DecisaoAgente).
Usa Claude para conduzir a qualificação, ancorado no contexto ontológico (curso +
captabilidade + elegibilidade) que o Bibliotecário monta. SEM API key, ou em
qualquer falha de parsing, faz fallback para o cérebro determinístico (offline).

Segurança: opt-out e curso-não-captável são decididos de forma determinística
(não se confia no LLM). A mensagem proposta pelo LLM ainda passa pelo Revisor
(fail-closed) no orquestrador — preço/data/carga inventados são bloqueados lá.
"""
import os
import re
import sys
import json
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from atena.core import contratos as C
from atena.core import guardrails as G
from atena.agentes.bibliotecario import Bibliotecario
from atena.core.orquestrador import brain_deterministico

import time as _time
import collections

_URL = "https://api.anthropic.com/v1/messages"
FALLBACKS = collections.deque(maxlen=100)   # timestamps p/ o monitor de degradação

# provedor -> variável de ambiente da chave (o modelo ativo vem de ATENA_MODELO="prov:id")
PROVEDORES = {"claude": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY", "gemini": "GEMINI_API_KEY"}


def chave_do(prov):
    if prov == "gemini":  # aceita os dois nomes usuais
        return os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY", "")
    return os.environ.get(PROVEDORES.get(prov, ""), "")


def modelo_atual():
    m = os.environ.get("ATENA_MODELO") or f"claude:{os.environ.get('ANTHROPIC_MODEL', 'claude-sonnet-4-6')}"
    prov, _, mid = m.partition(":")
    return (prov, mid) if prov in PROVEDORES and mid else ("claude", "claude-sonnet-4-6")


def _claude(key, mid, system, messages):
    import requests
    r = requests.post(_URL, timeout=40,
                      headers={"x-api-key": key, "anthropic-version": "2023-06-01",
                               "content-type": "application/json"},
                      json={"model": mid, "max_tokens": 1000, "system": system, "messages": messages})
    if r.status_code != 200:
        raise RuntimeError(f"Claude {r.status_code}: {r.text[:120]}")
    return r.json()["content"][0]["text"]


def _openai(key, mid, system, messages):
    import requests
    r = requests.post("https://api.openai.com/v1/chat/completions", timeout=40,
                      headers={"Authorization": f"Bearer {key}"},
                      json={"model": mid, "max_completion_tokens": 1000,
                            "messages": [{"role": "system", "content": system}] + messages})
    if r.status_code != 200:
        raise RuntimeError(f"OpenAI {r.status_code}: {r.text[:120]}")
    return r.json()["choices"][0]["message"]["content"]


def _gemini(key, mid, system, messages):
    import requests
    contents = [{"role": "user" if m["role"] == "user" else "model",
                 "parts": [{"text": m["content"]}]} for m in messages]
    r = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{mid}:generateContent",
        timeout=40, params={"key": key},
        json={"systemInstruction": {"parts": [{"text": system}]}, "contents": contents,
              "generationConfig": {"maxOutputTokens": 3000,
                                   "responseMimeType": "application/json"}})
    if r.status_code != 200:
        raise RuntimeError(f"Gemini {r.status_code}: {r.text[:120]}")
    return r.json()["candidates"][0]["content"]["parts"][0]["text"]


def chamar_llm(system, messages, modelo=None):
    """Despacha para o provedor do modelo ativo (ou do `modelo` explícito)."""
    prov, mid = modelo_atual() if not modelo else (modelo.partition(":")[0], modelo.partition(":")[2])
    key = chave_do(prov)
    if not key:
        raise RuntimeError(f"sem chave para o provedor '{prov}'")
    fn = {"claude": _claude, "openai": _openai, "gemini": _gemini}[prov]
    return fn(key, mid, system, messages)


def testar_modelo(modelo):
    """Valida chave, ID E o contrato JSON (o mesmo que o cérebro exige em produção)."""
    try:
        out = chamar_llm('Responda SOMENTE com este JSON, sem mais nada: {"ok": true}',
                         [{"role": "user", "content": "ping"}], modelo=modelo)
        if not _json(out):
            return {"ok": False, "erro": f"respondeu, mas fora do contrato JSON: {(out or '')[:80]!r}"}
        return {"ok": True, "resposta": (out or "").strip()[:60]}
    except Exception as e:
        return {"ok": False, "erro": str(e)[:200]}


def _turns(historico, mensagem_lead):
    """conversas -> turns Anthropic, fundindo papéis consecutivos iguais."""
    msgs = []
    for h in historico or []:
        role = "assistant" if h.get("papel") == "agente" else "user"
        if msgs and msgs[-1]["role"] == role:
            msgs[-1]["content"] += "\n" + (h.get("texto") or "")
        else:
            msgs.append({"role": role, "content": h.get("texto") or ""})
    ult = mensagem_lead or "(início da conversa)"
    if msgs and msgs[-1]["role"] == "user":
        msgs[-1]["content"] += "\n" + ult
    else:
        msgs.append({"role": "user", "content": ult})
    return msgs


def _json(texto):
    m = re.search(r"\{.*\}", texto or "", re.S)
    return json.loads(m.group(0)) if m else None


def _gate_instr(elegivel):
    """Instrução do gate acadêmico — condicional à pré-qualificação (evita re-perguntar)."""
    if elegivel is True:
        return ("GATE ACADÊMICO JÁ SATISFEITO: o ensino superior completo do lead JÁ está "
                "confirmado (pré-qualificado no formulário). NÃO pergunte sobre escolaridade "
                "de novo — siga direto para orientar sobre inscrição; pode usar 'qualificar'.\n")
    return ("GATE: ensino superior completo (todos os cursos). Use 'qualificar' SOMENTE depois "
            "que o lead confirmar explicitamente o ensino superior completo; enquanto "
            "pergunta/aguarda, use 'continuar'.\n")


def _system_prompt(ctx, lead_nome=None):
    c = ctx["curso"]
    nome = (lead_nome or "").strip()
    citados = "\n".join(
        f"CURSO CITADO PELO LEAD ({x['curso']['codigo']}, inscrição {x['captavel']['status']}):\n"
        f"{json.dumps(x['curso'], ensure_ascii=False)}"
        for x in ctx.get("cursos_citados", []))
    return (
        "Você é o Vinícius, atendente da Extensão FEQ Unicamp, qualificando um lead por WhatsApp. "
        "Apresente-se como Vinícius (nunca como IA/Atena). "
        + (f"O LEAD se chama {nome} — trate-o por esse nome; " if nome else "")
        + "NUNCA chame o lead de Vinícius (esse é VOCÊ).\n"
        "TOM: caloroso, gentil e humano; celebre o interesse do lead; UMA pergunta por vez; "
        "frases curtas de WhatsApp, sem parecer formulário.\n"
        f"CURSO DE INTERESSE DO LEAD (registro completo):\n{json.dumps(c, ensure_ascii=False)}\n"
        f"Elegibilidade já avaliada: {ctx.get('elegivel')}. Captável agora: {ctx['captavel'].get('captavel')}.\n"
        f"PORTFÓLIO COMPLETO DA FEQ (você conhece todos; use p/ orientar e redirecionar):\n"
        f"{json.dumps(ctx.get('portfolio', []), ensure_ascii=False)}\n"
        + (citados + "\n" if citados else "") +
        "GROUNDING: os dados acima são a ÚNICA fonte de fatos. NUNCA invente preço/data/carga/"
        "requisito/ementa. Curso com inscrição 'encerrada' => informe e ofereça alternativas "
        "abertas do portfólio.\n"
        "INTELIGÊNCIA + REGRA DE OURO (anti-alucinação): FATOS ESPECÍFICOS deste curso "
        "(preço, parcelas, taxa, datas, horário, carga horária, requisitos, conteúdo "
        "programático, vagas, link de inscrição) vêm SOMENTE dos dados acima — jamais os "
        "invente. O conteúdo programático e o objetivo do curso ESTÃO nos dados: use-os para "
        "responder o que o lead vai aprender, os módulos e a aplicação. Se um FATO específico "
        "do curso for pedido e realmente NÃO estiver nos dados (ex.: política de descontos), "
        "use proxima_acao='consultar_coordenacao' com a pergunta OBJETIVA em dados.duvida (ex.: "
        "'Lead pede descontos p/ servidores no FEQ-0333'); o sistema envia a mensagem padrão de "
        "espera — não a redija. MAS perguntas CONCEITUAIS/gerais da área — o que é uma "
        "tecnologia ou método, por que o tema importa, relevância para a carreira ou para o "
        "setor do lead, como o conteúdo se aplica a um caso que ele citou (ex.: reciclagem, "
        "agronegócio) — você DEVE responder com seu PRÓPRIO conhecimento técnico, de forma "
        "correta, específica e conectada ao curso, SEM escalar. Seja genuinamente inteligente e "
        "útil, nunca evasivo. EXCEÇÃO: mensagem iniciada por [INFORMAÇÃO DA COORDENAÇÃO] é fonte "
        "autorizada — responda com ela.\n"
        + _gate_instr(ctx.get("elegivel")) +
        "Não peça CPF/endereço nesta fase; escale (escalar_humano) em desconto/reclamação/"
        "intenção de fechar/dúvida fora da base.\n"
        "TROCA DE INTERESSE: se o lead demonstrar que o curso que quer é OUTRO do portfólio, "
        'retorne o código dele em dados.curso (ex.: "FEQ-0580") e siga a conversa nesse curso.\n'
        "PROCESSO DE INSCRIÇÃO (padrão Extecamp — use quando o lead quiser prosseguir ou "
        "perguntar como funciona): (1) você envia o link de inscrição do curso (formulário com "
        "os dados de matrícula); (2) ao final o lead aceita a taxa de inscrição e recebe o link "
        "de pagamento/boleto + o login da área do aluno; (3) com o pagamento reconhecido (cartão "
        "quase na hora; boleto até 3 dias úteis) ele acessa a área do aluno para enviar os "
        "documentos; (4) após o FIM das inscrições, a secretaria da FEQ envia por e-mail o 1º "
        "boleto do curso. Não invente etapas nem prazos além destes.\n"
        'Responda SOMENTE em JSON, preenchendo os campos com conteúdo REAL (nunca reticências '
        'ou placeholder): {"mensagem_ao_lead": "<o texto completo que será enviado ao lead>", '
        '"proxima_acao": "continuar|qualificar|descartar|em_espera|escalar_humano|encerrar_optout", '
        '"motivo": "<justificativa curta>", "escalonamento_motivo": null, '
        '"dados": {"escolaridade": null, "curso": null, "duvida": null}}'
    )


def cerebro_llm(ont, lead, mensagem_lead, historico=None):
    # Decisões determinísticas (não confiar no LLM):
    if G.detectar_optout(mensagem_lead):
        return brain_deterministico(ont, lead, mensagem_lead, historico)
    ctx = Bibliotecario(ont).contexto(lead["curso"], lead.get("escolaridade"), mensagem=mensagem_lead)
    if not ctx.get("existe") or not ctx["captavel"].get("captavel"):
        return brain_deterministico(ont, lead, mensagem_lead, historico)
    sysp = _system_prompt(ctx, lead.get("nome"))
    turns = _turns(historico, mensagem_lead)
    # tenta o modelo escolhido; se falhar e não for Claude, tenta Claude; só então o determinístico
    tentativas = [None]
    if modelo_atual()[0] != "claude" and chave_do("claude"):
        tentativas.append(f"claude:{os.environ.get('ANTHROPIC_MODEL', 'claude-sonnet-4-6')}")
    for m in tentativas:
        try:
            data = _json(chamar_llm(sysp, turns, modelo=m))
            if not data or data.get("proxima_acao") not in C.ACOES:
                raise ValueError("saída do LLM inválida")
            # guarda anti-placeholder: ação conversacional exige mensagem de verdade
            msg = (data.get("mensagem_ao_lead") or "").strip()
            if data["proxima_acao"] in (C.CONTINUAR, C.QUALIFICAR, C.DESCARTAR) and len(msg) < 10:
                raise ValueError(f"mensagem degenerada: {msg!r}")
            return C.DecisaoAgente(
                proxima_acao=data["proxima_acao"], motivo=data.get("motivo", ""),
                mensagem_ao_lead=data.get("mensagem_ao_lead"),
                escalonamento_motivo=data.get("escalonamento_motivo"),
                dados=data.get("dados") or {})
        except Exception as e:
            # visível no journalctl — diagnóstico de por que o LLM não respondeu
            print(f"[cerebro] falha em {m or ':'.join(modelo_atual())}: {type(e).__name__}: {e}",
                  flush=True)
    FALLBACKS.append(_time.time())
    return brain_deterministico(ont, lead, mensagem_lead, historico)


def cerebro_factory(ont):
    """Retorna um callable (lead, msg, historico) -> DecisaoAgente, p/ o Orquestrador."""
    return lambda lead, msg, hist=None: cerebro_llm(ont, lead, msg, hist)
