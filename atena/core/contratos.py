#!/usr/bin/env python3
"""
Atena · Contratos dos agentes (dataclasses tipadas, stdlib).
Mantém o I/O dos agentes explícito e serializável, sem dependência externa.
(A spec 12 mencionava Pydantic; optou-se por dataclasses para zero-dependência.)
"""
from dataclasses import dataclass, field
from typing import Optional, List, Dict

# Ações possíveis (saída do cérebro)
CONTINUAR = "continuar"
QUALIFICAR = "qualificar"
DESCARTAR = "descartar"
EM_ESPERA = "em_espera"
ESCALAR = "escalar_humano"
OPTOUT = "encerrar_optout"
CONSULTAR = "consultar_coordenacao"   # regra de ouro anti-alucinação
ACOES = {CONTINUAR, QUALIFICAR, DESCARTAR, EM_ESPERA, ESCALAR, OPTOUT, CONSULTAR}

# Mapeamentos ação -> efeito no FARO/estado
FASE_POR_ACAO = {QUALIFICAR: "Prospect", DESCARTAR: "Perdido", EM_ESPERA: "Em espera"}
ESTADO_POR_ACAO = {
    QUALIFICAR: "qualificado", DESCARTAR: "descartado", EM_ESPERA: "em_espera",
    ESCALAR: "escalado_humano", OPTOUT: "encerrado", CONTINUAR: "aguardando_resposta",
    CONSULTAR: "aguardando_coordenacao",
}


@dataclass
class Turno:
    papel: str   # "lead" | "agente"
    texto: str


@dataclass
class EntradaAgente:
    card_id: str
    curso: str
    estado: str
    mensagem_lead: Optional[str]
    historico: List[Turno] = field(default_factory=list)
    contexto_ontologico: Dict = field(default_factory=dict)


@dataclass
class DecisaoAgente:
    proxima_acao: str
    motivo: str = ""
    mensagem_ao_lead: Optional[str] = None
    escalonamento_motivo: Optional[str] = None
    dados: Dict = field(default_factory=dict)
    proveniencia: List[str] = field(default_factory=list)
