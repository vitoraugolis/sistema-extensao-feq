#!/usr/bin/env python3
"""
Atena · Guardrails (governança aplicada).
  • opt-out determinístico (não confia só no LLM)
  • janela de envio (horário comercial, America/Sao_Paulo, sem domingo/madrugada)
  • gatilhos sensíveis -> escalonamento humano
  • tier de risco de uma ação
"""
import os
import re
import hmac
import hashlib
import datetime

try:
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo("America/Sao_Paulo")
except Exception:
    TZ = None

OPTOUT_RE = re.compile(r"\b(sair|parar|pare|n[ãa]o quero|descadastr\w*|remover|stop|me tira)\b", re.I)
SENSIVEL_RE = re.compile(r"\b(desconto|abatimento|bolsa|reclama\w*|insatisf\w*|processo|advogad\w*|reembolso|cancelar matr[íi]cula|quero fechar|quero me matricular)\b", re.I)


# "você é robô?" — transparência: assumimos que é IA e oferecemos passar para uma pessoa.
IDENTIDADE_RE = re.compile(
    r"(você|voce|vc|tu)\s+(é|e|eh)\s+(um\s+|uma\s+)?(rob[ôo]|b[oô]t|ia\b|intelig[êe]ncia artificial|"
    r"m[áa]quina|humano|pessoa|gente|real|de verdade)"
    r"|\b(rob[ôo]|b[oô]t|chatbot)\s*\?"
    r"|(falando com)\s+(um\s+|uma\s+)?(rob[ôo]|b[oô]t|m[áa]quina|ia\b|pessoa|humano)", re.I)
# pedido explícito de atendimento humano
QUER_HUMANO_RE = re.compile(
    r"(falar|conversar|atendimento|atendente)\s+(com\s+)?(uma?\s+)?"
    r"(pessoa|humano|gente|atendente|respons[áa]vel|algu[ée]m)"
    r"|(passa|transfer|encaminh)\w*\s+(me\s+|para\s+|pra\s+|p/\s*)*(uma?\s+)?"
    r"(pessoa|humano|atendente|algu[ée]m|respons[áa]vel)"
    r"|quero\s+(uma?\s+)?(pessoa|humano|atendente)", re.I)


def detectar_optout(texto):
    return bool(OPTOUT_RE.search(texto or ""))


def pergunta_se_e_robo(texto):
    return bool(IDENTIDADE_RE.search(texto or ""))


def quer_humano(texto):
    return bool(QUER_HUMANO_RE.search(texto or ""))


def gatilho_sensivel(texto):
    return bool(SENSIVEL_RE.search(texto or ""))


def dentro_janela(agora=None):
    """Horário comercial 08h–20h, seg–sáb, fuso America/Sao_Paulo."""
    if os.environ.get("ATENA_JANELA_LIVRE") == "1":  # knob de TESTE — nunca ligar em produção
        return True
    if agora is None:
        agora = datetime.datetime.now(TZ) if TZ else datetime.datetime.now()
    if agora.weekday() == 6:  # domingo
        return False
    return 8 <= agora.hour < 20


def verificar_assinatura(secret, corpo_bytes, header):
    """HMAC-SHA256 do webhook (Meta/WhatsApp). Sem secret => passthrough (dev)."""
    if not secret:
        return True  # ponytail: sem APP_SECRET, não bloqueia em dev; setar secret ativa a trava
    if not header:
        return False
    esperado = "sha256=" + hmac.new(secret.encode(), corpo_bytes or b"", hashlib.sha256).hexdigest()
    return hmac.compare_digest(esperado, header)


def tier_da_acao(acao):
    """Tier de risco (ver spec 07)."""
    if acao in ("encerrar_optout",):
        return "escrita_reversivel"
    if acao in ("qualificar", "descartar", "em_espera", "continuar"):
        return "escrita_reversivel"
    return "escrita_reversivel"
