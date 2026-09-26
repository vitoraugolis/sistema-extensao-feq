#!/usr/bin/env python3
"""
Guia do curso em PDF (spec 08). Igual à LP: seção sem dado SOME, nunca inventa —
e o mesmo tipo de bug (dado interno vazando cru) apareceu aqui na 1ª inspeção
visual: "Público-alvo" mostrou "superiorCompleto" (o enum da ontologia) em vez da
frase legível que a LP já usa.

A geração de HTML (onde vivem os bugs de mapeamento de dado) é testada sem depender
de WeasyPrint/Pango — rápida e roda em qualquer máquina. A geração REAL do PDF
(seção 4) pula graciosamente se as libs de sistema não estiverem instaladas aqui
(mesmo padrão do projeto para capacidades condicionadas ao ambiente, ex.: sem
WHATSAPP_TOKEN -> dry-run em vez de falhar).
"""
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from atena.ontologia.servico import Ontologia
from atena.growth import pdf_guia as G

ONT = Ontologia()
ok = 0


def check(c, msg):
    global ok
    assert c, "FALHOU: " + msg
    ok += 1
    print("  ok:", msg)


# ------------------------------------------------------- 1) público-alvo (o bug real)
c0480 = ONT.curso("FEQ-0480")   # sem publico_alvo na ontologia
check("superiorCompleto" not in G._publico_alvo_html(c0480),
      "enum cru da ontologia NUNCA aparece na página (bug pego na inspeção visual)")
check("ensino superior completo" in G._publico_alvo_html(c0480),
      "cai na frase legível (_REQUISITO_TEXTO, a MESMA que a LP já usa)")
check(G._publico_alvo_html(c0480).count("<p>") == 1,
      "sem publico_alvo próprio, mostra UMA frase só (não duplica a mesma info 2x)")

c0331 = ONT.curso("FEQ-0331")   # tem publico_alvo próprio
h0331 = G._publico_alvo_html(c0331)
check("Profissionais de segurança" in h0331, "publico_alvo próprio é usado quando existe")
check("Requisito:" in h0331 and "ensino superior completo" in h0331,
      "com publico_alvo próprio, o requisito aparece como 2ª linha (são infos diferentes)")

# ------------------------------------------------------- 2) nunca inventa (professores)
check("Douglas Alves Goulart" in G._card_professor("Douglas Alves Goulart"),
      "professor com bio/foto renderiza")
sem_dado = G._card_professor("Fulano Sem Cadastro Nenhum")
check("Fulano Sem Cadastro Nenhum" in sem_dado, "nome aparece mesmo sem bio/foto/link")
check("None" not in sem_dado and "null" not in sem_dado,
      "ausência de dado nunca vaza como string 'None'/'null' no HTML")
check(G._card_professor(None) == "" and G._card_professor("") == "",
      "nome vazio não produz card fantasma")

# ------------------------------------------------------- 2b) identidade por instituto
# incidente 26/09/2026: lead do ECO-1100 recebeu o guia com marca e contatos da FEQ
import re
_sem_css = lambda h: re.sub(r"<style.*?</style>", "", h, flags=re.S)
eco = _sem_css(G._render_html(ONT.curso("ECO-1100")))
check(not re.search(r"FEQ|extfeq|3521-3911|98262|extensaofequnicamp", eco.replace("ECO-1100", "")),
      "guia do ECO-1100 não carrega NADA da FEQ (marca, e-mail, telefones, Instagram)")
check("iecoext@unicamp.br" in eco and "3521-5728" in eco, "guia do ECO-1100 traz os contatos do IE")
check("Declaração de frequência" in eco and "Certificado" not in eco,
      "ECO-1100 emite declaração de frequência — o guia não promete certificado")
check("Turmas reduzidas" not in eco and "3 motivos" not in eco,
      "afirmações institucionais da FEQ (turmas reduzidas, aulas ao vivo) ficam fora do ECO")
check(G._logo_branco(G.INSTITUTOS["ECO"]["logo"]) != G._logo_branco(G.INSTITUTOS["FEQ"]["logo"]),
      "logo do IE é diferente do da FEQ")
feq = _sem_css(G._render_html(ONT.curso("FEQ-0480")))
check("Extensão FEQ Unicamp" in feq and "Turmas reduzidas" in feq and "extfeq@unicamp.br" in feq,
      "guia da FEQ continua com a identidade da FEQ")
