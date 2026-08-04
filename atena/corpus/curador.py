#!/usr/bin/env python3
"""
Atena · Agente Curador do corpus (spec 18 §2c).

Objetivo declarado pelo usuário: "o sistema aprender continuamente até resolver
todas as dúvidas sem nem mesmo me consultar". A matéria-prima disso é a fala
HUMANA — quando uma pessoa digita a resposta na Central, ela está resolvendo um
problema que o agente não resolveu sozinho. Cada uma dessas é uma lição.

O trabalho do curador NÃO é copiar tudo: é FILTRAR o que merece virar legado.
A maior parte da fala humana é operacional e não generaliza ("já te retorno",
"pode ser amanhã?", "obrigado"). Guardar isso polui a recuperação e piora o
agente. Por isso cada candidata passa por um juízo:

  1. filtro barato (determinístico) descarta o obviamente inútil;
  2. juízo do LLM decide se generaliza e destila pergunta/resposta canônicas;
  3. sem chave de API, cai só no filtro barato e marca `revisar=True` — entra no
     corpus como provisório, nunca se perde a lição.

Fontes, em ordem de valor:
  humano:<user>   texto digitado por uma pessoa      -> lição forte
  llm+edicao:<u>  o LLM escreveu, a pessoa corrigiu  -> lição forte (a correção é o sinal)
  coordenacao     resposta autorizada da coordenação -> lição forte
"""
import os
import re
import sys
import json
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from atena.core.estado import Estado
from atena.corpus.vetor import Corpus, anonimizar

AUTORES_HUMANOS = ("humano:", "llm+edicao:", "coordenacao")
MIN_RESPOSTA = 60          # abaixo disso é bilhete operacional, não resolução
MIN_PERGUNTA = 10

# Falas que nunca são lição, por mais humanas que sejam.
_RUIDO = re.compile(
    r"^\s*(ok|okay|certo|combinado|perfeito|obrigad[oa]|de nada|bom dia|boa tarde|boa noite|"
    r"oi|olá|ol[áa]!|entendi|tudo bem|blz|beleza|valeu|👍|🙏|✅)\W*$", re.I)
_PROMESSA = re.compile(
    r"(vou (verificar|confirmar|checar|consultar|ver)|já (te )?retorno|volto a (falar|te)|"
    r"assim que (eu )?souber|um momento|só um instante|aguarde)", re.I)


def _fala_util(texto):
    """Filtro barato: descarta ruído antes de gastar LLM."""
    t = (texto or "").strip()
    if len(t) < MIN_RESPOSTA or _RUIDO.match(t):
        return False
    if t.startswith("[template") or t.startswith("[recuperação"):
        return False
    # promessa sem conteúdo ("vou confirmar e te aviso") não ensina nada
    if _PROMESSA.search(t) and len(t) < 160:
        return False
    return True


def candidatas(est, desde=None, limite=None):
    """[{card_id, pergunta, resposta, autor, ts, curso}] — fala humana precedida de
    pergunta do lead. É o par (problema, resolução) que queremos aprender."""
    saida = []
    for l in est.listar_leads():
        hist = est.historico(l["card_id"])
        for i, h in enumerate(hist):
            autor = h.get("autor") or ""
            if h.get("papel") != "agente" or not autor.startswith(AUTORES_HUMANOS):
                continue
            if desde and (h.get("ts") or "") < desde:
                continue
            if not _fala_util(h.get("texto")):
                continue
            # pergunta = última fala do lead antes desta resposta
            pergunta = next((hist[j].get("texto") for j in range(i - 1, -1, -1)
                             if hist[j].get("papel") == "lead"
                             and len((hist[j].get("texto") or "").strip()) >= MIN_PERGUNTA), None)
            if not pergunta:
                continue
            saida.append({"card_id": l["card_id"], "curso": l.get("curso"),
                          "pergunta": pergunta.strip(), "resposta": h["texto"].strip(),
                          "autor": autor, "ts": h.get("ts")})
            if limite and len(saida) >= limite:
                return saida
    return saida


