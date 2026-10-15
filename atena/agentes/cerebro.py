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
from atena.ontologia.servico import instituicao_do_codigo
from atena.core.contatos import secretaria_texto

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


# Saída estruturada do Claude via TOOL USE forçado. Pedir JSON em texto não bastava: o
# modelo escrevia prosa antes do objeto (ou só prosa) e a conversa caía no determinístico
# — foi o que deixou o agente "confuso" em produção. Com tool_choice a resposta É o objeto.
_FERRAMENTA = {
    "name": "responder_lead",
    "description": "Responde ao lead e decide o próximo passo do atendimento.",
    "input_schema": {
        "type": "object",
        "properties": {
            "mensagem_ao_lead": {"type": "string",
                                 "description": "Texto completo que será enviado ao lead."},
            "proxima_acao": {"type": "string", "enum": sorted(C.ACOES)},
            "motivo": {"type": "string", "description": "Justificativa em até 15 palavras."},
            "escalonamento_motivo": {"type": ["string", "null"]},
            "dados": {"type": "object", "properties": {
                "escolaridade": {"type": ["string", "null"]},
                "curso": {"type": ["string", "null"]},
                "duvida": {"type": ["string", "null"]},
                "anexo_guia": {"type": ["boolean", "null"],
                               "description": "true quando o lead pediu material/PDF/apresentação formal do curso."}}},
        },
        "required": ["mensagem_ao_lead", "proxima_acao", "motivo"],
    },
}


def _claude(key, mid, system, messages, ferramenta=None):
    """`ferramenta`: schema de saída estruturada. Precisa ser PARÂMETRO, não fixo — o
    tool use amarrado ao schema do cérebro fazia todo outro consumidor (curador,
    injetor, teste de modelo) receber o contrato errado e cair em fallback."""
    import requests
    corpo = {"model": mid, "max_tokens": 2000, "system": system, "messages": messages}
    if ferramenta:
        corpo["tools"] = [ferramenta]
        corpo["tool_choice"] = {"type": "tool", "name": ferramenta["name"]}
    r = requests.post(_URL, timeout=40,
                      headers={"x-api-key": key, "anthropic-version": "2023-06-01",
                               "content-type": "application/json"}, json=corpo)
    if r.status_code != 200:
        raise RuntimeError(f"Claude {r.status_code}: {r.text[:120]}")
    blocos = r.json().get("content", [])
    uso = next((b for b in blocos if b.get("type") == "tool_use"), None)
    if uso:
        return json.dumps(uso["input"], ensure_ascii=False)   # contrato de texto p/ o _json()
    return next((b.get("text", "") for b in blocos if b.get("type") == "text"), "")


def _openai(key, mid, system, messages, ferramenta=None):
    import requests
    r = requests.post("https://api.openai.com/v1/chat/completions", timeout=40,
                      headers={"Authorization": f"Bearer {key}"},
                      json={"model": mid, "max_completion_tokens": 2000,
                            # sem isto o modelo responde PROSA e o cérebro inteiro cai no
                            # determinístico (era o caso do gpt-4o-mini em produção)
                            "response_format": {"type": "json_object"},
                            "messages": [{"role": "system", "content": system}] + messages})
    if r.status_code != 200:
        raise RuntimeError(f"OpenAI {r.status_code}: {r.text[:120]}")
    return r.json()["choices"][0]["message"]["content"]


def _gemini(key, mid, system, messages, ferramenta=None):
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


