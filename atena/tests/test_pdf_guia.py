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

# ------------------------------------------------------- 3) seção sem dado SOME
c_esparso = ONT.curso("FEQ-0655")   # o mais esparso do catálogo (auditoria: quase sem *Json)
check(c_esparso is not None, "FEQ-0655 existe no catálogo (pré-condição do teste)")
check(G._empresas_html(ONT.curso("FEQ-1000")) != "", "curso COM empresas mostra a seção")
c_sem_empresas = dict(c0480, empresas=[])
check(G._empresas_html(c_sem_empresas) == "", "curso sem empresas -> seção some (não título vazio)")
check(G._sumario_empresas(c_sem_empresas) == "", "sumário também não referencia seção inexistente")

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