_PROMPT = (
    "Você audita o atendimento da Extensão FEQ Unicamp para construir a memória do "
    "sistema. Recebe UMA pergunta de lead e a resposta que um ATENDENTE HUMANO deu.\n"
    "Decida se essa troca vira CONHECIMENTO REUTILIZÁVEL — algo que ajudaria a "
    "responder outro lead com dúvida parecida no futuro.\n\n"
    "REJEITE (util=false) quando for: combinação de horário, tratativa de um caso "
    "único, dado pessoal, promessa sem conteúdo ('vou verificar'), conversa social, "
    "ou algo que só faz sentido naquele contexto específico.\n"
    "ACEITE (util=true) quando ensinar algo que se repete: como funciona um processo, "
    "requisito, política, explicação de conteúdo, contorno de um problema comum.\n\n"
    "Ao aceitar, REESCREVA de forma canônica e impessoal:\n"
    "- pergunta: a dúvida genérica por trás do caso (sem nome, telefone, datas do caso)\n"
    "- resposta: a resolução reutilizável, preservando os FATOS como o humano os deu\n"
    "- tema: 2 a 4 palavras (ex.: 'desconto ex-aluno', 'emissão de certificado')\n"
    "NUNCA invente fato que não esteja na resposta original.\n\n"
    'Responda SOMENTE em JSON: {"util": true|false, "motivo": "<curto>", '
    '"pergunta": "<canônica ou null>", "resposta": "<canônica ou null>", "tema": "<ou null>"}'
)


# Schema de saída do juízo. Sem isto o Claude devolveria o contrato do CÉREBRO
# (tool use é por schema), e todo julgamento cairia em "sem contrato".
_FERRAMENTA_JUIZO = {
    "name": "julgar_licao",
    "description": "Decide se uma troca vira conhecimento reutilizável e a reescreve.",
    "input_schema": {
        "type": "object",
        "properties": {
            "util": {"type": "boolean"},
            "motivo": {"type": "string"},
            "pergunta": {"type": ["string", "null"]},
            "resposta": {"type": ["string", "null"]},
            "tema": {"type": ["string", "null"]},
        },
        "required": ["util", "motivo"],
    },
}


def julgar(par):
    """Juízo do LLM. Sem chave/erro -> aceita provisoriamente (revisar=True): não
    perder a lição é mais importante que classificá-la na hora."""
    from atena.agentes.cerebro import chamar_llm, _json
    entrada = (f"PERGUNTA DO LEAD:\n{anonimizar(par['pergunta'])[:900]}\n\n"
               f"RESPOSTA DO HUMANO:\n{anonimizar(par['resposta'])[:1800]}")
    try:
        d = _json(chamar_llm(_PROMPT, [{"role": "user", "content": entrada}],
                             ferramenta=_FERRAMENTA_JUIZO))
        if not d or "util" not in d:
            raise ValueError("juízo sem contrato")
        return {**d, "revisar": False}
    except Exception as e:
        print(f"[curador] juízo indisponível ({type(e).__name__}) — aceitando p/ revisão", flush=True)
        return {"util": True, "motivo": "sem juízo do LLM", "pergunta": par["pergunta"],
                "resposta": par["resposta"], "tema": None, "revisar": True}


def curar(est=None, corpus=None, limite=40, desde=None, julgador=julgar):
    """Uma passada do curador. Retorna o que virou lição e o que foi descartado.
    Idempotente: a chave inclui card_id+ts, então relançar não duplica."""
    est = est or Estado()
    corpus = corpus or Corpus()
    pares = candidatas(est, desde=desde)
    novos, descartados, docs = [], [], []
    for par in pares[:limite]:
        chave = f"licao:{par['card_id']}:{par['ts']}"
        if corpus.tem(chave):
            continue
        j = julgador(par)
        if not j.get("util"):
            descartados.append({"card_id": par["card_id"], "motivo": j.get("motivo", "")})
            continue
        docs.append({
            "tipo": "licao", "chave": chave,
            "texto": (j.get("pergunta") or par["pergunta"]),      # indexa a pergunta
            "meta": {"resposta": (j.get("resposta") or par["resposta"])[:1500],
                     "tema": j.get("tema"), "curso": par["curso"], "autor": par["autor"],
                     "origem": "humano", "card_id": par["card_id"], "ts": par["ts"],
                     "revisar": j.get("revisar", False)}})
        novos.append({"card_id": par["card_id"], "tema": j.get("tema"),
                      "revisar": j.get("revisar", False)})
    gravados = corpus.indexar(docs) if docs else 0
    return {"candidatas": len(pares), "avaliadas": min(len(pares), limite),
            "licoes": gravados, "descartadas": len(descartados),
            "novas": novos, "rejeitadas": descartados[:10]}


if __name__ == "__main__":
    import atena  # fuso BRT
    print(json.dumps(curar(limite=int(sys.argv[1]) if len(sys.argv) > 1 else 40),
                     ensure_ascii=False, indent=2))