def chamar_llm(system, messages, modelo=None, ferramenta=None):
    """Despacha para o provedor do modelo ativo (ou do `modelo` explícito).
    `ferramenta` só afeta o Claude (tool use); OpenAI/Gemini já pedem JSON genérico."""
    prov, mid = modelo_atual() if not modelo else (modelo.partition(":")[0], modelo.partition(":")[2])
    key = chave_do(prov)
    if not key:
        raise RuntimeError(f"sem chave para o provedor '{prov}'")
    fn = {"claude": _claude, "openai": _openai, "gemini": _gemini}[prov]
    return fn(key, mid, system, messages, ferramenta)


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
    """Extrai o objeto JSON da resposta do LLM.
    O regex guloso anterior (\\{.*\\}) quebrava quando o modelo escrevia prosa antes do
    JSON ou cercas ``` — e o Claude faz isso com frequência. Aqui varremos cada '{' com
    raw_decode (que para no fim do objeto e ignora o que vier depois) e preferimos o
    objeto que traz 'proxima_acao', para não pegar uma chave solta citada no texto."""
    t = texto or ""
    dec = json.JSONDecoder()
    candidatos = []
    for i, ch in enumerate(t):
        if ch != "{":
            continue
        try:
            obj, _ = dec.raw_decode(t[i:])
        except ValueError:
            continue
        if isinstance(obj, dict):
            if "proxima_acao" in obj:
                return obj
            candidatos.append(obj)
    return candidatos[0] if candidatos else None


_NIVEL_TEXTO = {"medio": "ensino médio completo",
                "superiorIncompleto": "ensino superior em andamento",
                "superiorCompleto": "ensino superior completo",
                "pos": "pós-graduação concluída"}


def _gate_instr(elegivel, ctx=None):
    """Instrução do gate acadêmico — condicional à pré-qualificação (evita re-perguntar)
    e AO CURSO: o requisito não é o mesmo em todo o catálogo (FEQ-0299 e FEQ-0670
    pedem só ensino médio). Dizer 'superior completo' para todos barraria lead
    elegível e seria informação falsa."""
    ctx = ctx or {}
    if ctx.get("descoberta"):
        return ("GATE: o requisito de escolaridade VARIA por curso (está no campo 'requisito' "
                "de cada registro). Só afirme o requisito depois de saber qual curso o lead "
                "quer; enquanto descobre, use 'continuar'.\n")
    nivel = ((ctx.get("curso") or {}).get("requisito")) or "superiorCompleto"
    exigido = _NIVEL_TEXTO.get(nivel, "ensino superior completo")
    if elegivel is True:
        return (f"GATE ACADÊMICO JÁ SATISFEITO: o requisito deste curso ({exigido}) JÁ está "
                "confirmado para este lead. NÃO pergunte sobre escolaridade de novo — siga "
                "direto para orientar sobre inscrição; pode usar 'qualificar'.\n")
    return (f"GATE deste curso: {exigido}. Use 'qualificar' SOMENTE depois que o lead "
            f"confirmar explicitamente que tem {exigido}; enquanto pergunta/aguarda, use "
            "'continuar'. NÃO exija escolaridade maior que a do curso.\n")


def _moeda_br(v):
    return f"R$ {float(v):,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")


def _bloco_promo(c):
    """Instrução da promoção vigente. Só existe quando a ontologia diz que a campanha
    está no ar hoje — expirada, o bloco some e o agente volta ao preço de tabela."""
    pr = (c or {}).get("promocao")
    if not pr:
        return ""
    linhas = []
    for o in pr["ofertas"]:
        partes = []
        if o.get("n") and o.get("valor_parcela"):
            partes.append(f"{o['n']}x de {_moeda_br(o['valor_parcela'])}")
        if o.get("a_vista"):
            partes.append(f"{_moeda_br(o['a_vista'])} à vista")
        pct = f" ({o['desconto_pct']}% de desconto sobre o valor de tabela)" if o.get("desconto_pct") else ""
        linhas.append(f"  • {o['rotulo']}{pct} — {' ou '.join(partes)}. Condição: {o['condicao']}.")
    fim = "/".join(reversed((pr.get("fim") or "").split("-")))
    aplic = (f"COMO O DESCONTO CHEGA AO ALUNO (diga isto sempre que falar de valor ou de "
             f"inscrição, sem esperar o lead perguntar): {pr['como_aplicar']}\n"
             if pr.get("como_aplicar") else "")
    return (
        f"PROMOÇÃO VIGENTE — {pr['rotulo']} (válida até {fim}):\n" + "\n".join(linhas) + "\n"
        + aplic +
        "REGRA DE PREÇO (prevalece sobre qualquer outra instrução deste prompt): perguntou de "
        "valor, você responde com o preço PROMOCIONAL acima. NUNCA abra a resposta pelo valor de "
        "tabela — ele entra só depois, rotulado como 'valor de tabela', para mostrar a economia. "
        "Responder o preço cheio existindo campanha é erro grave: o lead recebe informação pior "
        "que a real e desiste.\n"
        "Os valores acima já estão calculados nos dados — reproduza-os exatamente, nunca "
        "recalcule nem arredonde. Se o lead se encaixa numa condição melhor (ex.: grupo), diga "
        "isso proativamente: é dinheiro no bolso dele. Não some a campanha com outros descontos "
        "nem prometa acúmulo — descontos não acumulam; se perguntarem, diga isso. "
        f"Depois de {fim} a promoção acaba: a urgência que você comunica é essa data real, "
        "nunca uma pressão inventada.\n")


