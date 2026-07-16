#!/usr/bin/env python3
"""
Atena · Growth — Bridge Page / LP inteligente (specs 16 §2 e 17).
GET /ponte?curso=X renderiza a LP (bridge-template.html + bridge-logic.js,
copy 100% ancorada na ontologia); POST /webhook/ponte cai em processar():
valida, ingere o lead (dedupe por telefone, opt-in LGPD fonte=ponte), registra
inicio_inscricao com o event_id DO NAVEGADOR (desduplicação Pixel×CAPI),
vincula a sessão de telemetria ao lead e devolve a URL do FORMULÁRIO oficial
(/curso/inscricao/). render/fragmento são reusados pelo deploy WordPress.
"""
import os
import re
import sys
import html
import json
import uuid
import base64
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from atena.growth import funil

_WEB = pathlib.Path(__file__).resolve().parents[1] / "web"
_EVENT_ID_RE = re.compile(r"^[A-Za-z0-9._-]{8,64}$")
_RAW_TOKENS = {"URL_CURSO", "CURSO", "TURMA", "CONTEUDO_HTML", "INVESTIMENTO_ITENS", "WA_LINK",
              "FOTO_PROFESSOR_HTML", "PROFESSOR_CREDENCIAL_HTML", "CURSOS_RELACIONADOS_HTML",
              "OUTROS_PROFESSORES_HTML"}
WA_NUMERO_DEFAULT = "551931672313"   # WABA da Extensão FEQ (+55 19 3167-2313)
# Ativos reais já publicados pela própria FEQ (não fabricamos foto de ninguém):
# logo oficial + hero do site institucional (feq.unicamp.br — laboratório/planta
# de processos, temático; a home de cursos usava um stock genérico) + fotos
# oficiais dos professores, extraídas do diretório de docentes (spec 17).
LOGO_URL_DEFAULT = ("https://cursos-extensao.feq.unicamp.br/wp-content/uploads/"
                    "2021/04/logo-redmencionado-300x70.png")
