#!/usr/bin/env python3
"""
Atena · Corpus vetorial (spec 18 §1).

Por que SQLite e não Chroma/FAISS/Qdrant: a VPS tem 414 MB de RAM (177 livres) e
não tem numpy. Subir um banco vetorial ao lado do Flask derruba o serviço, e
modelo de embedding local (torch ≈ 2 GB) é impensável. O volume real é pequeno
(~1.3k conversas), então busca exaustiva sobre alguns milhares de vetores de 256
dimensões custa menos que a latência de rede de um serviço externo.

Decisões que sustentam isso:
  • embedding de 256 dims (text-embedding-3-small é Matryoshka: truncar preserva
    quase toda a qualidade) -> 1 KB por documento em vez de 6 KB;
  • vetor NORMALIZADO na escrita -> similaridade de cosseno vira produto escalar;
  • busca por STREAMING do cursor (nunca materializa a tabela inteira em memória);
  • sem chave de API, embedding determinístico por hashing — o sistema funciona e
    é testável offline, como o resto do projeto (dry-run do WhatsApp/Meta).
"""
import os
import re
import json
import math
import heapq
import array
import sqlite3
import hashlib
import pathlib
import datetime
import threading

DIM = 256
MODELO_ONLINE = "text-embedding-3-small"
DB_PADRAO = pathlib.Path(__file__).resolve().parents[1] / "data" / "atena.db"

ESQUEMA = """
CREATE TABLE IF NOT EXISTS corpus(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  tipo TEXT, chave TEXT UNIQUE, texto TEXT, meta TEXT,
  emb BLOB, dim INTEGER, modelo TEXT, ts TEXT);
CREATE INDEX IF NOT EXISTS ix_corpus_tipo ON corpus(tipo);
"""

# --------------------------------------------------------------------- LGPD
_TEL = re.compile(r"\+?\d[\d\s().-]{7,}\d")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
_CPF = re.compile(r"\b\d{3}\.?\d{3}\.?\d{3}-?\d{2}\b")
_URL_TOKEN = re.compile(r"https?://\S*[?&](?:token|key|auth)=\S+", re.I)


def anonimizar(texto):
    """Tira PII antes de indexar. O corpus é um índice de COMO conversamos,
    não um cadastro de pessoas (spec 18 §1.6)."""
    t = texto or ""
    t = _URL_TOKEN.sub("[link]", t)
    t = _EMAIL.sub("[email]", t)
    t = _CPF.sub("[cpf]", t)
    t = _TEL.sub("[telefone]", t)
    return t.strip()


# ---------------------------------------------------------------- embeddings
def _normalizar(vec):
    n = math.sqrt(sum(v * v for v in vec)) or 1.0
    return array.array("f", [v / n for v in vec])


def _embedding_offline(texto, dim=DIM):
    """Hashing de palavras e bigramas em `dim` posições. Determinístico, sem rede.
    Qualidade menor que um modelo real — serve para dev/teste e como degradação
    graciosa, nunca como escolha preferida quando há chave."""
    vec = [0.0] * dim
    palavras = re.findall(r"\w+", (texto or "").lower())
    grams = palavras + [f"{a}_{b}" for a, b in zip(palavras, palavras[1:])]
    for g in grams:
        h = hashlib.blake2b(g.encode(), digest_size=8).digest()
        i = int.from_bytes(h[:4], "big") % dim
        sinal = 1.0 if h[4] % 2 else -1.0        # hashing com sinal reduz colisão
        vec[i] += sinal
    return _normalizar(vec)


def _embedding_online(textos, dim=DIM):
    """OpenAI embeddings. Lote de uma vez (a API aceita lista) — 1 ida de rede."""
    import requests
    chave = os.environ.get("OPENAI_API_KEY")
    r = requests.post("https://api.openai.com/v1/embeddings", timeout=60,
                      headers={"Authorization": f"Bearer {chave}"},
                      json={"model": MODELO_ONLINE, "input": textos, "dimensions": dim})
    if r.status_code != 200:
        raise RuntimeError(f"embeddings {r.status_code}: {r.text[:120]}")
    dados = sorted(r.json()["data"], key=lambda d: d["index"])
    return [_normalizar(d["embedding"]) for d in dados]


def embutir(textos, dim=DIM):
    """(vetores, modelo). Cai no offline sem chave OU se a API falhar — o ciclo de
    estudo não pode quebrar por indisponibilidade de terceiro."""
    textos = [t or "" for t in textos]
    if os.environ.get("OPENAI_API_KEY"):
        try:
            return _embedding_online(textos, dim), MODELO_ONLINE
        except Exception as e:
            print(f"[corpus] embedding online falhou ({type(e).__name__}: {e}) — "
                  f"usando hashing offline", flush=True)
    return [_embedding_offline(t, dim) for t in textos], "hashing-offline"


