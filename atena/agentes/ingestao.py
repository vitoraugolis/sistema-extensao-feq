#!/usr/bin/env python3
"""
Atena · Ingestão Variante B (Meta Lead Ads -> estado, fase Lead).
Dedupe por telefone (o SQLite já é consistente -> sem cache de latência),
gate de captabilidade no roteamento. Verificação de assinatura fica no app
(guardrails.verificar_assinatura). FARO opcional: estado é o SoR local.
ponytail: sem cache de 15min (não há lag de indexação local); sem chamada FARO.
"""
import re
import sys
import json
import datetime
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from atena.ontologia.servico import CODE_RE, normalizar_codigo


def _digits(p):
    return re.sub(r"\D", "", p or "")


def curso_de_texto(*textos):
    """Extrai o código do curso ('FEQ-0333') do nome do formulário/campanha.
    Padrão acordado: nome do formulário = curso + versão (ex.: 'FEQ-0333 v1').
    Normaliza para 4 dígitos: 'FEQ 331' -> 'FEQ-0331' (catálogo usa 4 dígitos)."""
    for t in textos:
        m = CODE_RE.search(t or "")
        if m:
            return normalizar_codigo(m.group(1), m.group(2))
    return ""


# chaves de nome conhecidas (os formulários variam: full_name / nome_completo / first_name…)
NOME_KEYS = ("full_name", "nome_completo", "nome", "fullname", "first_name")
# campos de contato conhecidos; o resto do form são perguntas qualificatórias
# Parâmetros de campanha que interessam. Ficam no lead como JSON para permitir
# analisar depois "qual anúncio traz lead que converte" (spec 19).
UTM_KEYS = ("utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term",
            "gclid", "fbclid")


def extrair_utm(dados):
    """UTMs vindos soltos no payload OU embutidos na `url` da página. A LP manda os
    dois; o fallback pela URL cobre integração antiga que só mandava a url."""
    from urllib.parse import urlparse, parse_qs
    out = {k: str(dados[k])[:200] for k in UTM_KEYS if dados.get(k)}
    if not out and dados.get("url"):
        try:
            q = parse_qs(urlparse(dados["url"]).query)
            out = {k: q[k][0][:200] for k in UTM_KEYS if q.get(k)}
        except Exception:
            pass
    return out


CONTATO = set(NOME_KEYS) | {"phone_number", "telefone", "email", "consent",
                            "curso", "curso_de_interesse"}
SUPERIOR_RE = re.compile(r"superior|gradua|bacharel|licenciat", re.I)
NEGATIVO_RE = re.compile(r"\b(n[ãa]o|nao|incompleto|cursando|ainda)\b", re.I)


def _extrai_nome(fd):
    """Nome do lead — chave varia por formulário. Tenta as conhecidas e, por fim,
    qualquer campo com 'nome'/'name' no nome do campo (menos email/usuário)."""
    for k in NOME_KEYS:
        if fd.get(k):
            return fd[k]
    for k, v in fd.items():
        kl = k.lower()
        if v and ("nome" in kl or "name" in kl) and "email" not in kl and "user" not in kl:
            return v
    return ""


def _qualificacao(fd):
    """Formulário com perguntas qualificatórias traz respostas além do contato.
    Só o lead que passou no funil do form chega aqui (o não-qualificado vira 'não
    lead' e não gera webhook). Se há pergunta sobre ensino superior respondida
    afirmativamente, marca a escolaridade -> o gate acadêmico não re-pergunta.
    Retorna (escolaridade|None, nota_legível)."""
    extras = [(k, v) for k, v in fd.items() if k not in CONTATO and (v or "").strip()]
    if not extras:
        return None, ""
    escolaridade = None
    for k, v in extras:
        if SUPERIOR_RE.search(k) or SUPERIOR_RE.search(v):
            escolaridade = None if NEGATIVO_RE.search(v) else "Superior Completo"
    nota = "; ".join(f"{k.replace('_', ' ').strip(' ?')}: {v}" for k, v in extras)
    return escolaridade, nota


def normalizar_meta(field_data, campaign_name="", leadgen_id="", curso=""):
    """Adapter Meta -> lead normalizado. field_data = [{name,values:[..]}].
    Curso vem do nome do formulário (via `curso`); fallbacks: campo do form, campanha."""
    fd = {i["name"]: (i.get("values") or [""])[0] for i in (field_data or [])}
    escolaridade, qualif = _qualificacao(fd)
    nome = _extrai_nome(fd)
    return {
        "nome": nome,
        "telefone": fd.get("phone_number") or fd.get("telefone") or "",
        "email": fd.get("email", ""),
        "curso": curso or fd.get("curso") or fd.get("curso_de_interesse") or curso_de_texto(campaign_name),
        "campanha": campaign_name, "lead_id": str(leadgen_id),
        "consentimento": fd.get("consent", "true"),
        "escolaridade": escolaridade,   # pré-qualificado no formulário -> pula o gate
        "qualificacao": qualif,
    }


