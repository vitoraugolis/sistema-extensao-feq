# Conector de Ingestão — Meta Lead Ads → FARO CRM (Fase 1)

Ponte que recebe leads de formulários instantâneos da Meta e cria o card no FARO,
na fase **Lead** do pipeline `[FEQ] CRM`, sem duplicar e já com consentimento LGPD.

---

## Passo 1 — Criar os campos no painel do FARO (sua ação)

A API do FARO **não cria campos** — só o painel. Em *Configurações do pipeline `[FEQ] CRM` → Campos*, crie exatamente estes (o nome importa; o conector referencia por nome):

| Campo | Tipo | Obrigatório | Opções (se select) |
|---|---|---|---|
| Nome Completo | text | sim | — |
| Telefone | phone | sim | — |
| E-mail | email | não | — |
| Curso de Interesse | select | não | FEQ-0360, FEQ-0480, FEQ-1000, FEQ-0499, FEQ-0333, FEQ-0580, FEQ-0331, FEQ-0655 |
| Origem | select | não | Meta Ads, Google Ads, Orgânico, Indicação |
| Campanha | text | não | — |
| Lead ID Externo | text | não | — |
| Escolaridade | select | não | Ensino Médio Completo, Superior Incompleto, Superior Completo, Pós-graduação |
| Status Qualificação | select | não | Não avaliado, Qualificado, Descartado |
| Motivo Descarte | text | não | — |
| Consentimento LGPD | boolean | não | — |
| Data Consentimento | date | não | — |
| Opt-out | boolean | não | — |
| Última Interação | date | não | — |

> Pode criar só os primeiros (Nome, Telefone, E-mail, Curso, Origem, Campanha, Lead ID Externo,
> Consentimento LGPD, Data Consentimento, Opt-out, Última Interação) para testar a ingestão.
> Escolaridade / Status Qualificação / Motivo Descarte entram com o agente de qualificação (Fase 2).

---

## Passo 2 — Mapa de fases (UUIDs reais, já no código)

| Funil | Fase no FARO | UUID |
|---|---|---|
| Lead | Lead | 25c2ebce-624f-48cc-8358-a12f16988e65 |
| Prospect | Prospect | 343b826f-12a6-4dd2-a9fc-04c50739a7bb |
| Inscrito | Inscrito | 4fe21843-e74c-4966-b81a-bbf738e42b9a |
| Matriculado | Matriculado | 52c8ca57-ab5b-4b99-ade4-be9a9fa0d18f |
| (on-hold) | Em espera | 295a20bc-c84d-4159-859b-23ccea643747 |
| (perda) | Perdido | 5a16e79b-dd4a-471d-ae50-ddbce03ddd13 |

O conector cria todo lead novo em **Lead**.

---

## Passo 3 — Rodar e testar (sem a Meta ainda)

```bash
pip install flask requests
cp config.example.env .env        # preencha FARO_KEY
set -a && source .env && set +a
python3 feq_ingestao_meta_faro.py
```

Em outro terminal, simule um lead e veja o card aparecer no FARO:

```bash
curl -X POST localhost:8080/simulate -H 'Content-Type: application/json' \
  -d '{"nome":"Maria Teste","telefone":"+55 (19) 99999-0001",
       "email":"maria@ex.com","curso":"FEQ-0480","campanha":"FEQ_480_TecnicasDeIA"}'
```

Rode o **mesmo comando duas vezes**: na segunda o conector responde `"acao":"atualizado"`
(não duplica) — é a deduplicação por telefone funcionando.

---

## Passo 4 — Plugar a Meta (quando o formulário estiver pronto)

1. No app da Meta, configure o webhook apontando para `https://SEU_DOMINIO/webhook/meta`
   (precisa de HTTPS público — em dev, use um túnel como o ngrok).
2. Use o mesmo `META_VERIFY_TOKEN` do `.env` no handshake.
3. Gere um **Page Access Token** e coloque em `META_PAGE_TOKEN` — é o que permite buscar
   os dados do lead via Graph API.
4. Ajuste, em `fetch_meta_lead()`, os nomes dos campos do SEU formulário (ex.: `full_name`,
   `phone_number`, `curso`) caso sejam diferentes.

---

## O que este conector NÃO faz (de propósito)

- **Não envia mensagem** a ninguém. Contato e qualificação são dos próximos agentes,
  com os guardrails do documento de arquitetura.
- **Não cria/edita estrutura** do FARO (fases/campos) — isso é só no painel.
- **Não fecha matrícula** nem mexe em orçamento de campanha — ações de aprovação humana.

## Segurança

- `FARO_KEY` só no `.env` (que não deve ir para git). Rotacione se vazar.
- Idealmente, use uma chave cujo dono seja membro **apenas** do `[FEQ] CRM`
  (a chave atual enxerga vários outros pipelines).
