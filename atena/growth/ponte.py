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
              "OUTROS_PROFESSORES_HTML", "SECAO_OBJETIVOS", "SECAO_HABILIDADES",
              "SECAO_DEPOIMENTOS", "EMPRESAS_HTML", "PROFESSORES_BIO_HTML", "GARANTIA_HTML",
              "SALARIO_HTML", "PASSOS_HTML", "FAQ_EXTRA_HTML", "NAV_DEPOIMENTOS",
              "PRAZO_LINHA", "SECAO_INSCRICAO", "NAV_INSCRICAO", "FAQ_COMO_INSCREVER",
              "PERIODO_OFERECIMENTO"}
WA_NUMERO_DEFAULT = "551931672313"   # WABA da Extensão FEQ (+55 19 3167-2313)
# Texto do requisito POR NÍVEL da ontologia (feq:nivelMinimo). Sem isto a LP dizia
# "ensino superior completo" para todo curso com requisito — falso nos cursos que
# pedem só ensino médio (FEQ-0299, FEQ-0670).
_REQUISITO_TEXTO = {
    "medio": "É necessário ensino médio completo.",
    "superiorIncompleto": "É necessário ensino superior em andamento (ou concluído).",
    "superiorCompleto": "É necessário ensino superior completo (graduação concluída em qualquer área).",
    "pos": "É necessária pós-graduação concluída.",
}
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
    # Fotos publicadas nas PÁGINAS OFICIAIS preexistentes dos cursos (mesma regra:
    # material já publicado pela FEQ; nunca fabricamos foto de pessoa real):
    "Vitor Machado Viana Cruz":
        "https://cursos-extensao.feq.unicamp.br/wp-content/uploads/2024/07/vitor_machado-e1721925782819.jpg",
    "Guilherme Jose de Castilho":
        "https://cursos-extensao.feq.unicamp.br/wp-content/uploads/2024/07/guilherme-300x300-1.png",
    "Edneu Jatkoski (Petrobras)":
        "https://cursos-extensao.feq.unicamp.br/wp-content/uploads/2024/07/edneu.png",
    "Claudio Vieira Neto":
        "https://cursos-extensao.feq.unicamp.br/wp-content/uploads/2025/04/1681221613629.jpg",
    "Gustavo Doubek":
        "https://cursos-extensao.feq.unicamp.br/wp-content/uploads/2026/02/Gustavo-Doubek.jpg",
    "Douglas Alves Goulart":
        "https://cursos-extensao.feq.unicamp.br/wp-content/uploads/2026/07/douglas-alves-goulart.jpg",
    # Docentes dos cursos incluídos em 21/07/2026 (fotos das páginas oficiais dos
    # cursos; Ronierik veio do diretório institucional feq.unicamp.br/funcionario/).
    "Leonardo Vasconcelos Fregolente":
        "https://cursos-extensao.feq.unicamp.br/wp-content/uploads/2024/01/leonardo.png",
    "Fabio Marcelo Brogna":
        "https://cursos-extensao.feq.unicamp.br/wp-content/uploads/2024/01/fabio-marcelo.jpeg",
    "Angela Hayashi":
        "https://cursos-extensao.feq.unicamp.br/wp-content/uploads/2024/02/image_2024-02-02_172849557-e1706905838346.png",
    "Savio Souza Venancio Vianna":
        "https://cursos-extensao.feq.unicamp.br/wp-content/uploads/2024/08/savio_vianna.jpg",
    "Eduardo Perez":
        "https://cursos-extensao.feq.unicamp.br/wp-content/uploads/2024/11/1c3aae43-bc40-46c1-aa8b-22778addf511.jpg",
    "Ronierik Pioli Vieira":
        "https://www.feq.unicamp.br/wp-content/uploads/2025/09/Ronierik-Pioli-Vieira.png",
}
# Docentes em DESTAQUE por curso (cards com foto), além do responsável — que já tem
# card próprio. FEQ-0480: escolha do usuário (20/07/2026); demais: os nomes que as
# LPs preexistentes destacavam. Douglas não tem foto/bio publicada -> card só com
# nome+link (avatar de iniciais; nunca inventamos foto).
PROFESSOR_DESTAQUE = {
    "FEQ-0480": ["Vitor Machado Viana Cruz", "Douglas Alves Goulart"],
    "FEQ-1000": ["Guilherme Jose de Castilho", "Edneu Jatkoski (Petrobras)",
                 "Flavio Vasconcelos da Silva"],
    "FEQ-0580": ["Claudio Vieira Neto"],
    "FEQ-0333": ["Gustavo Doubek"],
    "FEQ-0360": ["Fabio Marcelo Brogna", "Angela Hayashi"],
    "FEQ-0499": ["Eduardo Perez"],
    "FEQ-0670": ["André de Freitas Gonçalves", "Luiz Guilherme Lomônaco"],
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
    "Guilherme Jose de Castilho": {"tipo": "LinkedIn",
        "url": "https://www.linkedin.com/in/guilherme-castilho-3820b81a3/"},
    "Edneu Jatkoski (Petrobras)": {"tipo": "LinkedIn",
        "url": "https://www.linkedin.com/in/edneu-jatkoski-34a3a630/"},
    "João Lameu da Silva Júnior": {"tipo": "Lattes", "url": "http://lattes.cnpq.br/7563505845567082"},
    "Leonardo Vasconcelos Fregolente": {"tipo": "Lattes", "url": "http://lattes.cnpq.br/0146291251418497"},
    "Mariana Conceicao da Costa": {"tipo": "Lattes", "url": "http://lattes.cnpq.br/6460520627911836"},
    "Raphael Soeiro Suppino": {"tipo": "Lattes", "url": "http://lattes.cnpq.br/1437214547688523"},
    # Lattes do Doubek: a página oficial do FEQ-0333 publica 6149769815435543
    # (diverge do 2409386156783785 achado no site do PRH/FEQ) — vale a fonte do curso.
    "Gustavo Doubek": {"tipo": "Lattes", "url": "http://lattes.cnpq.br/6149769815435543"},
    "Sallie Elizabeth Greenberg": {"tipo": "LinkedIn", "url": "https://www.linkedin.com/in/dr-sallie-greenberg-1955388/"},
}
# Bios TRANSCRITAS das páginas oficiais preexistentes da Extensão FEQ (cursos-extensao.
# feq.unicamp.br) — nunca redigidas por nós. Nome sem entrada aqui = sem bio no card.
PROFESSOR_BIO = {
    "Flavio Vasconcelos da Silva": {
        "cargo": "Professor Associado FEQ Unicamp",
        "bio": "Livre-Docente em Controle de Processos pela Unicamp e Professor Associado da "
               "Faculdade de Engenharia Química Unicamp. Atua em Controle de Processos, IA "
               "(Controle Fuzzy e Redes Neurais), Instrumentação Industrial e Refrigeração Industrial."},
    "Vitor Machado Viana Cruz": {
        "cargo": "Doutor em Engenharia Química",
        "bio": "Doutor e Pós-Doutorando pela FEQ Unicamp. Experiência em processos de recuperação "
               "química e especialista em desenvolvimento de softwares para simulação de plantas "
               "químicas com interfaces interativas."},
    "Guilherme Jose de Castilho": {
        "cargo": "Professor Associado FEQ Unicamp",
        "bio": "Pós-doutor em Engenharia Química pela Unicamp e UBC-Canadá, Coordenador da Escola "
               "de Extensão e Professor na FEQ. Especialista em Sistemas Particulados e "
               "Fluidodinâmica, com foco em fluidização, leito fluidizado circulante e "
               "processamento de sinais."},
    "Edneu Jatkoski (Petrobras)": {
        "cargo": "Coordenador de Inspeção — Petrobras (REPLAN)",
        "bio": "Coordenador de Inspeção na Refinaria de Paulínia, com 40 anos de experiência. "
               "Graduado em Engenharia Civil e MBA em Gestão Industrial. Instrutor em cursos de "
               "formação da Petrobras e representante da empresa no CEPETRO (Unicamp)."},
    "Claudio Vieira Neto": {
        "cargo": "Especialista em Data Science",
        "bio": "Engenheiro Químico e Black Belt com experiência em projetos de IA, NLP, otimização "
               "e simulação. Atuou em iniciativas com impacto financeiro e operacional relevante e "
               "liderou times ágeis em grandes empresas."},
    "Leonardo Vasconcelos Fregolente": {
        "cargo": "Professor Associado FEQ Unicamp",
        "bio": "Professor Associado da FEQ Unicamp. Atuou 11 anos como engenheiro de "
               "processamento na Petrobras, em áreas técnicas e de gestão operacional, e "
               "coordena pesquisas em parceria com a indústria."},
    "Fabio Marcelo Brogna": {
        "cargo": "Engenheiro de Processamento Sênior — Petrobras",
        "bio": "Especialista em confiabilidade, análise de causa básica, riscos e segurança de "
               "processos, com experiência em otimização de processos industriais."},
    "Angela Hayashi": {
        "cargo": "Engenheira e Doutora em Processos Ambientais",
        "bio": "23 anos de experiência em gestão de riscos operacionais e segurança de processos. "
               "Auditora Líder (ABNT NBR ISO)."},
    "Eduardo Perez": {
        "cargo": "Engenheiro Sênior de Tecnologia de Manufatura Avançada — 3M",
        "bio": "29 anos de experiência em processos e métodos de revestimento, com atuação em "
               "eficiência energética de processos de manufatura."},
    "Ronierik Pioli Vieira": {
        "cargo": "Professor Associado e Coordenador de Graduação FEQ Unicamp",
        "bio": "Graduação, mestrado e doutorado em Engenharia Química, com pesquisas em "
               "polimerização. Pós-doutorado em funcionalização de biopolímeros no Centro de "
               "Química Macromolecular da Ghent University (Bélgica)."},
    "André de Freitas Gonçalves": {
        "cargo": "Professor Pesquisador Colaborador",
        "bio": "Engenheiro Químico, Mestre pela UFMG e Doutor em Engenharia Química pela Unicamp. "
               "Pesquisador em termodinâmica de fluidos confinados e simulação molecular."},
    "Luiz Guilherme Lomônaco": {
        "cargo": "Professor Pesquisador Colaborador",
        "bio": "Engenheiro Químico, Doutor em Engenharia Química pela Unicamp, especialista em "
               "simulação de dinâmica molecular aplicada a sistemas biológicos."},
    "Savio Souza Venancio Vianna": {
        "cargo": "Professor Associado FEQ Unicamp",
        "bio": "Engenheiro Químico pela Escola de Química da UFRJ e Mestre pela COPPE/UFRJ, com "
               "PhD em Explosões pela Universidade de Cambridge. Pesquisa modelagem computacional "
               "de escoamentos reativos e não reativos."},
    "Douglas Alves Goulart": {
        "cargo": "Cientista de Dados",
        "bio": "Cientista de dados com experiência em mercado financeiro, marketing, logística e "
               "Indústria 4.0, focado em soluções orientadas a dados para decisões data-driven. "
               "Domina Machine Learning, Computer Vision, Reinforcement Learning e Cloud Computing "
               "(AWS e GCP), com Python (TensorFlow, PyTorch, scikit-learn, XGBoost, Pandas) "
               "aplicado a controle e automação de processos."},
    "Luis Fernando Mercier Franco": {
        "cargo": "Professor Associado FEQ Unicamp",
        "bio": "Engenheiro Químico, Mestre e Doutor em Ciências pela Poli-USP; Livre-Docente pela "
               "FEQ Unicamp. Atua em termodinâmica, mecânica estatística e simulação molecular."},
    "Gustavo Doubek": {
        "cargo": "Professor Associado FEQ Unicamp",
        "bio": "Doutor em Engenharia de Materiais e Nuclear pela USP, com período de pesquisa em "
               "Yale. Mais de 10 anos em pesquisa e ensino em células a combustível e baterias, "
               "com foco em energia limpa e desenvolvimento de produtos."},
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
        '      <p class="text-xs uppercase tracking-widest text-slate-400 mb-3">Mais professores do curso</p>\n'
        '      <div class="flex flex-wrap gap-x-4 gap-y-2 text-sm text-slate-300">\n'
        '        ' + '\n        '.join(itens) + '\n'
        '      </div>\n'
        '    </div>'
    )


