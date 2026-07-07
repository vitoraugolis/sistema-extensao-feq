# 03 — Integração com a API FARO (contrato)

Base URL: `https://ecinffgoycqvktmrmvaz.supabase.co/functions/v1/<funcao>`
Auth: header `Authorization: Bearer <FARO_KEY>`.
Chaves: `sk_live_` (usuário, escopo por membership) · `sk_svc_` (serviço, bypassa permissão — só backend).

## Convenções
- Sucesso: `{ "success": true, "data": {...} }` ou objeto no topo (`{ "cards": [...], "total": N }`).
- Erro: `{ "error": "...", "details": "..." }` + status HTTP.
- Paginação: `limit` (padrão 100, máx 500) + `offset`; resposta traz `total`/`returned`.
- Datas de sistema em ISO 8601 UTC; campos `date` personalizados em DD/MM/AAAA.
- Moeda: aceita pt-BR (`1.234,56`) e en-US; retorna en-US.
- Telefone: busca digits-only **bidirecional** (`?phone=11999998888` casa `+55 (11) 99999-8888`).
- Campos por nome: ignora maiúsculas/acentos; UUID também aceito.

## Endpoints usados pelo sistema
| Endpoint | Método | Uso no sistema |
|---|---|---|
| `api-pipelines-list` | GET | Sonda: lista pipelines e fases. |
| `api-pipelines-fields` | GET | Sonda: campos de um pipeline. |
| `api-cards-get` | GET | **Dedupe por telefone**; leitura de card. |
| `api-cards-create` | POST | Cria lead em "Lead". |
| `api-cards-update` | POST | Atualiza campos (status, escolaridade, opt-out, última interação). |
| `api-cards-move` | POST | Move entre fases (lote até 100). |
| `api-cards-attach-file` | POST | Anexa comprovante (≤10 MB) — Fase 3/4. |
| `api-cards-from-stage` | GET | Trabalha a fila de uma fase (nutrição). |
| `api-cards-watch-new` / `watch-recent` | GET | Descoberta de novos cards (polling). |
| `api-cards-watch-late` | GET | Cards estourando SLA → reengajar. |
| `api-cards-watch-done` | GET | Cards que entraram em fase final. |
| `api-cards-check-stage-time` | GET | Métrica de tempo em fase. |

## Corpo de exemplo — criar lead
```json
POST /api-cards-create
{
  "pipeline_id": "49d65f66-ec1d-4c10-a5ff-71aef07f9279",
  "stage_id": "25c2ebce-624f-48cc-8358-a12f16988e65",
  "title": "Nome do Lead",
  "description": "Lead Meta Ads · campanha X",
  "fields": { "Nome Completo": "...", "Telefone": "+55 ...", "Curso de Interesse": "FEQ-0480",
              "Origem": "Meta Ads", "Consentimento LGPD": "true" }
}
```

## Regras de robustez (obrigatórias)
- **Dedupe antes de criar**: `GET api-cards-get?pipeline_id=...&phone=<digits>`. Recriar gera duplicata.
- **Idempotência reforçada**: gravar `Lead ID Externo` = leadgen_id.
- **Backoff** em 5xx: 1s, 2s, 4s, 8s.
- **Cache local** (≥15 min) de cards recém-criados — a busca por telefone tem latência de
  indexação; sem o cache, ingestões em sequência duplicam (bug real já observado e corrigido).
- **Cache de estrutura**: `pipelines-list`/`pipelines-fields` mudam pouco; cachear minutos/horas.
- **Nunca** usar `api-cards-delete` em fluxo de agente — preferir mover para "Perdido".

## Limites
Anexo 10 MB · move 100/req · página máx 500 · timeout edge ~50s · polling watch-* ≥ 1 min.

## Limitações da v1 (impactam o design)
- Sem criação de pipeline/fase/campo via API (só painel).
- Sem webhook de saída (usar polling watch-*).
