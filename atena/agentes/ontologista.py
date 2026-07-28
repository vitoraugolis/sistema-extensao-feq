#!/usr/bin/env python3
"""
Atena · Agente Ontologista (Injetor de conhecimento) — spec 13.
Interpreta uma atualização em linguagem natural, propõe um DIFF tipado no
catálogo + RELATÓRIO DE IMPACTO, valida em STAGING (SHACL) e — após aprovação
humana — aplica com versionamento. Determinístico e offline para os casos
comuns (reabrir inscrição, mudar carga, mudar preço à vista).
"""
import re
import sys
import json
import time
import copy
import tempfile
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from atena.ontologia.servico import Ontologia
from atena.ontologia import validacao
from atena.ontologia.versionamento import Versionamento

ONT_DIR = pathlib.Path(__file__).resolve().parents[1] / "ontologia"
CODE_RE = re.compile(r"FEQ[-\s]?(\d{3,4})", re.I)
DATE_RE = re.compile(r"(\d{2})/(\d{2})/(\d{4})")


def _iso(texto):
    m = DATE_RE.search(texto or "")
    return f"{m.group(3)}-{m.group(2)}-{m.group(1)}" if m else None


def _dec(s):
    return s.replace(".", "").replace(",", ".")


def _get(course, path):
    cur = course
    for k in path.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(k)
    return cur


def _set(course, path, val):
    ks = path.split(".")
    cur = course
    for k in ks[:-1]:
        if k not in cur or not isinstance(cur[k], dict):
            cur[k] = {"@type": "feq:Duracao"} if k == "cargaHoraria" else {}
        cur = cur[k]
    cur[ks[-1]] = val