def _secao_objetivos_html(objetivos):
    """Seção 'Objetivos do curso' (2 cards) — texto literal das páginas oficiais."""
    if not objetivos:
        return ""
    cards = "\n    ".join(
        '<div class="bg-white/5 border border-white/10 rounded-2xl p-6">'
        f'<p class="font-semibold text-amber-400">{_esc(o["t"])}</p>'
        f'<p class="mt-2 text-sm text-slate-300">{_esc(o["d"])}</p></div>'
        for o in objetivos)
    return ('<section data-secao="objetivos" class="secao-divisor max-w-4xl mx-auto px-6 py-16">\n'
            '  <h2 class="text-2xl font-bold mb-6 text-center">Objetivos do curso</h2>\n'
            f'  <div class="grid sm:grid-cols-2 gap-4">\n    {cards}\n  </div>\n</section>')


def _secao_habilidades_html(habilidades):
    """'O que você vai dominar' — chips com as habilidades publicadas."""
    if not habilidades:
        return ""
    chips = "\n    ".join(
        f'<span class="hab-chip">{_esc(h)}</span>' for h in habilidades)
    return ('<section data-secao="habilidades" class="secao-divisor max-w-4xl mx-auto px-6 py-16">\n'
            '  <h2 class="text-2xl font-bold mb-6 text-center">O que você vai dominar</h2>\n'
            f'  <div class="flex flex-wrap gap-2 justify-center">\n    {chips}\n  </div>\n</section>')


