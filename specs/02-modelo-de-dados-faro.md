# 02 — Modelo de Dados no FARO

Pipeline-alvo: **`[FEQ] CRM`** · ID `49d65f66-ec1d-4c10-a5ff-71aef07f9279`.
Pipelines, fases e campos são **read-only via API** — criados no painel do FARO.

## Fases (pipeline_stages) — UUIDs reais
| Funil | Fase no FARO | UUID |
|---|---|---|
| Lead | `Lead` | `25c2ebce-624f-48cc-8358-a12f16988e65` |
| Prospect | `Prospect` | `343b826f-12a6-4dd2-a9fc-04c50739a7bb` |
| Inscrito | `Inscrito` | `4fe21843-e74c-4966-b81a-bbf738e42b9a` |
| Matriculado | `Matriculado` | `52c8ca57-ab5b-4b99-ade4-be9a9fa0d18f` |
| (on-hold) | `Em espera` | `295a20bc-c84d-4159-859b-23ccea643747` |
| (perda) | `Perdido` | `5a16e79b-dd4a-471d-ae50-ddbce03ddd13` |

Leads novos entram em **Lead**. "Em espera" = nutrição/aguardando período. "Perdido" = descarte.

## Campos personalizados (pipeline_fields)
Criar no painel. A API referencia campos pelo nome (ignora maiúsculas/acentos).
| Campo | Tipo | Papel |
|---|---|---|
| Nome Completo | text | Identificação. Obrigatório. |
| Telefone | phone | Chave de dedupe + canal WhatsApp. Obrigatório. |
| E-mail | email | Canal secundário. |
| Curso de Interesse | select | Um dos 8 códigos FEQ. |
| Origem | select | Meta Ads · Google Ads · Orgânico · Indicação. |
| Campanha | text | ID/nome da campanha de origem. |
| Lead ID Externo | text | leadgen_id da Meta (idempotência). |
| Escolaridade | select | Gate acadêmico. |
| Status Qualificação | select | Não avaliado · Qualificado · Descartado. |
| Motivo Descarte | text | Registro do descarte. |
| Forma de Pagamento | select | Rumo à inscrição. |
| Consentimento LGPD | boolean | Base legal de contato. |
| Data Consentimento | date | Quando consentiu. |
| Opt-out | boolean | Se true, cessa toda comunicação. |
| Última Interação | date | Cadência/reengajamento. |

> Estado de verificação (jun/2026): a sonda mostrou **6 fases existentes e 0 campos**.
> Os campos acima precisam ser criados no painel; rode `faro_probe.py` para reconferir.

## Tipos de campo suportados pelo FARO
`text, textarea, number, currency, percentage, date, boolean, select, phone, email,
url, cpf_cnpj, rg, cep`.

## Auditoria
Toda criação/movimentação/atualização gera evento imutável em `card_activity`.
É a base da trilha de auditoria exigida pela governança — não criar log paralelo de PII.