HERO_BG_URL_DEFAULT = "https://www.feq.unicamp.br/wp-content/uploads/2024/10/Group-103.png"
GTM_ID_DEFAULT = "GTM-WPXLCWJ"   # já instalado no <head> do site (head code do usuário)
PROFESSOR_FOTOS = {
    "Flavio Vasconcelos da Silva":
        "https://www.feq.unicamp.br/wp-content/uploads/2025/09/D-Flavio-Vasconcelos-da-Silva-1.jpg",
    "Luis Fernando Mercier Franco":
        "https://www.feq.unicamp.br/wp-content/uploads/2025/09/Luis-Fernando-Mercier-Franco.png",
    "Osvaldir Pereira Taranto":
        "https://www.feq.unicamp.br/wp-content/uploads/2025/09/Osvaldir-Pereira-Taranto33333-300x300.png",
}
# Credencial real (diretório de docentes feq.unicamp.br/funcionario/*): departamento
# oficial + CV Lattes (currículo acadêmico público-padrão no Brasil) — nada de bio
# inventada, só os dois fatos verificáveis que o próprio diretório expõe.
PROFESSOR_CREDENCIAL = {
    "Flavio Vasconcelos da Silva": {
        "departamento": "Departamento de Engenharia de Sistemas Químicos – DESQ",
        "lattes": "http://lattes.cnpq.br/1108208002886617",
    },
    "Luis Fernando Mercier Franco": {
        "departamento": "Departamento de Engenharia de Sistemas Químicos – DESQ",
        "lattes": "http://lattes.cnpq.br/1624206548318855",
    },
    "Osvaldir Pereira Taranto": {
        "departamento": "Departamento de Engenharia de Processos – DEPro",
        "lattes": "http://lattes.cnpq.br/3804707640110777",
    },
}
# Demais professores dos cursos (não-responsáveis): link real encontrado por busca
# (Lattes, LinkedIn ou Google Scholar) quando a identidade pôde ser confirmada com
# confiança; nomes sem entrada aqui aparecem SEM link (nunca adivinhamos Lattes —
# o ID é opaco — nem cravamos um perfil de nome homônimo sem contexto batendo).
PROFESSOR_LINK = {
    "Flavio Vasconcelos da Silva": {"tipo": "Lattes", "url": "http://lattes.cnpq.br/1108208002886617"},
    "Claudio Vieira Neto": {"tipo": "LinkedIn", "url": "https://www.linkedin.com/in/claudio-vieira-neto"},
    "Douglas Alves Goulart": {"tipo": "LinkedIn", "url": "https://www.linkedin.com/in/douglas-alves-goulart/"},
    "Felipe Matheus Mota Sousa": {"tipo": "Google Scholar", "url": "https://scholar.google.com/citations?user=oshtt8oAAAAJ"},
    "Raphael Santana Almeida": {"tipo": "Google Scholar", "url": "https://scholar.google.com/citations?user=rg0xVnAAAAAJ"},
    "Victor Santana Ramos": {"tipo": "Lattes", "url": "http://lattes.cnpq.br/0419561926689242"},
    "Vitor Machado Viana Cruz": {"tipo": "Lattes", "url": "http://lattes.cnpq.br/0049996359988528"},
    "Vitor Augusto Oliveira da Silva": {"tipo": "LinkedIn", "url": "https://www.linkedin.com/in/vitor-aoliveira/"},
    "Dirceu Noriler": {"tipo": "Lattes",
        "url": "https://buscatextual.cnpq.br/buscatextual/visualizacv.do?id=K4762481H3&idiomaExibicao=2"},
    "Eduardo de Britto Perez": {"tipo": "Lattes", "url": "http://lattes.cnpq.br/8235156876575101"},
    "Guilherme Jose de Castilho": {"tipo": "Lattes", "url": "http://lattes.cnpq.br/6340353027133778"},
    "João Lameu da Silva Júnior": {"tipo": "Lattes", "url": "http://lattes.cnpq.br/7563505845567082"},
    "Leonardo Vasconcelos Fregolente": {"tipo": "Lattes", "url": "http://lattes.cnpq.br/0146291251418497"},
    "Mariana Conceicao da Costa": {"tipo": "Lattes", "url": "http://lattes.cnpq.br/6460520627911836"},
    "Raphael Soeiro Suppino": {"tipo": "Lattes", "url": "http://lattes.cnpq.br/1437214547688523"},
    "Gustavo Doubek": {"tipo": "Lattes", "url": "http://lattes.cnpq.br/2409386156783785"},
    "Sallie Elizabeth Greenberg": {"tipo": "LinkedIn", "url": "https://www.linkedin.com/in/dr-sallie-greenberg-1955388/"},
}


def url_inscricao(meta_row, c):
    """Destino do modal: SEMPRE o formulário /curso/inscricao/ (spec 17 §2).
    Preferência: url_extecamp da ontologia (governada); fallback cursos_metas.
    URLs antigas /curso/dados/ são normalizadas."""
    url = (c or {}).get("url_extecamp") or meta_row["url"]
    return url.replace("/curso/dados/", "/curso/inscricao/")


def _ddmm(iso):
    return "/".join(reversed((iso or "").split("-"))) if iso else ""


def _moeda(v):
    try:
        return "R$ " + f"{float(v):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except (TypeError, ValueError):
        return ""


def _esc(v):
    return html.escape(str(v), quote=False)


def _conteudo_html(texto):
    """'(1) A; (2) B; ...' -> <li> numerados. Sem programa na ontologia -> item neutro."""
    if not texto:
        return ('<li class="bg-white/5 border border-white/10 rounded-xl p-4 text-sm '
                'text-slate-300">Programa completo na página oficial da Extecamp.</li>')
    itens = [i.strip(" ;.") for i in re.split(r"\(\d+\)", texto) if i.strip(" ;.")]
    return "\n    ".join(
        f'<li class="bg-white/5 border border-white/10 rounded-xl p-4 text-sm text-slate-300">'
        f'<span class="text-amber-400 font-semibold mr-2">{i + 1:02d}</span>{_esc(t)}</li>'
        for i, t in enumerate(itens))


