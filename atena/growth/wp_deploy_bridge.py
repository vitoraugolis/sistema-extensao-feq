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
import re
import sys
import json
import pathlib
import argparse

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from atena.growth import ponte


# A regra de slug mora em ponte.py: a LP (cross-sell), o bloco de cursos da home e o
# deploy têm de produzir a MESMA URL — quando eram duas implementações, o cross-sell
# apontava para um slug que o deploy não publicava.
LEGADO_TURMA = ponte.LEGADO_TURMA
_slug = ponte.slug_lp


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


HOME_PAGE_ID = 8157          # "nova-home" — a home do site (Elementor)
HOME_ANCORA = "cursos"       # seção-título com a âncora #cursos, que o menu usa
HOME_TITULO = "Cursos da Extensão FEQ Unicamp"
# Fim do bloco: a seção de diferenciais. É o limite que garante que hero (antes) e
# diferenciais/logos/CTA (depois) fiquem intactos.
HOME_FIM = "Saia na frente"
_HOME_IDS = ("feqcards1", "feqcards2", "feqcards3")   # 'q' não é hex: não colide com id do Elementor


def _texto(no):
    return json.dumps(no, ensure_ascii=False)


def _e_bloco_de_cursos(no):
    """Seção que faz parte da grade de cursos? Aceita: grade original (links para as
    páginas antigas), título de curso, seção vazia (espaçador) e a nossa (já marcada).
    Qualquer outra coisa no meio do caminho aborta o deploy — é conteúdo que não é meu."""
    if (no.get("settings") or {}).get("_element_id") == _HOME_IDS[0]:
        return True
    t = _texto(no)
    if "cursos-longa-duracao" in t or "cursos-curta-duracao" in t:
        return True
    if _tem_titulo(no, "Curso"):
        return True
    return '"widgetType"' not in t          # seção sem widget nenhum (espaçador)


def _tem_titulo(no, texto):
    # \s* depois do ':' — json.dumps separa com espaço, o Elementor não; a regex tem
    # de casar as duas formas, senão o título do 2º bloco não é reconhecido.
    return bool(re.search(r'"title":\s*"[^"]*%s' % re.escape(texto), _texto(no)))


def _fatia_de_cursos(dados):
    """(i_titulo, ini, fim): a seção-título com a âncora #cursos, e o intervalo de
    seções [ini, fim) que forma a grade — do que vem depois do título até a seção de
    diferenciais. Devolve (None, None, None) se não reconhecer: melhor não publicar
    do que recortar o pedaço errado da home."""
    i_tit = next((i for i, s in enumerate(dados)
                  if (s.get("settings") or {}).get("_element_id") == HOME_ANCORA), None)
    i_fim = next((i for i, s in enumerate(dados) if _tem_titulo(s, HOME_FIM)), None)
    if i_tit is None or i_fim is None or i_fim <= i_tit + 1:
        return None, None, None
    if not all(_e_bloco_de_cursos(s) for s in dados[i_tit + 1:i_fim]):
        return None, None, None
    return i_tit, i_tit + 1, i_fim


def _secao_cards(html):
    return {"id": _HOME_IDS[0], "elType": "section", "isInner": False,
            "settings": {"_element_id": _HOME_IDS[0]},
            "elements": [{"id": _HOME_IDS[1], "elType": "column", "isInner": False,
                          "settings": {"_column_size": 100, "_inline_size": None},
                          "elements": [{"id": _HOME_IDS[2], "elType": "widget",
                                        "widgetType": "html", "isInner": False,
                                        "settings": {"html": html}, "elements": []}]}]}


