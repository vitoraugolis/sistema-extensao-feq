#!/usr/bin/env python3
"""
Atena · Growth — estado do motor de crescimento (spec 15).
Metas/prazos por turma, desempenho de mídia por dia, eventos de funil
(checkout/boleto/inscrição → CAPI) e trilha de ações de orçamento.
Mesmo arquivo SQLite do estado (WAL); mesmas convenções de estado.py.
"""
import sqlite3
import datetime
import threading
from pathlib import Path

DEFAULT_DB = Path(__file__).resolve().parents[1] / "data" / "atena.db"


def _db_padrao():
    """Growth e Estado gravam no MESMO arquivo. Cada um com sua constante já causou
    dano: apontar só o Estado para outro banco (teste/preview) fazia o Growth
    continuar escrevendo no real. Resolve em tempo de chamada, com uma fonte só."""
    from atena.core.estado import DEFAULT_DB as _EST_DB
    return _EST_DB

SCHEMA = """
CREATE TABLE IF NOT EXISTS cursos_metas(
  curso TEXT PRIMARY KEY, turma TEXT, url TEXT,
  meta_real INTEGER, meta_seg INTEGER, prazo TEXT,
  inscritos_extra INTEGER DEFAULT 0, atualizado TEXT);
CREATE TABLE IF NOT EXISTS ad_desempenho(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  dia TEXT, objeto_id TEXT, nome TEXT, campanha TEXT, curso TEXT,
  gasto REAL, impressoes INTEGER, cliques INTEGER, leads INTEGER,
  UNIQUE(dia, objeto_id));
CREATE TABLE IF NOT EXISTS eventos_funil(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  card_id TEXT, curso TEXT, tipo TEXT, ts TEXT, capi TEXT);
CREATE TABLE IF NOT EXISTS acoes_orcamento(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT, tipo TEXT, objeto_id TEXT, detalhe TEXT, aplicado INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS lp_sessoes(
  sessao_id TEXT PRIMARY KEY, curso TEXT, ts TEXT, duracao INTEGER,
  max_scroll INTEGER, ultima_secao TEXT, secoes_json TEXT,
  cta_cliques INTEGER DEFAULT 0, modal_aberto INTEGER DEFAULT 0,
  modal_enviado INTEGER DEFAULT 0, card_id TEXT);
"""

# ordem canônica das seções da LP (bridge-template data-secao) p/ o funil de leitura.
# objetivos/habilidades/depoimentos são condicionais (só cursos com dado oficial).
LP_SECOES = ["hero", "beneficios", "objetivos", "valor", "habilidades", "conteudo", "quem",
            "professor", "depoimentos", "investimento", "inscricao", "relacionados", "faq", "cta"]

# curso, turma, meta_real, meta_seg (+30%), url — as 4 turmas-alvo (spec 15 §1).
# Destino é SEMPRE o formulário /curso/inscricao/ (spec 17 §2), não a página /dados/.
METAS = [
    ("FEQ-0480", "009", 50, 65, "https://ins.extecamp.unicamp.br/curso/inscricao/FEQ-0480/009"),
    ("FEQ-0580", "001", 30, 39, "https://ins.extecamp.unicamp.br/curso/inscricao/FEQ-0580/001"),
    ("FEQ-0333", "001", 25, 33, "https://ins.extecamp.unicamp.br/curso/inscricao/FEQ-0333/001"),
    ("FEQ-1000", "018", 70, 91, "https://ins.extecamp.unicamp.br/curso/inscricao/FEQ-1000/018"),
    # Demais cursos do portfólio (20/07/2026): LP para TODOS, inclusive sem turma
    # aberta — nesse caso a página entra em modo LISTA DE ESPERA (ver ponte.render).
    ("FEQ-0360", "007", 20, 26, "https://ins.extecamp.unicamp.br/curso/inscricao/FEQ-0360/007"),
    ("FEQ-0331", "004", 15, 20, "https://ins.extecamp.unicamp.br/curso/inscricao/FEQ-0331/004"),
    ("FEQ-0299", "001", 20, 26, "https://ins.extecamp.unicamp.br/curso/inscricao/FEQ-0299/001"),
    ("FEQ-0670", "001", 15, 20, "https://ins.extecamp.unicamp.br/curso/inscricao/FEQ-0670/001"),
    # FEQ-0499 sem turma aberta: url vazia — a LP coleta lead (lista de espera).
    ("FEQ-0499", "001", 15, 20, ""),
]


