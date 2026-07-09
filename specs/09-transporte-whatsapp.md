# 09 — Transporte WhatsApp Business Cloud (a construir)

**Status:** implementado em `atena/transporte/whatsapp.py` (webhook→orquestrador; envio dry-run sem `WHATSAPP_TOKEN`, fila fora do horário). Envio real depende do token.

## Credenciais (env)
```
WHATSAPP_TOKEN=...            # token permanente (System User) — não expira até revogar
PHONE_NUMBER_ID=991579807382963
META_VERIFY_TOKEN=...         # handshake do webhook
```
Geração do token permanente: Business Settings → Usuários do sistema → Adicionar (Admin) →
atribuir o app (Controle total) → Gerar token com `whatsapp_business_messaging` +
`whatsapp_business_management`.

## Endpoints da Graph API
- Enviar mensagem: `POST https://graph.facebook.com/v19.0/<PHONE_NUMBER_ID>/messages`.
- Webhook de entrada: seu endpoint público (HTTPS) que a Meta chama com mensagens recebidas.

## Requisitos
- **F1** Webhook de entrada (`GET` handshake + `POST` mensagens) → encaminha ao `/converse`.
- **F2** Envio de texto dentro da janela de 24h; fora dela, **template aprovado**.
- **F3** Aplicar guardrail de **janela de horário** antes de enviar (fila se fora do horário).
- **F4** Mapear telefone ↔ card (dedupe por telefone no FARO).
- **F5** Registrar cada troca em `Última Interação`.
- **F6** Respeitar opt-out (não enviar a quem tem `Opt-out=true`).

## Templates (já criados pelo usuário — confirmar nomes/variáveis)
| Nome | Uso | Variáveis |
|---|---|---|
| `feq_abertura_curso` | 1º contato | {{1}} nome, {{2}} curso |
| `feq_reengajamento` | follow-up fora de 24h | {{1}} nome, {{2}} curso, {{3}} data |
| `feq_lembrete_inscricao` | prazo se aproximando | {{1}} nome, {{2}} curso, {{3}} data |

> As variáveis de data nos templates devem passar pelo Revisor antes do envio.

## Arquitetura sugerida
```
WhatsApp ──► webhook (novo serviço :8091) ──► POST /converse (:8090)
                                   │
                                   ▼
                            envio via Graph API (com guardrail de horário + templates)
```
O cérebro decide a mensagem; o transporte só entrega. Manter as camadas separadas.

## Conteúdo recebido (✅ implementado)
`whatsapp.extrair_mensagem` normaliza TODO tipo de mensagem do lead — texto, **cliques de
botão** de template (feq_lead: `button`/`interactive`) e **mídias** (`image/audio/video/
document/sticker`). Mídia: a URL da Meta expira em ~5 min e exige token, então `_baixar_midia`
baixa os bytes na hora para `data/media/<media_id>` e registra o mime (`midias`). A conversa
guarda `tipo`+`media_id` (colunas novas em `conversas`). `GET /api/media/<id>` serve o arquivo
(auth-guarded, path-safe) e a Central renderiza inline (img/audio/video/link). Mídia sem legenda
→ o cérebro recebe um rótulo (`[o lead enviou um áudio]`) p/ não misfire. Texto do lead é
escapado na UI (evita XSS). LGPD: `anonimizar_antigos` deve apagar as mídias do lead (TODO).

## Critérios de aceitação
- AC1 Mensagem só sai após aprovação do Revisor.
- AC2 Fora da janela de 24h, usa template; dentro, texto livre.
- AC3 Fora do horário comercial, a mensagem é enfileirada, não enviada.
- AC4 Lead com `Opt-out=true` nunca recebe mensagem.
- AC5 Nenhum envio em massa sem o fluxo de aprovação definido na governança.
