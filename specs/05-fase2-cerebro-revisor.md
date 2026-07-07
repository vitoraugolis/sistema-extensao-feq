# 05 — Fase 2: Cérebro + Revisor (orquestrado)

**Status:** implementado e testado (`test_orquestrador` 7/7, `test_agentes`, `test_revisor` 12/12).
Lead → Prospect/Perdido/Em espera, conduzido pelo orquestrador (`specs/12`) com Revisor ontológico.

## Componentes
- **Cérebro** `atena/agentes/cerebro.py` — Claude com o contrato `DecisaoAgente`, ancorado no
  contexto do Bibliotecário (curso + captabilidade + elegibilidade). **Sem `ANTHROPIC_API_KEY` ou em
  falha de parsing → fallback determinístico** (`orquestrador.brain_deterministico`), testável offline.
  Opt-out e curso-não-captável são decididos de forma determinística (não se confia no LLM).
- **Revisor** `atena/revisor/ontologico.py` — **fail-closed**. Passada estrutural (preço/data/carga/
  parcelas → âncoras do grafo); passada semântica (Claude) opcional, só adiciona bloqueios. Resolve os
  bugs da v1: carga ≠ hora-de-relógio (limiar 24h) e coerência de parcelas (`nº×valor ≈ à_vista`).
- **Orquestrador** `atena/core/orquestrador.py` — máquina de estados; roda o Revisor sobre a mensagem
  proposta (1 reescrita; 2º bloqueio → escala); aplica o efeito no estado; **nunca envia sozinho** — a
  mensagem aprovada vira **proposta para aprovação humana** (envio = tier externa/irreversível).

## Regra de ouro (anti-alucinação ativa)
Se o lead pede informação que **não está no grafo** (ementa, detalhe não cadastrado), o cérebro
retorna `proxima_acao=consultar_coordenacao` + `dados.duvida`. O orquestrador então: (1) envia ao
lead uma **mensagem de espera FIXA** ("vou consultar a coordenação e já retorno") — nunca redigida
pelo LLM; (2) registra a dúvida na fila **Dúvidas** da Central; (3) estado → `aguardando_coordenacao`.
Quando o humano responde na Central, o cérebro **elabora** a resposta ao lead usando a resposta da
coordenação como **fonte extra de âncoras** (`revisar(..., fonte_extra=)` — proveniência humana);
a mensagem vira proposta (ou sai direto, se aprovação automática ON). Dúvida que for **fato de
catálogo** (preço/data/carga) deve ir para o **Injetor** (atalho na própria aba), não para resposta
pontual — o dado entra na ontologia e vale para todas as conversas.

## Decisões e efeitos
`qualificar`→Prospect · `descartar`→Perdido (+motivo) · `em_espera`→Em espera · `encerrar_optout`→
opt-out · `escalar_humano`→pausa a cadência, grava motivo, entra na fila (corrige o no-op da v1).

## Critérios de aceitação (todos cobertos por teste)
- Inelegível nunca vira Prospect; curso encerrado não é engajado (escala).
- Toda mensagem passou pelo Revisor; preço/data/carga/parcelas inventados bloqueiam.
- Opt-out determinístico cessa e marca; gatilho sensível (desconto/reclamação) escala.
- Conversa e decisões persistem em SQLite (`specs/12`).