def _bloco_janela(ctx):
    """O que dizer quando a inscrição não está aberta. São DOIS casos, e tratá-los
    igual custa matrícula: turma que já começou não tem volta, mas prazo vencido com
    aulas ainda por começar é recuperável (vagas remanescentes + link fora do prazo)."""
    cap = ctx.get("captavel") or {}
    if cap.get("captavel"):
        return ""
    if cap.get("recuperavel"):
        pol = (ctx.get("politica_fora_do_prazo") or "").strip()
        inicio = "/".join(reversed((cap.get("aulas_inicio") or "").split("-")))
        return (
            "SITUAÇÃO ESPECIAL — PRAZO VENCIDO, MAS A TURMA AINDA NÃO COMEÇOU"
            + (f" (as aulas começam em {inicio})" if inicio else "") + ".\n"
            + (f"Política da coordenação: {pol}\n" if pol else "") +
            "Conduza assim, nesta ordem:\n"
            "  1. Seja honesto: o prazo oficial de inscrição já encerrou.\n"
            "  2. Em seguida, ofereça a saída — ainda há vagas remanescentes e a coordenação "
            "consegue gerar um link de inscrição fora do prazo para ele.\n"
            "  3. Peça o E-MAIL, que é o que permite gerar esse link, e avise que o link vale "
            "24 horas.\n"
            "  4. Assim que o lead mandar o e-mail, use proxima_acao='escalar_humano' com o "
            "e-mail no escalonamento_motivo. QUEM GERA O LINK É UMA PESSOA, não você: nunca "
            "diga que já gerou, não invente URL e não mande o link normal de inscrição (ele "
            "não funciona depois do prazo).\n"
            "NÃO ofereça lista de espera nem 'aviso quando reabrir' neste caso: a vaga é "
            "AGORA, e mandar o lead esperar joga fora uma matrícula possível.\n")
    return ("ATENÇÃO: a inscrição deste curso NÃO está aberta agora e a turma já começou. "
            "Seja honesto sobre isso já na primeira resposta, ofereça avisar o lead quando a "
            "próxima turma abrir (lista de espera) e sugira alternativas ABERTAS do portfólio "
            "que sirvam ao objetivo dele. NUNCA mande link de inscrição deste curso nem "
            "prometa data de reabertura.\n")


def _bloco_descontos(ctx):
    """Regra de desconto, CONDICIONAL à campanha.

    A versão sem promoção manda dizer o valor CHEIO — correta fora de campanha e
    desastrosa dentro dela. Como este trecho vinha DEPOIS do bloco da promoção no
    prompt, ele vencia a disputa: o mesmo agente respondia ora o preço de tabela,
    ora o promocional (o que o operador viu como "agentes confusos" em 06/08)."""
    base = ("DESCONTOS: TODOS os cursos da Extensão FEQ dão 10% para alunos e ex-alunos da "
            "Unicamp (qualquer nível — graduação, pós, extensão etc.) — informe isso "
            "diretamente quando perguntarem e confirme se o lead se enquadra. "
            "DESCONTOS NUNCA ACUMULAM: cada um é isolado e vale um só por inscrição (ex.: quem "
            "é ex-aluno e paga à vista fica com o maior, não com a soma). Diga isso direto se "
            "perguntarem; não é caso de consultar_coordenacao. ")
    if (ctx.get("curso") or {}).get("promocao"):
        return base + (
            "Este curso está EM CAMPANHA: o preço que você anuncia é o promocional, que já vem "
            "pronto nos dados — não recalcule nem arredonde. Sobre os 10% de ex-aluno, não os "
            "chame de desconto 'adicional' nem 'extra' enquanto a campanha valer: essas palavras "
            "afirmam um acúmulo que não existe. Diga que é outra política e que não soma com a "
            "campanha. ")
    return base + (
        "NUNCA CALCULE o valor com desconto aplicado (nem a parcela, nem o à vista): diga a "
        "porcentagem e o valor CHEIO que está nos dados; o abatimento é aplicado pela secretaria "
        "(o campo 'descontos' dos dados diz como, quando houver). Valor calculado por você não existe na base e é bloqueado antes de "
        "chegar ao lead. ")


