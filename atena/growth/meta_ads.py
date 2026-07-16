#!/usr/bin/env python3
"""
Atena · Growth — cliente Meta Marketing API + Conversions API (spec 15).
Sem META_ADS_TOKEN a leitura devolve [] e a escrita é DRY-RUN; sem
META_PIXEL_ID a CAPI é DRY-RUN (devolve o payload p/ inspeção). Backoff
exponencial em 429/5xx/rate-limit; falha final loga e devolve None —
nunca derruba a passada do motor.
"""
import os
import re
import time
import hashlib

_GRAPH = "https://graph.facebook.com/v19.0"
_RATE_CODES = {4, 17, 32, 613, 80000, 80003, 80004}


def _sha(v):
    return hashlib.sha256(v.encode()).hexdigest()


def tel_e164(telefone):
    """Digits-only com DDI 55 (formato que a Meta espera antes do hash)."""
    d = re.sub(r"\D", "", telefone or "")
    return d if d.startswith("55") else "55" + d


def _user_data(telefone=None, email=None, contexto=None):
    """user_data da CAPI: PII SÓ sai como SHA-256 (em: trim+lowercase; ph: E.164).
    contexto (opcional) = sinais de match do navegador {fbp, fbc, ip, ua} — não são PII
    hasheada por definição da Meta, vão em claro."""
    ud = {}
    if telefone:
        ud["ph"] = [_sha(tel_e164(telefone))]
    if email:
        ud["em"] = [_sha(email.strip().lower())]
    c = contexto or {}
    if c.get("fbp"):
        ud["fbp"] = c["fbp"]
    if c.get("fbc"):
        ud["fbc"] = c["fbc"]
    if c.get("ip"):
        ud["client_ip_address"] = c["ip"]
    if c.get("ua"):
        ud["client_user_agent"] = c["ua"]
    return ud


def _leads_de_actions(actions):
    return sum(int(a.get("value", 0)) for a in (actions or [])
               if a.get("action_type") in ("lead", "onsite_conversion.lead_grouped",
                                           "leadgen_grouped"))


class MetaAds:
    def __init__(self):
        self.token = os.environ.get("META_ADS_TOKEN")
        self.account = os.environ.get("META_AD_ACCOUNT_ID", "")   # act_...
        self.pixel = os.environ.get("META_PIXEL_ID", "")
        self.capi_token = os.environ.get("META_CAPI_TOKEN") or self.token

    @property
    def ativo(self):
        return bool(self.token and self.account)

    def _req(self, metodo, node, params=None, json_body=None, token=None):
        import requests
        atraso = 1
        for _ in range(4):
            try:
                r = requests.request(metodo, f"{_GRAPH}/{node}", timeout=30,
                                     params={**(params or {}),
                                             "access_token": token or self.token},
                                     json=json_body)
                if r.status_code < 400:
                    return r.json()
                err = (r.json().get("error") or {}) if r.text else {}
                if r.status_code == 429 or r.status_code >= 500 \
                        or err.get("code") in _RATE_CODES:
                    time.sleep(atraso); atraso *= 2; continue
                print(f"[meta-ads] {node}: {str(err)[:200]}", flush=True)
                return None
            except Exception as e:
                print(f"[meta-ads] {node} tentativa falhou: {e}", flush=True)
                time.sleep(atraso); atraso *= 2
        return None

    # ------------------------------------------------------------ leitura
    def insights(self, nivel="adset", periodo="last_7d"):
        """Métricas por dia × conjunto. Sem token -> [] (motor segue só com dados locais)."""
        if not self.ativo:
            return []
        j = self._req("GET", f"{self.account}/insights", params={
            "level": nivel, "date_preset": periodo, "time_increment": 1, "limit": 500,
            "fields": "adset_id,adset_name,campaign_name,spend,impressions,clicks,actions"})
        if not j:
            return []
        if (j.get("paging") or {}).get("next"):   # ponytail: 1 página (500 linhas ≈ 70 adsets×7d)
            print("[meta-ads] insights paginou além de 500 linhas — aumente limit", flush=True)
        return [{"dia": r.get("date_start", ""),
                 "objeto_id": r.get("adset_id", ""),
                 "nome": r.get("adset_name", ""),
                 "campanha": r.get("campaign_name", ""),
                 "gasto": float(r.get("spend") or 0),
                 "impressoes": int(r.get("impressions") or 0),
                 "cliques": int(r.get("clicks") or 0),
                 "leads": _leads_de_actions(r.get("actions"))}
                for r in j.get("data", [])]

    def orcamento(self, adset_id):
        """Verba diária atual (centavos) ou None. Vale p/ adset OU campanha (CBO)."""
        j = self._req("GET", adset_id, params={"fields": "daily_budget,status,name"})
        try:
            return int(j["daily_budget"])
        except (TypeError, KeyError, ValueError):
            return None

    def _listar(self, borda, campos):
        if not self.ativo:
            return []
        j = self._req("GET", f"{self.account}/{borda}", params={"fields": campos, "limit": 200})
        return (j or {}).get("data", [])

    def campanhas(self):
        return self._listar("campaigns", "id,name,status,effective_status,daily_budget,objective")

    def conjuntos(self):
        return self._listar("adsets", "id,name,status,effective_status,daily_budget,campaign_id")

    def anuncios(self):
        return self._listar("ads", "id,name,status,effective_status,adset_id")

    # ------------------------------------------------------------ escrita
    def pausar(self, objeto_id):
        if not self.token:
            return {"ok": True, "status": "dry_run", "objeto": objeto_id}
        j = self._req("POST", objeto_id, json_body={"status": "PAUSED"})
        return {"ok": bool(j and j.get("success")), "resp": j}

    def definir_orcamento(self, adset_id, centavos):
        if not self.token:
            return {"ok": True, "status": "dry_run", "objeto": adset_id, "centavos": centavos}
        j = self._req("POST", adset_id, json_body={"daily_budget": int(centavos)})
        return {"ok": bool(j and j.get("success")), "resp": j}

    # --------------------------------------------------------------- CAPI
    def capi(self, evento, telefone, event_id, custom=None, *, email=None,
             action_source=None, contexto=None):
        """Evento server-side ao pixel. PII só sai como SHA-256 (_user_data).
        event_id vindo do NAVEGADOR (ponte) garante a desduplicação Pixel×CAPI —
        nesse caso action_source=website e contexto traz fbp/fbc/ip/ua/url."""
        c = contexto or {}
        ev = {"event_name": evento, "event_time": int(time.time()),
              "event_id": event_id,
              "action_source": action_source or "system_generated",
              "user_data": _user_data(telefone, email, c),
              **({"custom_data": custom} if custom else {})}
        if c.get("url"):
            ev["event_source_url"] = c["url"]
        payload = {"data": [ev]}
        tec = os.environ.get("GROWTH_TEST_EVENT_CODE")
        if tec:
            payload["test_event_code"] = tec
        if not (self.pixel and self.capi_token):
            return {"ok": True, "status": "dry_run", "payload": payload}
        j = self._req("POST", f"{self.pixel}/events", json_body=payload, token=self.capi_token)
        return {"ok": bool(j and j.get("events_received")), "resp": j}
