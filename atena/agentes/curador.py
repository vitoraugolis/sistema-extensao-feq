#!/usr/bin/env python3
"""
Atena · Curador de Catálogo. Varre cursos e sinaliza o que precisa de ação:
curso encerrado com leads ativos, e alertaCatalogo (ex.: FEQ-0499 a confirmar).
Gatilho manual via endpoint (cron, se quiser cadência).
ponytail: sem scheduler residente — cron o endpoint /api/curador; sem auto-proposta
(o humano usa o Injetor). Adicionar quando houver volume.
"""
import re


class Curador:
    def __init__(self, estado, ont):
        self.est = estado
        self.ont = ont

    def varrer(self):
        ativos = {}
        for l in self.est.listar_leads():
            if l["fase"] in ("Lead", "Prospect"):
                cod = re.sub(r"\D", "", l["curso"] or "")
                ativos[cod] = ativos.get(cod, 0) + 1
        alertas = []
        for c in self.ont.cursos():
            cap = self.ont.captavel(c)
            info = self.ont.curso(c)
            n = ativos.get(re.sub(r"\D", "", c), 0)
            if not cap.get("captavel") and n:
                alertas.append({"curso": c, "tipo": "captabilidade",
                                "msg": f"{c} {cap['status']} com {n} lead(s) ativo(s) — pausar cadência."})
            if info.get("alerta"):
                alertas.append({"curso": c, "tipo": "alerta_catalogo", "msg": info["alerta"]})
        return alertas
