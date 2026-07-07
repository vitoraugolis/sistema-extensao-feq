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
import datetime
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))


FORM_CODE_RE = re.compile(r"FEQ[-_ ]?(\d{3,4})", re.I)


def _digits(p):
    return re.sub(r"\D", "", p or "")


def curso_de_texto(*textos):
    """Extrai o código do curso ('FEQ-0333') do nome do formulário/campanha.
    Padrão acordado: nome do formulário = curso + versão (ex.: 'FEQ-0333 v1')."""
    for t in textos:
        m = FORM_CODE_RE.search(t or "")
        if m:
            return f"FEQ-{m.group(1)}"
    return ""


def normalizar_meta(field_data, campaign_name="", leadgen_id="", curso=""):
    """Adapter Meta -> lead normalizado. field_data = [{name,values:[..]}].
    Curso vem do nome do formulário (via `curso`); fallbacks: campo do form, campanha."""
    fd = {i["name"]: (i.get("values") or [""])[0] for i in (field_data or [])}
    return {
        "nome": fd.get("full_name") or fd.get("nome") or "",
        "telefone": fd.get("phone_number") or fd.get("telefone") or "",
        "email": fd.get("email", ""),
        "curso": curso or fd.get("curso") or fd.get("curso_de_interesse") or curso_de_texto(campaign_name),
        "campanha": campaign_name, "lead_id": str(leadgen_id),
        "consentimento": fd.get("consent", "true"),
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
            self.est.set_lead(existente, curso=lead.get("curso") or self.est.get_lead(existente)["curso"])
            return {"ok": True, "acao": "atualizado", "card_id": existente}
        cap = self.ont.captavel(lead.get("curso", ""))
        # gate de captabilidade: curso não captável -> não engaja, vai p/ revisão
        ec = "aguardando_1o_contato" if cap.get("captavel") else "aguardando_revisao"
        card_id = f"meta-{_digits(tel)}"
        self.est.upsert_lead(card_id, nome=lead.get("nome", ""), telefone=tel,
                             curso=lead.get("curso", ""), fase="Lead", estado_conversa=ec)
        if self.faro and self.faro.ativo:   # espelha no system of record
            try:
                fid = self.faro.criar_lead(lead.get("nome"), tel, lead.get("curso"))
                if fid:
                    self.est.set_lead(card_id, faro_id=fid)
            except Exception as e:
                print(f"[faro] criar_lead falhou: {e}", flush=True)
        return {"ok": True, "acao": "criado", "card_id": card_id,
                "captavel": cap.get("captavel"), "estado_conversa": ec}