def _investimento_itens(c):
    p = (c or {}).get("plano") or {}
    itens = []
    if p.get("n") and p.get("valor_parcela"):
        itens.append(f"Em até <strong>{p['n']}x de {_moeda(p['valor_parcela'])}</strong>")
    if p.get("a_vista"):
        itens.append(f"À vista: <strong>{_moeda(p['a_vista'])}</strong>")
    if p.get("taxa"):
        itens.append(f"Taxa de inscrição: {_moeda(p['taxa'])}")
    if (c or {}).get("descontos"):
        itens.append(_esc(c["descontos"]))
    if not itens:
        itens = ["Valores e condições oficiais na página da Extecamp."]
    return "\n      ".join(f'<li>{i}</li>' for i in itens)


def _foto_professor_html(nome):
    """<img> com a foto oficial do professor (diretório de docentes da FEQ) se
    reconhecido; string vazia (layout sem foto) caso contrário — nunca inventa."""
    url = PROFESSOR_FOTOS.get((nome or "").strip())
    if not url:
        return ""
    return (f'<img src="{url}" alt="Foto de {_esc(nome)}" class="foto-professor" loading="lazy">')


def _professor_credencial_html(nome):
    """Departamento + link do Lattes (currículo acadêmico oficial) quando o
    professor está no diretório da FEQ; string vazia caso contrário."""
    cred = PROFESSOR_CREDENCIAL.get((nome or "").strip())
    if not cred:
        return ""
    return (f'<p class="mt-3 text-xs text-slate-400">{_esc(cred["departamento"])} · '
            f'<a href="{cred["lattes"]}" target="_blank" rel="noopener" '
            f'class="text-amber-400 hover:text-amber-300 underline">Currículo Lattes</a></p>')


def _outros_professores_html(nomes):
    """Lista dos demais professores do curso, abaixo do card do responsável.
    Cada nome ganha link (Lattes/LinkedIn/Google Scholar) SÓ se PROFESSOR_LINK tem
    uma entrada verificada; sem entrada -> nome puro, sem link (nunca inventamos)."""
    if not nomes:
        return ""
    itens = []
    for nome in nomes:
        link = PROFESSOR_LINK.get(nome)
        if link:
            itens.append(f'<a href="{_esc(link["url"])}" target="_blank" rel="noopener" '
                          f'class="text-amber-400 hover:text-amber-300 underline">{_esc(nome)}</a>')
        else:
            itens.append(f'<span>{_esc(nome)}</span>')
    return (
        '<div class="mt-6 pt-6 border-t border-white/10">\n'
        '      <p class="text-xs uppercase tracking-widest text-slate-400 mb-3">Demais professores do curso</p>\n'
        '      <div class="flex flex-wrap gap-x-4 gap-y-2 text-sm text-slate-300">\n'
        '        ' + '\n        '.join(itens) + '\n'
        '      </div>\n'
        '    </div>'
    )


def _cursos_relacionados_html(g, ont, curso_atual):
    """Cards de cross-sell pros outros cursos-alvo (spec 17) — nunca lista o
    próprio curso da página. Só usa cursos_metas + ontologia (mesma fonte que
    todo o resto da LP), sem inventar nada."""
    itens = []
    for m in g.cursos():
        if m["curso"] == curso_atual:
            continue
        try:
            c = ont.curso(m["curso"]) or {}
        except Exception:
            c = {}
        nome = c.get("nome") or m["curso"]
        tipo = c.get("tipo") or "Curso"
        # mesmo padrão de slug do wp_deploy_bridge.py — link relativo funciona
        # tanto publicado (mesma origem no WP) quanto localmente via /ponte.
        slug = f"inscricao-{m['curso'].lower()}-{m['turma']}"
        itens.append(
            '<a href="/%s/" class="bg-white/5 border border-white/10 rounded-2xl p-6 '
            'hover:border-amber-400/40 transition-colors block">'
            '<p class="text-xs uppercase tracking-widest text-amber-400 mb-2">%s</p>'
            '<p class="font-semibold text-slate-100">%s</p>'
            '<p class="mt-2 text-sm text-amber-400">Conhecer curso →</p></a>'
            % (slug, _esc(tipo), _esc(nome)))
    return "\n    ".join(itens)


