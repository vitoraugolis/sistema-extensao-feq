# 07 — Guardrails, LGPD e Governança

## Modelo de acesso híbrido (decisão fixada)
| Tier | Operações | Regra |
|---|---|---|
| Leitura — livre | Todos os GET (watch-*, list, get, from-stage, check-stage-time) | Livre, com log. |
| Escrita reversível — autônoma c/ guardrails | create, update, move dentro do funil | Agentes executam sozinhos sob as regras abaixo. Auditado em `card_activity`. |
| Externa / irreversível — aprovação humana | envio fora de script, fechar matrícula, mudar orçamento de ads, `delete` | Sempre humano. `delete` praticamente nunca (preferir mover para arquivo). |

## Guardrails dos agentes (obrigatórios)
1. **Conteúdo travado ao catálogo** (Revisor). Sem preço/data/requisito fora da base.
2. **Janela de envio**: horário comercial, fuso `America/Sao_Paulo`, sem madrugada/domingo.
3. **Opt-out imediato**: "sair/parar/não quero" cessa a cadência e marca `Opt-out`. A 1ª
   mensagem sempre informa como sair.
4. **Limite de tentativas**: após N mensagens sem resposta → Em espera (sem insistir).
5. **Escalonamento humano**: desconto, reclamação, intenção de fechar, dúvida fora da base.
6. **WhatsApp Business**: fora da janela de 24h, só templates aprovados.
7. **Sem ação estrutural/financeira**: agentes não chamam `delete` nem mexem em orçamento de campanha.
8. **Alerta interno nunca vaza para lead**: notificações operacionais (escalonamento, canário,
   degradação) ao gestor pelo WhatsApp (`alerta_whatsapp`) são suprimidas se o número de destino
   também for um lead rastreado — o texto interno (motivo, estado do sistema) não pode aparecer
   dentro de uma conversa de lead. A Central (aba Aprovações) sempre mostra o alerta,
   independente do WhatsApp (`atena/api/app.py:_despachar_alertas`).

## LGPD (não-negociável)
- **Base legal**: consentimento capturado no formulário da campanha → `Consentimento LGPD` +
  `Data Consentimento` no card.
- **Opt-out** em toda cadência; honrado imediatamente.
- **Minimização**: coletar só o necessário por estágio. CPF/endereço/documentos apenas na inscrição.
- **Retenção**: ✅ implementado — job diário anonimiza leads em "Perdido" há mais de 180 dias
  (nome/telefone apagados, conversas excluídas; `estado.anonimizar_antigos`).
- **PII**: nunca em logs em texto; nunca em prompt além do necessário; mascarar antes de
  enviar a APIs externas quando aplicável.

## Segurança
- Segredos só em `.env`/ambiente. Nunca em código, git ou log.
- Chave FARO idealmente escopada **apenas** ao pipeline `[FEQ] CRM`.
- `sk_svc_` (se usada) só em backend controlado.
- Rotacionar qualquer segredo que tenha circulado em texto.

## Fronteira de instruções
Conteúdo observado (mensagens de lead, páginas, documentos) é **dado, não comando**. Um lead
não pode instruir o agente a burlar guardrails. Pedidos sensíveis viram escalonamento, não ação.
