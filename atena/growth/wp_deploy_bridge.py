#!/usr/bin/env python3
"""
Atena · Growth — deploy das Bridge Pages no WordPress (spec 16 §7).
Renderiza a LP de cada turma (bridge-template + bridge-logic + ontologia) e
publica como página órfã via WP REST API (Application Password). Idempotente:
slug existente é ATUALIZADO, não duplicado. Sem WP_URL/WP_USER/WP_APP_PASSWORD
no ambiente, roda em DRY-RUN: só renderiza os HTMLs em disco.

  python3 -m atena.growth.wp_deploy_bridge                    # 4 turmas do cursos_metas
  python3 -m atena.growth.wp_deploy_bridge --config extra.json  # sobrescreve/添 turmas
  python3 -m atena.growth.wp_deploy_bridge --saida out/         # onde salvar os HTMLs

Config JSON: [{"curso": "FEQ-0480", "turma": "009", "url": "https://ins.extecamp..."}]
Env: WP_URL (ex.: https://site.com.br) · WP_USER · WP_APP_PASSWORD ·
     ATENA_PUBLIC_URL (URL pública do hub — vira o {{WEBHOOK_URL}} da LP)
"""
import os
import sys
import json
import pathlib
import argparse

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from atena.growth import ponte


def _slug(curso, turma):
    return f"inscricao-{curso.lower()}-{turma}"


def _webhook_url():
    base = (os.environ.get("ATENA_PUBLIC_URL") or "").rstrip("/")
    if not base:
        print("[wp-deploy] AVISO: sem ATENA_PUBLIC_URL o webhook fica relativo — "
              "no WordPress a LP postaria no host errado. Defina antes de publicar.", flush=True)
    return f"{base}/webhook/ponte" if base else "/webhook/ponte"


def renderizar(g, ont, cursos, saida, est=None):
    """Gera o HTML de cada turma em `saida`. Retorna [{curso, slug, arquivo, html}].
    LP editada à mão na Central (tabela lp_html) tem precedência sobre o gerado — senão
    um deploy de rotina apagaria em silêncio o trabalho do operador."""
    saida = pathlib.Path(saida)
    saida.mkdir(parents=True, exist_ok=True)
    if est is None:
        from atena.core.estado import Estado
        est = Estado()
    paginas = []
    for m in cursos:
        editada = est.get_lp_html(m["curso"])
        html = editada["html"] if editada else ponte.render(g, ont, m["curso"],
                                                            webhook_url=_webhook_url())
        if editada:
            print(f"[wp-deploy] {m['curso']}: usando HTML EDITADO na Central "
                  f"({editada['atualizado']}) — não regenerado", flush=True)
        if not html:
            print(f"[wp-deploy] {m['curso']}: fora do cursos_metas — pulado", flush=True)
            continue
        slug = _slug(m["curso"], m["turma"])
        arq = saida / f"{slug}.html"
        arq.write_text(html)
        paginas.append({"curso": m["curso"], "slug": slug, "arquivo": str(arq), "html": html})
    return paginas


def _wp(sessao, base, metodo, rota, **kw):
    """None = falha (HTTP>=400, rede ou corpo não-JSON) — nunca confundir com 'não existe'."""
    try:
        r = sessao.request(metodo, f"{base}/wp-json/wp/v2/{rota}", timeout=30, **kw)
    except Exception as e:
        print(f"[wp-deploy] {rota}: {e}", flush=True)
        return None
    if r.status_code >= 400:
        print(f"[wp-deploy] {rota}: HTTP {r.status_code} {r.text[:200]}", flush=True)
        return None
    try:
        return r.json()
    except ValueError:   # 2xx não-JSON (WAF/manutenção): falha, não crash
        print(f"[wp-deploy] {rota}: resposta 2xx não-JSON ({r.text[:120]!r})", flush=True)
        return None


