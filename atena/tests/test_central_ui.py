#!/usr/bin/env python3
"""
Central (atena/web/index.html): invariantes de CSS/estrutura que quebram calados.

Nasceu de um bug real (24/08/2026): `.conv-mais-menu` declarava `display:flex` e
`.hidden{display:none}` estava ANTES no arquivo. Especificidade idêntica (uma classe
cada) -> ganha quem vem depois -> o menu do composer ficava aberto para sempre, e
nenhum clique fora resolvia porque o problema nunca foi o JS.

O agravante: a verificação que rodou na entrega olhava
`classList.contains('hidden')` — a CLASSE alternava certinho, então "passou" com o
menu visível na tela o tempo todo. Estado de classe não é estado visível; aqui a
checagem é sobre o CSS que decide o que o operador enxerga.
"""
import re
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

HTML = (pathlib.Path(__file__).resolve().parents[1] / "web" / "index.html").read_text(encoding="utf-8")
CSS = "\n".join(re.findall(r"<style[^>]*>(.*?)</style>", HTML, re.S))
# sem tirar os comentários, o seletor sai grudado no /* ... */ anterior e
# nenhum fullmatch de seletor casa (o parser mentiria "não existe regra").
CSS = re.sub(r"/\*.*?\*/", "", CSS, flags=re.S)
ok = 0


def check(c, msg):
    global ok
    assert c, "FALHOU: " + msg
    ok += 1
    print("  ok:", msg)


def _regras(css):
    """seletor -> corpo, na ORDEM em que aparecem (a ordem é o que decide o empate)."""
    return [(s.strip(), corpo) for s, corpo in re.findall(r"([^{}]+)\{([^{}]*)\}", css)]


REGRAS = _regras(CSS)
ORDEM_HIDDEN = next((i for i, (s, c) in enumerate(REGRAS)
                     if s == ".hidden" and "display" in c), None)
check(ORDEM_HIDDEN is not None, ".hidden{display:none} existe no CSS da Central")

# --------------------------------------------------- 1) ninguém pode vencer o .hidden
# Classes que o HTML/JS alterna junto com `hidden` (é assim que a Central esconde tudo).
alternadas = set()
for lista in re.findall(r'class="([^"]*\bhidden\b[^"]*)"', HTML):
    alternadas |= {c for c in lista.split() if c and c != "hidden"}
check(alternadas, f"achei classes que convivem com .hidden ({len(alternadas)})")

conflitos = []
for i, (sel, corpo) in enumerate(REGRAS):
    if i < ORDEM_HIDDEN or not re.search(r"(^|;)\s*display\s*:", corpo):
        continue
    # só seletor de UMA classe simples empata com .hidden
    m = re.fullmatch(r"\.([A-Za-z0-9_-]+)", sel)
    if not m or m.group(1) not in alternadas:
        continue
    classe = m.group(1)
    par = any(re.fullmatch(rf"\.{re.escape(classe)}\.hidden|\.hidden\.{re.escape(classe)}", s)
              for s, _ in REGRAS)
    if not par:
        conflitos.append(classe)
check(not conflitos,
      "toda classe que define display DEPOIS de .hidden tem o par .X.hidden "
      f"(sem par, .hidden não tem efeito e o elemento fica visível para sempre): {conflitos}")

# o caso concreto que originou o teste
check(any(re.fullmatch(r"\.conv-mais-menu\.hidden", s) for s, _ in REGRAS),
      ".conv-mais-menu.hidden existe (o menu do composer consegue fechar)")

# --------------------------------------------------- 2) composer: um "+" e o enviar
composer = re.search(r'<div class="conv-composer">(.*?)</div>`\}`;', HTML, re.S)
composer = composer.group(1) if composer else ""
check('id="convMais"' in composer, 'composer tem o botão "+"')
check(composer.count("<button") - composer.count('id="convMaisMenu"') >= 2,
      "composer tem botões (o + e o enviar)")
for antigo in ('onclick="toggleTpl', 'onclick="toggleContato', 'onclick="toggleEmoji',
               'onclick="enviarGuia'):
    check(antigo not in composer,
          f"ação saiu da barra e foi para o menu (não sobrou {antigo}…)")
for rotulo in ("Arquivo, áudio ou foto", "Template aprovado", "Contato oficial",
               "Guia do curso em PDF", "Emoji"):
    check(rotulo in HTML, f"menu nomeia a ação: {rotulo}")

# --------------------------------------------------- 3) fechamento dos popovers
check("fecharPopovers" in HTML and "document.addEventListener('click'" in HTML,
      "existe fechamento por clique fora")
check("'Escape'" in HTML, "Esc também fecha")
fechar = re.search(r"document\.addEventListener\('click', e=>\{(.*?)\}\);", HTML, re.S)
fechar = fechar.group(1) if fechar else ""
check(".conv-mais-wrap" in fechar and ".conv-tpl" in fechar,
      "clique DENTRO do menu ou de um painel não fecha (o operador está escolhendo algo)")
# os ids que fecharPopovers precisa alcançar continuam existindo no template
for pid in ("convTpl", "convContato", "convEmoji"):
    check(f'id="{pid}"' in HTML, f"painel {pid} existe para ser fechado")

print(f"\n{ok} checks OK — UI da Central")