def _bloco_curso(ctx):
    """Trecho do prompt sobre o curso do lead. Em DESCOBERTA (lead ainda não disse
    qual curso quer — típico de quem chega pelo botão de WhatsApp da LP) troca o
    registro do curso por instrução explícita de descobrir o interesse."""
    if ctx.get("descoberta"):
        return (
            "SITUAÇÃO: o lead chegou pelo WhatsApp e AINDA NÃO disse qual curso quer "
            "(ou citou um que não existe no catálogo). Sua PRIMEIRA tarefa é descobrir "
            "isso de forma acolhedora: cumprimente, pergunte sobre qual curso ou área ele "
            "quer informações e, se ele estiver em dúvida, ajude a escolher usando o "
            "PORTFÓLIO abaixo (pergunte a formação/objetivo dele e sugira o curso que "
            "encaixa, dizendo por quê). NÃO despeje a lista inteira de uma vez: no máximo "
            "3 sugestões por mensagem, priorizando os de inscrição 'aberta'. Assim que "
            "identificar o curso, retorne o código dele em dados.curso e siga a conversa "
            "normalmente nesse curso. Use proxima_acao='continuar' enquanto descobre.\n"
            "IMPORTANTE: sem curso definido você NÃO tem fatos de curso nenhum para "
            "afirmar — não fale preço, data, carga nem requisito específico até saber "
            "qual é o curso (os dados de cada um estão no portfólio abaixo).\n")
    c = ctx["curso"]
    return (
        f"CURSO DE INTERESSE DO LEAD (registro completo):\n{json.dumps(c, ensure_ascii=False)}\n"
        + _bloco_promo(c) +
        f"Elegibilidade já avaliada: {ctx.get('elegivel')}. "
        f"Captável agora: {ctx['captavel'].get('captavel')} "
        f"(status da inscrição: {ctx['captavel'].get('status')}).\n"
        + _bloco_janela(ctx))


