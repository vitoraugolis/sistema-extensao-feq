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
CREATE TABLE IF NOT EXISTS msgs_vistas(msg_id TEXT PRIMARY KEY, ts TEXT);
CREATE TABLE IF NOT EXISTS alertas(
  id INTEGER PRIMARY KEY AUTOINCREMENT, tipo TEXT, texto TEXT,
  criado TEXT, notificado INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS usuarios(
  usuario TEXT PRIMARY KEY, senha_hash TEXT, papel TEXT, ativo INTEGER DEFAULT 1, criado TEXT);
CREATE TABLE IF NOT EXISTS midias(media_id TEXT PRIMARY KEY, mime TEXT, criado TEXT);
-- HTML de LP editado à mão na Central. Existir aqui = a página parou de ser gerada a
-- partir da ontologia (não recebe mais preço/data/lista de espera automáticos) até
-- que o operador restaure. Ver Gestão de LPs.
CREATE TABLE IF NOT EXISTS lp_html(
  curso TEXT PRIMARY KEY, html TEXT, atualizado TEXT, autor TEXT);
-- Funil COMERCIAL DE PROFESSORES (prospecção de docentes para novos cursos).
-- Nada a ver com `leads`: aqui não há ingestão automática, webhook, Revisor nem
-- agente — é cadastro manual do próprio operador. Tabela separada de propósito;
-- misturar com leads faria toda regra de captação/LGPD/nutrição valer para
-- professores, que não são leads de curso.
CREATE TABLE IF NOT EXISTS professores(
  id INTEGER PRIMARY KEY AUTOINCREMENT, nome TEXT, telefone TEXT, email TEXT,
  instituto TEXT, cursos TEXT, anotacoes TEXT, ultimo_contato TEXT,
  proposta TEXT, servicos TEXT, fase TEXT DEFAULT 'Lead',
  criado TEXT, atualizado TEXT);
-- Acesso a módulo restrito, concedido nominalmente pelo usuário master.
-- NÃO é papel: ser supervisor não dá acesso — só a concessão explícita dá.
CREATE TABLE IF NOT EXISTS modulo_acesso(
  usuario TEXT, modulo TEXT, concedido_por TEXT, criado TEXT,
  PRIMARY KEY(usuario, modulo));
"""

# Fases do funil comercial de professores (ordem = ordem das colunas no quadro).
FASES_PROFESSOR = ["Lead", "Aquecimento", "Proposta enviada", "Em negociação",
                   "Aceito", "Recusado", "Revisitar", "Inviável"]

FASES = ["Lead", "Prospect", "Inscrito", "Matriculado", "Em espera", "Perdido"]


def _agora():
    return datetime.datetime.now().isoformat(timespec="seconds")


def _tel_key(telefone):
    """Chave canônica de telefone tolerante ao 9º dígito móvel brasileiro.
    O formulário Meta guarda o número COM o 9 (5579996529586); o wa_id do WhatsApp
    entrega SEM o 9 (557996529586). Reduz ambos a DDD(2)+assinante(8) para casarem."""
    d = re.sub(r"\D", "", telefone or "")
    if d.startswith("55") and len(d) in (12, 13):
        d = d[2:]                          # tira o país -> DDD + local
    if len(d) == 11 and d[2] == "9":       # DDD + 9(tronco móvel) + 8 -> remove o 9
        d = d[:2] + d[3:]
    return d[-10:] if len(d) >= 10 else d   # DDD(2) + 8 dígitos


def normalizar_e164(telefone):
    """Telefone em formato internacional (só dígitos, começando pelo país).

    Serve para o link wa.me funcionar: sem o código do país ele não abre a conversa.
    A decisão é por COMPRIMENTO, não por prefixo — DDD 55 existe (Santa Maria/RS),
    então "5599999999" é um fixo brasileiro SEM país, não um número já normalizado.

      10 díg. = DDD + fixo(8)        -> 55 + número
      11 díg. = DDD + celular(9)     -> 55 + número
      12/13 díg. começando com 55    -> já está certo (o wa_id do WhatsApp cai aqui)
      demais                         -> devolve como está: pode ser ESTRANGEIRO
                                        (ex.: +351 de Portugal na base) e prefixar
                                        55 destruiria o número.
    """
    d = re.sub(r"\D", "", telefone or "")
    if len(d) in (10, 11):
        return "55" + d
    return d


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
        for mig in ("ALTER TABLE leads ADD COLUMN reengajos INTEGER DEFAULT 0",
                    "ALTER TABLE leads ADD COLUMN faro_id TEXT",
                    "ALTER TABLE leads ADD COLUMN cursos_interesse TEXT",  # CSV; ativo=coluna curso
                    "ALTER TABLE leads ADD COLUMN optin_ts TEXT",      # LGPD: quando consentiu
                    "ALTER TABLE leads ADD COLUMN optin_fonte TEXT",   # LGPD: onde (ex.: meta_lead_ad)
                    "ALTER TABLE conversas ADD COLUMN tipo TEXT DEFAULT 'texto'",
                    "ALTER TABLE conversas ADD COLUMN media_id TEXT",
                    "ALTER TABLE conversas ADD COLUMN wamid TEXT",     # id do WhatsApp p/ status
                    "ALTER TABLE conversas ADD COLUMN status TEXT",    # enviado/entregue/lido/falhou
                    "ALTER TABLE leads ADD COLUMN modo TEXT DEFAULT 'auto'",   # auto=agente responde; manual=humano conduz
                    # QUEM escreveu a mensagem do 'agente'. Sem isto, texto digitado por
                    # humano e texto gerado pelo LLM ficam indistinguíveis no banco — e é
                    # justamente a fala humana que ensina o sistema (corpus, spec 18).
                    "ALTER TABLE conversas ADD COLUMN autor TEXT",
                    # Card de CRM: contato completo, notas do operador e origem da
                    # campanha. O e-mail JÁ chegava do formulário Meta e era descartado.
                    "ALTER TABLE leads ADD COLUMN email TEXT",
                    "ALTER TABLE leads ADD COLUMN notas TEXT",
                    "ALTER TABLE leads ADD COLUMN utm TEXT",      # JSON: source/medium/campaign...
                    # Retentativa de entrega: POR QUE a Meta recusou (131026 número não
                    # recebe / 131049 pacing de marketing / 130472 experimento) e PARA QUAL
                    # número mandamos — sem o destino não dá para saber qual variante do 9º
                    # dígito funcionou e corrigir o cadastro do lead.
                    "ALTER TABLE conversas ADD COLUMN erro_codigo INTEGER",
                    "ALTER TABLE conversas ADD COLUMN destino TEXT",
                    # Marca d'água de leitura do OPERADOR (não do agente). cards_vistos
                    # existia sem uso desde o início; ganha aqui a função de dizer até que
                    # mensagem a conversa foi lida por um humano.
                    "ALTER TABLE cards_vistos ADD COLUMN ultimo_id INTEGER",
                    # Reação (emoji) sobre uma mensagem existente — não é linha nova na
                    # thread, é uma marca sobre uma já enviada/recebida (ver marcar_reacao).
                    "ALTER TABLE conversas ADD COLUMN reacao TEXT",
                    # Anexo a mandar JUNTO com esta proposta, se aprovada (ex.: "guia_curso") —
                    # o flag do cérebro (dados.anexo_guia) precisa sobreviver até a aprovação
                    # humana, que pode vir bem depois da decisão.
                    "ALTER TABLE decisoes ADD COLUMN anexo TEXT",
                    # Transcrição de áudio / descrição de imagem (agentes/percepcao.py).
                    # Coluna SEPARADA de `texto` de propósito: texto é o que o lead
                    # escreveu, isto é o que a máquina entendeu. Misturar os dois faria
                    # o operador (e o corpus, spec 18) lerem palpite de IA como se fosse
                    # a fala literal do lead.
                    "ALTER TABLE conversas ADD COLUMN transcricao TEXT"):
            try:
                self.conn.execute(mig)
            except sqlite3.OperationalError:
                pass  # coluna já existe
        self.conn.commit()

    # ---------------------------------------------------------------- leads
    def upsert_lead(self, card_id, **campos):
        # Telefone entra SEMPRE em formato internacional: é o que faz o link wa.me
        # da Central abrir a conversa. Normalizar aqui (e não em cada chamador)
        # garante que vale para ingestão, WhatsApp, LP e criação manual.
        if campos.get("telefone"):
            campos["telefone"] = normalizar_e164(campos["telefone"])
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
        if campos.get("telefone"):
            campos["telefone"] = normalizar_e164(campos["telefone"])
        with self._lock:
            self._set(card_id, **campos)

    def get_lead(self, card_id):
        with self._lock:
            r = self.conn.execute("SELECT * FROM leads WHERE card_id=?", (card_id,)).fetchone()
        return dict(r) if r else None

    def listar_leads(self):
        with self._lock:
            rows = self.conn.execute("SELECT * FROM leads ORDER BY atualizado DESC").fetchall()
        return [dict(r) for r in rows]

    @staticmethod
    def utm_de(lead):
        """UTMs do lead como dict (a coluna guarda JSON). Nunca estoura em dado sujo."""
        try:
            d = json.loads((lead or {}).get("utm") or "{}")
            return d if isinstance(d, dict) else {}
        except (ValueError, TypeError):
            return {}

    def set_notas(self, card_id, notas):
        self.set_lead(card_id, notas=(notas or "").strip()[:4000])

    @staticmethod
    def cursos_interesse(lead):
        """Lista de cursos de interesse do lead (o ativo `curso` sempre incluído, 1º)."""
        s = [c for c in (lead.get("cursos_interesse") or "").split(",") if c]
        if lead.get("curso") and lead["curso"] not in s:
            s.insert(0, lead["curso"])
        return s

    def add_curso_interesse(self, card_id, curso, ativar=False):
        """Acrescenta um curso ao conjunto de interesse (não remove o ativo).
        ativar=True também torna esse curso o ativo (`curso`)."""
        lead = self.get_lead(card_id)
        if not lead or not curso:
            return
        cursos = self.cursos_interesse(lead)
        if curso not in cursos:
            cursos.append(curso)
        campos = {"cursos_interesse": ",".join(cursos)}
        if ativar:
            campos["curso"] = curso
        self.set_lead(card_id, **campos)

    def lead_por_telefone(self, telefone):
        d = re.sub(r"\D", "", telefone or "")
        if len(d) < 8:
            return None
        k = _tel_key(telefone)
        for l in self.listar_leads():  # ponytail: scan linear; indexar por telefone se crescer
            e = re.sub(r"\D", "", l["telefone"] or "")
            if len(e) < 8:
                continue
            # 1) chave canônica BR (tolera 9º dígito móvel que o wa_id do WhatsApp omite);
            # 2) sufixo bidirecional (tolera +55/DDD divergentes, como o FARO)
            if _tel_key(l["telefone"]) == k or e.endswith(d) or d.endswith(e):
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
        with self._lock:
            r = self.conn.execute("SELECT * FROM usuarios WHERE usuario=?", (usuario,)).fetchone()
        return dict(r) if r else None

    def listar_usuarios(self):
        with self._lock:
            rows = self.conn.execute(
                "SELECT usuario,papel,ativo,criado FROM usuarios ORDER BY criado").fetchall()
        return [dict(r) for r in rows]

    def set_usuario_ativo(self, usuario, ativo):
        with self._lock:
            self.conn.execute("UPDATE usuarios SET ativo=? WHERE usuario=?", (int(ativo), usuario))
            self.conn.commit()

    # ------------------------------------------------- acesso a módulo restrito
    def tem_acesso_modulo(self, usuario, modulo):
        with self._lock:
            r = self.conn.execute("SELECT 1 FROM modulo_acesso WHERE usuario=? AND modulo=?",
                                  (usuario, modulo)).fetchone()
        return r is not None

    def conceder_modulo(self, usuario, modulo, por):
        with self._lock:
            self.conn.execute(
                "INSERT OR REPLACE INTO modulo_acesso(usuario,modulo,concedido_por,criado)"
                " VALUES(?,?,?,?)", (usuario, modulo, por, _agora()))
            self.conn.commit()

    def revogar_modulo(self, usuario, modulo):
        with self._lock:
            n = self.conn.execute("DELETE FROM modulo_acesso WHERE usuario=? AND modulo=?",
                                  (usuario, modulo)).rowcount
            self.conn.commit()
        return n > 0

    def listar_acesso_modulo(self, modulo):
        with self._lock:
            rows = self.conn.execute(
                "SELECT usuario, concedido_por, criado FROM modulo_acesso WHERE modulo=?"
                " ORDER BY criado", (modulo,)).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------- funil comercial de professores (CRUD)
    _CAMPOS_PROF = ("nome", "telefone", "email", "instituto", "cursos", "anotacoes",
                    "ultimo_contato", "proposta", "servicos", "fase")

    def listar_professores(self):
        with self._lock:
            rows = self.conn.execute(
                "SELECT * FROM professores ORDER BY atualizado DESC").fetchall()
        return [dict(r) for r in rows]

    def get_professor(self, pid):
        with self._lock:
            r = self.conn.execute("SELECT * FROM professores WHERE id=?", (pid,)).fetchone()
        return dict(r) if r else None

    def add_professor(self, **campos):
        campos = {k: v for k, v in campos.items() if k in self._CAMPOS_PROF}
        if campos.get("telefone"):
            campos["telefone"] = normalizar_e164(campos["telefone"])
        campos.setdefault("fase", FASES_PROFESSOR[0])
        campos["criado"] = campos["atualizado"] = _agora()
        with self._lock:
            cur = self.conn.execute(
                f"INSERT INTO professores({','.join(campos)}) "
                f"VALUES({','.join('?' * len(campos))})", list(campos.values()))
            self.conn.commit()
        return cur.lastrowid

    def set_professor(self, pid, **campos):
        campos = {k: v for k, v in campos.items() if k in self._CAMPOS_PROF}
        if not campos:
            return False
        if campos.get("telefone"):
            campos["telefone"] = normalizar_e164(campos["telefone"])
        campos["atualizado"] = _agora()
        with self._lock:
            n = self.conn.execute(
                f"UPDATE professores SET {','.join(k + '=?' for k in campos)} WHERE id=?",
                list(campos.values()) + [pid]).rowcount
            self.conn.commit()
        return n > 0

    def del_professor(self, pid):
        with self._lock:
            n = self.conn.execute("DELETE FROM professores WHERE id=?", (pid,)).rowcount
            self.conn.commit()
        return n > 0

    # ---------------------------------------------------------------- config
    def get_config(self, chave, default=None):
        with self._lock:
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
        with self._lock:
            rows = self.conn.execute(
                "SELECT * FROM duvidas WHERE status=? ORDER BY id DESC", (status,)).fetchall()
        return [dict(r) for r in rows]

    def get_duvida(self, did):
        with self._lock:
            r = self.conn.execute("SELECT * FROM duvidas WHERE id=?", (did,)).fetchone()
        return dict(r) if r else None

    def responder_duvida(self, did, resposta, status="respondida"):
        with self._lock:
            self.conn.execute(
                "UPDATE duvidas SET resposta=?, status=?, respondido_em=? WHERE id=?",
                (resposta, status, _agora(), did))
            self.conn.commit()

    # ------------------------------------------------- dedupe de webhook
    def msg_ja_vista(self, msg_id):
        """Marca e informa: True se este message_id já foi processado (reentrega)."""
        if not msg_id:
            return False
        with self._lock:
            cur = self.conn.execute(
                "INSERT OR IGNORE INTO msgs_vistas(msg_id, ts) VALUES(?,?)", (msg_id, _agora()))
            self.conn.commit()
            return cur.rowcount == 0

    def viu_msg(self, msg_id):
        """Só CONSULTA (não marca): True se msg_id já foi processado. Use para
        deduplicar sem 'queimar' o id quando o processamento ainda pode falhar."""
        if not msg_id:
            return False
        with self._lock:
            r = self.conn.execute("SELECT 1 FROM msgs_vistas WHERE msg_id=?", (msg_id,)).fetchone()
        return r is not None

    # ----------------------------------------------------------------- alertas
    def add_alerta(self, tipo, texto):
        with self._lock:
            self.conn.execute("INSERT INTO alertas(tipo,texto,criado) VALUES(?,?,?)",
                              (tipo, texto, _agora()))
            self.conn.commit()

    def alertas_nao_notificados(self):
        with self._lock:
            rows = self.conn.execute(
                "SELECT * FROM alertas WHERE notificado=0 ORDER BY id").fetchall()
        return [dict(r) for r in rows]

    def marcar_alerta_notificado(self, aid):
        with self._lock:
            self.conn.execute("UPDATE alertas SET notificado=1 WHERE id=?", (aid,))
            self.conn.commit()

    def listar_alertas(self, limite=10):
        with self._lock:
            rows = self.conn.execute(
                "SELECT * FROM alertas ORDER BY id DESC LIMIT ?", (limite,)).fetchall()
        return [dict(r) for r in rows]

    # -------------------------------------------------------------- LGPD
    def anonimizar_antigos(self, dias=180):
        """Leads perdidos há mais de N dias: apaga PII e o conteúdo das conversas."""
        limite = (datetime.datetime.now() - datetime.timedelta(days=dias)).isoformat()
        n = 0
        for l in self.listar_leads():
            if l["fase"] == "Perdido" and (l["atualizado"] or "9999") < limite and l["telefone"]:
                with self._lock:
                    self.conn.execute(
                        "UPDATE leads SET nome='(anonimizado)', telefone='', email='',"
                        " notas='' WHERE card_id=?",   # utm fica: é dado de campanha, não pessoal
                        (l["card_id"],))
                    self.conn.execute("DELETE FROM conversas WHERE card_id=?", (l["card_id"],))
                    # A ficha da etapa 2 (growth/db.py::inscricoes) tem CPF, documento e
                    # endereço — é o dado MAIS sensível que guardamos. Some junto, aqui,
                    # e não numa varredura paralela do Growth: `inscricoes` vive no mesmo
                    # arquivo SQLite, e uma segunda rotina que alguém precise lembrar de
                    # chamar é exatamente como PII vira retenção silenciosa.
                    # A tabela é criada pelo Growth: num processo que só abriu o Estado
                    # (testes, script avulso) ela não existe — e a varredura LGPD não pode
                    # abortar por causa disso, ou o resto do PII deixaria de ser apagado.
                    try:
                        self.conn.execute("DELETE FROM inscricoes WHERE card_id=?", (l["card_id"],))
                    except sqlite3.OperationalError:
                        pass
                    self.conn.commit()
                n += 1
        if n:
            self.log_acao("sistema", f"lgpd: {n} lead(s) anonimizado(s)")
        return n

    # ------------------------------------------------------------- auditoria
    def log_acao(self, ator, acao, alvo=""):
        with self._lock:
            self.conn.execute("INSERT INTO auditoria(ator,acao,alvo,ts) VALUES(?,?,?,?)",
                              (ator, acao, alvo, _agora()))
            self.conn.commit()

    def listar_auditoria(self, limite=100):
        with self._lock:
            rows = self.conn.execute(
                "SELECT * FROM auditoria ORDER BY id DESC LIMIT ?", (limite,)).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------ conversas
    def add_conversa(self, card_id, papel, texto, tipo="texto", media_id=None,
                     wamid=None, status=None, autor=None, destino=None, transcricao=None):
        """autor: quem produziu a fala do 'agente' — 'humano:<usuario>' (digitada na
        Central), 'llm' (cérebro), 'template', 'coordenacao'. É o que permite ao corpus
        aprender com as resoluções humanas (spec 18).
        transcricao: leitura automática da mídia (áudio/imagem) — nunca fala do lead."""
        with self._lock:
            self.conn.execute(
                "INSERT INTO conversas(card_id,papel,texto,tipo,media_id,wamid,status,ts,autor,"
                "destino,transcricao) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (card_id, papel, texto, tipo, media_id, wamid, status, _agora(), autor,
                 destino, transcricao))
            self.conn.commit()

    def set_transcricao(self, conversa_id, transcricao):
        """Transcreve depois do fato: áudio que chegou antes desta função existir (ou
        quando a API falhou na hora) ainda pode ser lido pelo operador na Central."""
        with self._lock:
            self.conn.execute("UPDATE conversas SET transcricao=? WHERE id=?",
                              (transcricao, conversa_id))
            self.conn.commit()

    def get_conversa(self, conversa_id):
        with self._lock:
            r = self.conn.execute("SELECT * FROM conversas WHERE id=?", (conversa_id,)).fetchone()
        return dict(r) if r else None

    def historico(self, card_id):
        with self._lock:
            rows = self.conn.execute(
                "SELECT id,papel,texto,tipo,media_id,wamid,status,ts,autor,reacao,transcricao"
                " FROM conversas WHERE card_id=? ORDER BY id",
                (card_id,)).fetchall()
        return [dict(r) for r in rows]

    def marcar_reacao(self, wamid, emoji):
        """emoji vazio/None = reação removida (a Meta manda esse evento quando o lead
        desfaz). Não cria linha nova — anexa à mensagem existente. Devolve o card_id
        (para o webhook poder notificar), ou None se o wamid não é conhecido."""
        if not wamid:
            return None
        with self._lock:
            r = self.conn.execute("SELECT card_id FROM conversas WHERE wamid=?", (wamid,)).fetchone()
            if not r:
                return None
            self.conn.execute("UPDATE conversas SET reacao=? WHERE wamid=?", (emoji or None, wamid))
            self.conn.commit()
        return r["card_id"]

    # ------------------------------------------------------- leitura pelo operador
    def marcar_lido(self, card_id):
        """Conversa lida ATÉ a última mensagem existente agora. Devolve a marca d'água
        ANTERIOR — é ela que diz à UI onde desenhar a linha de 'não lidas'."""
        with self._lock:
            r = self.conn.execute("SELECT ultimo_id FROM cards_vistos WHERE card_id=?",
                                  (card_id,)).fetchone()
            antes = (r["ultimo_id"] if r else None) or 0
            m = self.conn.execute("SELECT MAX(id) m FROM conversas WHERE card_id=?",
                                  (card_id,)).fetchone()
            agora_id = m["m"] or 0
            self.conn.execute(
                "INSERT INTO cards_vistos(card_id, visto_em, ultimo_id) VALUES(?,?,?)"
                " ON CONFLICT(card_id) DO UPDATE SET visto_em=excluded.visto_em,"
                " ultimo_id=MAX(COALESCE(cards_vistos.ultimo_id,0), excluded.ultimo_id)",
                (card_id, _agora(), agora_id))
            self.conn.commit()
        return antes

    def nao_lidas(self):
        """{card_id: qtd} de mensagens DO LEAD que o operador ainda não viu.

        Conta só papel='lead' de propósito: o agente responder é ação NOSSA e não pode
        zerar o contador — o operador precisa saber o que chegou mesmo com a IA atendendo.
        Conversa nunca aberta tem marca d'água 0, então tudo dela conta como não lido.
        """
        with self._lock:
            rows = self.conn.execute(
                "SELECT c.card_id, COUNT(*) n FROM conversas c"
                " LEFT JOIN cards_vistos v ON v.card_id=c.card_id"
                " WHERE c.papel='lead' AND c.id > COALESCE(v.ultimo_id, 0)"
                " GROUP BY c.card_id").fetchall()
        return {r["card_id"]: r["n"] for r in rows}

    def atualizar_status_msg(self, wamid, status, codigo=None):
        """Webhook de status do WhatsApp -> atualiza o status da nossa mensagem enviada.
        Não rebaixa (lido não vira entregue). Retorna o card_id (ou None)."""
        if not wamid:
            return None
        ordem = {"enviado": 1, "entregue": 2, "lido": 3, "falhou": 9}
        with self._lock:
            r = self.conn.execute(
                "SELECT card_id, status FROM conversas WHERE wamid=?", (wamid,)).fetchone()
            if not r:
                return None
            if ordem.get(status, 0) >= ordem.get(r["status"], 0):   # falhou sempre passa
                self.conn.execute("UPDATE conversas SET status=?, erro_codigo=? WHERE wamid=?",
                                  (status, codigo, wamid))
                self.conn.commit()
            return r["card_id"]

    def envio_por_wamid(self, wamid):
        with self._lock:
            r = self.conn.execute(
                "SELECT card_id, texto, status, destino, erro_codigo FROM conversas"
                " WHERE wamid=?", (wamid,)).fetchone()
        return dict(r) if r else None

    def envios_do_lead(self, card_id):
        """Tentativas de envio ao lead, em ordem — a base da escada de retentativa."""
        with self._lock:
            rows = self.conn.execute(
                "SELECT texto, status, destino, erro_codigo, ts FROM conversas"
                " WHERE card_id=? AND papel='agente' AND wamid IS NOT NULL ORDER BY id",
                (card_id,)).fetchall()
        return [dict(r) for r in rows]

    def leads_sem_entrega(self):
        """Leads em que TODA tentativa falhou e nenhuma chegou. São os que perdemos
        de fato — quem já recebeu algo tem canal aberto e não precisa de retentativa."""
        with self._lock:
            rows = self.conn.execute(
                "SELECT l.* FROM leads l WHERE l.optout=0"
                " AND EXISTS(SELECT 1 FROM conversas c WHERE c.card_id=l.card_id"
                "            AND c.papel='agente' AND c.status='falhou')"
                " AND NOT EXISTS(SELECT 1 FROM conversas c WHERE c.card_id=l.card_id"
                "                AND c.papel='agente' AND c.status IN ('entregue','lido'))"
                " AND NOT EXISTS(SELECT 1 FROM conversas c WHERE c.card_id=l.card_id"
                "                AND c.papel='lead')").fetchall()
        return [dict(r) for r in rows]

    # --------------------------------------------------------------- mídias
    # ------------------------------------------------------------- LPs editadas
    def set_lp_html(self, curso, html, autor="operador"):
        with self._lock:
            self.conn.execute(
                "INSERT INTO lp_html(curso,html,atualizado,autor) VALUES(?,?,?,?) "
                "ON CONFLICT(curso) DO UPDATE SET html=excluded.html, "
                "atualizado=excluded.atualizado, autor=excluded.autor",
                (curso, html, _agora(), autor))
            self.conn.commit()

    def get_lp_html(self, curso):
        with self._lock:
            r = self.conn.execute("SELECT * FROM lp_html WHERE curso=?", (curso,)).fetchone()
        return dict(r) if r else None

    def del_lp_html(self, curso):
        with self._lock:
            n = self.conn.execute("DELETE FROM lp_html WHERE curso=?", (curso,)).rowcount
            self.conn.commit()
        return n

    def cursos_lp_editada(self):
        with self._lock:
            return {r["curso"]: r["atualizado"]
                    for r in self.conn.execute("SELECT curso, atualizado FROM lp_html")}

    def add_midia(self, media_id, mime):
        with self._lock:
            self.conn.execute("INSERT OR IGNORE INTO midias(media_id,mime,criado) VALUES(?,?,?)",
                              (media_id, mime or "", _agora()))
            self.conn.commit()

    def midia_mime(self, media_id):
        with self._lock:
            r = self.conn.execute("SELECT mime FROM midias WHERE media_id=?", (media_id,)).fetchone()
        return r["mime"] if r else None

    def ultima_lead_msg(self, card_id):
        with self._lock:
            r = self.conn.execute(
                "SELECT texto FROM conversas WHERE card_id=? AND papel='lead' ORDER BY id DESC LIMIT 1",
                (card_id,)).fetchone()
        return r["texto"] if r else None

    # ------------------------------------------------------------- decisoes
    def add_decisao(self, card_id, agente, acao, mensagem=None, motivo="", ancoras=None,
                    aprovado_revisor=1, bloqueios=None, status="acao", anexo=None):
        with self._lock:
            self.conn.execute(
                """INSERT INTO decisoes(card_id,agente,acao,mensagem,motivo,ancoras_json,
                   aprovado_revisor,bloqueios_json,status,ts,anexo) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (card_id, agente, acao, mensagem, motivo, json.dumps(ancoras or []),
                 int(aprovado_revisor), json.dumps(bloqueios or []), status, _agora(), anexo))
            self.conn.commit()

    def decisao_pendente(self, card_id):
        with self._lock:
            r = self.conn.execute(
                "SELECT * FROM decisoes WHERE card_id=? AND status='proposta' ORDER BY id DESC LIMIT 1",
                (card_id,)).fetchone()
        return dict(r) if r else None

    def coordenacao_represada(self, card_id):
        """Resposta de coordenação (dúvida respondida) represada por ter caído fora da
        janela de 24h. A mais ANTIGA pendente — entregue quando o lead reabrir a conversa."""
        with self._lock:
            r = self.conn.execute(
                "SELECT * FROM decisoes WHERE card_id=? AND agente='coordenacao' AND status='proposta' "
                "ORDER BY id LIMIT 1", (card_id,)).fetchone()
        return dict(r) if r else None

    def listar_decisoes(self, card_id):
        with self._lock:
            rows = self.conn.execute(
                "SELECT * FROM decisoes WHERE card_id=? ORDER BY id DESC", (card_id,)).fetchall()
        return [dict(r) for r in rows]

    def set_decisao_status(self, decisao_id, status):
        with self._lock:
            self.conn.execute("UPDATE decisoes SET status=? WHERE id=?", (status, decisao_id))
            self.conn.commit()

    def ultima_lead_ts(self, card_id):
        with self._lock:
            r = self.conn.execute(
                "SELECT ts FROM conversas WHERE card_id=? AND papel='lead' ORDER BY id DESC LIMIT 1",
                (card_id,)).fetchone()
        return r["ts"] if r else None

    # -------------------------------------------------------------- métricas
    def stats_funil(self):
        out = {f: 0 for f in FASES}
        with self._lock:
            rows = self.conn.execute("SELECT fase, COUNT(*) n FROM leads GROUP BY fase").fetchall()
        for r in rows:
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
        with self._lock:
            r = self.conn.execute("SELECT * FROM propostas WHERE id=?", (pid,)).fetchone()
        if not r:
            return None
        d = dict(r)
        for k in ("diff_json", "impacto_json", "validacao_json"):
            d[k.replace("_json", "")] = json.loads(d.pop(k) or "null")
        return d

    def listar_propostas(self, status=None):
        q = "SELECT * FROM propostas" + (" WHERE status=?" if status else "") + " ORDER BY criado DESC"
        with self._lock:
            rows = self.conn.execute(q, (status,) if status else ()).fetchall()
        return [self.get_proposta(r["id"]) for r in rows]

    def set_proposta_status(self, pid, status):
        with self._lock:
            self.conn.execute("UPDATE propostas SET status=? WHERE id=?", (status, pid))
            self.conn.commit()

    # ------------------------------------------------------------------ seed
    def seed_demo(self):
        with self._lock:
            ja_tem = self.conn.execute("SELECT COUNT(*) c FROM leads").fetchone()["c"]
        if ja_tem:
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

    def purgar_demo(self):
        """Remove os leads sintéticos (seed-*/demo-*) e tudo ligado a eles.
        Deixa a Central só com leads reais vindos do sistema. Retorna nº de leads removidos."""
        with self._lock:
            n = self.conn.execute(
                "SELECT COUNT(*) c FROM leads WHERE card_id LIKE 'seed-%' OR card_id LIKE 'demo-%'"
            ).fetchone()["c"]
            for tabela in ("conversas", "decisoes", "tarefas", "cards_vistos", "duvidas", "leads"):
                self.conn.execute(
                    f"DELETE FROM {tabela} WHERE card_id LIKE 'seed-%' OR card_id LIKE 'demo-%'")
            self.conn.commit()
        return n
