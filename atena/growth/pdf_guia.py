#!/usr/bin/env python3
"""
Atena · Guia do Curso em PDF (spec 08) — um documento formal por curso, para leads
que pedem material além da conversa/LP. Referência de estrutura: "Guia do Curso
FEQ-0480.pdf" (documento oficial já publicado pela Extensão FEQ), com a paleta
visual da própria bridge page (bridge.css) em vez da paleta do documento de
referência — mantém a identidade visual única em toda a jornada (LP -> WhatsApp -> PDF).

SEM página de investimento/preço: o PDF fica salvo no celular do lead e não se
atualiza sozinho — preço muda por campanha (spec 15) e um PDF desatualizado mentiria
sobre um valor que talvez nem exista mais. O mesmo material de referência também
omite preço, pela mesma razão.

Tudo aqui vem da ontologia ou de atena/core/contatos.py — zero texto gerado por LLM,
mesmo princípio anti-alucinação do resto do sistema. Seção sem dado SOME (nunca
inventa), igual ponte.py. O único elemento não vindo da ontologia é o motivo
geométrico da capa/divisores: decoração abstrata, que não afirma nada sobre o curso.

Cache em disco com invalidação por hash (mesma ideia do lp_auto.py): gera sob
demanda, não em cron — um PDF não muda de conteúdo sozinho entre visitas como uma
LP muda; só precisa ser refeito quando a ontologia daquele curso mudar.
"""
import os
import sys
import base64
import hashlib
import pathlib
import functools
from io import BytesIO

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from atena.growth.ponte import PROFESSOR_BIO, PROFESSOR_FOTOS, PROFESSOR_LINK, _esc, _REQUISITO_TEXTO
from atena.core.contatos import CONTATOS_OFICIAIS, SITE, INSTAGRAM

WEB = pathlib.Path(__file__).resolve().parents[1] / "web"
TEMPLATE_PATH = WEB / "pdf-guia-template.html"
CACHE_DIR = pathlib.Path(__file__).resolve().parents[1] / "data" / "guias"

# Identidade institucional do guia, por prefixo do código. Incidente real (26/09/2026):
# o template era 100% FEQ e um lead do ECO-1100 (Instituto de Economia) recebeu o
# guia com logo, textos e contatos da FEQ. Fail-closed: prefixo fora daqui -> SEM
# guia (obter devolve None) em vez de cair na identidade da FEQ.
# `motivos`: a página "3 motivos" e os selos "Turmas reduzidas"/"Conhecimento
# atualizado" são afirmações da FEQ (aulas ao vivo, interação em tempo real) — falsas
# para o ECO-1100 (assíncrono, 50 vagas); só entram onde foram confirmadas.
INSTITUTOS = {
    "FEQ": {
        "nome": "Extensão FEQ Unicamp", "curto": "a Extensão FEQ", "a": "à", "de": "da", "sigla": "FEQ Unicamp",
        "logo": WEB / "assets" / "feq.png", "contato": "secretaria",
        "site": SITE, "instagram": INSTAGRAM, "motivos": True,
        "boas_vindas": " Os cursos de extensão da FEQ combinam metodologia, qualidade de "
                       "ensino e geração de networking profissional — sem exigência de "
                       "monografia para conclusão: todo o conhecimento é voltado à aplicação "
                       "prática desde o primeiro dia.",
    },
    "ECO": {
        "nome": "Instituto de Economia (IE) Unicamp", "curto": "o Instituto de Economia", "a": "ao", "de": "do",
        "sigla": "Instituto de Economia Unicamp",
        "logo": WEB / "assets" / "ie.png", "contato": "secretaria_ie",
        # site/Instagram do IE não confirmados: omite em vez de linkar a FEQ
        "site": None, "instagram": None, "motivos": False, "boas_vindas": "",
    },
}


def instituto(codigo):
    """Config do instituto do curso, ou None se não houver guia para ele."""
    return INSTITUTOS.get((codigo or "").split("-")[0].upper())