def _programa_html(c):
    """Programa: accordion módulo→subtópicos quando temos o detalhe oficial;
    senão, a lista numerada simples de sempre (nunca inventa subtópico)."""
    det = c.get("programa_detalhe") or []
    if not det:
        return _conteudo_html(c.get("conteudo_programatico"))
    itens = []
    for i, m in enumerate(det):
        subs = "".join(f'<li>{_esc(s)}</li>' for s in m.get("s") or [])
        corpo = f'<ul class="prog-subs">{subs}</ul>' if subs else ""
        itens.append(
            '<details class="prog-mod">'
            f'<summary><span class="text-amber-400 font-semibold mr-2">{i + 1:02d}</span>'
            f'{_esc(m["t"])}</summary>{corpo}</details>')
    return "\n    ".join(itens)


def _secao_depoimentos_html(depoimentos):
    """Depoimentos REAIS transcritos das páginas oficiais preexistentes."""
    if not depoimentos:
        return ""
    def _avatar(d):
        """Foto real publicada nas páginas oficiais; sem foto, iniciais (nunca inventar rosto)."""
        if d.get("foto"):
            return (f'<img class="depo-foto" src="{_esc(d["foto"])}" alt="Foto de {_esc(d["nome"])}" '
                    f'loading="lazy" width="48" height="48">')
        return f'<span class="depo-avatar" aria-hidden="true">{_esc(_iniciais(d["nome"]))}</span>'

    cards = "\n    ".join(
        '<figure class="depoimento">'
        '<span class="depo-aspa" aria-hidden="true">“</span>'
        f'<p class="depo-texto">{_esc(d["texto"])}</p>'
        '<figcaption>'
        f'{_avatar(d)}'
        f'<span class="depo-quem"><strong>{_esc(d["nome"])}</strong>'
        f'<span class="muted-cap">{_esc(d.get("cargo") or "")}</span></span>'
        '</figcaption></figure>'
        for d in depoimentos)
    return ('<section id="depoimentos" data-secao="depoimentos" '
            'class="secao-divisor max-w-4xl mx-auto px-6 py-16">\n'
            '  <h2 class="text-2xl font-bold mb-2 text-center">Quem fez, recomenda</h2>\n'
            '  <p class="text-sm text-slate-400 mb-6 text-center">Depoimentos publicados por '
            'ex-alunos da Extensão FEQ Unicamp.</p>\n'
            f'  <div class="grid sm:grid-cols-2 gap-4 items-start">\n    {cards}\n  </div>\n</section>')