class Ontologista:
    def __init__(self, estado, catalogo_path=None, vocab_path=None):
        self.est = estado
        self.catalogo_path = pathlib.Path(catalogo_path or ONT_DIR / "catalogo.jsonld")
        self.vocab_path = pathlib.Path(vocab_path or ONT_DIR / "vocabulario.ttl")
        self.ver = Versionamento(self.catalogo_path)
        self.ont = Ontologia(self.catalogo_path, self.vocab_path)

    # ------------------------------------------------------------- interpretação
    def interpretar(self, texto):
        m = CODE_RE.search(texto or "")
        if not m:
            return None, [], "indefinido"
        code = f"FEQ-{m.group(1)}"
        diffs, tipos = [], []
        t = (texto or "").lower()
        # Trabalha SEM o código do curso: seus dígitos (ex.: 0333) contaminavam os
        # números do dado e quebravam o parsing de carga/preço.
        tc = CODE_RE.sub(" ", t)
        d = _iso(texto)
        # inscrição: reabrir/prorrogar/estender/nova data até DD/MM/AAAA
        if d and "inscri" in t and re.search(r"(reabr|abriu|reabriu|prorrog|estend|at[ée]|nov[oa] (data|prazo))", t):
            diffs.append(("janelaInscricao.fim", d))
            tipos.append("reabrir_inscricao")
        # carga horária: "120h" ou "120 horas", em qualquer posição, quando o tema é carga
        if "carga" in tc or "hora" in tc:
            mc = re.search(r"(\d{2,4})\s*h(?:oras?)?\b", tc)
            if mc:
                diffs.append(("cargaHoraria.horas", int(mc.group(1))))
                tipos.append("carga")
        # preço à vista: valor em R$ perto de "à vista" (antes OU depois — janela larga)
        if "vista" in tc:
            mv = re.search(r"vista\D*?([\d.]+,\d{2})", tc) or re.search(r"([\d.]+,\d{2})\D{0,10}vista", tc)
            if mv:
                diffs.append(("planoPagamento.aVista", _dec(mv.group(1))))
                tipos.append("preco")
        # parcelas: "20x de R$ 892,81" / "em 20 vezes de R$ 892,81" / "20 parcelas de ..."
        # (a trava SHACL exige numParcelas × valorParcela ≈ aVista, então preço só passa
        #  quando o operador informa o plano coerente — parcelas E à vista.)
        mp = re.search(r"(\d{1,3})\s*(?:x|vezes|parcelas?)\D*?([\d.]+,\d{2})", tc)
        if mp:
            diffs.append(("planoPagamento.numParcelas", int(mp.group(1))))
            diffs.append(("planoPagamento.valorParcela", _dec(mp.group(2))))
            if "preco" not in tipos:
                tipos.append("preco")
        return code, diffs, ("+".join(dict.fromkeys(tipos)) or "indefinido")

    # ------------------------------------------------------------------- helpers
    def _carregar(self):
        return json.loads(self.catalogo_path.read_text(encoding="utf-8"))

    def _find(self, cat, code):
        alvo = re.sub(r"\D", "", code)
        for c in cat.get("@graph", []):
            if re.sub(r"\D", "", c.get("codigo", "")) == alvo:
                return c
        return None

    def _captavel_em(self, cat2, code):
        with tempfile.NamedTemporaryFile("w", suffix=".jsonld", delete=False, encoding="utf-8") as f:
            json.dump(cat2, f, ensure_ascii=False)
            tmp = f.name
        try:
            return Ontologia(tmp, self.vocab_path).captavel(code)
        finally:
            pathlib.Path(tmp).unlink(missing_ok=True)

    def _leads(self, code, fases):
        return sum(1 for l in self.est.listar_leads()
                   if re.sub(r"\D", "", l["curso"] or "") == re.sub(r"\D", "", code) and l["fase"] in fases)

    def _impacto(self, code, tipo, cat2):
        imp = []
        antes = self.ont.captavel(code)
        depois = self._captavel_em(cat2, code)
        if antes.get("status") != depois.get("status"):
            imp.append(f"Captabilidade: {antes.get('status')} → {depois.get('status')}.")
        if depois.get("captavel") and not antes.get("captavel"):
            imp.append(f"{self._leads(code, ['Em espera'])} lead(s) em espera podem ser reengajados.")
        if antes.get("captavel") and not depois.get("captavel"):
            imp.append(f"{self._leads(code, ['Lead', 'Prospect'])} lead(s) ativos: pausar cadência e revisar.")
        if "preco" in tipo or "carga" in tipo:
            imp.append("Afirmações cacheadas (preço/carga) podem ficar desatualizadas — revisar mensagens/templates.")
        if "reabrir" in tipo or "preco" in tipo:
            imp.append("Pode exigir reaprovação de template na Meta.")
        if not imp:
            imp.append("Sem impacto operacional além da atualização do dado.")
        return imp

    # -------------------------------------------------------------------- propor
    def propor(self, texto, autor="operador"):
        pid = f"prop-{int(time.time() * 1000)}"
        code, raw, tipo = self.interpretar(texto)
        if not code or not raw:
            self.est.add_proposta(pid, autor, texto, code or "?", "indefinido", [],
                                  ["Não consegui mapear a atualização. Informe o curso (FEQ-XXXX) e o que muda (data de inscrição, carga ou preço à vista)."],
                                  {"ok": False, "mensagem": "interpretação ambígua"}, status="esclarecer")
            return self.est.get_proposta(pid)
        cat = self._carregar()
        course = self._find(cat, code)
        if not course:
            self.est.add_proposta(pid, autor, texto, code, tipo, [],
                                  [f"Curso {code} não está no catálogo."],
                                  {"ok": False, "mensagem": "curso inexistente"}, status="invalida")
            return self.est.get_proposta(pid)
        diff = []
        cat2 = copy.deepcopy(cat)
        course2 = self._find(cat2, code)
        for path, para in raw:
            diff.append({"campo": path, "de": _get(course, path), "para": para})
            _set(course2, path, para)
        data_graph = validacao.grafo_de_catalogo(cat2, self.vocab_path)
        ok, txt = validacao.validar(data_graph, shapes_path=ONT_DIR / "shapes.ttl")
        impacto = self._impacto(code, tipo, cat2)
        self.est.add_proposta(pid, autor, texto, code, tipo, diff, impacto,
                              {"ok": bool(ok), "mensagem": "SHACL conforme" if ok else txt[:300]},
                              status="proposta" if ok else "invalida")
        return self.est.get_proposta(pid)

    # ------------------------------------------------------------------- aprovar
    def aprovar(self, pid, autor="supervisor"):
        p = self.est.get_proposta(pid)
        if not p or p["status"] != "proposta":
            return {"ok": False, "erro": "proposta inexistente ou não aprovável"}
        if not p["validacao"]["ok"]:
            return {"ok": False, "erro": "proposta não passou na validação SHACL"}
        cat = self._carregar()
        course = self._find(cat, p["alvo"])
        for d in p["diff"]:
            _set(course, d["campo"], d["para"])
        entry = self.ver.aplicar(json.dumps(cat, ensure_ascii=False, indent=2), autor,
                                 f"Injetor: {p['tipo']} em {p['alvo']}")
        self.ont = Ontologia(self.catalogo_path, self.vocab_path)  # recarrega grafo
        self.est.set_proposta_status(pid, "aprovada")
        return {"ok": True, "versao": entry}

    def rejeitar(self, pid):
        self.est.set_proposta_status(pid, "rejeitada")
        return {"ok": True}