def _inst(c):
    """Instituto do curso, já ajustado ao curso: os "3 motivos" e os selos da FEQ
    afirmam aulas ao vivo e turmas reduzidas — falsos num curso de oferecimento
    contínuo e horário livre (FEQ-1150), mesmo sendo da FEQ."""
    inst = instituto(c["codigo"])
    return {**inst, "motivos": False} if c.get("continuo") else inst


AMBAR = "#fbbf24"
TEAL = "#2dd4bf"


def _datauri(dados, mime):
    return f"data:{mime};base64," + base64.b64encode(dados).decode()


@functools.lru_cache(maxsize=None)
def _logo_branco(logo_path):
    """Logo do instituto recolorido para branco, embutido como data URI.

    WeasyPrint IGNORA `filter: brightness(0) invert(1)` (testado: "Ignored `filter`,
    unknown property") — era exatamente por isso que o logo azul-escuro sumia contra
    a capa navy. Pillow não é dependência nova: o próprio WeasyPrint já a exige.
    Preserva o canal alfa, então as bordas suaves do logo continuam suaves."""
    from PIL import Image
    img = Image.open(logo_path).convert("RGBA")
    branco = Image.new("RGBA", img.size, (255, 255, 255, 255))
    branco.putalpha(img.getchannel("A"))
    buf = BytesIO()
    branco.save(buf, "PNG")     # sem chunk de tempo: bytes estáveis -> hash estável
    return _datauri(buf.getvalue(), "image/png")


def _motivo_svg(codigo, variante=0):
    """Motivo geométrico (malha de nós conectados + arcos) da capa e dos divisores.

    DETERMINÍSTICO a partir do código do curso: o mesmo curso desenha sempre o mesmo
    motivo. Isso não é capricho — o guia é cacheado por hash do HTML, e um desenho
    sorteado a cada geração invalidaria o cache toda vez. Cursos diferentes, porém,
    não saem idênticos.

    É decoração abstrata, não ilustração de conteúdo: não representa nem afirma nada
    sobre o curso (a regra anti-alucinação do módulo vale para fatos, não para
    ornamento geométrico)."""
    estado = [sum(ord(ch) for ch in (codigo or "FEQ")) + variante * 977 or 1]

    def r():
        estado[0] = (estado[0] * 1103515245 + 12345) % 2147483648
        return estado[0] / 2147483648

    # nós numa grade 5x5 com deslocamento — grade pura fica mecânica, sorteio puro
    # fica sujo; a grade com jitter dá uma malha organizada mas viva
    nos = []
    for lin in range(5):
        for col in range(5):
            nos.append((round(70 + col * 165 + (r() - .5) * 105, 1),
                        round(70 + lin * 165 + (r() - .5) * 105, 1)))

    arestas = []
    for i, (x1, y1) in enumerate(nos):
        for x2, y2 in nos[i + 1:]:
            if (x2 - x1) ** 2 + (y2 - y1) ** 2 < 205 ** 2:
                arestas.append(f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}"/>')

    arcos = "".join(f'<circle cx="795" cy="45" r="{r_}" />' for r_ in (210, 350, 490, 630, 770))
    pontos = "".join(
        f'<circle cx="{x}" cy="{y}" r="{round(3 + r() * 5, 1)}" '
        f'fill="{AMBAR if r() > .62 else TEAL}" opacity="{round(.30 + r() * .5, 2)}"/>'
        for x, y in nos)

    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 800 800" width="800" height="800">'
        f'<g fill="none" stroke="{TEAL}" stroke-width="1.1" opacity="0.13">{arcos}</g>'
        f'<g stroke="{AMBAR}" stroke-width="0.9" opacity="0.20">{"".join(arestas)}</g>'
        f'{pontos}</svg>')
    return _datauri(svg.encode("utf-8"), "image/svg+xml")


def _data_br(iso):
    """'2026-09-14' -> '14/09/2026'. Vazio se não houver data (o chamador omite o item)."""
    partes = (iso or "").split("-")
    return "/".join(reversed(partes)) if len(partes) == 3 else ""


