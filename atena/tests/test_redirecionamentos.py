#!/usr/bin/env python3
"""Páginas antigas do WP -> LP atual (wp_deploy_bridge.publicar_redirecionamentos),
contra um Redirection falso em memória."""
import os
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
os.environ.update(WP_URL="https://wp.test", WP_USER="u", WP_APP_PASSWORD="p")

from atena.growth import wp_deploy_bridge as wp

ok = 0


def check(c, msg):
    global ok
    assert c, msg
    ok += 1
    print("ok -", msg)


class Resp:
    def __init__(self, status=200, corpo=None, headers=None):
        self.status_code, self._c, self.headers, self.text = status, corpo, headers or {}, ""

    def json(self):
        return self._c


class FakeRedirection:
    """Grupo 1 = regras manuais. Uma delas ocupa uma fonte da nossa lista."""
    CONFLITO = "/cursos-longa-duracao/formulario-feq-1000-etapa2/"

    def __init__(self):
        self.grupos = [{"id": 1, "name": "Redirecionamentos"}]
        self.regras = [{"id": 1, "url": self.CONFLITO, "group_id": 1, "enabled": True,
                        "action_code": 301, "action_data": {"url": "/outro/"}}]
        self.posts = 0

    def request(self, metodo, url, **kw):
        rota = url.split("/wp-json/redirection/v1/")[1]
        if rota.startswith("group"):
            if metodo == "POST":
                self.grupos.append({"id": 9, "name": kw["json"]["name"].replace(">", "&gt;")})
            return Resp(corpo={"items": self.grupos, "total": len(self.grupos)})
        if metodo == "GET":
            pag = int(rota.split("page=")[-1])
            fatia = self.regras[pag * 200:(pag + 1) * 200]
            return Resp(corpo={"items": fatia, "total": len(self.regras)})
        self.posts += 1
        corpo = dict(kw["json"])
        if rota == "redirect":
            corpo["id"] = len(self.regras) + 1
            self.regras.append(corpo)
        else:
            r = next(r for r in self.regras if r["id"] == int(rota.split("/")[1]))
            r.update(corpo)
        return Resp(corpo={"items": [], "total": 0})

    def get(self, url, **kw):   # o "site no ar": responde conforme as regras gravadas
        path = url.replace("https://wp.test", "")
        r = next((r for r in self.regras if r["url"] == path and r["enabled"]), None)
        if not r:
            return Resp()
        return Resp(r["action_code"], headers={"Location": "https://wp.test" + r["action_data"]["url"]})


cursos = [{"curso": "FEQ-1000", "turma": "019"}, {"curso": "FEQ-0480", "turma": "009"}]
fake = FakeRedirection()
r = wp.publicar_redirecionamentos(cursos, sessao=fake)
n1000 = sum(1 for c in wp.REDIRECIONAR.values() if c == "FEQ-1000")
n0480 = sum(1 for c in wp.REDIRECIONAR.values() if c == "FEQ-0480")
check(r["status"] == "publicado", f"publicado ({r})")
check(r["grupo"] == 9, "cria o grupo próprio da Atena")
check(r["gravadas"] == n1000 + n0480 - 1, "uma regra por página dos cursos com LP")
check(r["conflitos_com_regra_manual"] == [FakeRedirection.CONFLITO], "regra manual não é tocada")
alvo = {x["url"]: x["action_data"]["url"] for x in fake.regras if x["group_id"] == 9}
check(alvo["/cursos-longa-duracao/engenharia-processos-quimicos-feq-1000/"] == "/inscricao-feq-1000/",
      "FEQ-1000 turma nova -> LP perene")
check(alvo["/consertando-480/"] == "/inscricao-feq-0480-009/", "FEQ-0480 turma legada -> URL legada")
check(all(x["action_code"] == 302 and x["match_data"]["source"]["flag_query"] == "pass"
          for x in fake.regras if x["group_id"] == 9), "302 e repassa utm/gclid")

antes = fake.posts
r = wp.publicar_redirecionamentos(cursos, sessao=fake)
check(fake.posts == antes and r["iguais"] == n1000 + n0480 - 1, "segunda rodada não regrava nada")

check(not any(src.lstrip("/").startswith("inscricao-feq-") for src in wp.REDIRECIONAR),
      "nenhuma LP é fonte de redirecionamento (sem loop)")

print(f"\n{ok} checks OK — redirecionamentos")