def _secao_empresas_html(empresas):
    """Prova social oficial: 'nossos alunos atuam em' (lista publicada pela FEQ)."""
    if not empresas:
        return ""
    tags = "\n    ".join(f'<span class="empresa-tag">{_esc(e)}</span>' for e in empresas)
    return ('<div class="mt-8 text-center">\n'
            '  <p class="text-xs uppercase tracking-widest text-slate-400 mb-3">'
            'Nossos alunos atuam em empresas como</p>\n'
            f'  <div class="flex flex-wrap gap-2 justify-center">\n    {tags}\n  </div>\n</div>')


def _iniciais(nome):
    partes = [p for p in (nome or "").split() if p and p[0].isupper()]
    return "".join(p[0] for p in partes[:2]) or "?"


def _professores_bio_html(c):
    """Cards de DESTAQUE do corpo docente (PROFESSOR_DESTAQUE por curso), com foto
    publicada em página oficial — o responsável já tem card próprio, com a bio via
    {{PROFESSOR_DESC}}. Sem foto publicada -> avatar de iniciais (nunca inventa);
    bio/cargo/link só quando existem nas fontes oficiais."""
    cards = []
    for nome in PROFESSOR_DESTAQUE.get(c.get("codigo") or "", []):
        if nome == c.get("professor"):
            continue   # responsável já tem o card grande acima
        b = PROFESSOR_BIO.get(nome) or {}
        foto = _foto_professor_html(nome) or f'<span class="avatar-iniciais">{_esc(_iniciais(nome))}</span>'
        cred = _professor_credencial_html(nome)
        link = PROFESSOR_LINK.get(nome)
        rotulo = ""
        if not cred and link:
            rotulo = (f'<p class="mt-3 text-xs text-slate-400"><a href="{_esc(link["url"])}" '
                      'target="_blank" rel="noopener" class="text-amber-400 hover:text-amber-300 '
                      f'underline">{_esc(link["tipo"])}</a></p>')
        bio = f'<p class="mt-3 text-sm text-slate-300">{_esc(b["bio"])}</p>' if b.get("bio") else ""
        cargo = (f'<p class="text-xs text-amber-400">{_esc(b["cargo"])}</p>' if b.get("cargo")
                 else '<p class="text-xs text-slate-400">Professor do curso</p>')
        cards.append(
            '<div class="bg-white/5 border border-white/10 rounded-2xl p-6">'
            f'<div class="flex items-center gap-4">{foto}<div>'
            f'<p class="font-semibold text-slate-100">{_esc(nome)}</p>'
            f'{cargo}</div></div>{bio}{cred or rotulo}</div>')
    if not cards:
        return ""
    return '<div class="grid sm:grid-cols-2 gap-4 mt-6 items-start">\n    ' + "\n    ".join(cards) + "\n  </div>"


