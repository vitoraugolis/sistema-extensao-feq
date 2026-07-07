#!/usr/bin/env python3
"""
Atena · Estado persistido (SQLite, WAL).
Leads, conversas, decisões (trace/transparência), tarefas, cards vistos e
propostas do Injetor. Fonte única do que a Central de Operação mostra.
"""
import re
import json
import sqlite3
import datetime
import threading
from pathlib import Path

DEFAULT_DB = Path(__file__).resolve().parents[1] / "data" / "atena.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS leads(
  card_id TEXT PRIMARY KEY, nome TEXT, telefone TEXT, curso TEXT,
  fase TEXT DEFAULT 'Lead', estado_conversa TEXT DEFAULT 'aguardando_1o_contato',
  escolaridade TEXT, optout INTEGER DEFAULT 0, motivo TEXT,
  criado TEXT, atualizado TEXT);
CREATE TABLE IF NOT EXISTS conversas(
  id INTEGER PRIMARY KEY AUTOINCREMENT, card_id TEXT, papel TEXT, texto TEXT, ts TEXT);
CREATE TABLE IF NOT EXISTS decisoes(
  id INTEGER PRIMARY KEY AUTOINCREMENT, card_id TEXT, agente TEXT, acao TEXT,
  mensagem TEXT, motivo TEXT, ancoras_json TEXT, aprovado_revisor INTEGER,
  bloqueios_json TEXT, status TEXT, ts TEXT);
CREATE TABLE IF NOT EXISTS tarefas(
  id INTEGER PRIMARY KEY AUTOINCREMENT, card_id TEXT, tipo TEXT,
  agendado_para TEXT, status TEXT DEFAULT 'pendente');
CREATE TABLE IF NOT EXISTS cards_vistos(card_id TEXT PRIMARY KEY, visto_em TEXT);
CREATE TABLE IF NOT EXISTS propostas(
  id TEXT PRIMARY KEY, autor TEXT, criado TEXT, texto TEXT, alvo TEXT, tipo TEXT,
  diff_json TEXT, impacto_json TEXT, validacao_json TEXT, status TEXT);
CREATE TABLE IF NOT EXISTS auditoria(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ator TEXT, acao TEXT, alvo TEXT, ts TEXT);
CREATE TABLE IF NOT EXISTS duvidas(
  id INTEGER PRIMARY KEY AUTOINCREMENT, card_id TEXT, pergunta TEXT, contexto TEXT,
  resposta TEXT, status TEXT DEFAULT 'aberta', criado TEXT, respondido_em TEXT);
CREATE TABLE IF NOT EXISTS config(chave TEXT PRIMARY KEY, valor TEXT);
CREATE TABLE IF NOT EXISTS usuarios(
  usuario TEXT PRIMARY KEY, senha_hash TEXT, papel TEXT, ativo INTEGER DEFAULT 1, criado TEXT);