# -------------------------------------------------------------------- store
class Corpus:
    def __init__(self, db=None):
        self.path = ":memory:" if db == ":memory:" else str(db or DB_PADRAO)
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            if self.path != ":memory:":
                self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.executescript(ESQUEMA)
            self.conn.commit()

    # ---------------------------------------------------------- escrita
    def indexar(self, docs, lote=64):
        """docs: [{tipo, chave, texto, meta}]. Idempotente pela `chave` — o ciclo de
        estudo roda todo dia sem duplicar. Retorna quantos foram gravados."""
        novos = [d for d in docs if d.get("texto", "").strip() and not self.tem(d["chave"])]
        gravados = 0
        for i in range(0, len(novos), lote):
            bloco = novos[i:i + lote]
            textos = [anonimizar(d["texto"])[:4000] for d in bloco]
            vetores, modelo = embutir(textos)
            agora = datetime.datetime.now().isoformat(timespec="seconds")
            with self._lock:
                self.conn.executemany(
                    "INSERT OR IGNORE INTO corpus(tipo,chave,texto,meta,emb,dim,modelo,ts)"
                    " VALUES(?,?,?,?,?,?,?,?)",
                    [(d.get("tipo", "qa"), d["chave"], txt,
                      json.dumps(d.get("meta") or {}, ensure_ascii=False),
                      v.tobytes(), len(v), modelo, agora)
                     for d, txt, v in zip(bloco, textos, vetores)])
                self.conn.commit()
            gravados += len(bloco)
        return gravados

    def tem(self, chave):
        with self._lock:
            return self.conn.execute("SELECT 1 FROM corpus WHERE chave=?",
                                     (chave,)).fetchone() is not None

    # ----------------------------------------------------------- leitura
    def buscar(self, consulta, k=5, tipo=None, curso=None, minimo=0.0):
        """Top-k por cosseno. Faz STREAMING do cursor: memória O(k), não O(n) —
        requisito da VPS de 177 MB livres."""
        q = embutir([consulta])[0][0]
        sql = "SELECT id,tipo,chave,texto,meta,emb,dim FROM corpus"
        cond, args = [], []
        if tipo:
            cond.append("tipo=?"); args.append(tipo)
        if curso:                      # empurra o filtro para o SQL, não para o Python.
            # Casa o CAMPO curso, não o código solto: o código aparece dentro da
            # `resposta` de outros documentos e o filtro frouxo trazia curso errado.
            cond.append("meta LIKE ?"); args.append(f'%"curso": "{curso}"%')
        if cond:
            sql += " WHERE " + " AND ".join(cond)
        melhores = []
        with self._lock:
            cur = self.conn.execute(sql, args)
            while True:
                linhas = cur.fetchmany(256)
                if not linhas:
                    break
                for r in linhas:
                    if r["dim"] != len(q):
                        continue        # embedding de outra geração: ignora, não mistura
                    v = array.array("f"); v.frombytes(r["emb"])
                    escore = sum(a * b for a, b in zip(q, v))   # ambos normalizados
                    if escore < minimo:
                        continue
                    item = (escore, r["id"], r["tipo"], r["chave"], r["texto"], r["meta"])
                    if len(melhores) < k:
                        heapq.heappush(melhores, item)
                    elif escore > melhores[0][0]:
                        heapq.heapreplace(melhores, item)
        saida = sorted(melhores, reverse=True)
        return [{"escore": round(e, 4), "id": i, "tipo": t, "chave": c, "texto": tx,
                 "meta": json.loads(m or "{}")} for e, i, t, c, tx, m in saida]

    def estatisticas(self):
        with self._lock:
            por_tipo = {r["tipo"]: r["n"] for r in self.conn.execute(
                "SELECT tipo, COUNT(*) n FROM corpus GROUP BY tipo")}
            tot = self.conn.execute("SELECT COUNT(*) n, MAX(ts) u FROM corpus").fetchone()
            modelos = [r["modelo"] for r in self.conn.execute(
                "SELECT DISTINCT modelo FROM corpus")]
        return {"total": tot["n"], "atualizado": tot["u"], "por_tipo": por_tipo,
                "modelos": modelos, "dim": DIM}

    def limpar(self, tipo=None):
        with self._lock:
            n = self.conn.execute("DELETE FROM corpus" + (" WHERE tipo=?" if tipo else ""),
                                  (tipo,) if tipo else ()).rowcount
            self.conn.commit()
        return n