def _agora():
    return datetime.datetime.now().isoformat(timespec="seconds")


class Growth:
    def __init__(self, db=None):
        self.path = ":memory:" if db == ":memory:" else str(db or _db_padrao())
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        if self.path != ":memory:":
            self.conn.execute("PRAGMA journal_mode=WAL")
        self._lock = threading.Lock()
        self.conn.executescript(SCHEMA)
        try:
            self.conn.execute("ALTER TABLE eventos_funil ADD COLUMN valor REAL")  # spec 16
        except sqlite3.OperationalError:
            pass  # coluna já existe
        # spec 17 §2: destino é o formulário de inscrição, não a página de dados
        self.conn.execute("UPDATE cursos_metas SET url=REPLACE(url,'/curso/dados/','/curso/inscricao/')"
                          " WHERE url LIKE '%/curso/dados/%'")
        for c, t, mr, ms, url in METAS:
            self.conn.execute(
                "INSERT OR IGNORE INTO cursos_metas(curso,turma,url,meta_real,meta_seg,atualizado)"
                " VALUES(?,?,?,?,?,?)", (c, t, url, mr, ms, _agora()))
        self.conn.commit()

    # ------------------------------------------------------------- cursos
    def cursos(self):
        with self._lock:
            rows = self.conn.execute("SELECT * FROM cursos_metas").fetchall()
        return [dict(r) for r in rows]

    def set_prazo(self, curso, prazo):
        with self._lock:
            self.conn.execute("UPDATE cursos_metas SET prazo=?, atualizado=? WHERE curso=?",
                              (prazo, _agora(), curso))
            self.conn.commit()

    def set_oferecimento(self, curso, turma, url):
        """Primeiro (e único) writer em tempo de execução de turma/url — até aqui, os
        dois só existiam no seed hardcoded de METAS. Chamado pelo Injetor ao aprovar
        um 'novo_oferecimento' (atena/agentes/ontologista.py). Normaliza espaço em
        branco aqui, na única porta de escrita — é onde importa fazer isso uma vez."""
        with self._lock:
            n = self.conn.execute(
                "UPDATE cursos_metas SET turma=?, url=?, atualizado=? WHERE curso=?",
                ((turma or "").strip(), (url or "").strip(), _agora(), curso)).rowcount
            self.conn.commit()
        return n > 0

    def inscritos(self, curso):
        with self._lock:
            n = self.conn.execute(
                "SELECT COUNT(DISTINCT card_id) FROM eventos_funil"
                " WHERE curso=? AND tipo='inscricao_confirmada'", (curso,)).fetchone()[0]
            extra = self.conn.execute(
                "SELECT inscritos_extra FROM cursos_metas WHERE curso=?", (curso,)).fetchone()
        return n + (extra[0] if extra else 0)

    def vel_7d(self, curso, agora=None):
        """Inscrições confirmadas nos últimos 7 dias / 7 (inscrições/dia)."""
        agora = agora or datetime.datetime.now()
        desde = (agora - datetime.timedelta(days=7)).isoformat(timespec="seconds")
        with self._lock:
            n = self.conn.execute(
                "SELECT COUNT(DISTINCT card_id) FROM eventos_funil"
                " WHERE curso=? AND tipo='inscricao_confirmada' AND ts>=?", (curso, desde)).fetchone()[0]
        return n / 7.0

    # -------------------------------------------------------------- mídia
    def add_desempenho(self, r):
        with self._lock:
            self.conn.execute(
                "INSERT OR REPLACE INTO ad_desempenho"
                "(dia,objeto_id,nome,campanha,curso,gasto,impressoes,cliques,leads)"
                " VALUES(?,?,?,?,?,?,?,?,?)",
                (r["dia"], r["objeto_id"], r.get("nome", ""), r.get("campanha", ""),
                 r.get("curso", ""), r.get("gasto", 0), r.get("impressoes", 0),
                 r.get("cliques", 0), r.get("leads", 0)))
            self.conn.commit()

    def desempenho(self, dias=7, agora=None):
        agora = agora or datetime.datetime.now()
        desde = (agora - datetime.timedelta(days=dias)).date().isoformat()
        with self._lock:
            rows = self.conn.execute("SELECT * FROM ad_desempenho WHERE dia>=?", (desde,)).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------- funil
    def add_evento(self, card_id, curso, tipo, ts=None, capi=None, valor=None):
        with self._lock:
            self.conn.execute(
                "INSERT INTO eventos_funil(card_id,curso,tipo,ts,capi,valor) VALUES(?,?,?,?,?,?)",
                (card_id, curso, tipo, ts or _agora(), capi, valor))
            self.conn.commit()

    def eventos(self, card_id=None, tipo=None):
        q, args = "SELECT * FROM eventos_funil WHERE 1=1", []
        if card_id:
            q += " AND card_id=?"; args.append(card_id)
        if tipo:
            q += " AND tipo=?"; args.append(tipo)
        with self._lock:
            rows = self.conn.execute(q + " ORDER BY ts", args).fetchall()
        return [dict(r) for r in rows]

    def checkouts_pendentes(self):
        """Leads com inicio_inscricao/boleto_gerado e SEM inscricao_confirmada.
        Retorna [{card_id, curso, ultimo_checkout, tipo_checkout, toques, ultimo_toque}]."""
        with self._lock:
            rows = self.conn.execute("""
                SELECT card_id, MAX(ts) ultimo_checkout,
                  (SELECT curso FROM eventos_funil t WHERE t.card_id=e.card_id
                     AND t.tipo IN ('inicio_inscricao','boleto_gerado')
                     ORDER BY t.ts DESC LIMIT 1) curso,
                  (SELECT tipo FROM eventos_funil t WHERE t.card_id=e.card_id
                     AND t.tipo IN ('inicio_inscricao','boleto_gerado')
                     ORDER BY t.ts DESC LIMIT 1) tipo_checkout,
                  (SELECT COUNT(*) FROM eventos_funil r WHERE r.card_id=e.card_id
                     AND r.tipo='recuperacao_enviada') toques,
                  (SELECT MAX(ts) FROM eventos_funil r WHERE r.card_id=e.card_id
                     AND r.tipo='recuperacao_enviada') ultimo_toque
                FROM eventos_funil e
                WHERE tipo IN ('inicio_inscricao','boleto_gerado')
                  AND card_id NOT IN (SELECT card_id FROM eventos_funil
                                      WHERE tipo IN ('inscricao_confirmada','matricula_confirmada'))
                GROUP BY card_id""").fetchall()
        return [dict(r) for r in rows]

    def verificacoes_pendentes(self):
        """Fila da aba Verificação (spec 17 §8b): checkouts sem conversão E sem
        veredito humano. 'Não inscreveu' grava verificacao_negativa e sai daqui
        (mas segue na régua); 'Inscreveu' grava inscricao_confirmada e sai das duas."""
        with self._lock:
            negs = {r[0] for r in self.conn.execute(
                "SELECT DISTINCT card_id FROM eventos_funil WHERE tipo='verificacao_negativa'")}
        return [p for p in self.checkouts_pendentes() if p["card_id"] not in negs]

    # --------------------------------------------------- telemetria da LP
    def upsert_lp_sessao(self, d):
        """Resumo de sessão da LP (beacon). Upsert por sessao_id preservando o
        card_id já vinculado (o beacon de saída chega depois do modal)."""
        import json as _json
        with self._lock:
            self.conn.execute("""
                INSERT INTO lp_sessoes(sessao_id,curso,ts,duracao,max_scroll,ultima_secao,
                                       secoes_json,cta_cliques,modal_aberto,modal_enviado)
                VALUES(?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(sessao_id) DO UPDATE SET
                  duracao=excluded.duracao, max_scroll=excluded.max_scroll,
                  ultima_secao=excluded.ultima_secao, secoes_json=excluded.secoes_json,
                  cta_cliques=excluded.cta_cliques,
                  modal_aberto=MAX(modal_aberto, excluded.modal_aberto),
                  modal_enviado=MAX(modal_enviado, excluded.modal_enviado)""",
                (str(d.get("sessao_id", ""))[:64], str(d.get("curso", ""))[:16], _agora(),
                 int(d.get("duracao") or 0), int(d.get("max_scroll") or 0),
                 str(d.get("ultima_secao", ""))[:32],
                 _json.dumps(d.get("secoes") or {})[:2000],
                 int(d.get("cta_cliques") or 0), 1 if d.get("modal_aberto") else 0,
                 1 if d.get("modal_enviado") else 0))
            self.conn.commit()

    def vincular_sessao(self, sessao_id, card_id):
        with self._lock:
            self.conn.execute("UPDATE lp_sessoes SET card_id=? WHERE sessao_id=?",
                              (card_id, sessao_id))
            self.conn.commit()

    def lp_analytics(self, curso=None, dias=30):
        """Funil de leitura da LP: onde o lead chega, quanto fica e onde abandona."""
        import json as _json
        import statistics
        desde = (datetime.datetime.now() - datetime.timedelta(days=dias)).isoformat(timespec="seconds")
        q, args = "SELECT * FROM lp_sessoes WHERE ts>=?", [desde]
        if curso:
            q += " AND curso=?"; args.append(curso)
        with self._lock:
            rows = [dict(r) for r in self.conn.execute(q, args).fetchall()]
        n = len(rows)
        if not n:
            return {"sessoes": 0}
        secoes = {}
        for s in LP_SECOES:
            tempos = []
            alcancou = 0
            for r in rows:
                sj = _json.loads(r["secoes_json"] or "{}")
                if s in sj:
                    alcancou += 1
                    tempos.append(sj[s])
            secoes[s] = {"alcance_pct": round(100 * alcancou / n),
                         "tempo_mediano_s": round(statistics.median(tempos)) if tempos else 0}
        quedas = [(LP_SECOES[i], secoes[LP_SECOES[i - 1]]["alcance_pct"] - secoes[LP_SECOES[i]]["alcance_pct"])
                  for i in range(1, len(LP_SECOES))]
        maior_abandono = max(quedas, key=lambda x: x[1]) if quedas else (None, 0)
        saidas = {}
        for r in rows:
            if r["ultima_secao"]:
                saidas[r["ultima_secao"]] = saidas.get(r["ultima_secao"], 0) + 1
        return {"sessoes": n, "dias": dias, "curso": curso or "todos",
                "duracao_mediana_s": round(statistics.median([r["duracao"] or 0 for r in rows])),
                "scroll_max_mediano_pct": round(statistics.median([r["max_scroll"] or 0 for r in rows])),
                "funil_secoes": secoes,
                "maior_abandono": {"antes_de": maior_abandono[0], "queda_pct": maior_abandono[1]},
                "saiu_em": dict(sorted(saidas.items(), key=lambda kv: -kv[1])),
                "taxas": {"cta_pct": round(100 * sum(1 for r in rows if r["cta_cliques"]) / n),
                          "modal_aberto_pct": round(100 * sum(1 for r in rows if r["modal_aberto"]) / n),
                          "modal_enviado_pct": round(100 * sum(1 for r in rows if r["modal_enviado"]) / n)}}

    # ---------------------------------------------------------- orçamento
    def add_acao(self, tipo, objeto_id, detalhe, aplicado=0):
        with self._lock:
            self.conn.execute(
                "INSERT INTO acoes_orcamento(ts,tipo,objeto_id,detalhe,aplicado) VALUES(?,?,?,?,?)",
                (_agora(), tipo, objeto_id, detalhe, 1 if aplicado else 0))
            self.conn.commit()

    def acao_recente(self, tipo, objeto_id, dias=3, agora=None):
        agora = agora or datetime.datetime.now()
        desde = (agora - datetime.timedelta(days=dias)).isoformat(timespec="seconds")
        with self._lock:
            r = self.conn.execute(
                "SELECT 1 FROM acoes_orcamento WHERE tipo=? AND objeto_id=? AND ts>=?",
                (tipo, objeto_id, desde)).fetchone()
        return bool(r)

    def listar_acoes(self, limite=50):
        with self._lock:
            rows = self.conn.execute(
                "SELECT * FROM acoes_orcamento ORDER BY id DESC LIMIT ?", (limite,)).fetchall()
        return [dict(r) for r in rows]