def _garantia_html(c):
    if not c.get("garantia_7d"):
        return ""
    return ('<p class="mt-4 garantia-selo">🛡️ Garantia de 7 dias — reembolso de 100% da '
            'matrícula se você não gostar do curso na primeira semana.</p>')


def _salario_html(c):
    s = c.get("salario_ref")
    if not s:
        return ""
    return ('<p class="mt-3 text-xs text-slate-400">Perspectiva profissional: salário de '
            f'referência {_esc(s)}.</p>')


def _periodo_html(c, aberto=True):
    """Faixa com o PERÍODO DE OFERECIMENTO (início e fim das aulas) — dado oficial
    da Extecamp, guardado na ontologia (janelaOferecimento). Some se o curso não
    tiver as datas. Curso sem turma aberta: rotula como 'última turma' para não dar
    a entender que essas datas valem para quem entra na lista de espera agora."""
    ini, fim = _ddmm(c.get("aulas_inicio")), _ddmm(c.get("aulas_fim"))
    if not (ini and fim):
        return ""
    rotulo = "Período do curso" if aberto else "Período da última turma"
    return (
        '<section data-secao="periodo" class="secao-divisor max-w-4xl mx-auto px-6 py-10">\n'
        '  <div class="periodo-faixa">\n'
        '    <span class="periodo-icone" aria-hidden="true">📅</span>\n'
        '    <div>\n'
        f'      <p class="periodo-rotulo">{rotulo}</p>\n'
        f'      <p class="periodo-datas">As aulas acontecem de <strong>{ini}</strong> '
        f'a <strong>{fim}</strong>.</p>\n'
        '    </div>\n'
        '  </div>\n</section>')


def _passos_html(c):
    """Como se inscrever — 5 passos publicados nas páginas oficiais (Extecamp)."""
    taxa = _moeda((c.get("plano") or {}).get("taxa")) or "o valor publicado na Extecamp"
    passos = [
        "Preencha o formulário de inscrição no sistema oficial da Unicamp (Extecamp) e aguarde "
        "o e-mail com as instruções de acesso ao portal do aluno e o termo de compromisso.",
        "Baixe e preencha o termo de compromisso e envie os documentos na aba "
        "“Documentos” do portal do aluno.",
        f"Pague o boleto da taxa de inscrição ({taxa}).",
        "Aguarde a validação da documentação e a confirmação de abertura da turma "
        "(ao encerramento das inscrições).",
        "Com a turma confirmada, a secretaria entra em contato por e-mail para o pagamento "
        "e a efetivação da matrícula. Pronto — você é aluno da Extensão Unicamp!"]
    itens = "\n    ".join(
        f'<li class="passo"><span class="passo-n">{i + 1}</span><span>{p}</span></li>'
        for i, p in enumerate(passos))
    return itens