def _iniciais(nome):
    partes = (nome or "?").split()
    return ((partes[0][0] if partes else "?") + (partes[-1][0] if len(partes) > 1 else "")).upper()


def _capa_chips(c):
    """Chips de credencial da capa: só entram os que existem na ontologia."""
    chips = []
    if c.get("carga_horas"):
        chips.append(f"<b>{c['carga_horas']}h</b> de carga horária")
    if _data_br(c.get("aulas_inicio")) and not c.get("continuo"):
        chips.append(f"Início em <b>{_data_br(c['aulas_inicio'])}</b>")
    chips.append(f"{_certificado(c)} <b>Unicamp</b>")
    return "\n".join(f'<div class="capa-chip">{ch}</div>' for ch in chips)


def _certificado(c):
    """ECO-1100 emite DECLARAÇÃO de frequência, não certificado (ontologia,
    `certificacao`) — o guia prometia "Certificado Unicamp" para todo o catálogo."""
    return ("Declaração de frequência" if (c.get("certificacao") or "").startswith("Declaração")
            else "Certificado")


def _ficha_html(c):
    """Ficha técnica: o resumo que o lead procura antes de ler o resto."""
    itens = [
        ("Modalidade", c.get("modalidade")),
        ("Carga horária", f"{c['carga_horas']} horas" if c.get("carga_horas") else None),
        ("Tipo", c.get("tipo")),
        ("Início das aulas", None if c.get("continuo") else _data_br(c.get("aulas_inicio"))),
        ("Horário", c.get("horario")),
        ("Plataforma", c.get("plataforma")),
    ]
    blocos = "".join(
        f'<div class="ficha-item"><div class="ficha-rot">{_esc(rot)}</div>'
        f'<div class="ficha-val">{_esc(val)}</div></div>'
        for rot, val in itens if val)
    return f'<div class="ficha">{blocos}</div>' if blocos else ""


def _sumario_html(c):
    """Sumário montado a partir do que REALMENTE vai existir no documento — listar
    uma seção que foi omitida por falta de dado seria mentir sobre o próprio guia."""
    inst = _inst(c)
    secoes = (["Por que estudar na Extensão FEQ"] if inst["motivos"] else []) \
        + ["Objetivos do curso", "Metodologia"]
    if c.get("habilidades"):
        secoes.append("O que você vai dominar")
    secoes.append("Diferenciais")
    if c.get("empresas"):
        secoes.append("Empresas de alunos formados")
    secoes.append("Público-alvo")
    if c.get("depoimentos"):
        secoes.append("Quem já fez o curso")
    secoes += ["Professores", "Conteúdo programático", "Contato"]
    return "\n".join(
        f'<div class="sumario-item"><span class="sumario-n">{i:02d}</span>'
        f'<span>{_esc(s)}</span></div>' for i, s in enumerate(secoes, 1))


def _selo(titulo, texto):
    return f'<div class="selo"><b>{_esc(titulo)}</b><span>{_esc(texto)}</span></div>'


def _selos_html(c):
    selos = []
    if c.get("carga_horas"):
        selos.append(_selo(f"{c['carga_horas']}h de carga horária", f"{_certificado(c)} da Unicamp ao concluir."))
    if c.get("horario"):
        selos.append(_selo("Horário das aulas", c["horario"]))
    if c.get("formato_aulas_detalhe") or c.get("formato_aulas_resumo"):
        selos.append(_selo(c.get("formato_aulas_resumo") or "Formato das aulas",
                           c.get("formato_aulas_detalhe") or ""))
    if c.get("plataforma"):
        selos.append(_selo("Plataforma", c["plataforma"]))
    return "\n".join(selos)


def _avaliacao_html(c):
    if not c.get("avaliacao"):
        return ""
    return f'<p style="margin-top:5mm;color:#334155;">{_esc(c["avaliacao"])}</p>'


