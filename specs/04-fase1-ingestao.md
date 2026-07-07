# 04 — Fase 1: Ingestão de Leads (Meta → Atena → FARO · Variante B)

**Módulo-alvo:** `atena/agentes/ingestao.py` (porta a lógica de `feq_ingestao_meta_faro.py`).
**Status:** implementado em `atena/agentes/ingestao.py` (dedupe por sufixo de telefone, gate de captabilidade); webhook + assinatura HMAC no `api/app.py`.

## Decisão (revertida em 26/06/2026)
A **Atena recebe os leads diretamente da Meta** (Variante B). Não há mais Make como ingestor. O
formulário instantâneo dispara o webhook; a Atena busca os dados via Graph API, deduplica e cria o
card no FARO em **Lead**. (Histórico: chegou-se a definir Make→FARO; revertido.)

## Requisitos funcionais
- **F1** Webhook `/webhook/meta`: handshake `GET` (verify token) + `POST` leadgen.
- **F2** `fetch_meta_lead(leadgen_id)` via Graph API com **token permanente de System User**.
- **F3** Deduplicar por telefone antes de criar (não duplicar → atualizar/enriquecer).
- **F4** Criar card em "Lead": Nome, Telefone, E-mail, Curso, Origem=Meta Ads, Campanha, Lead ID
  Externo, **Consentimento LGPD (do formulário)**, Data Consentimento, Opt-out=false.
- **F5** `/simulate` para testar o caminho FARO sem a Meta (mantém AC offline).
- **F6** `/health`.

## Must-fixes do webhook público (novos — endpoint exposto na internet)
- **S1 Verificar `X-Hub-Signature-256`** (HMAC-SHA256 com `META_APP_SECRET`) em todo POST. Sem isso,
  qualquer um injeta leads falsos. **Bloqueante.**
- **S2 ACK rápido + processamento assíncrono.** Responder `200` imediatamente e processar fora do
  request (fila SQLite). A Meta reentrega se demorar → sem isso há timeout e duplicata.
- **S3 Infra de borda:** HTTPS público (ngrok em dev; domínio/TLS em prod), `META_VERIFY_TOKEN`,
  token permanente com `leads_retrieval`.
- **S4 Mapear campos do formulário real** em `fetch_meta_lead` (`full_name`/`phone_number`/`curso`…).

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
- AC5 Curso não-captável → card criado mas **SDR não disparado**; vai para revisão.
- AC6 Nenhuma mensagem ao lead nesta fase.
