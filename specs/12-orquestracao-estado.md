# 12 — Orquestração e Estado (Atena Core)

**Módulos:** `atena/core/{orquestrador.py, estado.py, contratos.py, guardrails.py}`.
**Status:** implementado e testado (`test_orquestrador` 7/7). **Store:** SQLite (WAL) → Postgres na VPS.

## Princípio
O orquestrador é o **regente**: recebe um evento (lead novo, mensagem do lead, tick do scheduler),
seleciona o agente, recebe uma **decisão proposta**, passa pelo **Revisor + guardrails**, e só então
**executa os efeitos** (escritas no FARO, envio WhatsApp). Agentes não têm efeito colateral.

## Máquina de estados da conversa (≠ fases do FARO)
| Estado | Ação | Saída |
|---|---|---|
| `aguardando_1o_contato` | template de abertura (se curso **captável**) | → `aguardando_resposta` |
| `aguardando_resposta` | espera 24h; fora da janela, só template | respondeu → `qualificacao`; silêncio N× → `em_espera` |
| `qualificacao` | confirma curso, checa elegibilidade ontológica + disponibilidade | elegível → `qualificado`; inelegível → `descartado`; fora do período → `em_espera` |
| `qualificado` | atualiza FARO + passa à inscrição | FARO → Prospect |
| `descartado` | registra motivo | FARO → Perdido |
| `em_espera` | reengaja no período (Curador/Nutrição) | volta a `aguardando_resposta` |
| `escalado_humano` | **pausa o agente**, notifica humano, aguarda resolução | humano resolve → estado-alvo |

Transversais (qualquer ponto): **opt-out** (determinístico, regex) → cessa + marca; **escalonamento**
(desconto, reclamação, intenção de fechar, dúvida fora da base) → `escalado_humano`.

## Escalonamento humano (corrige o no-op da v1)
`escalar_humano` **não** é mais um update silencioso. O orquestrador: (1) seta estado
`escalado_humano` e **pausa a cadência**; (2) grava `Motivo Escalonamento` no card + flag; (3)
**notifica o humano** (canal a definir: e-mail/WhatsApp interno/fila de revisão); (4) só retoma após
resolução. Nada é enviado ao lead nesse estado sem aprovação.

## Schema SQLite (núcleo)
```sql
leads(card_id PK, telefone, curso, estado_conversa, escolaridade, optout INT, criado, atualizado)
conversas(id PK, card_id FK, papel TEXT, texto TEXT, ts)         -- histórico multi-dia (resolve v1)
decisoes(id PK, card_id FK, agente, acao, motivo, ancoras_json,  -- trace/transparência (/inspecao)
         aprovado_revisor INT, bloqueios_json, ts)
tarefas(id PK, card_id FK, tipo, agendado_para, status)          -- cadência/nutrição (scheduler)
cards_vistos(card_id PK, visto_em)                               -- idempotência da descoberta
```

## Contratos de agente (`contratos.py`, dataclasses tipadas — zero-dependência)
```
EntradaAgente { card_id, curso, estado, historico:[Turno], mensagem_lead?, contexto_ontologico }
DecisaoAgente { mensagem_ao_lead?, proxima_acao, motivo, escalonamento_motivo?, dados, proveniencia:[Ancora] }
proxima_acao ∈ {continuar, qualificar, descartar, em_espera, escalar_humano, encerrar_optout}
```
`contexto_ontologico` é injetado pelo Bibliotecário: registro do curso + captabilidade + elegibilidade
já computadas — o agente **não** consulta o grafo direto.

## Loop de feedback fechado + orçamento de execução
- Revisor bloqueou → devolve ao agente com os motivos; **máx. 1 reescrita**; persistir 2º bloqueio →
  `escalado_humano` (não insistir).
- **Orçamento por tarefa**: limites de tokens/tempo/reescritas para impedir loop infinito.

## Disparo (event-driven + scheduler)
- **Webhook Meta** → Ingestão → cria card → enfileira `aguardando_1o_contato`.
- **Webhook WhatsApp** (entrada) → carrega conversa por telefone → orquestrador.
- **Scheduler (APScheduler)**: tick de nutrição (`watch-late`), reconciliação (`watch-recent`),
  Curador diário (recalcula captabilidade).

## Critérios de aceitação
- AC1 Conversa persiste entre mensagens (reabrir por telefone reconstrói o histórico).
- AC2 `escalar_humano` pausa a cadência e gera notificação — nunca é só log.
- AC3 Nenhum efeito externo ocorre sem Revisor aprovado + guardrail de horário.
- AC4 Reescrita limitada a 1; 2º bloqueio escala, não insiste.
- AC5 `/inspecao?card_id=` devolve o trace de decisões com âncoras (transparência).