def _objetivos_html(c):
    obj = c.get("objetivos") or []
    if not obj:
        return f'<p>{_esc(c.get("objetivo") or f"Consulte {instituto(c['codigo'])['curto']} para o detalhamento dos objetivos.")}</p>'
    return "\n".join(
        f'<div class="item-num"><div class="bola">{i+1}</div><div>'
        f'<b>{_esc(o.get("t",""))}</b><span>{_esc(o.get("d",""))}</span></div></div>'
        for i, o in enumerate(obj))


def _objetivo_resumo(c):
    txt = c.get("objetivo") or ""
    corte = txt.find(":")
    frase = txt[:corte] if 0 < corte < 220 else txt.split(". ")[0]
    return _esc((frase.strip().rstrip(".:") + ".") if frase and len(frase) < len(txt) else (txt or ""))


def _empresas_html(c):
    emp = c.get("empresas") or []
    if not emp:
        return ""
    chips = "".join(f'<span class="empresa-chip">{_esc(e)}</span>' for e in emp)
    return ('<div class="secao"><h2 class="titulo">Empresas de alunos formados</h2>'
            f'<div>{chips}</div></div>')


def _pagina_habilidades(c):
    """Página inteira condicional: sem habilidadesJson, não existe (nem no sumário)."""
    hab = c.get("habilidades") or []
    if not hab:
        return ""
    linhas = "\n".join(
        f'<div class="hab"><div class="hab-marca"></div><span>{_esc(h)}</span></div>' for h in hab)
    return ('<div class="pagina"><h2 class="titulo">O que você vai dominar</h2>'
            f'{linhas}</div>')


def _pagina_depoimentos(c):
    """Depoimentos reais publicados nas páginas oficiais do curso — nunca inventados."""
    deps = c.get("depoimentos") or []
    if not deps:
        return ""
    cards = "\n".join(
        f'<div class="depo"><p class="depo-txt">“{_esc(d.get("texto",""))}”</p>'
        f'<div class="depo-nome">{_esc(d.get("nome",""))}</div>'
        + (f'<div class="depo-cargo">{_esc(d["cargo"])}</div>' if d.get("cargo") else "")
        + '</div>' for d in deps)
    return ('<div class="pagina"><h2 class="titulo">Quem já fez o curso</h2>'
            f'{cards}</div>')


def _publico_alvo_html(c):
    """Requisito vem da ontologia como enum cru (ex.: 'superiorCompleto') — nunca
    mostrar isso a um leigo; _REQUISITO_TEXTO é a MESMA frase que a LP já usa (bug
    real pego na inspeção visual: a 1ª geração vazou o enum cru na tela).
    Só repete a frase de requisito numa 2ª linha quando publico_alvo é um texto
    DIFERENTE (poucos cursos têm os dois) — senão a página dizia a mesma frase 2x."""
    requisito = _esc(_REQUISITO_TEXTO.get(c.get("requisito"), f"Consulte {instituto(c['codigo'])['curto']}."))
    alvo = c.get("publico_alvo")
    if alvo:
        return f'<p>{_esc(alvo)}</p><p style="color:#64748b;font-size:9.5pt;">Requisito: {requisito}</p>'
    return f"<p>{requisito}</p>"


def _garantia_selo(c):
    if not c.get("garantia_7d"):
        return ""
    return _selo("Garantia de 7 dias", "Não gostou? Reembolso total nos primeiros 7 dias, sem burocracia.")


def _card_professor(nome):
    """Um professor = um card. Sem bio/foto/link cadastrados -> só o nome, nunca
    inventa (mesma regra de _outros_professores_html em ponte.py).

    As iniciais ficam SEMPRE no card, atrás da foto: se o download da foto falhar
    durante a geração (são URLs das páginas oficiais da FEQ), o card degrada para a
    inicial em vez de abrir um buraco branco."""
    if not nome:
        return ""
    bio = PROFESSOR_BIO.get(nome) or {}
    foto = PROFESSOR_FOTOS.get(nome)
    link = PROFESSOR_LINK.get(nome)
    media = (f'<div class="prof-media">{_esc(_iniciais(nome))}'
             + (f'<img src="{_esc(foto)}" alt="">' if foto else "") + '</div>')
    return (f'{media}<div>'
            f'<div class="prof-nome">{_esc(nome)}</div>'
            + (f'<div class="prof-cargo">{_esc(bio["cargo"])}</div>' if bio.get("cargo") else "")
            + (f'<div class="prof-bio">{_esc(bio["bio"])}</div>' if bio.get("bio") else "")
            + (f'<div class="prof-link">{_esc(link["tipo"])}: {_esc(link["url"])}</div>' if link else "")
            + '</div>')