def _system_prompt(ctx, lead_nome=None, precedentes=""):
    nome = (lead_nome or "").strip()
    # Instituto do curso DESTE lead — ECO-1100 é do Instituto de Economia, não da FEQ
    # (specs/08 § "Cursos fora da FEQ"); sem curso ainda (descoberta), assume FEQ (a
    # maioria do portfólio) até o lead dizer qual curso quer.
    instituicao = instituicao_do_codigo((ctx.get("curso") or {}).get("codigo"))
    citados = "\n".join(
        f"CURSO CITADO PELO LEAD ({x['curso']['codigo']}, inscrição {x['captavel']['status']}):\n"
        f"{json.dumps(x['curso'], ensure_ascii=False)}"
        for x in ctx.get("cursos_citados", []))
    return (
        f"Você é o Vinícius, atendente {instituicao}, qualificando um lead por WhatsApp. "
        "Apresente-se como Vinícius. "
        "SE O LEAD PERGUNTAR se você é robô/IA/bot/pessoa de verdade, seja HONESTO e direto: "
        f"confirme que sim, é um agente de inteligência artificial {instituicao}, e complete "
        "que se ele preferir falar com uma pessoa do time é só dizer que você repassa na hora. "
        "Não minta nem desconverse sobre isso. Se ele pedir atendimento humano, use "
        "proxima_acao='escalar_humano' e avise que está repassando. "
        + (f"O LEAD se chama {nome} — trate-o por esse nome; " if nome else "")
        + "NUNCA chame o lead de Vinícius (esse é VOCÊ).\n"
        "TOM: converse como um humano de verdade no WhatsApp — natural, direto e cordial, "
        "UMA pergunta por vez, frases curtas, sem parecer formulário. NADA de bajulação nem "
        "reações exageradas: NÃO diga 'nossa que legal', 'que profissão incrível', 'que nome "
        "bonito', 'amei', nem encha de elogios ou emojis. No máximo um emoji ocasional, e só "
        "quando couber. Reconheça o que o lead diz em meia frase e siga direto ao ponto útil "
        "(responder a dúvida, dar a informação, avançar a inscrição). Seja quente pela clareza "
        "e prestatividade, não pelo entusiasmo forçado.\n"
        "FORMATAÇÃO WHATSAPP: negrito é um ASTERISCO simples (*palavra*), NUNCA dois (**palavra** "
        "aparece quebrado para o lead). Ao listar itens (módulos, passos, opções), coloque CADA "
        "item em sua PRÓPRIA linha, separando com quebra de linha real (\\n) — nunca emende tudo "
        "num parágrafo corrido. Deixe uma linha em branco entre blocos para arejar a leitura.\n"
        + _bloco_curso(ctx)
        + f"PORTFÓLIO COMPLETO (você conhece todos; a maioria é da FEQ, mas repare no "
        f"código — ECO-1100 é do Instituto de Economia, não da FEQ; use p/ orientar e "
        f"redirecionar sem misturar a identidade institucional):\n"
        f"{json.dumps(ctx.get('portfolio', []), ensure_ascii=False)}\n"
        + (citados + "\n" if citados else "")
        + (precedentes or "") +
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
        + _gate_instr(ctx.get("elegivel"), ctx)
        + _bloco_descontos(ctx) +
        "Se houver política específica do curso, ela "
        "estará no campo 'descontos' dos dados; use-a. Para OUTRAS categorias de desconto não "
        "listadas (ex.: servidor público) ou negociação de valor, use consultar_coordenacao. "
        "Não peça CPF/endereço nesta fase; escale (escalar_humano) em reclamação/insatisfação/"
        "intenção de fechar/dúvida fora da base.\n"
        "TROCA DE INTERESSE: se o lead demonstrar que o curso que quer é OUTRO do portfólio, "
        'retorne o código dele em dados.curso (ex.: "FEQ-0580") e siga a conversa nesse curso.\n'
        "MATERIAL EM PDF: se o lead pedir um material formal do curso (PDF, apresentação, "
        "guia, algo para 'analisar' ou 'mostrar para alguém'), diga na mensagem que você vai "
        "mandar o guia completo em seguida e marque dados.anexo_guia=true — o sistema anexa o "
        "PDF automaticamente após esta mensagem; NUNCA invente um link de download.\n"
        "LINK DE INSCRIÇÃO: use o campo 'url_inscricao' dos dados COPIADO CARACTERE POR "
        "CARACTERE. Ele já vem pronto — não monte, não converta, não troque o número da turma "
        "e não use o 'url_extecamp' (esse é a página de detalhes). Link com a turma errada leva "
        "o lead ao formulário de outra turma e a inscrição se perde; o Revisor bloqueia "
        "qualquer URL que não seja exatamente a dos dados.\n"
        "PROCESSO DE INSCRIÇÃO (padrão Extecamp — use quando o lead quiser prosseguir ou "
        "perguntar como funciona): (1) você envia o link de inscrição do curso (formulário com "
        "os dados de matrícula); (2) ao final o lead aceita a taxa de inscrição e recebe o link "
        "de pagamento/boleto + o login da área do aluno; (3) com o pagamento reconhecido (cartão "
        "quase na hora; boleto até 3 dias úteis) ele acessa a área do aluno para enviar os "
        "documentos; (4) após o FIM das inscrições, a secretaria da FEQ envia por e-mail o 1º "
        "boleto do curso. Não invente etapas nem prazos além destes.\n"
        f"CONTATO DA SECRETARIA (informe exatamente assim quando o lead pedir; não invente "
        f"outro e-mail nem outro número): {secretaria_texto((ctx.get('curso') or {}).get('codigo'))}.\n"
        'Responda SOMENTE em JSON (nada de texto fora do objeto, nada de cercas ```), '
        'preenchendo os campos com conteúdo REAL (nunca reticências ou placeholder): '
        '{"mensagem_ao_lead": "<o texto completo que será enviado ao lead>", '
        '"proxima_acao": "continuar|qualificar|descartar|em_espera|escalar_humano|'
        'consultar_coordenacao|encerrar_optout", '
        '"motivo": "<justificativa em no MÁXIMO 15 palavras>", "escalonamento_motivo": null, '
        '"dados": {"escolaridade": null, "curso": null, "duvida": null, "anexo_guia": null}}'
    )


