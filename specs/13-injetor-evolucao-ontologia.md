# 13 — Injetor de Conhecimento e Evolução da Ontologia

**Módulos-alvo:** `atena/agentes/ontologista.py`, `atena/ontologia/versionamento.py`, `api/app.py`.
**Status:** implementado e testado (`test_injetor` 4/4). **Tier de risco:** mudança de ontologia é reversível mas
**alto impacto** → exige **aprovação humana** + versionamento (governança, ver `07`).

## Problema
O catálogo é **dinâmico**: cursos fecham, abrem, mudam preço/data; chegam pedidos de adequação. Hoje
isso exigiria editar `catalogo.jsonld` na mão e rezar. Precisa virar um **fluxo governado**: o
operador insere informação (texto livre, formulário ou documento) e o sistema **interpreta, propõe a
mudança, calcula o impacto, valida e só aplica após aprovação** — com proveniência e rollback.

## Onde o catálogo mora em produção (e o deploy que o apagava)

O Injetor **escreve** em `atena/ontologia/catalogo.jsonld` e em `atena/ontologia/versoes/`
(pre-imagens do rollback). Esses dois são **dado de produção**, não código — na mesma
categoria de `atena/data/` e `.env`.

Até 31/08/2026 o `deploy_vps.sh` não os excluía do rsync: toda mudança aprovada em
produção — com diff explícito, SHACL conforme, versionada e com pre-imagem — era desfeita
pelo deploy seguinte, **em silêncio**. Nada no processo mostrava a reversão: a proposta
continuava marcada como aprovada, o histórico continuava lá, e só a ontologia voltava
atrás. Foi descoberto ao prorrogar o FEQ-0580 e conferir o arquivo do VPS contra o local.

Agora os dois são excluídos do rsync e **semeados só no bootstrap** (`--ignore-existing`),
igual ao `.env`. Consequência de desenho, deliberada: **quem muda o catálogo é o Injetor**,
não uma edição no repositório. O arquivo versionado em git é semente de instalação nova,
não fonte da verdade — para trazer produção de volta ao repositório, copie o arquivo do
VPS (foi o que se fez com a prorrogação do FEQ-0580).

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

## Declarar novo oferecimento (✅ implementado)
Um caso especial, ESTRUTURADO em vez de texto livre: `Ontologista.propor_oferecimento()`
(`atena/agentes/ontologista.py`). Diferente de `interpretar()` (1 campo por regra, seguro
adivinhar em prosa), aqui são 5 campos atômicos — `janelaInscricao.{inicio,fim}`,
`janelaOferecimento.{inicio,fim}`, `urlExtecamp` (campo de nível raiz do curso, **não**
aninhado em `planoPagamento`) — cruzando **dois armazenamentos hoje desconectados**:
`catalogo.jsonld` (ontologia) e `cursos_metas` (`atena/growth/db.py`, turma/URL da LP).
`turma`+`url_extecamp` viajam serializados no campo `texto` da proposta (não existem no
vocabulário) até a aprovação. Reaproveita `_get`/`_set`/`_impacto`/validação SHACL de
`propor()` sem alterar `aprovar()`.

Rota `POST /api/injetor/oferecimento`; `injetor_aprovar` ganha um branch: proposta tipo
`novo_oferecimento` exige `papel=="supervisor"` (mesmo gate de `lp_publicar`, porque muda
ontologia governada E publica página pública), e após `ONTO.aprovar()` chama
`Growth.set_oferecimento(curso, turma, url)` — **primeiro writer runtime** dessas colunas,
que antes só existiam no seed hardcoded — e então `lp_auto.republicar_um(...)` (feedback
imediato, não espera o tick da hora). Sequência validar→ontologia→cursos_metas→publicar é
idempotente ponta a ponta (reclicar "Aprovar" após uma falha no meio completa o que faltou).

**URL perene**: `wp_deploy_bridge._slug()` ganha `LEGADO_TURMA`, cópia estática das turmas
já publicadas no dia desta feature (conferida ao vivo em produção). Turma igual à legada →
slug numerado de sempre (campanhas pagas atuais não quebram). Qualquer turma diferente —
todo oferecimento declarado por este fluxo — nasce no slug perene `inscricao-{curso}`, sem
número de turma: a turma seguinte cai no MESMO slug (WP já resolve por GET-then-update).

## Auto-republicação de LP por deriva (✅ implementado — spec 17)
Ver `atena/growth/lp_auto.py`: tick horário (`op_lp_auto`) compara o hash do que
`ponte.render()` produziria agora contra o hash do último tick; republica sozinho se
mudou e a LP não está editada à mão. LP editada nunca é sobrescrita — ganha uma flag de
"pode estar desatualizada" (`lp_drift_<curso>`, chip na UI de Gestão de LPs) comparando
contra a baseline gravada no MOMENTO da edição, não a cada tick (senão o aviso se
autoapagaria um tick depois da deriva real).