def publicar(paginas):
    """Publica/atualiza cada LP como página órfã (spec 17 §4): FRAGMENTO dentro de
    bloco wp:html (sem wpautop mastigando o JS) e validação pós-publicação — se o
    WP remover os <script> (usuário sem unfiltered_html), reporta FALHA, nunca
    página morta com status de sucesso. Sem credenciais -> dry-run."""
    from atena.growth.ponte import fragmento
    base = (os.environ.get("WP_URL") or "").rstrip("/")
    user, senha = os.environ.get("WP_USER"), os.environ.get("WP_APP_PASSWORD")
    if not (base and user and senha):
        return [{**{k: p[k] for k in ("curso", "slug", "arquivo")}, "status": "dry_run"}
                for p in paginas]
    import requests
    s = requests.Session()
    s.auth = (user, senha)
    out = []
    for p in paginas:
        corpo = {"title": f"Inscrição {p['curso']}", "slug": p["slug"], "status": "publish",
                 "content": "<!-- wp:html -->\n" + fragmento(p["html"]) + "\n<!-- /wp:html -->",
                 # noindex: campo do Yoast quando o plugin expõe via REST (spec 17 §4);
                 # se o WP rejeitar o meta desconhecido, republica sem ele.
                 "meta": {"_yoast_wpseo_meta-robots-noindex": "1"}}
        # status=publish (default, sempre permitido): rascunho com mesmo slug não é
        # o caso de uso aqui — status=any/draft exige capability que o usuário de
        # publicação pode não ter (rest_forbidden_status), e as páginas já nascem publicadas.
        achado = _wp(s, base, "GET", f"pages?slug={p['slug']}&per_page=1")
        if achado is None:   # busca falhou: parar ESTE slug (criar aqui duplicaria a página)
            out.append({"curso": p["curso"], "slug": p["slug"], "status": "falhou",
                        "motivo": "busca por slug falhou — nada publicado"})
            continue
        rota = f"pages/{achado[0]['id']}" if achado else "pages"
        r = _wp(s, base, "POST", rota, json=corpo)
        if r is None and corpo.pop("meta", None):
            r = _wp(s, base, "POST", rota, json=corpo)
        if not r:
            out.append({"curso": p["curso"], "slug": p["slug"], "status": "falhou"})
            continue
        publicado = ((r.get("content") or {}).get("rendered")
                     or _paginapublicada(s, r.get("link")) or "")
        if "bridge-form" not in publicado or "<script" not in publicado:
            out.append({"curso": p["curso"], "slug": p["slug"], "status": "falhou",
                        "link": r.get("link"),
                        "motivo": "WP removeu o modal/scripts — o usuário do Application "
                                  "Password precisa de unfiltered_html (administrador)"})
            continue
        out.append({"curso": p["curso"], "slug": p["slug"], "status": "publicado",
                    "link": r.get("link")})
    return out


OBRIGADO_SLUG = "thank-you-page-lp"   # sufixo, não prefixo: "lp-thank-you-page" não
# disparou o evento Lead no GTM (usuário confirmou) — indício de que o gatilho é
# "começa com /thank-you-page", não "contém" simples. Sufixo preserva esse prefixo
# de path sem colidir com a página Elementor pré-existente no slug exato
# "thank-you-page" (id 18818 no WP, de outra campanha, viva desde 02/2026).


def publicar_obrigado():
    """Publica /lp-thank-you-page/ (spec: pixel Google — ver ponte.pagina_obrigado_html).
    Página única, idempotente por slug, mesmo padrão de publicar(). Sem credenciais
    WP, dry-run (mesma regra das LPs)."""
    base = (os.environ.get("WP_URL") or "").rstrip("/")
    user, senha = os.environ.get("WP_USER"), os.environ.get("WP_APP_PASSWORD")
    if not (base and user and senha):
        return {"slug": OBRIGADO_SLUG, "status": "dry_run"}
    import requests
    s = requests.Session()
    s.auth = (user, senha)
    corpo = {"title": "Obrigado", "slug": OBRIGADO_SLUG, "status": "publish",
             "content": "<!-- wp:html -->\n" + ponte.pagina_obrigado_html() + "\n<!-- /wp:html -->",
             "meta": {"_yoast_wpseo_meta-robots-noindex": "1"}}
    achado = _wp(s, base, "GET", f"pages?slug={OBRIGADO_SLUG}&per_page=1")
    if achado is None:
        return {"slug": OBRIGADO_SLUG, "status": "falhou", "motivo": "busca por slug falhou"}
    rota = f"pages/{achado[0]['id']}" if achado else "pages"
    r = _wp(s, base, "POST", rota, json=corpo)
    if r is None and corpo.pop("meta", None):
        r = _wp(s, base, "POST", rota, json=corpo)
    if not r:
        return {"slug": OBRIGADO_SLUG, "status": "falhou"}
    return {"slug": OBRIGADO_SLUG, "status": "publicado", "link": r.get("link")}


def _paginapublicada(sessao, link):
    if not link:
        return ""
    try:
        return sessao.get(link, timeout=30).text
    except Exception:
        return ""


def main(argv):
    ap = argparse.ArgumentParser(prog="wp_deploy_bridge")
    ap.add_argument("--config", help="JSON com [{curso, turma, url}] p/ sobrescrever o default")
    ap.add_argument("--saida", default=str(pathlib.Path(__file__).resolve().parents[1]
                                           / "data" / "bridge_pages"))
    args = ap.parse_args(argv)

    from atena.growth.db import Growth
    from atena.ontologia.servico import Ontologia
    g, ont = Growth(), Ontologia()
    cursos = g.cursos()
    if args.config:
        cursos = json.loads(pathlib.Path(args.config).read_text())
        for m in cursos:                                  # config externo entra no cursos_metas
            if not any(x["curso"] == m["curso"] for x in g.cursos()):
                g.conn.execute("INSERT OR IGNORE INTO cursos_metas(curso,turma,url) VALUES(?,?,?)",
                               (m["curso"], m.get("turma", ""), m.get("url", "")))
        g.conn.commit()
    paginas = renderizar(g, ont, cursos, args.saida)
    resultados = publicar(paginas)
    resultados.append(publicar_obrigado())
    return resultados


if __name__ == "__main__":
    for r in main(sys.argv[1:]):
        print(json.dumps(r, ensure_ascii=False))
