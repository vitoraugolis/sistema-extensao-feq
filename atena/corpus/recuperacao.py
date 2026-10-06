#!/usr/bin/env python3
"""
Atena · Recuperação de precedentes para o cérebro (spec 18 §1.7).

REGRA DE OURO: precedente NÃO é fato. Preço, data, carga e requisito continuam
vindo só da ontologia, e o Revisor segue fail-closed. O que o precedente traz é
COBERTURA — mostra que uma pergunta parecida já foi respondida e como. Sem isso o
agente escalava para a coordenação perguntas cuja resposta o próprio histórico já
tinha (achado real de 02/08/2026: "desconto para ex-aluno" foi escalado mesmo
existindo resposta pronta no histórico).

Nunca quebra a conversa: qualquer falha (corpus vazio, embedding fora do ar)
devolve string vazia e o cérebro segue como antes.
"""
import re
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

LIMIAR = 0.55        # abaixo disso é ruído; medido sobre os dados reais
MAX_PRECEDENTES = 3

# Respostas que NÃO servem de precedente: são falhas de atendimento, não acertos.
_RUINS = ("não tenho de pronto", "nao tenho de pronto", "vou consultar a coordenação",
          "vou confirmar com a coordenação", "vou pedir para alguém da equipe",
          "[template", "só um instante")

_CORPUS = None


def _corpus():
    global _CORPUS
    if _CORPUS is None:
        from atena.corpus.vetor import Corpus
        _CORPUS = Corpus()
    return _CORPUS


# Data é FATO do oferecimento e vem do registro do curso, nunca de conversa antiga:
# precedente que cita data envelhece na virada de turma (44 respostas do FEQ-1000
# citavam o calendário da 018 quando a 019 abriu) e o Revisor bloquearia a cópia.
_DATA_RE = re.compile(r"\b\d{1,2}/\d{1,2}(/\d{2,4})?\b")


def _util(resposta):
    r = (resposta or "").strip().lower()
    return len(r) >= 40 and not any(x in r for x in _RUINS) and not _DATA_RE.search(r)


def precedentes(mensagem, curso=None, k=MAX_PRECEDENTES, corpus=None):
    """[{pergunta, resposta, escore, tipo}] — só o que passou do limiar e é útil."""
    if not (mensagem or "").strip():
        return []
    try:
        c = corpus or _corpus()
        achados, vistos = [], set()
        # Busca no curso do lead E fora dele. Muita pergunta é de POLÍTICA, não do
        # curso: "desconto para ex-aluno da Unicamp" vale para todos, e a melhor
        # resposta que temos veio de outro curso. Como precedente não é fonte de
        # fato (o registro do curso é), aproveitar entre cursos é seguro e útil.
        for escopo in ([curso, None] if curso else [None]):
            # ordem de valor: lição curada de resolução HUMANA > resposta da coordenação
            # > precedente qualquer. O que uma pessoa escreveu para resolver o problema
            # vale mais que o que o próprio agente já respondeu.
            for tipo in ("licao", "duvida", "qa"):
                for x in c.buscar(mensagem, k=k * 2, tipo=tipo, curso=escopo, minimo=LIMIAR):
                    resp = (x["meta"] or {}).get("resposta")
                    if not _util(resp) or x["chave"] in vistos:
                        continue
                    vistos.add(x["chave"])
                    achados.append({"pergunta": x["texto"], "resposta": resp,
                                    "escore": x["escore"], "tipo": tipo,
                                    "mesmo_curso": (x["meta"] or {}).get("curso") == curso})
        ordem = {"licao": 0, "duvida": 1, "qa": 2}
        achados.sort(key=lambda a: (ordem.get(a["tipo"], 9), not a["mesmo_curso"], -a["escore"]))
        return achados[:k]
    except Exception as e:
        print(f"[corpus] recuperação indisponível ({type(e).__name__}: {e})", flush=True)
        return []


def bloco_prompt(mensagem, curso=None, corpus=None):
    """Trecho pronto para o system prompt — vazio quando não há precedente bom."""
    achados = precedentes(mensagem, curso, corpus=corpus)
    if not achados:
        return ""
    linhas = "\n".join(
        f"- Já perguntaram: \"{a['pergunta'][:180]}\"\n  Respondemos: \"{a['resposta'][:400]}\""
        + {"licao": "   [LIÇÃO — foi assim que um humano resolveu este problema]",
           "duvida": "   [resposta da COORDENAÇÃO — fonte autorizada]"}.get(a["tipo"], "")
        for a in achados)
    return (
        "PRECEDENTES (como já respondemos perguntas parecidas):\n" + linhas + "\n"
        "Use-os para SABER QUE VOCÊ CONSEGUE RESPONDER — não escale para a coordenação "
        "algo que já está respondido aqui. Mas ATENÇÃO: precedente NÃO é fonte de fato. "
        "Preço, data, carga, requisito e ementa vêm SEMPRE do registro do curso acima "
        "(um precedente pode ser de turma antiga e estar desatualizado). Também não copie "
        "o estilo do precedente — siga o TOM definido no começo destas instruções.\n")