def _secao_inscricao_html(c, aberto):
    """'Como se inscrever' (5 passos oficiais da Extecamp) — só com turma aberta.
    Sem turma, prometer um passo a passo de inscrição seria falso; a captação
    acontece pelo modal de lista de espera."""
    if not aberto:
        return ""
    return (
        '<section id="inscricao" data-secao="inscricao" class="secao-divisor max-w-4xl mx-auto px-6 py-16">\n'
        '  <h2 class="text-2xl font-bold mb-2">Como se inscrever</h2>\n'
        '  <p class="text-sm text-slate-400 mb-6">O processo acontece no sistema oficial da '
        'Unicamp (Extecamp) — leva poucos minutos.</p>\n'
        f'  <ol class="space-y-3 list-none">\n    {_passos_html(c)}\n  </ol>\n'
        '  <div class="text-center">\n'
        '    <button data-cta class="mt-8 bg-amber-400 hover:bg-amber-300 text-slate-900 '
        'font-bold px-8 py-4 rounded-xl">Começar minha inscrição →</button>\n'
        '  </div>\n</section>')


def _faq_extra_html(c):
    """FAQ extra ancorado: certificação/MEC (texto oficial), avaliação e plataforma."""
    itens = []
    aval = c.get("avaliacao")
    if aval:
        plat = c.get("plataforma")
        extra = (f' As aulas ficam disponíveis na plataforma {_esc(plat)}.' if plat else "")
        itens.append(("Como funcionam as avaliações e entregas?", _esc(aval) + extra))
    itens.append((
        "Esse curso é lato-sensu? É reconhecido pelo MEC?",
        "A certificação é reconhecida pelo MEC e aclamada pelo mercado, mas o curso não se "
        "enquadra como pós-graduação lato-sensu: é um curso de "
        + _esc((c.get("tipo") or "formação").lower())
        + " e a Unicamp certifica diretamente todos os formados no tema abordado. Na prática, "
        "a ementa é constantemente atualizada com as tendências do mercado e o corpo docente "
        "inclui profissionais com ampla experiência na indústria."))
    return "\n    ".join(
        '<details class="bg-white/5 border border-white/10 rounded-xl p-5">'
        f'<summary class="cursor-pointer font-semibold">{p}</summary>'
        f'<p class="mt-2 text-sm text-slate-300">{r}</p></details>'
        for p, r in itens)


def _cursos_relacionados_html(g, ont, curso_atual, limite=3, hoje=None):
    """Cards de cross-sell (spec 17) — nunca lista o próprio curso da página.
    Com o portfólio inteiro no ar (9 LPs), mostrar todos viraria um paredão: fica
    no `limite`, priorizando quem está com INSCRIÇÃO ABERTA (mandar o lead pra um
    curso fechado é pior que não sugerir nada). Só usa cursos_metas + ontologia."""
    outros = [m for m in g.cursos() if m["curso"] != curso_atual]

    def _aberto(cod):
        try:
            return bool(ont.captavel(cod, hoje).get("captavel"))
        except Exception:
            return True
    # sort ESTÁVEL só por aberto/fechado: dentro de cada grupo preserva a ordem do
    # cursos_metas, que começa pelas turmas-alvo da campanha (prioridade comercial).
    outros.sort(key=lambda m: not _aberto(m["curso"]))
    itens = []
    for m in outros[:limite]:
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