def _professores_html(c):
    nomes = [c.get("professor")] + list(c.get("professores_adicionais") or [])
    return "\n".join(f'<div class="prof-card">{_card_professor(n)}</div>' for n in nomes if n)


def _modulos_html(c):
    prog = c.get("programa_detalhe") or []
    if not prog:
        return f'<p>{_esc(c.get("conteudo_programatico") or "Programa completo disponível na página oficial da Extecamp.")}</p>'
    out = []
    for i, m in enumerate(prog):
        subs = "".join(f"<li>{_esc(s)}</li>" for s in (m.get("s") or []))
        out.append(f'<div class="modulo"><div class="modulo-tit">'
                   f'<div class="modulo-n">{i+1}</div>'
                   f'<span>{_esc(m.get("t",""))}</span></div><ul>{subs}</ul></div>')
    return "\n".join(out)


def _contatos_html(inst):
    """Só os canais confirmados DO INSTITUTO do curso — canal sem dado some."""
    sec = CONTATOS_OFICIAIS[inst["contato"]]
    linhas = [("WhatsApp: " if sec.get("whatsapp") else "") + sec["telefone_exibicao"],
              sec.get("telefone2_exibicao")]
    if inst["instagram"]:
        linhas.append("Instagram: " + inst["instagram"])
    linhas.append(sec.get("email"))
    if sec.get("horario_atendimento"):
        linhas.append("Atendimento: " + sec["horario_atendimento"])
    html = "".join(f'<p class="contato-linha">{_esc(l)}</p>' for l in linhas if l)
    if inst["site"]:
        html += f'<p class="contato-linha"><a href="{_esc(inst["site"])}">{_esc(inst["site"])}</a></p>'
    return html


def _bloco_motivos(inst):
    if not inst["motivos"]:
        return ""
    return ('<div class="divisor"><div class="divisor-fundo"></div>'
            '<div class="divisor-motivo"><img src="{{MOTIVO_DIVISOR}}" alt=""></div>'
            f'<div class="divisor-conteudo"><p class="tag">Por que estudar na</p><h1>{_esc(inst["nome"])}</h1>'
            '<div class="divisor-regua"></div></div></div>'
            '<div class="pagina"><h2 class="titulo">3 motivos essenciais</h2>'
            '<div class="item-num"><div class="bola">1</div><div><b>Disponibilidade</b><span>Aulas 100% online, ao vivo e gravadas, para assistir de onde e quando quiser.</span></div></div>'
            '<div class="item-num"><div class="bola">2</div><div><b>Networking</b><span>Corpo docente dividido entre pesquisadores da Unicamp e profissionais referência no mercado. Alunos de todo o Brasil.</span></div></div>'
            '<div class="item-num"><div class="bola">3</div><div><b>Formação profissional</b><span>Sem exigência de monografia — todo o conhecimento é aplicado à sua carreira desde o primeiro dia.</span></div></div>'
            '</div>')


def _selos_institucionais(inst):
    if not inst["motivos"]:
        return ""
    return (_selo("Turmas reduzidas", "Atendimento individual e interação em tempo real entre aluno e professor.")
            + _selo("Conhecimento atualizado", "Domine ferramentas e conhecimentos revolucionários antes deles chegarem ao seu dia a dia."))


