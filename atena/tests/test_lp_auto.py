#!/usr/bin/env python3
"""
Auto-republicação de LP por deriva da ontologia (spec 17/18).

Antes, uma LP só ia ao ar de novo com clique manual em "Publicar" — preço de
promoção expirando ou curso mudando de status ficava visível na prévia mas não na
página real até alguém lembrar. O risco aqui é o oposto do de sempre: não é
alucinar um fato, é ESQUECER de aplicar um fato que já mudou.
"""
import os
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

os.environ["WHATSAPP_TOKEN"] = ""
os.environ["ATENA_DEV"] = "1"

from atena.core.estado import Estado
from atena.growth.db import Growth
from atena.growth import lp_auto

ok = 0


def check(c, msg):
    global ok
    assert c, "FALHOU: " + msg
    ok += 1
    print("  ok:", msg)


class OntFake:
    """Controla o HTML "gerado" por curso sem depender da ontologia real —
    o que importa aqui é o mecanismo de diff, não o conteúdo."""
    def __init__(self):
        self.html = {}

    def curso(self, c):
        return {"codigo": c}


import atena.growth.ponte as ponte_mod


def _fake_render(g, ont, curso, webhook_url=None, hoje=None):
    return ont.html.get(curso)


ponte_mod.render = _fake_render   # patch de módulo: lp_auto chama ponte.render(...)


class WPFake:
    """Substitui wp_deploy_bridge dentro de republicar_um via monkeypatch do módulo."""
    publicados = []

    @staticmethod
    def renderizar(g, ont, cursos, saida, est=None):
        return [{"curso": cursos[0]["curso"], "slug": "x", "html": ont.html[cursos[0]["curso"]]}]

    @staticmethod
    def publicar(paginas):
        WPFake.publicados.append(paginas[0]["curso"])
        return [{"curso": paginas[0]["curso"], "status": "publicado"}]


import atena.growth.wp_deploy_bridge as wpdb
wpdb.renderizar = WPFake.renderizar
wpdb.publicar = WPFake.publicar

est = Estado(":memory:")
g = Growth(":memory:")
ont = OntFake()
ont.html = {c["curso"]: f"<html>{c['curso']} v1</html>" for c in g.cursos()}

# ---------------------------------------------------------------- 1) primeiro tick
r = lp_auto.rodar(g, ont, est)
check(r["publicados"] == [], "1º tick nunca publica — só grava a baseline (evita rajada no deploy)")
check(est.get_config("lp_hash_FEQ-0480") == lp_auto._hash(ont.html["FEQ-0480"]),
      "baseline gravada é o hash do conteúdo atual")

# ---------------------------------------------------------------- 2) nada mudou
WPFake.publicados.clear()
r = lp_auto.rodar(g, ont, est)
check(r["publicados"] == [] and WPFake.publicados == [], "sem mudança, tick é no-op (não chama WP)")

# ---------------------------------------------------------------- 3) mudou, não editada -> publica
ont.html["FEQ-0480"] = "<html>FEQ-0480 v2 (promoção nova)</html>"
r = lp_auto.rodar(g, ont, est)
check(r["publicados"] == ["FEQ-0480"], "conteúdo mudou -> republica sozinho")
check(WPFake.publicados == ["FEQ-0480"], "chamou WP.publicar de fato")
check(est.get_config("lp_hash_FEQ-0480") == lp_auto._hash(ont.html["FEQ-0480"]),
      "baseline avança para o novo conteúdo (não republica de novo à toa)")

WPFake.publicados.clear()
r = lp_auto.rodar(g, ont, est)
check(r["publicados"] == [], "depois de republicar, próximo tick com o mesmo conteúdo é no-op")

# ---------------------------------------------------------------- 4) editada à mão -> nunca publica
est.set_lp_html("FEQ-0331", "<html>edição manual do operador</html>", autor="operador")
# a baseline de uma edição é gravada em lp_salvar (rota), não aqui — simula esse passo:
est.set_config("lp_hash_FEQ-0331", lp_auto._hash(ont.html["FEQ-0331"]))
est.set_config("lp_drift_FEQ-0331", "0")

r = lp_auto.rodar(g, ont, est)
check("FEQ-0331" not in r["publicados"], "LP editada nunca é republicada automaticamente")
check(r["drift"] == [], "ontologia não mudou desde a edição -> sem aviso de deriva")

ont.html["FEQ-0331"] = "<html>FEQ-0331 v2 (a ontologia mudou depois da edição)</html>"
r = lp_auto.rodar(g, ont, est)
check("FEQ-0331" not in r["publicados"], "mesmo com deriva, edição continua intocada")
check(r["drift"] == ["FEQ-0331"], "mas o operador é avisado que pode estar desatualizada")
check(est.get_config("lp_drift_FEQ-0331") == "1", "flag persistida (a UI lê isso em /api/lps)")

# a flag NÃO deve se autoapagar num tick seguinte sem nova edição (o bug que o
# agente de design apontou: comparar contra "agora" faria isto piscar de volta a 0)
r2 = lp_auto.rodar(g, ont, est)
check(r2["drift"] == ["FEQ-0331"], "aviso de deriva persiste entre ticks (não se autoapaga)")

print(f"\n{ok}/{ok} passaram.")
