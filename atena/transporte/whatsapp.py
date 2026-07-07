#!/usr/bin/env python3
"""
Atena · Transporte WhatsApp Business Cloud.
Webhook de entrada -> orquestrador; envio guardado. Sem WHATSAPP_TOKEN o envio é
DRY-RUN (não manda nada). Fora do horário comercial, enfileira (tarefa) em vez de
enviar. Opt-out respeitado. Decisão da mensagem é do orquestrador; aqui só entrega.
ponytail: janela de 24h não rastreada por timestamp ainda -> envia texto livre em
dev; em produção, fora de 24h exige template (TODO marcado).
"""
import os
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from atena.core import guardrails as G

_GRAPH = "https://graph.facebook.com/v19.0"


class WhatsApp:
    def __init__(self, estado, orquestrador):
        self.est = estado
        self.orq = orquestrador
        self.token = os.environ.get("WHATSAPP_TOKEN")
        self.phone_id = os.environ.get("PHONE_NUMBER_ID", "")

    def handshake(self, args):
        if args.get("hub.verify_token") == os.environ.get("META_VERIFY_TOKEN", "troque-este-token"):
            return args.get("hub.challenge", ""), 200
        return "forbidden", 403

    def receber(self, payload):
        """Extrai telefone+texto e encaminha ao orquestrador."""
        out = []
        for entry in payload.get("entry", []):
            for ch in entry.get("changes", []):
                v = ch.get("value", {})
                for m in v.get("messages", []):
                    tel = m.get("from", "")
                    texto = (m.get("text") or {}).get("body", "")
                    card_id = self.est.lead_por_telefone(tel)
                    if not card_id:  # número desconhecido -> stub p/ revisão (não inventa curso)
                        card_id = f"wa-{tel}"
                        self.est.upsert_lead(card_id, nome="", telefone=tel, curso="",
                                             fase="Lead", estado_conversa="aguardando_revisao")
                        self.est.add_conversa(card_id, "lead", texto)
                        out.append({"card_id": card_id, "acao": "revisao_numero_desconhecido"})
                        continue
                    out.append({"card_id": card_id, **self.orq.processar_mensagem(card_id, texto)})
        return out

    def enviar(self, telefone, texto):
        if not texto:
            return {"ok": False, "motivo": "sem texto"}
        # guardrails de destinatário: opt-out e janela de 24h (texto livre só dentro dela)
        card_id = self.est.lead_por_telefone(telefone) if self.est else None
        if card_id:
            lead = self.est.get_lead(card_id)
            if lead and lead.get("optout"):
                return {"ok": False, "status": "bloqueado", "motivo": "lead em opt-out"}
            ts = self.est.ultima_lead_ts(card_id)
            import datetime
            if not ts or (datetime.datetime.now() - datetime.datetime.fromisoformat(ts)).total_seconds() > 86400:
                return {"ok": False, "status": "fora_da_janela_24h",
                        "motivo": "sem mensagem do lead nas últimas 24h — use template aprovado"}
        if not G.dentro_janela():
            self.est.log_acao("sistema", "envio_enfileirado", telefone)
            return {"ok": True, "status": "enfileirado", "motivo": "fora do horário comercial"}
        if not self.token:
            return {"ok": True, "status": "dry_run", "para": telefone, "texto": texto}
        import requests
        r = requests.post(f"{_GRAPH}/{self.phone_id}/messages", timeout=30,
                          headers={"Authorization": f"Bearer {self.token}",
                                   "Content-Type": "application/json"},
                          json={"messaging_product": "whatsapp", "to": telefone,
                                "type": "text", "text": {"body": texto}})
        return {"ok": r.status_code == 200, "status": r.status_code}

    def enviar_template(self, telefone, nome, idioma="pt_BR", params=None):
        """1º contato / fora da janela de 24h: exige template aprovado.
        ponytail: sem gate de horário aqui (template é operador/sistema-iniciado, pré-aprovado)."""
        card_id = self.est.lead_por_telefone(telefone) if self.est else None
        if card_id:
            lead = self.est.get_lead(card_id)
            if lead and lead.get("optout"):
                return {"ok": False, "status": "bloqueado", "motivo": "lead em opt-out"}
        if not self.token:
            return {"ok": True, "status": "dry_run", "template": nome, "para": telefone}
        tpl = {"name": nome, "language": {"code": idioma}}
        if params:
            tpl["components"] = [{"type": "body",
                                  "parameters": [{"type": "text", "text": str(p)} for p in params]}]
        import requests
        r = requests.post(f"{_GRAPH}/{self.phone_id}/messages", timeout=30,
                          headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"},
                          json={"messaging_product": "whatsapp", "to": telefone,
                                "type": "template", "template": tpl})
        return {"ok": r.status_code == 200, "status": r.status_code,
                "resp": r.json() if r.text else {}}