def cerebro_llm(ont, lead, mensagem_lead, historico=None):
    # Decisões determinísticas (não confiar no LLM):
    if G.detectar_optout(mensagem_lead):
        return brain_deterministico(ont, lead, mensagem_lead, historico)
    ctx = Bibliotecario(ont).contexto(lead["curso"], lead.get("escolaridade"), mensagem=mensagem_lead,
                                     historico=historico)
    # O agente atende em TRÊS situações (antes só a 1ª chegava aqui; as outras duas
    # caíam no determinístico e viravam escalonamento sem resposta ao lead):
    #   1. curso definido e com inscrição aberta  -> venda normal
    #   2. curso definido, inscrição ENCERRADA    -> informa e oferece alternativas
    #      abertas / lista de espera (o prompt já cobre; os dados do grafo travam)
    #   3. SEM curso (botão de WhatsApp da LP)    -> descoberta: pergunta qual curso
    if not ctx.get("existe") and not ctx.get("descoberta"):
        return brain_deterministico(ont, lead, mensagem_lead, historico)
    # Precedentes do corpus (spec 18): nunca bloqueia a conversa — sem corpus, string vazia
    from atena.corpus.recuperacao import bloco_prompt
    sysp = _system_prompt(ctx, lead.get("nome"),
                          precedentes=bloco_prompt(mensagem_lead, lead.get("curso")))
    turns = _turns(historico, mensagem_lead)
    # tenta o modelo escolhido; se falhar e não for Claude, tenta Claude; só então o determinístico
    tentativas = [None]
    if modelo_atual()[0] != "claude" and chave_do("claude"):
        tentativas.append(f"claude:{os.environ.get('ANTHROPIC_MODEL', 'claude-sonnet-4-6')}")
    for m in tentativas:
        try:
            data = _json(chamar_llm(sysp, turns, modelo=m, ferramenta=_FERRAMENTA))
            if not data or data.get("proxima_acao") not in C.ACOES:
                raise ValueError("saída do LLM inválida")
            # guarda anti-placeholder: ação conversacional exige mensagem de verdade
            msg = (data.get("mensagem_ao_lead") or "").strip()
            if data["proxima_acao"] in (C.CONTINUAR, C.QUALIFICAR, C.DESCARTAR) and len(msg) < 10:
                raise ValueError(f"mensagem degenerada: {msg!r}")
            # OPT-OUT NUNCA é juízo do LLM (21/07/2026): o modelo devolveu
            # 'encerrar_optout' com motivo "Link enviado para inscrição" — confundiu
            # ENCERRAR o atendimento com o lead pedir descadastro, e silenciou de vez
            # um lead de máxima intenção (tinha clicado "Receber link para prosseguir").
            # Opt-out é irreversível e de peso legal (LGPD): só vale se as PALAVRAS DO
            # LEAD pedirem, o que já é detectado deterministicamente no topo desta função.
            if data["proxima_acao"] == C.OPTOUT and not G.detectar_optout(mensagem_lead):
                print(f"[cerebro] LLM pediu opt-out sem o lead pedir — rebaixado p/ continuar "
                      f"(motivo do LLM: {data.get('motivo')!r})", flush=True)
                data["proxima_acao"] = C.CONTINUAR
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
