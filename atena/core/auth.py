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
LIVRE = {"/api/login", "/health"}
SUPERVISOR_APROVA = ("/api/injetor",)   # POST .../aprovar exige supervisor
_FALHAS = {}                            # ip -> [timestamps]  (rate limit de login)


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


def _garantir_secret(app):
    sec = os.environ.get("ATENA_SECRET")
    if not sec:
        sec = secrets.token_hex(32)
        env = pathlib.Path(__file__).resolve().parents[2] / ".env"
        try:
            with env.open("a") as f:
                f.write(f"\nATENA_SECRET={sec}\n")
        except Exception:
            pass  # sem .env gravável: sessão vale só até o restart
        os.environ["ATENA_SECRET"] = sec
    app.secret_key = sec


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
        return jsonify({"usuario": session.get("usuario"), "papel": session.get("papel")})

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
        if request.method == "POST" and any(path.startswith(p) and path.endswith("/aprovar") for p in SUPERVISOR_APROVA):
            if session.get("papel") != "supervisor":
                return jsonify({"erro": "requer papel supervisor"}), 403
        if request.method in ("POST", "PUT", "DELETE"):
            estado.log_acao(session["usuario"], request.method + " " + path)
        return None