def render_tokens(meta_row, c, aberto=True):
    """Tokens da LP a partir do cursos_metas + ontologia (c = ont.curso(...) ou {}).
    Regra anti-alucinação: todo fato vem da ontologia; sem âncora -> texto neutro
    apontando para a página oficial da Extecamp.
    aberto=False: a página vira LISTA DE ESPERA — mesmos fatos do curso, mas os
    CTAs/modal captam contato ("avise-me quando reabrir") em vez de prometer uma
    inscrição que não existe hoje."""
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
    # O nível vem da ontologia — NUNCA assumir "superior completo": há cursos que
    # pedem só ensino médio (FEQ-0299, FEQ-0670) e afirmar o contrário barraria
    # lead elegível e seria informação falsa na página.
    requisito = _REQUISITO_TEXTO.get(c.get("requisito") or "",
                                     "Consulte os requisitos na página oficial da Extecamp.")
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
        "PROFESSOR_DESC": (PROFESSOR_BIO.get((c.get("professor") or "").strip()) or {}).get("bio")
                          or "Docente da Faculdade de Engenharia Química da Unicamp, responsável "
                             "pelo curso junto à Extecamp (Escola de Extensão da Unicamp).",
        "REQUISITO": requisito,
        "CONTEUDO_HTML": _programa_html(c),
        "INVESTIMENTO_ITENS": _investimento_itens(c),
        "FOTO_PROFESSOR_HTML": _foto_professor_html(c.get("professor")),
        "PROFESSOR_CREDENCIAL_HTML": _professor_credencial_html(c.get("professor")),
        "OUTROS_PROFESSORES_HTML": _outros_professores_html(c.get("professores_adicionais")),
        # Conteúdo rico das páginas oficiais preexistentes (seções somem se sem dado):
        "SECAO_OBJETIVOS": _secao_objetivos_html(c.get("objetivos")),
        "SECAO_HABILIDADES": _secao_habilidades_html(c.get("habilidades")),
        "SECAO_DEPOIMENTOS": _secao_depoimentos_html(c.get("depoimentos")),
        "NAV_DEPOIMENTOS": ('<a href="#depoimentos">Depoimentos</a>'
                            if c.get("depoimentos") else ""),
        "EMPRESAS_HTML": _secao_empresas_html(c.get("empresas")),
        "PROFESSORES_BIO_HTML": _professores_bio_html(c),
        "GARANTIA_HTML": _garantia_html(c),
        "SALARIO_HTML": _salario_html(c),
        "PASSOS_HTML": _passos_html(c),
        "PERIODO_OFERECIMENTO": _periodo_html(c, aberto),
        "FAQ_EXTRA_HTML": _faq_extra_html(c),
        # --- inscrição aberta × lista de espera (spec 17 §2b) ---------------------
        "CTA_LABEL": "Inscreva-se →" if aberto else "Quero ser avisado →",
        "PRAZO_LINHA": (
            f"Inscrições abertas até <strong class=\"text-white\">{_ddmm(c.get('inscricoes_fim')) or 'a data publicada na Extecamp'}</strong> · turma {meta_row['turma']}"
            if aberto else
            "Inscrições <strong class=\"text-white\">encerradas no momento</strong> — entre na "
            "lista de espera e avisamos assim que a próxima turma abrir."),
        "MODAL_TITULO": ("Para iniciar sua inscrição no sistema da Unicamp, informe seu contato rápido:"
                         if aberto else
                         "Entre na lista de espera deste curso. Assim que a próxima turma abrir, "
                         "a gente te avisa pelo WhatsApp:"),
        "MODAL_BOTAO": "Continuar para Extecamp →" if aberto else "Entrar na lista de espera",
        "CTA_FINAL_TITULO": (f"Garanta sua vaga na turma {meta_row['turma']}" if aberto
                             else "Fique sabendo em primeira mão"),
        "CTA_FINAL_SUB": (
            f"Inscrições até {_ddmm(c.get('inscricoes_fim')) or 'a data publicada'} no sistema oficial da Unicamp."
            if aberto else
            "Sem turma aberta agora. Deixe seu contato e avisamos assim que as inscrições reabrirem."),
        # A seção "Como se inscrever" (passos da Extecamp) só faz sentido com turma aberta.
        "SECAO_INSCRICAO": _secao_inscricao_html(c, aberto),
        "NAV_INSCRICAO": '<a href="#inscricao">Inscrição</a>' if aberto else "",
        "INVEST_TARJA": (f"Inscrições abertas até {_ddmm(c.get('inscricoes_fim'))}" if aberto
                         else "Valores da última turma · sem inscrições abertas"),
        "INVEST_RODAPE": (
            "Pagamento e emissão de boletos direto no sistema oficial da Unicamp (Extecamp)."
            if aberto else
            "Valores praticados na última turma, para você se planejar — podem ser reajustados "
            "quando a próxima abrir. Entre na lista de espera para receber as condições oficiais."),
        "FAQ_COMO_INSCREVER": (
            '<details class="bg-white/5 border border-white/10 rounded-xl p-5">'
            '<summary class="cursor-pointer font-semibold">Como funciona a inscrição?</summary>'
            '<p class="mt-2 text-sm text-slate-300">Ao clicar em Inscreva-se, você informa um '
            'contato rápido e segue direto para o formulário oficial da Extecamp (sistema da '
            f'Unicamp), onde a inscrição da turma {meta_row["curso"]}/{meta_row["turma"]} é '
            'concluída — com taxa de inscrição, envio de documentos e boletos gerados por lá.'
            '</p></details>'
            if aberto else
            '<details class="bg-white/5 border border-white/10 rounded-xl p-5">'
            '<summary class="cursor-pointer font-semibold">Como entro na lista de espera?</summary>'
            '<p class="mt-2 text-sm text-slate-300">Basta deixar nome e WhatsApp no botão desta '
            'página. Assim que a próxima turma for confirmada na Extecamp, avisamos você com as '
            'datas e o link oficial de inscrição — sem compromisso e sem custo.</p></details>'),
        "FAQ_PRAZO": (
            f"As inscrições desta turma vão até {_ddmm(c.get('inscricoes_fim')) or 'a data publicada na Extecamp'}."
            if aberto else
            "Não há turma com inscrições abertas neste momento. Deixe seu contato na lista de "
            "espera que avisamos assim que a próxima for confirmada."),
    }


