#!/usr/bin/env python3
"""
Atena · Reconciliação Meta (rede de segurança do webhook — spec 04).
O webhook `leadgen` pode não entregar (config/App Review/entrega instável da
Meta). Este poller varre as Páginas -> formulários instantâneos ATIVOS -> leads
e ingere+ativa os que faltaram, para que nenhum lead dependa só do push.

Dedup em duas camadas: leadgen id (`msgs_vistas` via est.viu_msg/msg_ja_vista)
e telefone (no próprio ingest). ponytail: pega só a 1ª página de leads (mais
recentes) por form — a uma cadência de ~5 min sobra folga; se um form receber
mais que `limite_form` entre polls, o excedente entra no poll seguinte.
"""
import os

BASE = "https://graph.facebook.com/v19.0"


def _graph(url, params):
    import requests
    r = requests.get(url, params=params, timeout=20)
    return r.json() if r.text else {}


def _token():
    return os.environ.get("META_PAGE_TOKEN") or os.environ.get("WHATSAPP_TOKEN")


def poll_meta(est, ont, ing, contato_fn=None, limite_form=50):
    """Varre Páginas->forms ATIVOS->leads e ingere os novos. Retorna nº de novos
    ativados. `contato_fn(card_id)` dispara o 1º contato (None = só ingere)."""
    from atena.agentes.ingestao import normalizar_meta, curso_de_texto
    tok = _token()
    if not tok:
        return 0
    try:
        pages = _graph(f"{BASE}/me/accounts",
                       {"fields": "id,access_token", "access_token": tok}).get("data", [])
    except Exception as e:
        print(f"[reconcilia] me/accounts falhou: {e}", flush=True)
        return 0
    novos = 0
    for pg in pages:
        pt = pg.get("access_token") or tok
        try:
            forms = _graph(f"{BASE}/{pg['id']}/leadgen_forms",
                           {"fields": "id,name,status", "limit": 100, "access_token": pt}).get("data", [])
        except Exception:
            continue
        for f in forms:
            if f.get("status") != "ACTIVE":
                continue
            curso = curso_de_texto(f.get("name", ""))
            try:
                leads = _graph(f"{BASE}/{f['id']}/leads",
                               {"fields": "id,field_data", "limit": limite_form,
                                "access_token": pt}).get("data", [])
            except Exception:
                continue
            for l in leads:
                lid = str(l.get("id", ""))
                if not lid or est.viu_msg(f"leadgen:{lid}"):   # já processado -> pula
                    continue
                try:
                    lead = normalizar_meta(l.get("field_data"), leadgen_id=lid, curso=curso)
                    r = ing.ingest(lead)
                    est.msg_ja_vista(f"leadgen:{lid}")         # marca após ingerir
                    if r.get("ok") and r.get("acao") == "criado" and r.get("captavel") and contato_fn:
                        contato_fn(r["card_id"])
                        novos += 1
                except Exception as e:
                    print(f"[reconcilia] lead {lid} falhou: {e}", flush=True)
    if novos:
        est.log_acao("sistema", f"reconciliação Meta: {novos} lead(s) novos ingeridos e ativados")
    return novos


# --- ECO-1100: LP no Vercel grava os leads no BigQuery (guara-db.eco1100) ---------
BQ_PROJETO = "guara-db"
BQ_SQL = ("SELECT nome, email, whatsapp, consentimento_lgpd, event_id, utm_source, utm_medium,"
          " utm_campaign, utm_content, utm_term, UNIX_SECONDS(criado_em) AS ts"
          " FROM eco1100.leads_eco_1100"
          " WHERE IFNULL(utm_source, '') != 'seed_teste' ORDER BY criado_em")
# ponytail: lead mais velho que isto só vira card, sem 1º contato — evita abordar quem
# preencheu há dias (backlog da integração, poller parado). 24h é chute; ajustar aqui.
BQ_CONTATO_MAX_H = 24


def _bq_linhas():
    """Linhas da tabela de leads do ECO-1100. Sem GOOGLE_BQ_SA_FILE -> [] (inerte).
    ponytail: varre a tabela toda (~dezenas de linhas; 10 MB mínimo cobrado por query,
    ~3 GB/dia a cada 5 min, dentro do free tier de 1 TB/mês). Particionar por
    criado_em + filtrar por janela se a tabela crescer muito."""
    arq = os.environ.get("GOOGLE_BQ_SA_FILE")
    if not arq:
        return []
    from google.oauth2 import service_account
    from google.auth.transport.requests import AuthorizedSession
    cred = service_account.Credentials.from_service_account_file(
        arq, scopes=["https://www.googleapis.com/auth/bigquery.readonly"])
    r = AuthorizedSession(cred).post(
        f"https://bigquery.googleapis.com/bigquery/v2/projects/{BQ_PROJETO}/queries",
        json={"query": BQ_SQL, "useLegacySql": False, "timeoutMs": 20000}, timeout=30)
    j = r.json()
    if r.status_code != 200 or not j.get("jobComplete"):
        raise RuntimeError(f"BigQuery {r.status_code}: {str(j)[:200]}")
    nomes = [f["name"] for f in j["schema"]["fields"]]
    return [dict(zip(nomes, (c["v"] for c in row["f"]))) for row in j.get("rows", [])]


