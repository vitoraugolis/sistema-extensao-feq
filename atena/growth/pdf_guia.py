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
inventa), igual ponte.py.

Cache em disco com invalidação por hash (mesma ideia do lp_auto.py): gera sob
demanda, não em cron — um PDF não muda de conteúdo sozinho entre visitas como uma
LP muda; só precisa ser refeito quando a ontologia daquele curso mudar.
"""
import os
import sys
import hashlib
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from atena.growth.ponte import PROFESSOR_BIO, PROFESSOR_FOTOS, PROFESSOR_LINK, _esc, _REQUISITO_TEXTO
from atena.core.contatos import CONTATOS_OFICIAIS, SITE, INSTAGRAM

TEMPLATE_PATH = pathlib.Path(__file__).resolve().parents[1] / "web" / "pdf-guia-template.html"
CACHE_DIR = pathlib.Path(__file__).resolve().parents[1] / "data" / "guias"
LOGO_URL_DEFAULT = "https://cursos-extensao.feq.unicamp.br/wp-content/uploads/2021/04/logo-redmencionado-300x70.png"


def _iniciais(nome):
    partes = (nome or "?").split()
    return ((partes[0][0] if partes else "?") + (partes[-1][0] if len(partes) > 1 else "")).upper()


def _selo(titulo, texto):
    return f'<div class="selo"><b>{_esc(titulo)}</b><span>{_esc(texto)}</span></div>'


def _selos_html(c):
    selos = []
    if c.get("carga_horas"):
        selos.append(_selo(f"{c['carga_horas']}h de carga horária", "Certificado da Unicamp ao concluir."))
    if c.get("horario"):
        selos.append(_selo("Horário das aulas", c["horario"]))
    if c.get("formato_aulas_detalhe") or c.get("formato_aulas_resumo"):
        selos.append(_selo(c.get("formato_aulas_resumo") or "Formato das aulas",
                           c.get("formato_aulas_detalhe") or ""))
    return "\n".join(selos)


def _avaliacao_html(c):
    if not c.get("avaliacao"):
        return ""
    return f'<p style="margin-top:6mm;color:#334155;">{_esc(c["avaliacao"])}</p>'


def _objetivos_html(c):
    obj = c.get("objetivos") or []
    if not obj:
        return f'<p>{_esc(c.get("objetivo") or "Consulte a Extensão FEQ para o detalhamento dos objetivos.")}</p>'
    return "\n".join(
        f'<div class="item-num"><div class="bola">{i+1}</div><div><b>{_esc(o.get("t",""))}</b>{_esc(o.get("d",""))}</div></div>'
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
    return (f'<h2 class="titulo" style="margin-top:14mm;">Empresas de alunos formados</h2>'
           f'<div>{chips}</div>')


def _sumario_empresas(c):
    return '<div class="sumario-item"><span>Empresas de alunos formados</span></div>' if c.get("empresas") else ""


def _publico_alvo_html(c):
    """Requisito vem da ontologia como enum cru (ex.: 'superiorCompleto') — nunca
    mostrar isso a um leigo; _REQUISITO_TEXTO é a MESMA frase que a LP já usa (bug
    real pego na inspeção visual: a 1ª geração vazou o enum cru na tela).
    Só repete a frase de requisito numa 2ª linha quando publico_alvo é um texto
    DIFERENTE (poucos cursos têm os dois) — senão a página dizia a mesma frase 2x."""
    requisito = _esc(_REQUISITO_TEXTO.get(c.get("requisito"), "Consulte a Extensão FEQ."))
    alvo = c.get("publico_alvo")
    if alvo:
        return f'<p>{_esc(alvo)}</p><p style="color:#64748b;font-size:10pt;">Requisito: {requisito}</p>'
    return f"<p>{requisito}</p>"


def _garantia_selo(c):
    if not c.get("garantia_7d"):
        return ""
    return _selo("Garantia de 7 dias", "Não gostou? Reembolso total nos primeiros 7 dias, sem burocracia.")


def _card_professor(nome, destaque=False):
    """Um professor = uma linha de card. Sem bio/foto/link cadastrados -> só o nome,
    nunca inventa (mesma regra de _outros_professores_html em ponte.py)."""
    if not nome:
        return ""
    bio = PROFESSOR_BIO.get(nome) or {}
    foto = PROFESSOR_FOTOS.get(nome)
    link = PROFESSOR_LINK.get(nome)
    avatar = (f'<img class="prof-foto" src="{_esc(foto)}">' if foto
             else f'<div class="prof-avatar">{_esc(_iniciais(nome))}</div>')
    linha_link = (f'<div class="prof-link">{_esc(link["tipo"])}: {_esc(link["url"])}</div>' if link else "")
    return (f'<div class="prof-card">{avatar}<div>'
           f'<div class="prof-nome">{_esc(nome)}</div>'
           + (f'<div class="prof-cargo">{_esc(bio["cargo"])}</div>' if bio.get("cargo") else "")
           + (f'<div class="prof-bio">{_esc(bio["bio"])}</div>' if bio.get("bio") else "")
           + linha_link + "</div></div>")


def _professores_html(c):
    nomes = [c.get("professor")] + [n for n in (c.get("professores_adicionais") or [])]
    return "\n".join(_card_professor(n) for n in nomes if n)


def _modulos_html(c):
    prog = c.get("programa_detalhe") or []
    if not prog:
        return f'<p>{_esc(c.get("conteudo_programatico") or "Programa completo disponível na página oficial da Extecamp.")}</p>'
    out = []
    for i, m in enumerate(prog):
        subs = "".join(f"<li>{_esc(s)}</li>" for s in (m.get("s") or []))
        out.append(f'<div class="modulo"><div class="modulo-tit">'
                   f'<div class="bola" style="width:7mm;height:7mm;font-size:9pt;">{i+1}</div>'
                   f'<span>{_esc(m.get("t",""))}</span></div><ul>{subs}</ul></div>')
    return "\n".join(out)


def _tokens(c):
    sec = CONTATOS_OFICIAIS["secretaria"]
    wa = CONTATOS_OFICIAIS["whatsapp"]
    return {
        "NOME_CURSO": _esc(c.get("nome") or c["codigo"]),
        "CODIGO_CURSO": _esc(c["codigo"]),
        "TIPO_CURSO": _esc(c.get("tipo") or "Curso"),
        "MODALIDADE": _esc(c.get("modalidade") or ""),
        "LOGO_URL": os.environ.get("GROWTH_LOGO_URL") or LOGO_URL_DEFAULT,
        "OBJETIVO_RESUMO": _objetivo_resumo(c),
        "OBJETIVOS_HTML": _objetivos_html(c),
        "SELOS_HTML": _selos_html(c),
        "AVALIACAO_HTML": _avaliacao_html(c),
        "GARANTIA_SELO": _garantia_selo(c),
        "EMPRESAS_PAGINA": _empresas_html(c),
        "SUMARIO_EMPRESAS": _sumario_empresas(c),
        "PUBLICO_ALVO_HTML": _publico_alvo_html(c),
        "PROFESSORES_HTML": _professores_html(c),
        "MODULOS_HTML": _modulos_html(c),
        "CONTATO_TELEFONE": _esc(sec["telefone_exibicao"]),
        "CONTATO_WHATSAPP": _esc(wa["telefone_exibicao"]),
        "CONTATO_INSTAGRAM": _esc(INSTAGRAM),
        "CONTATO_EMAIL": _esc(sec["email"]),
        "CONTATO_SITE": _esc(SITE),
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
    if not c:
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
    pdf = HTML(string=html_atual).write_pdf()
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
