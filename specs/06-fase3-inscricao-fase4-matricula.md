# 06 — Fases 3 e 4: Inscrição e Matrícula (a implementar)

## Fase 3 — Suporte à Inscrição (Prospect → Inscrito)
**Status:** especificada, não implementada.

### Objetivo
Levar o Prospect a concluir o formulário de inscrição da Extecamp.

### Requisitos
- **F1** Enviar o link de inscrição correto do curso (campo `extecamp` da base).
- **F2** Acompanhar e responder dúvidas do processo (usando a base; Revisor ativo).
- **F3** Detectar conclusão da inscrição e mover card para **Inscrito**.
- **F4** Registrar `Forma de Pagamento` quando coletada.
- **F5** Anexar comprovantes ao card via `api-cards-attach-file` (≤10 MB).

### Como detectar "Inscrito"
A Extecamp é externa; sem integração direta na v1. Opções (decisão em aberto):
(a) confirmação declarada pelo lead + validação humana; (b) o próprio lead envia comprovante
da taxa de inscrição; (c) integração futura com a Extecamp. **Default v1:** (a)+(b) com
checagem humana leve antes do move para Inscrito.

### Critérios de aceitação
- AC1 Nenhum Prospect vira Inscrito sem evidência mínima (declaração + comprovante).
- AC2 Link de inscrição enviado é o do curso correto (Revisor confere a URL).

## Fase 4 — Fechamento de Matrícula (Inscrito → Matriculado)
**Status:** especificada, não implementada. **Portão de aprovação humana.**

### Definição de Matriculado
Inscrito que: pagou a **taxa de inscrição** + submeteu **documentos** na área do aluno +
pagou o **1º boleto de mensalidade** (emitido quando o curso é confirmado).

### Requisitos
- **F1** Acompanhar os três marcos (taxa, documentos, 1º boleto).
- **F2** **Propor** a matrícula — nunca fechar sozinho. Escalonamento humano obrigatório.
- **F3** Ao confirmar (humano), mover card para **Matriculado**.
- **F4** Anexar comprovantes ao card.

### Guardrails específicos
- Pedido de **desconto/condição especial** → humano.
- Qualquer ação que envolva **pagamento** → humano (a Atena não movimenta dinheiro).

### Critérios de aceitação
- AC1 Nenhuma matrícula é marcada sem aprovação humana explícita.
- AC2 Os três marcos ficam registrados/anexados no card antes de "Matriculado".
