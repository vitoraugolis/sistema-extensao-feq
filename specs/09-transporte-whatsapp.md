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

## Retentativa de entrega (✅ implementado — `transporte/reenvio.py`)
A Meta aceita o POST e só depois recusa a entrega, pelo webhook de status. Medido em
05/08/2026: 41 leads com falha, **29 que nunca receberam nada**. As causas pedem remédios
opostos, então `conversas.erro_codigo` guarda o código da Meta e `conversas.destino` guarda
para qual número mandamos.

| código | causa | remédio |
|---|---|---|
| 131026 | o número não recebe naquela forma (72% dos casos) | retentar na **variante do 9º dígito** — é problema de número, não de template |
| 131049 / 130472 | a Meta limita **MARKETING** para aquele usuário | esperar 24h e usar **template complementar**, preferindo UTILITY |
| demais | desconhecida | esperar 6h e trocar de template |

Regras: máx. 3 tentativas por lead; só leads elegíveis (mesmo critério da nutrição — curso
captável, sem opt-out, quem nunca respondeu); esgotada a escada, vira alerta
`entrega_impossivel` na Central em vez de o lead morrer em silêncio. **Quando a variante
entrega, o telefone do lead é corrigido no cadastro** (`_corrigir_numero`), então os próximos
contatos já vão ao número certo.

Limitação conhecida: **todo template FEQ é MARKETING** na WABA, então contra o pacing a troca
de template hoje vale pouco — quem resolve é a espera. `reenvio.UTILITY_SUGERIDO` traz o corpo
de um template UTILITY pronto para o operador criar pela UI (a criação exige aprovação da Meta).

Resultado da primeira passada em produção (05/08/2026): 12 retentativas, **10 entregues** e 10
números corrigidos; 5 leads encaminhados ao operador com motivo.

- AC6 Falha de entrega gera retentativa condicionada ao código do erro, nunca cega.
- AC7 Esgotadas as tentativas, o lead aparece para o humano com o motivo e um canal alternativo.

## Reações, contatos e emoji (✅ implementado)
`extrair_mensagem` tratava `reaction`/`contacts` como qualquer tipo desconhecido — viravam
o rótulo genérico `"[reaction]"`/`"[contacts]"`, perdendo toda a informação.

**Reação**: interceptada em `WhatsApp.receber()` **antes** de `extrair_mensagem`/
`processar_mensagem` — não é uma nova fala para o cérebro responder, é uma marca sobre uma
mensagem existente (identificada pelo `wamid` alvo). `Estado.marcar_reacao(wamid, emoji)`
anexa direto na linha existente (`conversas.reacao`), não cria linha nova; emoji vazio =
reação removida. Mesmo mecanismo nos dois sentidos — `WhatsApp.enviar_reacao` (operador →
lead) chama o mesmo `marcar_reacao` no sucesso, então o badge aparece igual em ambos.

**Contato**: `extrair_mensagem` serializa nome+telefone em JSON no campo `texto`
(`tipo="contatos"`) para a Central desenhar um cartão — mas esse JSON **nunca** pode chegar
ao cérebro como se fosse fala do lead (`Orquestrador._processar_mensagem` intercepta
`tipo=="contatos"` e manda um rótulo fixo, igual ao já feito para mídia sem legenda).
Envio usa `atena/core/contatos.py::CONTATOS_OFICIAIS` (fonte única, também usada pelo guia
em PDF — spec 08) — cartão profissional em vez de ditar telefone em texto corrido.

**Emoji**: puro front-end (grade fixa, zero dependência externa), insere na posição do
cursor em `#convInput`.

- AC8 Reação recebida nunca aciona o cérebro nem vira mensagem nova na thread.
- AC9 Conteúdo estruturado (JSON de contato) nunca vaza cru para o LLM como fala do lead.