def render_tokens(meta_row, c):
    """Tokens da LP a partir do cursos_metas + ontologia (c = ont.curso(...) ou {}).
    Regra anti-alucinação: todo fato vem da ontologia; sem âncora -> texto neutro
    apontando para a página oficial da Extecamp."""
    c = c or {}
    horario = c.get("horario") or ""
    plano = c.get("plano") or {}
    n, vp, taxa = plano.get("n"), plano.get("valor_parcela"), plano.get("taxa")
    if n:
        plano_curto = f"{n}x" + (f" de {_moeda(vp)}" if vp else "")
        plano_det = f"Pagamento em até {n} parcelas" + (f" de {_moeda(vp)}" if vp else "")
        if taxa:
            plano_det += f" + taxa de inscrição de {_moeda(taxa)}"
        plano_det += "."
    else:
        plano_curto, plano_det = "Unicamp", "Condições de pagamento na página oficial da Extecamp."
    if c.get("descontos"):
        plano_det += " " + c["descontos"]
    formato_resumo = c.get("formato_aulas_resumo") or (
        "Sábados" if "sábado" in horario.lower() else (horario.split()[0].capitalize() if horario else "Flexível"))
    formato_detalhe = c.get("formato_aulas_detalhe") or (
        f"{horario} — pensado para quem trabalha." if horario else "Consulte a Extecamp.")
    requisito = ("É necessário ensino superior completo (graduação concluída em qualquer área)."
                 if c.get("requisito") else "Consulte os requisitos na página oficial da Extecamp.")
    if c.get("publico_alvo"):
        requisito += " Público-alvo: " + c["publico_alvo"]
    wa = re.sub(r"\D", "", os.environ.get("GROWTH_WA_NUMERO", WA_NUMERO_DEFAULT))
    oi = f"Olá! Vim da página do curso {meta_row['curso']} e quero saber mais."
    return {
        "CURSO": meta_row["curso"],
        "TURMA": meta_row["turma"],
        "URL_CURSO": url_inscricao(meta_row, c),
        "WA_LINK": f"https://wa.me/{wa}?text={oi.replace(' ', '%20')}",
        "NOME_CURSO": c.get("nome") or meta_row["curso"],
        "TIPO_CURSO": c.get("tipo") or "Curso",
        "MODALIDADE": c.get("modalidade") or "Detalhes na Extecamp",
        "OBJETIVO": c.get("objetivo") or c.get("insight_venda")
                    or "Curso oficial da Extensão FEQ Unicamp — programa completo na página da Extecamp.",
        "PRAZO": _ddmm(c.get("inscricoes_fim")) or "a data publicada na Extecamp",
        "CARGA": str(c.get("carga_horas") or "?"),
        "HORARIO": horario or "consulte a Extecamp",
        "FORMATO_AULAS_RESUMO": formato_resumo,
        "FORMATO_AULAS_DETALHE": formato_detalhe,
        "PLANO": plano_curto,
        "PLANO_DETALHE": plano_det,
        "PROFESSOR": c.get("professor") or "Corpo docente FEQ/Unicamp",
        "REQUISITO": requisito,
        "CONTEUDO_HTML": _conteudo_html(c.get("conteudo_programatico")),
        "INVESTIMENTO_ITENS": _investimento_itens(c),
        "FOTO_PROFESSOR_HTML": _foto_professor_html(c.get("professor")),
        "PROFESSOR_CREDENCIAL_HTML": _professor_credencial_html(c.get("professor")),
        "OUTROS_PROFESSORES_HTML": _outros_professores_html(c.get("professores_adicionais")),
    }