def _bridge_bootstrap(tokens, webhook_url, pixel_id, aberto=True):
    """window.BRIDGE + bridge-logic.js, em UM bloco base64 (spec 17 / bug WP
    convert_chars): o WordPress normaliza "&" bruto -> "&#038;" em todo o
    post_content, mesmo dentro de <script>, quebrando o "&&" do JS. Base64
    usa só [A-Za-z0-9+/=] — imune a essa e a qualquer outra normalização de
    texto (aspas retas->curvas, travessões, reticências).
    aberto=False (sem turma na Extecamp): destino vazio — o JS confirma a
    entrada na lista de espera em vez de redirecionar (spec 17 §2b)."""
    config_js = "window.BRIDGE=" + json.dumps({
        "curso": tokens["CURSO"],
        "destino": tokens["URL_CURSO"] if aberto else "",
        "aberto": bool(aberto),
        "webhook": webhook_url, "pixel": pixel_id}) + ";"
    completo = config_js + (_WEB / "bridge-logic.js").read_text()
    return base64.b64encode(completo.encode()).decode()


def render(g, ont, curso, webhook_url="/webhook/ponte", hoje=None):
    """HTML final da LP (template + JS + tokens). Curso desconhecido -> None.
    Curso SEM turma aberta na Extecamp -> LP em modo lista de espera (spec 17 §2b):
    mesma página, mas o CTA capta o contato em vez de mandar pra inscrição."""
    alvo = next((m for m in g.cursos() if m["curso"] == curso), None)
    if not alvo:
        return None
    try:
        c = ont.curso(curso)
    except Exception:
        c = None
    try:
        aberto = bool(ont.captavel(curso, hoje).get("captavel"))
    except Exception:
        aberto = True    # sem ontologia (ex.: FakeOnt nos testes): comporta como aberto
    tokens = render_tokens(alvo, c, aberto=aberto)
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
            .replace("{{BRIDGE_B64}}", _bridge_bootstrap(tokens, webhook_url, pixel_id, aberto)))


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
    # Curso SEM turma aberta: o lead entrou na LISTA DE ESPERA, não num checkout.
    # Registrar 'inicio_inscricao' aqui seria errado em cadeia — jogaria o lead na
    # aba Verificação pedindo pro operador conferir na Extecamp uma inscrição que
    # não existe, e o poria na régua de recuperação de checkout abandonado.
    try:
        aberto = bool(ont.captavel(curso).get("captavel")) if ont else True
    except Exception:
        aberto = True
    funil.registrar(g, ads, est, r["card_id"],
                    "inicio_inscricao" if aberto else "lista_espera",
                    curso=curso, event_id=eid, contexto=contexto)
    if dados.get("sessao_id"):           # liga a telemetria da sessão ao lead (spec 17 §5)
        g.vincular_sessao(str(dados["sessao_id"])[:64], r["card_id"])
    try:
        c = ont.curso(curso) if ont else None
    except Exception:
        c = None
    return {"ok": True, "card_id": r["card_id"], "event_id": eid,
            "redirect": url_inscricao(alvo, c)}
