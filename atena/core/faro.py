#!/usr/bin/env python3
"""
Atena · Cliente FARO (system of record externo — spec 02/03).
Espelha leads e transições de fase no pipeline [FEQ] CRM. Sem FARO_KEY no
ambiente, todas as operações são no-op (o SQLite local segue como espelho).
Backoff exponencial em 5xx; erros nunca derrubam o fluxo de conversa.
"""
import os
import time

BASE = "https://ecinffgoycqvktmrmvaz.supabase.co/functions/v1"
PIPELINE = os.environ.get("FARO_PIPELINE_ID", "49d65f66-ec1d-4c10-a5ff-71aef07f9279")
STAGES = {  # fase local -> UUID no FARO (spec 02)
    "Lead": "25c2ebce-624f-48cc-8358-a12f16988e65",
    "Prospect": "343b826f-12a6-4dd2-a9fc-04c50739a7bb",
    "Inscrito": "4fe21843-e74c-4966-b81a-bbf738e42b9a",
    "Matriculado": "52c8ca57-ab5b-4b99-ade4-be9a9fa0d18f",
    "Em espera": "295a20bc-c84d-4159-859b-23ccea643747",
    "Perdido": "5a16e79b-dd4a-471d-ae50-ddbce03ddd13",
}


class Faro:
    def __init__(self):
        self.key = os.environ.get("FARO_KEY")

    @property
    def ativo(self):
        return bool(self.key)

    def _post(self, path, body):
        if not self.ativo:
            return None
        import requests
        atraso = 1
        for _ in range(3):
            try:
                r = requests.post(f"{BASE}/{path}", timeout=15, json=body,
                                  headers={"Authorization": f"Bearer {self.key}",
                                           "Content-Type": "application/json"})
                if r.status_code < 500:
                    return r.json() if r.text else {}
            except Exception:
                pass
            time.sleep(atraso)
            atraso *= 2
        print(f"[faro] {path} falhou após 3 tentativas", flush=True)
        return None

    def criar_lead(self, nome, telefone, curso):
        """Cria card em 'Lead'; retorna o id do card no FARO (ou None)."""
        d = self._post("api-cards-create", {
            "pipeline_id": PIPELINE, "stage_id": STAGES["Lead"],
            "title": nome or telefone or "Lead",
            "fields": {"Nome Completo": nome or "", "Telefone": telefone or "",
                       "Curso de Interesse": curso or "", "Origem": "Meta Ads"}})
        return (d or {}).get("card", {}).get("id")

    def mover(self, faro_id, fase_local):
        if faro_id and fase_local in STAGES:
            self._post("api-cards-move", {"card_id": faro_id, "stage_id": STAGES[fase_local]})

    def atualizar(self, faro_id, fields):
        if faro_id and fields:
            self._post("api-cards-update", {"card_id": faro_id, "fields": fields})
