# 01 — Arquitetura (v2: multiagente + ancoragem ontológica)

> v2 substitui os dois Flask monolíticos por um **monólito modular** (decisão fixada): agentes como
> módulos com contrato, orquestrador como máquina de estados persistida, ontologia como substrato de
> grounding. A visão de microsserviços/AWS do SPEC original é o alvo de **escala**, não o dia 1.

## Camadas
```
Meta Lead Ads ──(webhook /webhook/meta)──► ATENA CORE (orquestrador)
                  polling watch-recent ───►   │ máquina de estados (SQLite) · delega · loop de feedback
(backstop)                                     │ executa efeitos só após Revisor + guardrails
                                               ▼
        ┌─────────── Agentes especializados ───────────┐   ┌── Revisor ontológico (fail-closed) ──┐
        │ Ingestão·SDR·Qualificação·Nutrição·Inscrição │◄─►│ claim → âncora no grafo + SHACL       │
        │ Fechamento·Curador·Bibliotecário             │   └──────────────┬───────────────────────┘
        └───────────────────┬──────────────────────────┘                  │ consulta
                            │ grounding                                    ▼
                            ▼                              ┌── Ontologia (JSON-LD + SHACL) ──┐
                  ┌── Bibliotecário/KG ──┐◄───────────────►│ catálogo · temporal · elegib.   │
                  └──────────────────────┘                 └─────────────────────────────────┘
        ┌── Governança: gate humano · audit append-only · PII/LGPD · FARO (SoR) · WhatsApp Cloud ──┐
```

## Estrutura do pacote (`atena/`)
| Módulo | Responsabilidade |
|---|---|
| `ontologia/` | Grafo + serviço de consulta (elegibilidade, captabilidade, âncoras). Ver `11`. |
| `core/orquestrador.py` | Máquina de estados, delegação, loop de feedback, orçamento de execução. |
| `core/estado.py` | SQLite: leads, conversas, decisões, tarefas, cards_vistos. Ver `12`. |
| `core/contratos.py` | I/O tipado dos agentes (dataclasses). |
| `core/guardrails.py` | Janela de horário, opt-out, tiers de risco, gate humano. |
| `agentes/*.py` | Os agentes (funções puras, sem efeito colateral). |
| `revisor/ontologico.py` | Validação fail-closed contra o grafo. |
| `transporte/whatsapp.py` | Webhook + envio, janela 24h/templates, fila de horário. |
| `api/app.py` | `/webhook/meta`, `/converse`, `/reviewer`, `/health`, `/inspecao`. |

## Roster de agentes (7 do design + 3 novos exigidos pela crítica)
1. **Ingestão & Roteamento** — Variante B: recebe Meta, deduplica, cria card, roteia por curso.
2. **SDR / Contato** — 1º contato (template); só engaja curso **captável** (gate do Curador).
3. **Qualificação** — elegibilidade ontológica (ordinal) + fit; qualifica/descarta/em espera.
4. **Nutrição / Cadência** — follow-up, reengajamento (watch-late), playbook 0480.
5. **Suporte à Inscrição** — orienta Extecamp (Fase 3).
6. **Fechamento de Matrícula** — **propõe**; aprovação humana (Fase 4).
7. **Revisor** — valida toda saída contra o grafo (fail-closed, proveniência).
8. **Curador de Catálogo** *(novo)* — recalcula captabilidade/vagas/`alertaCatalogo`; bloqueia
   engajamento de curso encerrado; escala dado a confirmar ao humano.
9. **Bibliotecário/Ontologista** *(novo)* — única porta de grounding; consulta o grafo (futuro: RAG).
10. **Auditor/Transparência** *(novo)* — escreve o trace de decisão (raciocínio + âncoras + ação).

## Contrato de agente (padrão)
Agente = função pura `(entrada) → (decisão, ações_propostas, proveniência)`, **sem efeito colateral**.
O orquestrador executa os efeitos (escritas FARO, envios) só após Revisor + guardrails. Centraliza o
gate humano e a auditoria; mantém os agentes testáveis offline.

## Tiers de risco (governança como dado — ver `07`)
| Tier | Operações | Regra |
|---|---|---|
| leitura | GET watch-*/list/get | livre, com log |
| escrita_reversivel | create/update/move no funil | autônomo sob guardrails; audita em `card_activity` |
| externa_irreversivel | envio fora de script, fechar matrícula, orçamento de ads, `delete` | **humano** |

## Padrões transversais
- **Ingestão Variante B** (ver `04`): webhook Meta + reconciliação por polling `watch-recent`.
- **Polling, não webhook de saída** do FARO (limitação v1): descobertas via `watch-*` (≥1 min).
- **Backoff exponencial** (1s,2s,4s,8s) em 5xx; **cache** de cards recém-criados (15 min) e de estrutura.
- **Idempotência**: dedupe por telefone + `Lead ID Externo`.
- **Persistência**: estado/conversa/audit em SQLite (WAL) → Postgres na VPS.

## Stack
Python 3.13 · Flask · requests · `rdflib`+`pyshacl` (ontologia) · APScheduler (cadência) ·
Claude API (`claude-sonnet-4-6` no cérebro; Revisor com prompt independente, idealmente modelo
distinto) · WhatsApp Business Cloud (transporte) · SQLite → Postgres.

## Evolução futura (não bloqueante)
RAG híbrido (BM25 + denso) para FAQs/objeções · fila (SQS/Redis) e serviços separados quando escalar.
