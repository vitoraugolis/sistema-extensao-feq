# 13 — Injetor de Conhecimento e Evolução da Ontologia

**Módulos-alvo:** `atena/agentes/ontologista.py`, `atena/ontologia/versionamento.py`, `api/app.py`.
**Status:** implementado e testado (`test_injetor` 4/4). **Tier de risco:** mudança de ontologia é reversível mas
**alto impacto** → exige **aprovação humana** + versionamento (governança, ver `07`).

## Problema
O catálogo é **dinâmico**: cursos fecham, abrem, mudam preço/data; chegam pedidos de adequação. Hoje
isso exigiria editar `catalogo.jsonld` na mão e rezar. Precisa virar um **fluxo governado**: o
operador insere informação (texto livre, formulário ou documento) e o sistema **interpreta, propõe a
mudança, calcula o impacto, valida e só aplica após aprovação** — com proveniência e rollback.

## Pipeline (5 etapas)
```
1. CAPTURA      operador submete update (texto livre | formulário | doc)        → /injetor
2. INTERPRETA   agente Ontologista (LLM) entende a intenção e mapeia ao grafo
3. PROPÕE       gera DIFF tipado (catalogo/vocabulario/shapes) + RELATÓRIO DE IMPACTO
4. VALIDA       aplica o diff em grafo de STAGING e roda validar.py (SHACL)      → bloqueia se violar
5. APROVA→APLICA  humano revisa diff+impacto → aprova → commit versionado → recarrega grafo
```
Nada entra no grafo de produção sem passar por (4) **e** (5).

## Relatório de impacto (o "pensar o que precisa mudar")
O Ontologista cruza o diff proposto com o **estado vivo do sistema** e lista consequências:
- **Captabilidade:** curso reabriu → SDR pode reengajar `em_espera`; curso fechou → pausar cadência,
  mover leads ativos para `em_espera`/revisão.
- **Leads afetados:** quantos cards em cada fase pertencem ao curso alterado.
- **Mensagens/Templates:** mudança de preço/data **invalida** afirmações já cacheadas e pode exigir
  novo template aprovado na Meta.
- **Ontologia estrutural:** curso novo precisa de `requisito/janela/plano`; gate diferente muda
  `nivelMinimo` (dado, não prompt); campo novo pode exigir shape nova.
- **Alertas:** resolve/abre `alertaCatalogo` (ex.: confirmar FEQ-0499).

## Versionamento e proveniência
- Cada mudança é uma `prov:Activity` (autor, timestamp, motivo, fonte) ligada às triplas alteradas.
- Snapshots versionados do grafo (tabela `ontologia_versoes` ou commits git) → **rollback** a qualquer
  versão. O `wasDerivedFrom` de cada curso aponta para a versão/fonte que o originou.

## Relação com o Curador de Catálogo (agente 8)
Mesmo maquinário, gatilho diferente: **Injetor = mudança iniciada por humano**; **Curador = proposta
iniciada pelo sistema** (varredura diária que detecta janela encerrada, vaga estourada, dado nulo).
Ambos produzem uma *proposta de mudança* que segue o pipeline 4→5.

## Interfaces
```
POST /injetor                 # {tipo: texto|form|doc, conteudo, autor}
GET  /injetor/proposta/{id}   # diff + relatório de impacto + resultado da validação
POST /injetor/aprovar/{id}    # aplica (commit versionado) — exige autenticação
POST /injetor/rejeitar/{id}
GET  /injetor/historico       # versões + proveniência (rollback)
```

## Critérios de aceitação
- AC1 Nenhuma mudança aplica sem `validar.py` verde **e** aprovação humana.
- AC2 Toda mudança fica versionada com autor/motivo/timestamp e é reversível.
- AC3 O relatório de impacto lista leads e fases afetados antes da aprovação.
- AC4 Curso que fecha → leads ativos não recebem mais cadência de captação (entram em revisão).
- AC5 Texto livre ambíguo → o Ontologista pede esclarecimento, não adivinha.