def poll_bigquery_eco(est, ing, contato_fn=None, linhas_fn=None, agora=None):
    """Ingere os leads novos da LP do ECO-1100 (BigQuery). Mesmo contrato de
    poll_meta: retorna nº de novos ativados (1º contato disparado)."""
    import re, time
    try:
        linhas = (linhas_fn or _bq_linhas)()
    except Exception as e:
        print(f"[reconcilia-bq] consulta falhou: {e}", flush=True)
        return 0
    agora = agora or time.time()
    novos = 0
    for l in linhas:
        tel = re.sub(r"\D", "", l.get("whatsapp") or "")
        ts = float(l.get("ts") or 0)
        # event_id falta em linhas antigas -> chave por telefone+instante de criação
        chave = f"bq-eco:{l.get('event_id') or f'{tel}:{int(ts)}'}"
        if not tel or est.viu_msg(chave):
            continue
        consentiu = str(l.get("consentimento_lgpd")).lower() == "true"
        lead = {"nome": l.get("nome") or "", "telefone": tel, "email": l.get("email") or "",
                "curso": "ECO-1100", "lead_id": l.get("event_id") or "",
                "consentimento": "true" if consentiu else "false", "fonte": "lp_eco1100",
                **{k: l[k] for k in ("utm_source", "utm_medium", "utm_campaign",
                                     "utm_content", "utm_term") if l.get(k)}}
        try:
            r = ing.ingest(lead)
            est.msg_ja_vista(chave)
            if (r.get("ok") and r.get("acao") == "criado" and r.get("captavel") and consentiu
                    and contato_fn and agora - ts < BQ_CONTATO_MAX_H * 3600):
                contato_fn(r["card_id"])
                novos += 1
        except Exception as e:
            print(f"[reconcilia-bq] lead {chave} falhou: {e}", flush=True)
    if novos:
        est.log_acao("sistema", f"LP ECO-1100 (BigQuery): {novos} lead(s) novos ingeridos e ativados")
    return novos


if __name__ == "__main__":   # smoke: sem rede, com _graph falso
    import sys, pathlib
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
    class _Est:
        def __init__(self): self.vistos=set(); self.ingeridos=[]; self.contatos=[]; self.logs=[]
        def viu_msg(self, m): return m in self.vistos
        def msg_ja_vista(self, m):
            was = m in self.vistos; self.vistos.add(m); return was
        def log_acao(self, *a): self.logs.append(a)
    class _Ing:
        def __init__(self, est): self.est=est
        def ingest(self, lead):
            self.est.ingeridos.append(lead)
            return {"ok": True, "acao": "criado", "card_id": "meta-"+lead["telefone"][-6:], "captavel": True}
    fake = {
        "me/accounts": {"data": [{"id": "P1", "access_token": "pt"}]},
        "P1/leadgen_forms": {"data": [{"id": "F1", "name": "FEQ-0333 v2", "status": "ACTIVE"},
                                      {"id": "F2", "name": "Antigo", "status": "PAUSED"}]},
        "F1/leads": {"data": [{"id": "L1", "field_data": [{"name": "phone_number", "values": ["+55 19 90000-0001"]},
                                                          {"name": "full_name", "values": ["Ana"]}]},
                              {"id": "L2", "field_data": [{"name": "phone_number", "values": ["+55 19 90000-0002"]}]}]},
    }
    def _fake_graph(url, params):
        for k, v in fake.items():
            if url.endswith(k):
                return v
        return {"data": []}
    globals()["_graph"] = _fake_graph
    os.environ["META_PAGE_TOKEN"] = "x"
    est = _Est(); est_ont = None
    contatos = []
    n = poll_meta(est, est_ont, _Ing(est), contato_fn=lambda cid: contatos.append(cid))
    assert n == 2, n
    assert len(est.ingeridos) == 2 and len(contatos) == 2         # form PAUSED ignorado
    assert poll_meta(est, est_ont, _Ing(est), contato_fn=lambda cid: contatos.append(cid)) == 0  # dedup: nada novo
    print("reconciliacao smoke OK:", n, "novos; re-poll 0 (dedup)")

    # BigQuery ECO-1100: contato só p/ lead recente com consentimento; dedup por event_id
    agora = 1_800_000_000
    linhas = [{"nome": "Bia", "whatsapp": "(11) 90000-0003", "consentimento_lgpd": "true",
               "event_id": "e1", "utm_source": "ig", "ts": str(agora - 600)},
              {"nome": "Caio", "whatsapp": "11900000004", "consentimento_lgpd": "true",
               "event_id": None, "ts": str(agora - 3 * 86400)},        # velho: só card
              {"nome": "Duda", "whatsapp": "11900000005", "consentimento_lgpd": "false",
               "event_id": "e3", "ts": str(agora - 60)}]              # sem consentimento: só card
    est = _Est(); contatos = []
    n = poll_bigquery_eco(est, _Ing(est), lambda cid: contatos.append(cid), lambda: linhas, agora)
    assert n == 1 and len(est.ingeridos) == 3, (n, est.ingeridos)
    assert est.ingeridos[0]["curso"] == "ECO-1100" and est.ingeridos[0]["utm_source"] == "ig"
    assert poll_bigquery_eco(est, _Ing(est), lambda cid: contatos.append(cid), lambda: linhas, agora) == 0
    assert len(est.ingeridos) == 3                                  # re-poll não re-ingere
    print("bigquery ECO-1100 smoke OK")