def publicar_home_cursos(g, ont, backup_dir=None):
    """Troca APENAS o bloco de cursos da home pelos cards gerados da ontologia.

    A home é uma página Elementor: quem manda no que aparece é o `_elementor_data`
    (o `post_content` é só o snapshot que o Elementor grava junto). O bloco ocupa
    VÁRIAS seções — dois títulos e duas grades — e vira uma só, com o widget HTML dos
    cards; hero, diferenciais, logos, menu e rodapé não são tocados.
    O `_elementor_data` original é salvo em disco antes de escrever: restaurar é subir
    o arquivo de volta. Sem credenciais, dry-run.
    A conferência é pelo `content.rendered` da API (render do Elementor, sem cache de
    página no meio) — a home no ar pode demorar a atualizar se houver cache."""
    html = ponte.home_cursos_html(g, ont)
    if not html:
        return {"status": "falhou", "motivo": "nenhum curso no cursos_metas"}
    base = (os.environ.get("WP_URL") or "").rstrip("/")
    user, senha = os.environ.get("WP_USER"), os.environ.get("WP_APP_PASSWORD")
    if not (base and user and senha):
        return {"status": "dry_run", "cards": html.count('class="feq-card"')}
    import requests
    s = requests.Session()
    s.auth = (user, senha)
    pg = _wp(s, base, "GET", f"pages/{HOME_PAGE_ID}?context=edit&_fields=id,link,meta")
    if not pg:
        return {"status": "falhou", "motivo": "não consegui ler a home"}
    try:
        dados = json.loads((pg.get("meta") or {}).get("_elementor_data") or "[]")
    except ValueError:
        return {"status": "falhou", "motivo": "_elementor_data ilegível"}
    if backup_dir:
        bkp = pathlib.Path(backup_dir) / "home_elementor_antes.json"
        bkp.parent.mkdir(parents=True, exist_ok=True)
        if not bkp.exists():          # nunca sobrescreve o backup do estado ORIGINAL
            bkp.write_text(json.dumps(dados, ensure_ascii=False))
            print(f"[wp-deploy] backup do Elementor da home em {bkp}", flush=True)
    i_tit, ini, fim = _fatia_de_cursos(dados)
    if ini is None:
        return {"status": "falhou",
                "motivo": "não reconheci o bloco de cursos (âncora #cursos, seção de "
                          f"'{HOME_FIM}' ou conteúdo estranho no meio) — nada publicado"}

    def _retitula(no):
        if no.get("widgetType") == "heading" and (no.get("settings") or {}).get("title"):
            no["settings"]["title"] = HOME_TITULO
            return True
        return any(_retitula(f) for f in no.get("elements") or [])
    _retitula(dados[i_tit])
    trocadas = fim - ini
    dados[ini:fim] = [_secao_cards(html)]
    r = _wp(s, base, "POST", f"pages/{HOME_PAGE_ID}",
            json={"meta": {"_elementor_data": json.dumps(dados, ensure_ascii=False)}})
    if not r:
        return {"status": "falhou", "motivo": "POST recusado (meta _elementor_data)"}
    # confere no DADO gravado (o render do Elementor é servido de cache e só é
    # invalidado por um save do próprio Elementor — ver retorno abaixo)
    depois = _wp(s, base, "GET", f"pages/{HOME_PAGE_ID}?context=edit&_fields=meta") or {}
    gravado = (depois.get("meta") or {}).get("_elementor_data") or ""
    if ponte.HOME_MARCA not in gravado:
        return {"status": "falhou", "motivo": "o POST voltou 200 mas o bloco não ficou gravado"}
    resto = json.dumps(dados[fim - trocadas + 1:], ensure_ascii=False)
    vivo = _paginapublicada(s, pg.get("link") or base + "/")
    # carimbo de VERSÃO, não só a presença do bloco: o Elementor serve a versão
    # anterior do cache, e "existe um bloco nosso" respondia publicado do mesmo jeito.
    versao = html.split(" -->")[0].split("v=")[-1]
    if versao in vivo:
        return {"status": "publicado", "link": pg.get("link"), "versao": versao,
                "cards": vivo.count('class="feq-card"'), "secoes_trocadas": trocadas}
    return {"status": "gravado_aguardando_cache", "link": pg.get("link"), "versao": versao,
            "no_ar_agora": ("versão anterior do bloco" if ponte.HOME_MARCA in vivo
                            else "grade antiga"),
            # sem as aspas: dentro do JSON do Elementor elas vêm escapadas
            "cards": gravado.count("feq-card\\\" href") or gravado.count("feq-card"),
            "secoes_trocadas": trocadas,
            "resto_da_home_intacto": {"diferenciais": HOME_FIM in resto,
                                      "logos": "braskem-1" in resto,
                                      "cta_final": "Pronto para iniciar" in resto},
            "acao": "o bloco está no builder; o Elementor serve HTML em cache e só o "
                    "invalida no save dele — abrir a home no Elementor e clicar em "
                    "Update (ou Elementor > Ferramentas > Regenerar arquivos e dados) "
                    "publica o bloco na tela"}


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
    ap.add_argument("--home", action="store_true",
                    help="publica TAMBÉM o bloco de cursos da home (só a grade de cards)")
    ap.add_argument("--so-home", action="store_true", help="publica só o bloco da home")
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
    if args.so_home:
        return [publicar_home_cursos(g, ont, backup_dir=args.saida)]
    paginas = renderizar(g, ont, cursos, args.saida)
    resultados = publicar(paginas)
    resultados.append(publicar_obrigado())
    if args.home:
        resultados.append(publicar_home_cursos(g, ont, backup_dir=args.saida))
    return resultados


if __name__ == "__main__":
    for r in main(sys.argv[1:]):
        print(json.dumps(r, ensure_ascii=False))
