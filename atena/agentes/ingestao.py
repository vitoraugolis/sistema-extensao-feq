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


def _digits(p):
    return re.sub(r"\D", "", p or "")


def normalizar_meta(field_data, campaign_name="", leadgen_id=""):
    """Adapter Meta -> lead normalizado. field_data = [{name,values:[..]}]."""
    fd = {i["name"]: (i.get("values") or [""])[0] for i in (field_data or [])}
    return {
        "nome": fd.get("full_name") or fd.get("nome") or "",
        "telefone": fd.get("phone_number") or fd.get("telefone") or "",
        "email": fd.get("email", ""),
        "curso": fd.get("curso") or fd.get("curso_de_interesse") or "",
        "campanha": campaign_name, "lead_id": str(leadgen_id),
        "consentimento": fd.get("consent", "true"),
    }


def fetch_meta_lead(leadgen_id):
    """Webhook leadgen real só traz o ID — busca os dados na Graph API.
    Token: META_PAGE_TOKEN (preferido) ou WHATSAPP_TOKEN (se tiver leads_retrieval)."""
    import os
    import requests
    token = os.environ.get("META_PAGE_TOKEN") or os.environ.get("WHATSAPP_TOKEN")
    if not token or not leadgen_id:
        return None
    try:
        r = requests.get(f"https://graph.facebook.com/v19.0/{leadgen_id}",
                         params={"access_token": token}, timeout=20)
        if r.status_code != 200:
            print(f"[ingestao] Graph {r.status_code}: {r.text[:150]}", flush=True)
            return None
        data = r.json()
        return normalizar_meta(data.get("field_data"), data.get("campaign_name", ""), leadgen_id)
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