def _bridge_bootstrap(tokens, webhook_url, pixel_id):
    """window.BRIDGE + bridge-logic.js, em UM bloco base64 (spec 17 / bug WP
    convert_chars): o WordPress normaliza "&" bruto -> "&#038;" em todo o
    post_content, mesmo dentro de <script>, quebrando o "&&" do JS. Base64
    usa só [A-Za-z0-9+/=] — imune a essa e a qualquer outra normalização de
    texto (aspas retas->curvas, travessões, reticências)."""
    config_js = "window.BRIDGE=" + json.dumps({
        "curso": tokens["CURSO"], "destino": tokens["URL_CURSO"],
        "webhook": webhook_url, "pixel": pixel_id}) + ";"
    completo = config_js + (_WEB / "bridge-logic.js").read_text()
    return base64.b64encode(completo.encode()).decode()


def render(g, ont, curso, webhook_url="/webhook/ponte"):
    """HTML final da LP (template + JS + tokens). Curso desconhecido -> None."""
    alvo = next((m for m in g.cursos() if m["curso"] == curso), None)
    if not alvo:
        return None
    try:
        c = ont.curso(curso)
    except Exception:
        c = None
    tokens = render_tokens(alvo, c)
    pagina = (_WEB / "bridge-template.html").read_text()
    for k, v in tokens.items():
        pagina = pagina.replace("{{%s}}" % k, str(v) if k in _RAW_TOKENS else _esc(v))
    pixel_id = os.environ.get("META_PIXEL_ID", "")
    hero_bg = os.environ.get("GROWTH_HERO_BG_URL") or HERO_BG_URL_DEFAULT
    logo = os.environ.get("GROWTH_LOGO_URL") or LOGO_URL_DEFAULT
    css = (_WEB / "bridge.css").read_text().replace("{{HERO_BG_URL}}", hero_bg)
    return (pagina
            .replace("{{CURSOS_RELACIONADOS_HTML}}", _cursos_relacionados_html(g, ont, curso))
            .replace("{{WEBHOOK_URL}}", webhook_url)
            .replace("{{LOGO_URL}}", logo)
            .replace("{{BRIDGE_CSS}}", css)
            .replace("{{BRIDGE_B64}}", _bridge_bootstrap(tokens, webhook_url, pixel_id)))


def _pagina_obrigado_bootstrap():
    """Lê ?to= e segue pro destino real após o pageview disparar o GTM (mesma
    técnica base64 de _bridge_bootstrap — imune ao convert_chars do WP)."""
    js = ('var p=new URLSearchParams(window.location.search);'
          'var to=p.get("to");'
          'if(to){setTimeout(function(){window.location.href=to;},600);}')
    return base64.b64encode(js.encode()).decode()


