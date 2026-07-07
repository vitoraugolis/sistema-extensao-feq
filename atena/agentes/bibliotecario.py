#!/usr/bin/env python3
"""
Atena · Bibliotecário (gateway de grounding).
Única porta entre os agentes e o grafo: monta o CONTEXTO ONTOLÓGICO do cérebro.
v2 (revisão de conhecimento): além do curso do card, injeta a VISÃO DE PORTFÓLIO
(todos os cursos, com status de inscrição) e o registro completo de qualquer
curso que o lead CITAR na mensagem — o agente conhece o catálogo inteiro,
mas continua só podendo afirmar o que está no grafo.
"""
import re

CODE_RE = re.compile(r"FEQ[-\s]?(\d{3,4})", re.I)


class Bibliotecario:
    def __init__(self, ont):
        self.ont = ont

    def contexto(self, curso, escolaridade=None, mensagem=None):
        info = self.ont.curso(curso)
        if not info:
            return {"curso": curso, "existe": False}
        cap = self.ont.captavel(curso)
        eleg = self.ont.elegivel(escolaridade, curso) if escolaridade else None

        citados = []
        vistos = {info["codigo"]}
        for m in CODE_RE.findall(mensagem or ""):
            c = self.ont.curso(f"FEQ-{m}")
            if c and c["codigo"] not in vistos:
                vistos.add(c["codigo"])
                citados.append({"curso": c, "captavel": self.ont.captavel(c["codigo"])})

        return {
            "existe": True,
            "curso": info,
            "captavel": cap,
            "elegivel": eleg,
            "portfolio": self.ont.portfolio(),
            "cursos_citados": citados,
        }
