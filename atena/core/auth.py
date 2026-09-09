#!/usr/bin/env python3
"""
Atena · Auth multiusuário + RBAC + auditoria (a Central expõe PII).
- Usuários no SQLite com PBKDF2-HMAC-SHA256 (stdlib) — criados pela própria Central.
- Bootstrap: sem usuários no banco e sem ATENA_USERS, vale operador/operador
  (papel supervisor, para criar as contas reais). Assim que existir um usuário
  no banco, o bootstrap DESLIGA sozinho.
- ATENA_USERS="nome:senha:papel,..." (env) continua honrado (ex.: contas de emergência).
- Rate limit de login: 5 falhas/60s por IP -> 429.
- ATENA_SECRET: gerado e persistido no .env na 1ª execução (sessões sobrevivem a restart).
"""
import os
import time
import hmac
import hashlib
import secrets
import pathlib
from flask import session, request, jsonify

PAPEIS = ("operador", "supervisor")
# Rotas sem sessão da Central. A da coleta está aqui porque o coletor roda DENTRO
# da aba da Extecamp (spec 20 §2) — outra origem, onde o cookie da Central não chega
# e não deve chegar. Ela não fica desprotegida: autentica sozinha, pelo token de 12h
# que `/api/coleta/sessao` (essa sim, com sessão) entrega ao operador, comparado com
# hmac.compare_digest. Sem o token, 403 na própria rota.
# Só o RECEPTOR é livre: "/api/coleta" (a fila de trabalho, que devolve PII de lead)
# continua exigindo sessão — LIVRE casa por igualdade, não por prefixo.
LIVRE = {"/api/login", "/health", "/api/coleta/pagina"}
SUPERVISOR_APROVA = ("/api/injetor",)   # POST .../aprovar exige supervisor
_FALHAS = {}                            # ip -> [timestamps]  (rate limit de login)

# Módulos restritos: prefixo de rota -> nome do módulo. Acesso NÃO vem de papel —
# um supervisor sem concessão não entra (em produção há outros supervisores, e o
# funil de professores é do master). Só o master concede e revoga, nominalmente.
MODULOS_RESTRITOS = {"/api/professores": "professores", "/api/modulo-acesso": "professores"}


def master():
    """Usuário dono dos módulos restritos. Em env para não ficar cravado no código."""
    return os.environ.get("ATENA_MASTER", "vitor")


def eh_master(usuario):
    return bool(usuario) and usuario == master()


def pode_modulo(estado, usuario, modulo):
    return eh_master(usuario) or estado.tem_acesso_modulo(usuario, modulo)


# ------------------------------------------------------------------ senhas
def hash_senha(senha, salt=None):
    salt = salt or secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac("sha256", senha.encode(), bytes.fromhex(salt), 100_000)
    return f"{salt}:{h.hex()}"

def verificar_senha(senha, guardado):
    try:
        salt, _ = guardado.split(":", 1)
        return hmac.compare_digest(hash_senha(senha, salt), guardado)
    except Exception:
        return False


def _usuarios_env():
    users = {}
    for item in os.environ.get("ATENA_USERS", "").split(","):
        p = item.strip().split(":")
        if len(p) == 3:
            users[p[0]] = {"senha": p[1], "papel": p[2]}
    return users


def _secret_do_arquivo(env):
    """ATENA_SECRET já gravado no .env, se houver. Devolve a PRIMEIRA ocorrência —
    é a que o loader (`os.environ.setdefault`) usa, então é a que vale de verdade."""
    try:
        for ln in env.read_text(encoding="utf-8", errors="replace").splitlines():
            t = ln.strip()
            if t.startswith("ATENA_SECRET=") and t.split("=", 1)[1].strip():
                return t.split("=", 1)[1].strip().strip('"').strip("'")
    except Exception:
        pass
    return None


def _garantir_secret(app):
    sec = os.environ.get("ATENA_SECRET")
    if not sec:
        env = pathlib.Path(__file__).resolve().parents[2] / ".env"
        # O .env pode já ter a chave sem ela estar no ambiente: acontece em todo
        # entrypoint que não passa pelo _load_env do app.py. Antes daqui saía uma
        # linha NOVA a cada vez — o .env de produção acumulou SEIS ATENA_SECRET
        # diferentes. Como o loader usa a PRIMEIRA ocorrência, cada linha nova
        # nascia morta, e quem editasse a última não veria efeito nenhum.
        sec = _secret_do_arquivo(env)
        if not sec:
            sec = secrets.token_hex(32)
            try:
                with env.open("a") as f:
                    f.write(f"\nATENA_SECRET={sec}\n")
            except Exception:
                pass  # sem .env gravável: sessão vale só até o restart
        os.environ["ATENA_SECRET"] = sec
    app.secret_key = sec
    app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax",
                      SESSION_COOKIE_SECURE=os.environ.get("ATENA_COOKIE_SECURE", "0") == "1")