def _tokens(c):
    inst = _inst(c)
    # BLOCO_MOTIVOS primeiro: ele contém {{MOTIVO_DIVISOR}}, resolvido depois
    return {
        "BLOCO_MOTIVOS": _bloco_motivos(inst),
        "INST_NOME": _esc(inst["nome"]),
        "INST_CURTO": _esc(inst["curto"]),
        "INST_A": inst["a"],
        "INST_DE": inst["de"],
        "INST_SIGLA": _esc(inst["sigla"]),
        "BOAS_VINDAS_INST": _esc(inst["boas_vindas"]),
        "SELOS_INSTITUCIONAIS": _selos_institucionais(inst),
        "CERTIFICADO": _esc(_certificado(c)),
        "EMITIDO": "emitida" if _certificado(c).startswith("Declaração") else "emitido",
        "NOME_CURSO": _esc(c.get("nome") or c["codigo"]),
        "CODIGO_CURSO": _esc(c["codigo"]),
        "TIPO_CURSO": _esc(c.get("tipo") or "Curso"),
        "MODALIDADE": _esc(c.get("modalidade") or ""),
        "LOGO_BRANCO": _logo_branco(inst["logo"]),
        "MOTIVO_CAPA": _motivo_svg(c["codigo"], variante=0),
        "MOTIVO_DIVISOR": _motivo_svg(c["codigo"], variante=1),
        "CAPA_CHIPS": _capa_chips(c),
        "FICHA_HTML": _ficha_html(c),
        "SUMARIO_HTML": _sumario_html(c),
        "OBJETIVO_RESUMO": _objetivo_resumo(c),
        "OBJETIVOS_HTML": _objetivos_html(c),
        "SELOS_HTML": _selos_html(c),
        "AVALIACAO_HTML": _avaliacao_html(c),
        "PAGINA_HABILIDADES": _pagina_habilidades(c),
        "GARANTIA_SELO": _garantia_selo(c),
        "EMPRESAS_PAGINA": _empresas_html(c),
        "PUBLICO_ALVO_HTML": _publico_alvo_html(c),
        "PAGINA_DEPOIMENTOS": _pagina_depoimentos(c),
        "PROFESSORES_HTML": _professores_html(c),
        "MODULOS_HTML": _modulos_html(c),
        "CONTATOS_HTML": _contatos_html(inst),
    }


def _render_html(curso_dict):
    tpl = TEMPLATE_PATH.read_text(encoding="utf-8")
    for k, v in _tokens(curso_dict).items():
        tpl = tpl.replace("{{" + k + "}}", v)
    return tpl


def _hash(html_str):
    return hashlib.sha256((html_str or "").encode("utf-8")).hexdigest()


def obter(ont, codigo, forcar=False):
    """PDF do curso, com cache em disco por hash do HTML gerado. Gera de novo só
    quando o HTML mudaria (a ontologia mudou) — mesmo princípio do lp_auto.py:
    comparar o que SERIA gerado agora, não confiar em nenhum timestamp externo."""
    c = ont.curso(codigo)
    if not c or not instituto(codigo):   # instituto sem identidade própria: sem guia
        return None
    html_atual = _render_html(c)
    h = _hash(html_atual)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    pdf_path = CACHE_DIR / f"{codigo}.pdf"
    hash_path = CACHE_DIR / f"{codigo}.hash"
    if not forcar and pdf_path.exists() and hash_path.exists() and hash_path.read_text().strip() == h:
        return pdf_path.read_bytes()
    # import adiado: só quem gera PDF de fato precisa da lib (e das bibliotecas de
    # sistema Pango/Cairo/GDK-Pixbuf — ver deploy_vps.sh)
    from weasyprint import HTML
    pdf = HTML(string=html_atual, base_url=str(WEB)).write_pdf()
    pdf_path.write_bytes(pdf)
    hash_path.write_text(h)
    return pdf


if __name__ == "__main__":
    import atena  # fuso BRT
    from atena.ontologia.servico import Ontologia
    ont = Ontologia()
    alvo = sys.argv[1] if len(sys.argv) > 1 else None
    cursos = [alvo] if alvo else ont.cursos()
    for cod in cursos:
        pdf = obter(ont, cod, forcar=True)
        print(f"{cod}: {'OK, ' + str(len(pdf)) + ' bytes' if pdf else 'FALHOU'} -> data/guias/{cod}.pdf")
