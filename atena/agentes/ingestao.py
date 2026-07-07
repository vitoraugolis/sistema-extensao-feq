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


class Ingestao:
    def __init__(self, estado, ont):
        self.est = estado
        self.ont = ont

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
        return {"ok": True, "acao": "criado", "card_id": card_id,
                "captavel": cap.get("captavel"), "estado_conversa": ec}
