#!/usr/bin/env python3
"""
Atena · Ingestão do corpus (spec 18 §1.5 e §2c).

Transforma o que o sistema já viveu em memória consultável:
  qa       — pergunta real do lead -> resposta que FOI ENVIADA (precedente bom)
  duvida   — pergunta -> resposta da coordenação (fonte humana autorizada)
  curso    — ficha do curso vinda da ontologia (âncora factual)
  episodio — conversa inteira + desfecho, matéria-prima da avaliação (§3)

Idempotente: cada documento tem `chave` natural, então rodar todo dia não duplica.
"""
import sys
import json
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from atena.core.estado import Estado
from atena.ontologia.servico import Ontologia
from atena.corpus.vetor import Corpus

MIN_PERGUNTA = 12          # abaixo disso é "oi", "sim", "ok" — não é pergunta
MIN_RESPOSTA = 40


def _e_pergunta(t):
    t = (t or "").strip()
    return len(t) >= MIN_PERGUNTA and ("?" in t or len(t.split()) >= 5)


def pares_qa(est):
    """Par (pergunta do lead, resposta enviada logo em seguida). Só conta resposta
    que realmente saiu (status enviado/entregue/lido) — proposta recusada não é
    precedente bom, e mensagem que falhou não foi vista pelo lead."""
    docs = []
    for l in est.listar_leads():
        hist = est.historico(l["card_id"])
        for i, h in enumerate(hist[:-1]):
            prox = hist[i + 1]
            if h.get("papel") != "lead" or prox.get("papel") != "agente":
                continue
            if (prox.get("status") or "") not in ("enviado", "entregue", "lido"):
                continue
            p, r = (h.get("texto") or "").strip(), (prox.get("texto") or "").strip()
            if not _e_pergunta(p) or len(r) < MIN_RESPOSTA or r.startswith("[template"):
                continue
            docs.append({
                "tipo": "qa", "chave": f"qa:{l['card_id']}:{h.get('ts')}",
                # indexa a PERGUNTA (é por ela que se busca); resposta vai no meta
                "texto": p,
                "meta": {"curso": l.get("curso"), "card_id": l["card_id"],
                         "resposta": r[:1200], "ts": h.get("ts")}})
    return docs


def duvidas(est):
    """Resposta da coordenação = fonte autorizada. Descartadas ficam de fora."""
    docs = []
    for d in est.listar_duvidas("respondida"):
        resp = (d.get("resposta") or "").strip()
        if not resp or resp.startswith("[descartada]"):
            continue
        docs.append({"tipo": "duvida", "chave": f"duvida:{d['id']}",
                     "texto": (d.get("pergunta") or "").strip(),
                     "meta": {"resposta": resp[:1200], "card_id": d.get("card_id"),
                              "ts": d.get("criado")}})
    return docs


def cursos(ont):
    """Ficha factual do curso. Fica no corpus para busca por afinidade temática
    (ex.: 'quero algo de segurança de processos' -> cursos certos)."""
    docs = []
    for c in ont.portfolio():
        cod = c.get("codigo")
        full = ont.curso(cod) or {}
        texto = " · ".join(str(x) for x in [
            full.get("nome"), full.get("objetivo"), full.get("conteudo_programatico")] if x)
        if not texto.strip():
            continue
        docs.append({"tipo": "curso", "chave": f"curso:{cod}", "texto": texto[:4000],
                     "meta": {"curso": cod, "nome": full.get("nome"),
                              "inscricao": c.get("inscricao")}})
    return docs


def episodios(est, minimo_turnos=4):
    """Conversa inteira + desfecho. É o insumo do item 3 (avaliação): sem episódio
    rotulado não há recompensa. Só entra conversa com alguma substância."""
    docs = []
    for l in est.listar_leads():
        hist = est.historico(l["card_id"])
        if len(hist) < minimo_turnos:
            continue
        transcricao = "\n".join(
            f"{'LEAD' if h.get('papel') == 'lead' else 'AGENTE'}: {(h.get('texto') or '').strip()}"
            for h in hist if (h.get("texto") or "").strip())
        docs.append({
            "tipo": "episodio", "chave": f"episodio:{l['card_id']}:{len(hist)}",
            "texto": transcricao[:4000],
            "meta": {"curso": l.get("curso"), "card_id": l["card_id"],
                     "desfecho": l.get("fase"), "estado": l.get("estado_conversa"),
                     "optout": bool(l.get("optout")), "turnos": len(hist)}})
    return docs


def rodar(est=None, ont=None, corpus=None, tipos=("qa", "duvida", "curso", "episodio")):
    """Uma passada de ingestão. Retorna o que entrou, por tipo."""
    est = est or Estado()
    ont = ont or Ontologia()
    corpus = corpus or Corpus()
    fontes = {"qa": lambda: pares_qa(est), "duvida": lambda: duvidas(est),
              "curso": lambda: cursos(ont), "episodio": lambda: episodios(est)}
    saida = {}
    for t in tipos:
        docs = fontes[t]()
        saida[t] = {"candidatos": len(docs), "novos": corpus.indexar(docs)}
    saida["total_corpus"] = corpus.estatisticas()["total"]
    return saida


if __name__ == "__main__":
    import atena  # fuso BRT
    print(json.dumps(rodar(), ensure_ascii=False, indent=2))
