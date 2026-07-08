# 04 — Fase 1: Ingestão de Leads (Meta → Atena → FARO · Variante B)

**Módulo-alvo:** `atena/agentes/ingestao.py` (porta a lógica de `feq_ingestao_meta_faro.py`).
**Status:** implementado em `atena/agentes/ingestao.py` (dedupe por telefone, gate de captabilidade,
curso pelo nome do formulário); webhook assíncrono + assinatura HMAC + dedupe de leadgen + 1º contato
automático (captável) no `api/app.py`.

## Decisão (revertida em 26/06/2026)
A **Atena recebe os leads diretamente da Meta** (Variante B). Não há mais Make como ingestor. O
formulário instantâneo dispara o webhook; a Atena busca os dados via Graph API, deduplica e cria o
card no FARO em **Lead**. (Histórico: chegou-se a definir Make→FARO; revertido.)

## Convenção do formulário instantâneo (fixada 07/07/2026)
- **Nome do formulário = código do curso + versão**, ex.: `FEQ-0333 v1`. É daqui que a Atena
  resolve o curso (o formulário coleta **só Nome, Telefone e E-mail** — não há campo de curso).
- Resolução: `curso_de_texto(nome_do_form, campanha)` via regex `FEQ[-_ ]?(\d{3,4})` →
  `FEQ-XXXX`. Fallback: nome da campanha, se o form não casar.
- **Formulário qualificatório (opcional).** O form pode ter perguntas de qualificação
  condicionais (ex.: FEQ-0333 v1 pergunta ensino superior completo e disponibilidade para a
  aula presencial 01/08/2026). Só quem responde afirmativamente chega ao fim e vira lead — o
  desqualificado vira "não lead" e **não gera webhook**. Logo, **todo lead recebido já passou
  no funil do form**. `_qualificacao(field_data)`: respostas além do contato → nota na auditoria;
  se há pergunta sobre ensino superior respondida afirmativamente, grava `escolaridade=Superior
  Completo` no lead → **o gate acadêmico do agente não re-pergunta** (o agente já parte para o
  curso/inscrição). Corrige o incidente "perguntou ensino superior duas vezes".

## Requisitos funcionais
- **F1** Webhook `/webhook/meta`: handshake `GET` (verify token) + `POST` leadgen.
- **F2** `fetch_meta_lead(leadgen_id, form_id)` via Graph API com **token permanente de System
  User** (`leads_retrieval`): busca `field_data`+`campaign_name`+`form_id` do lead e o `name`
  do formulário; resolve o curso pelo nome do form.
- **F3** Deduplicar por telefone antes de criar (não duplicar → atualizar/enriquecer).
- **F4** Criar card em "Lead": Nome, Telefone, E-mail, Curso, Origem=Meta Ads, Campanha, Lead ID
  Externo, **Consentimento LGPD (do formulário)**, Data Consentimento, Opt-out=false.
- **F5** `/simulate` para testar o caminho FARO sem a Meta (mantém AC offline).
- **F6** `/health`.
- **F7** **1º contato automático**: lead captável recém-criado dispara `feq_abertura_curso`
  (template aprovado) e vai para `aguardando_resposta` — a conversa no WhatsApp continua o
  processo. Não-captável não recebe mensagem (F-gate → revisão).

## Must-fixes do webhook público (novos — endpoint exposto na internet)
- **S1 Verificar `X-Hub-Signature-256`** (HMAC-SHA256 com `META_APP_SECRET`) em todo POST. Sem isso,
  qualquer um injeta leads falsos. **Bloqueante.**
- **S2 ACK rápido + processamento assíncrono.** ✅ Implementado: `200` imediato, turno processado
  em thread; reentregas deduplicadas por `message_id` (`msgs_vistas`). Leadgen real busca os dados
  via Graph API (`fetch_meta_lead`; exige token com `leads_retrieval` — `META_PAGE_TOKEN`).
- **S3 Infra de borda:** HTTPS público (ngrok em dev; domínio/TLS em prod), `META_VERIFY_TOKEN`,
  token permanente com `leads_retrieval`.
- **S4 Mapear campos do formulário real** em `fetch_meta_lead` (`full_name`/`phone_number`/`email`);
  ✅ **curso resolvido pelo NOME DO FORMULÁRIO** (`FEQ-0333 v1`), não por campo do form.
- **S5 Dedupe de leadgen** por `leadgen:{id}` (`msgs_vistas`): a Meta reentrega o mesmo evento;
  ✅ processado uma vez só.

## Dedupe (3 camadas — responsabilidade da Atena novamente)
1. **Cache local** (telefone/lead_id, TTL 15 min) — latência de indexação + reentrega de webhook.
2. **Busca no FARO** por telefone (fonte da verdade entre sessões).
3. **Criação** + grava no cache. Em empate, fica com o card mais antigo.

## Reconciliação (rede de segurança)
Polling leve `api-cards-watch-recent` (≥1 min) detecta lead que o webhook não entregou. Idempotente
via `cards_vistos` (ver `12`).

## Gate de captabilidade no roteamento (integra com o Curador)
Ao criar o card, o roteador consulta a ontologia: se o curso **não é captável hoje** (inscrição
encerrada/futura) ou tem `alertaCatalogo`, **não** dispara o SDR — marca para revisão do humano/
Curador. Evita engajar lead para curso que não pode comprar (ex.: 0655/0360 encerrados hoje).

## Fronteira de adaptadores (extensível)
`ingestao.py` expõe `ingest(lead_normalizado)`. Cada fonte é um adaptador que normaliza e chama
`ingest`: `adaptador_meta` (agora). **Google Lead Ads = `adaptador_google` (futuro)**, sem retrabalho
no núcleo. Decisão de escopo Google: em aberto.

## Critérios de aceitação
- AC1 Dois `/simulate` mesmo telefone → 1 card (2º = `"atualizado"`).
- AC2 POST sem assinatura válida → **rejeitado** (401/403), nada criado.
- AC3 Card em "Lead" com todos os campos; `fields_skipped` vazio.
- AC4 Consentimento gravado a partir do formulário (não fixo `true`).
- AC5 Curso não-captável → card criado mas **1º contato NÃO disparado**; vai para revisão + alerta.
- AC6 Curso captável → **`feq_abertura_curso` disparado automaticamente** (template aprovado);
  respostas do lead seguem a governança normal (auto-aprovar OFF → fila de aprovação).
- AC7 Curso resolvido do nome do formulário (`FEQ-0333 v1` → `FEQ-0333`), mesmo sem campo de curso.
- AC8 Mesmo `leadgen_id` reentregue → processado uma única vez (sem 2ª mensagem).