def init_app(app, estado):
    _garantir_secret(app)

    def autenticar(usuario, senha):
        """Retorna papel ou None. Ordem: banco -> env -> bootstrap DEV."""
        u = estado.get_usuario(usuario)
        if u:
            return u["papel"] if u["ativo"] and verificar_senha(senha, u["senha_hash"]) else None
        env = _usuarios_env()
        if usuario in env:
            return env[usuario]["papel"] if hmac.compare_digest(env[usuario]["senha"], senha) else None
        if not estado.listar_usuarios() and not env and usuario == "operador" and senha == "operador":
            return "supervisor"   # bootstrap — morre quando o 1º usuário real existir
        return None

    @app.post("/api/login")
    def login():
        ip = request.remote_addr or "?"
        agora = time.time()
        _FALHAS[ip] = [t for t in _FALHAS.get(ip, []) if agora - t < 60]
        if len(_FALHAS[ip]) >= 5:
            return jsonify({"erro": "muitas tentativas — aguarde 1 minuto"}), 429
        b = request.get_json(silent=True) or {}
        papel = autenticar(b.get("usuario", ""), b.get("senha", ""))
        if papel:
            _FALHAS.pop(ip, None)
            session["usuario"], session["papel"] = b["usuario"], papel
            estado.log_acao(b["usuario"], "login")
            return jsonify({"ok": True, "papel": papel})
        _FALHAS[ip].append(agora)
        return jsonify({"ok": False, "erro": "credenciais inválidas"}), 401

    @app.post("/api/logout")
    def logout():
        session.clear()
        return jsonify({"ok": True})

    @app.get("/api/me")
    def me():
        u = session.get("usuario")
        # `modulos` diz à Central quais itens de menu desenhar. É conveniência de UI:
        # quem manda é o guard abaixo, que bloqueia a rota mesmo se alguém forjar o menu.
        return jsonify({"usuario": u, "papel": session.get("papel"),
                        "master": eh_master(u),
                        "modulos": sorted({m for m in MODULOS_RESTRITOS.values()
                                           if pode_modulo(estado, u, m)})})

    # -------------------------------------------------- gestão de usuários
    @app.get("/api/usuarios")
    def usuarios_listar():
        if session.get("papel") != "supervisor":
            return jsonify({"erro": "requer papel supervisor"}), 403
        return jsonify(estado.listar_usuarios())

    @app.post("/api/usuarios")
    def usuarios_criar():
        if session.get("papel") != "supervisor":
            return jsonify({"erro": "requer papel supervisor"}), 403
        b = request.get_json(silent=True) or {}
        u, s, p = (b.get("usuario") or "").strip(), b.get("senha") or "", b.get("papel") or "operador"
        if not u or len(s) < 6:
            return jsonify({"erro": "usuário obrigatório e senha com mínimo de 6 caracteres"}), 400
        if p not in PAPEIS:
            return jsonify({"erro": f"papel deve ser um de {PAPEIS}"}), 400
        if estado.get_usuario(u):
            return jsonify({"erro": "usuário já existe"}), 409
        estado.add_usuario(u, hash_senha(s), p)
        estado.log_acao(session["usuario"], f"criou usuário {u} ({p})")
        return jsonify({"ok": True, "usuario": u, "papel": p})

    @app.post("/api/usuarios/<u>/desativar")
    def usuarios_desativar(u):
        if session.get("papel") != "supervisor":
            return jsonify({"erro": "requer papel supervisor"}), 403
        if u == session.get("usuario"):
            return jsonify({"erro": "não é possível desativar a si mesmo"}), 400
        if not estado.get_usuario(u):
            return jsonify({"erro": "usuário não existe"}), 404
        estado.set_usuario_ativo(u, 0)
        estado.log_acao(session["usuario"], f"desativou usuário {u}")
        return jsonify({"ok": True})

    @app.before_request
    def guard():
        path = request.path
        if path in LIVRE or path == "/" or not path.startswith("/api"):
            return None
        if not session.get("usuario"):
            return jsonify({"erro": "não autenticado"}), 401
        # Módulo restrito: barra AQUI, não em cada rota. Uma rota nova sob o prefixo
        # nasce protegida por padrão — esquecer o decorador não vira vazamento.
        for prefixo, modulo in MODULOS_RESTRITOS.items():
            if path.startswith(prefixo) and not pode_modulo(estado, session["usuario"], modulo):
                return jsonify({"erro": "sem acesso a este módulo"}), 403
        if request.method == "POST" and any(path.startswith(p) and path.endswith("/aprovar") for p in SUPERVISOR_APROVA):
            if session.get("papel") != "supervisor":
                return jsonify({"erro": "requer papel supervisor"}), 403
        if request.method in ("POST", "PUT", "DELETE"):
            estado.log_acao(session["usuario"], request.method + " " + path)
        return None
