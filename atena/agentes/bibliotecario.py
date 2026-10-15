#!/usr/bin/env python3
"""
Atena · Bibliotecário (gateway de grounding).
Única porta entre os agentes e o grafo: monta o CONTEXTO ONTOLÓGICO do cérebro.
v2 (revisão de conhecimento): além do curso do card, injeta a VISÃO DE PORTFÓLIO
(todos os cursos, com status de inscrição) e o registro completo de qualquer
curso que o lead CITAR na mensagem — o agente conhece o catálogo inteiro,
mas continua só podendo afirmar o que está no grafo.
"""
from atena.ontologia.servico import CODE_RE, normalizar_codigo


class Bibliotecario:
    def __init__(self, ont):
        self.ont = ont

    def _citados(self, mensagem, vistos, hoje=None):
        out = []
        for p, n in CODE_RE.findall(mensagem or ""):
            c = self.ont.curso(normalizar_codigo(p, n))
            if c and c["codigo"] not in vistos:
                vistos.add(c["codigo"])
                out.append({"curso": c, "captavel": self.ont.captavel(c["codigo"], hoje)})
        return out

    @staticmethod
    def _busca(mensagem, historico, n=3):
        """Mensagem atual + as `n` últimas do lead (mais recente primeiro)."""
        # ponytail: janela fixa de 3 mensagens; vira parâmetro se o prompt inchar
        do_lead = [h.get("texto") or "" for h in (historico or []) if h.get("papel") == "lead"]
        return "\n".join([mensagem or ""] + do_lead[::-1][:n])

    def contexto(self, curso, escolaridade=None, mensagem=None, hoje=None, historico=None):
        """hoje: injetável p/ teste, como em `ont.captavel` e `brain_deterministico`.
        historico: as últimas mensagens DO LEAD também contam como citação — sem isso um
        "?" ou "e o outro?" logo depois de citar o curso chegava ao cérebro sem os dados.
        A situação do curso ("aberto", "fora do prazo", "encerrado") sai do calendário,
        então teste que não fixa a data passa hoje e falha sozinho semana que vem."""
        info = self.ont.curso(curso)
        if not info:
            # SEM curso definido (ex.: lead que chegou pelo botão de WhatsApp da LP
            # sem dizer o curso) NÃO é beco sem saída: o agente atende em modo
            # DESCOBERTA — com o portfólio inteiro — para perguntar/orientar qual
            # curso interessa. Antes isso virava escalonamento silencioso.
            return {"curso": None, "existe": False, "descoberta": True,
                    "captavel": {}, "elegivel": None,
                    "portfolio": self.ont.portfolio(),
                    "cursos_citados": self._citados(self._busca(mensagem, historico), set(), hoje)}
        cap = self.ont.captavel(curso, hoje)
        eleg = self.ont.elegivel(escolaridade, curso) if escolaridade else None
        citados = self._citados(self._busca(mensagem, historico), {info["codigo"]}, hoje)

        return {
            "existe": True,
            "curso": info,
            "captavel": cap,
            "elegivel": eleg,
            # só carrega a política quando ela é aplicável: prompt não é depósito de regras
            "politica_fora_do_prazo": (self.ont.politica("politicaForaDoPrazo")
                                       if cap.get("recuperavel") else None),
            "portfolio": self.ont.portfolio(),
            "cursos_citados": citados,
        }