def _graph(node, token, fields):
    import requests
    r = requests.get(f"https://graph.facebook.com/v19.0/{node}",
                     params={"access_token": token, "fields": fields}, timeout=20)
    if r.status_code != 200:
        print(f"[ingestao] Graph {node} {r.status_code}: {r.text[:150]}", flush=True)
        return None
    return r.json()


def fetch_meta_lead(leadgen_id, form_id=None):
    """Webhook leadgen real só traz o ID — busca dados na Graph API e resolve o
    curso pelo NOME DO FORMULÁRIO. Token com escopo leads_retrieval."""
    import os
    token = os.environ.get("META_PAGE_TOKEN") or os.environ.get("WHATSAPP_TOKEN")
    if not token or not leadgen_id:
        return None
    try:
        data = _graph(leadgen_id, token, "field_data,campaign_name,form_id")
        if not data:
            return None
        fid = form_id or data.get("form_id")
        nome_form = (_graph(fid, token, "name") or {}).get("name", "") if fid else ""
        curso = curso_de_texto(nome_form, data.get("campaign_name", ""))
        lead = normalizar_meta(data.get("field_data"), data.get("campaign_name", ""),
                               leadgen_id, curso=curso)
        lead["form"] = nome_form
        return lead
    except Exception as e:
        print(f"[ingestao] fetch_meta_lead falhou: {e}", flush=True)
        return None


class Ingestao:
    def __init__(self, estado, ont, faro=None):
        self.est = estado
        self.ont = ont
        self.faro = faro

    def ingest(self, lead):
        tel = lead.get("telefone", "")
        if not _digits(tel):
            return {"ok": False, "motivo": "lead sem telefone"}
        existente = self.est.lead_por_telefone(tel)
        if existente:
            # mesma pessoa, outro formulário -> ACUMULA o curso de interesse; NÃO troca o
            # curso ativo (não interrompe a conversa em andamento). Se o card não tinha
            # curso ativo, o novo assume.
            atual = self.est.get_lead(existente)
            novo = lead.get("curso")
            if novo:
                self.est.add_curso_interesse(existente, novo, ativar=not atual.get("curso"))
            # completa contato/origem que faltavam, sem sobrescrever o que já existe:
            # o 1º toque é o que atribui a campanha, não o último.
            faltando = {}
            if not atual.get("email") and (lead.get("email") or "").strip():
                faltando["email"] = lead["email"].strip()[:180]
            if not atual.get("utm"):
                u = extrair_utm(lead)
                if u:
                    faltando["utm"] = json.dumps(u, ensure_ascii=False)
            if faltando:
                self.est.set_lead(existente, **faltando)
            return {"ok": True, "acao": "atualizado", "card_id": existente}
        cap = self.ont.captavel(lead.get("curso", ""))
        # gate de captabilidade: curso não captável -> não engaja, vai p/ revisão
        ec = "aguardando_1o_contato" if cap.get("captavel") else "aguardando_revisao"
        card_id = f"meta-{_digits(tel)}"
        # LGPD: o envio do formulário Lead Ads com consentimento é o opt-in — registrado explícito
        optin = {} if str(lead.get("consentimento", "")).strip().lower() in ("false", "0", "nao", "não") \
            else {"optin_ts": datetime.datetime.now().isoformat(timespec="seconds"),
                  "optin_fonte": lead.get("fonte") or "meta_lead_ad"}
        utm = extrair_utm(lead)
        self.est.upsert_lead(card_id, nome=lead.get("nome", ""), telefone=tel,
                             email=(lead.get("email") or "").strip()[:180],
                             utm=json.dumps(utm, ensure_ascii=False) if utm else None,
                             curso=lead.get("curso", ""), cursos_interesse=lead.get("curso", ""),
                             fase="Lead", estado_conversa=ec, escolaridade=lead.get("escolaridade"),
                             **optin)
        if lead.get("qualificacao"):   # respostas do form -> auditoria (e o gate já foi pulado)
            self.est.log_acao("ingestao", f"pré-qualificado no formulário — {lead['qualificacao']}", card_id)
        if self.faro and self.faro.ativo:   # espelha no system of record
            try:
                fid = self.faro.criar_lead(lead.get("nome"), tel, lead.get("curso"))
                if fid:
                    self.est.set_lead(card_id, faro_id=fid)
            except Exception as e:
                print(f"[faro] criar_lead falhou: {e}", flush=True)
        return {"ok": True, "acao": "criado", "card_id": card_id,
                "captavel": cap.get("captavel"), "estado_conversa": ec}