"""

FASES = ["Lead", "Prospect", "Inscrito", "Matriculado", "Em espera", "Perdido"]


def _agora():
    return datetime.datetime.now().isoformat(timespec="seconds")


class Estado:
    def __init__(self, db=None):
        self.path = ":memory:" if db == ":memory:" else str(db or DEFAULT_DB)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        if self.path != ":memory:":
            self.conn.execute("PRAGMA journal_mode=WAL")
        self._lock = threading.Lock()
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    # ---------------------------------------------------------------- leads
    def upsert_lead(self, card_id, **campos):
        with self._lock:
            existe = self.conn.execute("SELECT 1 FROM leads WHERE card_id=?", (card_id,)).fetchone()
            if existe:
                self._set(card_id, **campos)
            else:
                campos.setdefault("criado", _agora())
                campos["atualizado"] = _agora()
                cols = ["card_id"] + list(campos)
                vals = [card_id] + list(campos.values())
                ph = ",".join("?" * len(cols))
                self.conn.execute(f"INSERT INTO leads({','.join(cols)}) VALUES({ph})", vals)
                self.conn.commit()
        return card_id

    def _set(self, card_id, **campos):
        if not campos:
            return
        campos["atualizado"] = _agora()
        sets = ",".join(f"{k}=?" for k in campos)
        self.conn.execute(f"UPDATE leads SET {sets} WHERE card_id=?", list(campos.values()) + [card_id])
        self.conn.commit()

    def set_lead(self, card_id, **campos):
        with self._lock:
            self._set(card_id, **campos)

    def get_lead(self, card_id):
        r = self.conn.execute("SELECT * FROM leads WHERE card_id=?", (card_id,)).fetchone()
        return dict(r) if r else None

    def listar_leads(self):
        return [dict(r) for r in self.conn.execute("SELECT * FROM leads ORDER BY atualizado DESC")]

    def lead_por_telefone(self, telefone):
        d = re.sub(r"\D", "", telefone or "")
        if len(d) < 8:
            return None
        for l in self.listar_leads():  # ponytail: scan linear; indexar por telefone se crescer
            e = re.sub(r"\D", "", l["telefone"] or "")
            # match por sufixo (bidirecional) tolera +55/DDD divergentes, como o FARO
            if len(e) >= 8 and (e.endswith(d) or d.endswith(e)):
                return l["card_id"]
        return None

    # -------------------------------------------------------------- usuarios
    def add_usuario(self, usuario, senha_hash, papel):
        with self._lock:
            self.conn.execute(
                "INSERT INTO usuarios(usuario,senha_hash,papel,criado) VALUES(?,?,?,?)",
                (usuario, senha_hash, papel, _agora()))
            self.conn.commit()

    def get_usuario(self, usuario):
        r = self.conn.execute("SELECT * FROM usuarios WHERE usuario=?", (usuario,)).fetchone()
        return dict(r) if r else None

    def listar_usuarios(self):
        return [dict(r) for r in self.conn.execute(
            "SELECT usuario,papel,ativo,criado FROM usuarios ORDER BY criado")]

    def set_usuario_ativo(self, usuario, ativo):
        with self._lock:
            self.conn.execute("UPDATE usuarios SET ativo=? WHERE usuario=?", (int(ativo), usuario))
            self.conn.commit()

    # ---------------------------------------------------------------- config
    def get_config(self, chave, default=None):
        r = self.conn.execute("SELECT valor FROM config WHERE chave=?", (chave,)).fetchone()
        return r["valor"] if r else default

    def set_config(self, chave, valor):
        with self._lock:
            self.conn.execute("INSERT OR REPLACE INTO config(chave,valor) VALUES(?,?)", (chave, valor))
            self.conn.commit()

    # --------------------------------------------------------------- duvidas
    def add_duvida(self, card_id, pergunta, contexto=""):
        with self._lock:
            cur = self.conn.execute(
                "INSERT INTO duvidas(card_id,pergunta,contexto,criado) VALUES(?,?,?,?)",
                (card_id, pergunta, contexto, _agora()))
            self.conn.commit()
            return cur.lastrowid

    def listar_duvidas(self, status="aberta"):
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM duvidas WHERE status=? ORDER BY id DESC", (status,))]

    def get_duvida(self, did):
        r = self.conn.execute("SELECT * FROM duvidas WHERE id=?", (did,)).fetchone()
        return dict(r) if r else None

    def responder_duvida(self, did, resposta, status="respondida"):
        with self._lock:
            self.conn.execute(
                "UPDATE duvidas SET resposta=?, status=?, respondido_em=? WHERE id=?",
                (resposta, status, _agora(), did))
            self.conn.commit()

    # ------------------------------------------------------------- auditoria
    def log_acao(self, ator, acao, alvo=""):
        with self._lock:
            self.conn.execute("INSERT INTO auditoria(ator,acao,alvo,ts) VALUES(?,?,?,?)",
                              (ator, acao, alvo, _agora()))
            self.conn.commit()

    def listar_auditoria(self, limite=100):
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM auditoria ORDER BY id DESC LIMIT ?", (limite,))]

    # ------------------------------------------------------------ conversas
    def add_conversa(self, card_id, papel, texto):
        with self._lock:
            self.conn.execute("INSERT INTO conversas(card_id,papel,texto,ts) VALUES(?,?,?,?)",
                              (card_id, papel, texto, _agora()))
            self.conn.commit()

    def historico(self, card_id):
        return [dict(r) for r in self.conn.execute(
            "SELECT papel,texto,ts FROM conversas WHERE card_id=? ORDER BY id", (card_id,))]

    def ultima_lead_msg(self, card_id):
        r = self.conn.execute(
            "SELECT texto FROM conversas WHERE card_id=? AND papel='lead' ORDER BY id DESC LIMIT 1",
            (card_id,)).fetchone()
        return r["texto"] if r else None

    # ------------------------------------------------------------- decisoes
    def add_decisao(self, card_id, agente, acao, mensagem=None, motivo="", ancoras=None,
                    aprovado_revisor=1, bloqueios=None, status="acao"):
        with self._lock:
            self.conn.execute(
                """INSERT INTO decisoes(card_id,agente,acao,mensagem,motivo,ancoras_json,
                   aprovado_revisor,bloqueios_json,status,ts) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (card_id, agente, acao, mensagem, motivo, json.dumps(ancoras or []),
                 int(aprovado_revisor), json.dumps(bloqueios or []), status, _agora()))
            self.conn.commit()

    def decisao_pendente(self, card_id):
        r = self.conn.execute(
            "SELECT * FROM decisoes WHERE card_id=? AND status='proposta' ORDER BY id DESC LIMIT 1",
            (card_id,)).fetchone()
        return dict(r) if r else None

    def listar_decisoes(self, card_id):
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM decisoes WHERE card_id=? ORDER BY id DESC", (card_id,))]

    def set_decisao_status(self, decisao_id, status):
        with self._lock:
            self.conn.execute("UPDATE decisoes SET status=? WHERE id=?", (status, decisao_id))
            self.conn.commit()

    def ultima_lead_ts(self, card_id):
        r = self.conn.execute(
            "SELECT ts FROM conversas WHERE card_id=? AND papel='lead' ORDER BY id DESC LIMIT 1",
            (card_id,)).fetchone()
        return r["ts"] if r else None

    # -------------------------------------------------------------- métricas
    def stats_funil(self):
        out = {f: 0 for f in FASES}
        for r in self.conn.execute("SELECT fase, COUNT(*) n FROM leads GROUP BY fase"):
            out[r["fase"]] = r["n"]
        return out

    # ------------------------------------------------------------- propostas
    def add_proposta(self, pid, autor, texto, alvo, tipo, diff, impacto, validacao, status="proposta"):
        with self._lock:
            self.conn.execute(
                """INSERT OR REPLACE INTO propostas(id,autor,criado,texto,alvo,tipo,
                   diff_json,impacto_json,validacao_json,status) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (pid, autor, _agora(), texto, alvo, tipo, json.dumps(diff),
                 json.dumps(impacto), json.dumps(validacao), status))
            self.conn.commit()

    def get_proposta(self, pid):
        r = self.conn.execute("SELECT * FROM propostas WHERE id=?", (pid,)).fetchone()
        if not r:
            return None
        d = dict(r)
        for k in ("diff_json", "impacto_json", "validacao_json"):
            d[k.replace("_json", "")] = json.loads(d.pop(k) or "null")
        return d

    def listar_propostas(self, status=None):
        q = "SELECT * FROM propostas" + (" WHERE status=?" if status else "") + " ORDER BY criado DESC"
        rows = self.conn.execute(q, (status,) if status else ()).fetchall()
        return [self.get_proposta(r["id"]) for r in rows]

    def set_proposta_status(self, pid, status):
        with self._lock:
            self.conn.execute("UPDATE propostas SET status=? WHERE id=?", (status, pid))
            self.conn.commit()

    # ------------------------------------------------------------------ seed
    def seed_demo(self):
        if self.conn.execute("SELECT COUNT(*) c FROM leads").fetchone()["c"]:
            return
        plano = [
            ("Lead", 10, "aguardando_resposta"), ("Prospect", 6, "qualificado"),
            ("Inscrito", 4, "qualificado"), ("Matriculado", 2, "qualificado"),
            ("Em espera", 3, "em_espera"), ("Perdido", 5, "descartado"),
        ]
        cursos = ["FEQ-0480", "FEQ-1000", "FEQ-0580", "FEQ-0331", "FEQ-0655", "FEQ-0360"]
        i = 0
        for fase, n, ec in plano:
            for _ in range(n):
                i += 1
                self.upsert_lead(f"seed-{i:03d}", nome=f"Lead {i:03d}", telefone=f"+55 19 9{i:08d}",
                                 curso=cursos[i % len(cursos)], fase=fase, estado_conversa=ec,
                                 escolaridade="Superior Completo" if fase != "Perdido" else "Ensino Médio Completo")
        # lead escalado (gera item de escalonamento na fila)
        self.upsert_lead("seed-esc", nome="João Souza", telefone="+55 19 999990001",
                         curso="FEQ-0360", fase="Lead", estado_conversa="escalado_humano",
                         escolaridade="Superior Completo", motivo="pedido de desconto")
        # conversa viva com proposta aguardando aprovação
        self.upsert_lead("demo-marina", nome="Marina Alves", telefone="+55 19 999990002",
                         curso="FEQ-0480", fase="Lead", estado_conversa="qualificacao",
                         escolaridade="Superior Completo")
        self.add_conversa("demo-marina", "lead", "Tenho graduação em engenharia química. Queria saber o valor.")
        self.add_decisao("demo-marina", agente="qualificacao", acao="qualificar",
                         mensagem="Que bom, Marina! O FEQ-0480 são 19x de R$ 866,09, ou R$ 16.455,75 à vista. Posso te enviar o link de inscrição?",
                         motivo="elegível + interesse", ancoras=["parcelamento 19x", "valor R$ 16.455,75"],
                         aprovado_revisor=1, status="proposta")