def pagina_obrigado_html():
    """/lp-thank-you-page/ (spec 16/17 + conversão de Lead do Google Ads/GTM):
    interstício entre o modal e o formulário oficial da Extecamp — existe só
    pra dar ao GTM (GTM-WPXLCWJ, já instalado no <head> do site) uma URL com
    'thank-you-page' pra observar antes de seguir pro destino real (?to=).
    Mesmos seletores de bridge.css pra tirar cabeçalho/faixa de título do tema
    (página órfã, sem saída pro resto do site — igual às 4 LPs)."""
    gtm_id = os.environ.get("GROWTH_GTM_ID") or GTM_ID_DEFAULT
    logo = os.environ.get("GROWTH_LOGO_URL") or LOGO_URL_DEFAULT
    return (
        '<style>\n'
        '.page-header, .pre-content { display: none !important; }\n'
        '#site-header, #header-container, .mob-menu-header-holder,\n'
        '.mobmenu-panel { display: none !important; }\n'
        '#main.clearfix, #container.row-inner, #content,\n'
        'article.page.type-page, .entry-content.clearfix {\n'
        '  max-width: none !important; width: 100% !important; margin: 0 !important;\n'
        '  padding: 0 !important; border: none !important; box-shadow: none !important;\n'
        '  background: transparent !important;\n'
        '}\n'
        '.lp-obrigado {\n'
        '  min-height: 100vh; display: flex; align-items: center; justify-content: center;\n'
        '  flex-direction: column; text-align: center; background: #020617; color: #f1f5f9;\n'
        '  padding: 3rem 1.5rem;\n'
        '}\n'
        '.lp-obrigado img { height: 36px; margin-bottom: 2.5rem; }\n'
        '.lp-obrigado .spinner {\n'
        '  width: 28px; height: 28px; border-radius: 999px; margin-bottom: 1.5rem;\n'
        '  border: 3px solid rgba(241,245,249,.15); border-top-color: #fbbf24;\n'
        '  animation: lp-obrigado-girar 0.8s linear infinite;\n'
        '}\n'
        '@keyframes lp-obrigado-girar { to { transform: rotate(360deg); } }\n'
        '.lp-obrigado p:first-of-type { font-size: 1.5rem; font-weight: 700; margin-bottom: .75rem; }\n'
        '.lp-obrigado p:last-of-type { color: #94a3b8; max-width: 28rem; }\n'
        '</style>\n'
        f'<div class="lp-obrigado">\n'
        f'  <img src="{logo}" alt="FEQ Unicamp">\n'
        '  <div class="spinner"></div>\n'
        '  <p>Recebemos seus dados!</p>\n'
        '  <p>Redirecionando você para o formulário oficial da Unicamp...</p>\n'
        '</div>\n'
        f'<noscript><iframe src="https://www.googletagmanager.com/ns.html?id={gtm_id}" '
        'height="0" width="0" style="display:none;visibility:hidden"></iframe></noscript>\n'
        '<script>(function(){var s=document.createElement("script");'
        f's.textContent=atob("{_pagina_obrigado_bootstrap()}");'
        'document.body.appendChild(s);})();</script>'
    )


def fragmento(html_completo):
    """Miolo da LP p/ publicar no WordPress (entre os marcadores LP-FEQ), sem
    doctype/head — o tema do WP embrulha; o bloco wp:html preserva o JS (spec 17 §4)."""
    m = re.search(r"<!-- LP-FEQ-INICIO.*?-->(.*)<!-- LP-FEQ-FIM -->", html_completo, re.S)
    return m.group(1).strip() if m else html_completo


def processar(dados, est, g, ads, ing, ont=None):
    """Webhook da ponte (público — valida tudo). dados = JSON do navegador
    ({nome, whatsapp|telefone, curso_id|curso, event_id, sessao_id, fbp, fbc, url})
    + ip/ua injetados pela rota. Retorna {ok, card_id, event_id, redirect}."""
    tel = re.sub(r"\D", "", dados.get("telefone") or dados.get("whatsapp") or "")
    curso = (dados.get("curso") or dados.get("curso_id") or "").strip().upper()
    alvo = next((m for m in g.cursos() if m["curso"] == curso), None)
    if not 10 <= len(tel) <= 13:
        return {"ok": False, "motivo": "telefone inválido"}
    if not alvo:
        return {"ok": False, "motivo": f"curso desconhecido: {curso or '?'}"}
    eid = dados.get("event_id") or ""
    if not _EVENT_ID_RE.match(eid):
        eid = str(uuid.uuid4())          # id do navegador ausente/sujo: gera (sem dedup, mas rastreia)
    nome = (dados.get("nome") or "").strip()[:120]
    r = ing.ingest({"nome": nome, "telefone": tel, "curso": curso,
                    "consentimento": "true", "fonte": "ponte"})
    if not r.get("ok"):
        return {"ok": False, "motivo": r.get("motivo", "ingestão falhou")}
    contexto = {k: dados.get(k) for k in ("fbp", "fbc", "ip", "ua", "url") if dados.get(k)}
    funil.registrar(g, ads, est, r["card_id"], "inicio_inscricao",
                    curso=curso, event_id=eid, contexto=contexto)
    if dados.get("sessao_id"):           # liga a telemetria da sessão ao lead (spec 17 §5)
        g.vincular_sessao(str(dados["sessao_id"])[:64], r["card_id"])
    try:
        c = ont.curso(curso) if ont else None
    except Exception:
        c = None
    return {"ok": True, "card_id": r["card_id"], "event_id": eid,
            "redirect": url_inscricao(alvo, c)}