check(G.instituto("XYZ-0001") is None, "instituto desconhecido: sem guia (fail-closed, não cai na FEQ)")

# ------------------------------------------------------- 3) seção sem dado SOME
c_esparso = ONT.curso("FEQ-0655")   # o mais esparso do catálogo (auditoria: quase sem *Json)
check(c_esparso is not None, "FEQ-0655 existe no catálogo (pré-condição do teste)")
check(G._empresas_html(ONT.curso("FEQ-1000")) != "", "curso COM empresas mostra a seção")
c_sem_empresas = dict(c0480, empresas=[])
check(G._empresas_html(c_sem_empresas) == "", "curso sem empresas -> seção some (não título vazio)")

# o sumário lista o que o documento REALMENTE tem — anunciar uma seção que foi
# omitida por falta de dado seria o guia mentindo sobre si mesmo
check("Empresas de alunos formados" not in G._sumario_html(c_sem_empresas),
      "sumário não anuncia seção que não foi gerada")
check("Empresas de alunos formados" in G._sumario_html(c0480),
      "sumário anuncia a seção quando ela existe")
check(G._pagina_habilidades(dict(c0480, habilidades=[])) == ""
      and G._pagina_depoimentos(dict(c0480, depoimentos=[])) == "",
      "páginas de habilidades/depoimentos somem inteiras sem dado")
check("O que você vai dominar" not in G._sumario_html(dict(c0480, habilidades=[])),
      "sumário acompanha a página condicional que sumiu")

c_sem_modulos = dict(c0480, programa_detalhe=[])
check("<div class=\"modulo\">" not in G._modulos_html(c_sem_modulos),
      "sem programaDetalheJson, cai no texto corrido (não numera lista vazia)")
check(G._modulos_html(ONT.curso("FEQ-0480")).count('class="modulo"') == 16,
      "com programaDetalheJson, todos os 16 módulos do FEQ-0480 aparecem numerados")

# ------------------------------------------------------- 4) nenhum token esquecido
for cod in ("FEQ-0480", "FEQ-0331", "FEQ-0655", "FEQ-1000"):
    html_final = G._render_html(ONT.curso(cod))
    check("{{" not in html_final, f"{cod}: nenhum placeholder {{{{}}}} sobrou sem substituir")

# ------------------------------------------------------- 5) geração real do PDF (opcional)
try:
    import weasyprint  # noqa: F401
    TEM_WEASYPRINT = True
except OSError:
    # instalado mas sem as libs de sistema (Pango/Cairo/GDK-Pixbuf) — mesmo caso do
    # VPS antes do deploy_vps.sh instalar os pacotes apt
    TEM_WEASYPRINT = False
except ImportError:
    TEM_WEASYPRINT = False

if not TEM_WEASYPRINT:
    print("  (pulado) WeasyPrint/Pango não disponível neste ambiente — "
          "geração real do PDF não testada aqui.")
else:
    import tempfile
    G.CACHE_DIR = pathlib.Path(tempfile.mkdtemp())   # isola do cache real em disco

    pdf = G.obter(ONT, "FEQ-0480", forcar=True)
    check(pdf is not None and pdf[:4] == b"%PDF", "PDF válido gerado (assinatura %PDF)")
    check(len(pdf) > 5000, "PDF tem conteúdo de verdade (não é uma página vazia)")

    pdf_esparso = G.obter(ONT, "FEQ-0655", forcar=True)
    check(pdf_esparso is not None and pdf_esparso[:4] == b"%PDF",
          "curso mais esparso do catálogo NÃO quebra a geração")

    check(G.obter(ONT, "FEQ-9999") is None, "curso inexistente devolve None, não exceção")

    # cache por hash: sem forçar, 2ª chamada com o MESMO conteúdo não gera de novo
    h1 = G._hash((G.CACHE_DIR / "FEQ-0480.hash").read_text())
    antes = (G.CACHE_DIR / "FEQ-0480.pdf").read_bytes()
    pdf2 = G.obter(ONT, "FEQ-0480")
    check(pdf2 == antes, "sem mudança na ontologia, cache devolve os MESMOS bytes (não regenera à toa)")

print(f"\n{ok}/{ok} passaram.")
