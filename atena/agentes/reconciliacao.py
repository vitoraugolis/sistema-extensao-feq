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
