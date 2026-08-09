#!/usr/bin/env python3
"""
Atena · Growth — auto-republicação de LPs quando a ontologia deriva (spec 17/18).

Antes só existia o botão manual "Publicar" em Gestão de LPs. Preço de promoção
expirando, curso mudando de status (aberta -> fora_do_prazo -> encerrada), tudo isso
ficava visível na PRÉVIA mas só ia ao ar se alguém lembrasse de clicar. Este módulo
fecha esse laço sozinho.

A comparação é por HASH do HTML que `ponte.render()` produziria agora — não por uma
lista de "quais campos mudaram", porque `render()` já é a função que decide o que
aparece; reimplementar essa lógica aqui duplicaria regra e divergiria com o tempo.

LP editada à mão NUNCA é republicada automaticamente (mesmo princípio já existente em
`wp_deploy_bridge.renderizar`) — aqui só marcamos que ela pode estar desatualizada
(`lp_drift_<curso>`), para o operador decidir.
"""
import sys
import hashlib
import pathlib
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from atena.growth import ponte


def _hash(html):
    return hashlib.sha256((html or "").encode("utf-8")).hexdigest()


def _webhook_url():
    from atena.growth.wp_deploy_bridge import _webhook_url as f
    return f()


def republicar_um(g, ont, est, curso):
    """Renderiza e publica UM curso (reaproveita o mesmo caminho do botão manual).
    Usado pelo tick de deriva e pela aprovação de um novo oferecimento (item 3b)."""
    from atena.growth import wp_deploy_bridge as WP
    m = next((x for x in g.cursos() if x["curso"] == curso), None)
    if not m:
        return None
    paginas = WP.renderizar(g, ont, [m], pathlib.Path(tempfile.mkdtemp()), est=est)
    if not paginas:
        return None
    r = WP.publicar(paginas)[0]
    est.set_config(f"lp_hash_{curso}", _hash(paginas[0]["html"]))
    return r


def rodar(g, ont, est):
    """Uma passada: republica quem mudou, sinaliza quem está editado-e-desatualizado.
    Idempotente e barata quando nada mudou — cada curso custa 1 render em memória."""
    publicados, drift = [], []
    editadas = est.cursos_lp_editada()
    for m in g.cursos():
        curso = m["curso"]
        gerado = ponte.render(g, ont, curso, webhook_url=_webhook_url())
        if gerado is None:
            continue
        h = _hash(gerado)
        chave = f"lp_hash_{curso}"
        antigo = est.get_config(chave)
        if curso in editadas:
            # a baseline de uma LP editada é gravada NO MOMENTO da edição (lp_salvar),
            # nunca aqui — comparar contra o hash de agora apagaria a marca de "mudou"
            # um tick depois, mesmo com a edição continuando desatualizada.
            if antigo is not None and antigo != h:
                est.set_config(f"lp_drift_{curso}", "1")
                drift.append(curso)
            continue
        if antigo is None:
            est.set_config(chave, h)          # 1º tick: só grava baseline, não publica
            continue
        if antigo != h:
            if republicar_um(g, ont, est, curso):
                publicados.append(curso)
    return {"publicados": publicados, "drift": drift}
