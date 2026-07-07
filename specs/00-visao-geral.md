# 00 — Visão Geral e Glossário

## Propósito
Sistema de captação de alunos para a Extensão FEQ Unicamp, operado por agentes de IA
orquestrados pela **Atena**. Objetivo de negócio: aumentar a conversão de leads pagos
(Meta/Google) em alunos matriculados, reduzindo o vazamento de funil — especialmente
o gargalo já observado (ex.: FEQ-0655 com 21 leads e apenas 3 inscritos).

## Princípios
1. **Spec-driven**: a spec precede o código; código sem spec é dívida.
2. **Grounding factual**: nenhum agente afirma preço/data/requisito que não esteja na
   base de conhecimento (`cursos_feq.json`). O Revisor é a trava.
3. **Governança híbrida**: leitura livre; ação externa/irreversível com aprovação.
4. **FARO como system of record**: a Atena orquestra, não duplica dados.
5. **Menor privilégio**: chaves escopadas, segredos só em ambiente, auditoria via
   `card_activity` do FARO.

## Glossário
| Termo | Definição |
|---|---|
| **Atena** | Orquestradora; aplica guardrails, delega aos agentes, audita. |
| **FARO** | CRM existente (Supabase + edge functions REST). System of record. |
| **Pipeline `[FEQ] CRM`** | Funil-alvo no FARO. ID `49d65f66-ec1d-4c10-a5ff-71aef07f9279`. |
| **Lead** | Pessoa que declarou interesse num curso. |
| **Prospect** | Lead qualificado: tem o requisito acadêmico do curso e fit. |
| **Inscrito** | Concluiu o formulário de inscrição da Extecamp. |
| **Matriculado** | Pagou taxa + enviou documentos + pagou 1º boleto de mensalidade. |
| **Revisor** | Subagente que valida cada mensagem contra a base do curso. |
| **Guardrail** | Regra que limita a autonomia do agente (horário, opt-out, escalonamento). |
| **WABA** | WhatsApp Business Account. |

## Definição dos estágios (contrato de negócio)
- **Lead**: declarou interesse em um curso.
- **Prospect**: lead com qualificação para fazer o curso (gate: ensino superior completo,
  válido para todos os 8 cursos atuais) e situação em que o curso faz sentido.
- **Inscrito**: concluiu o formulário de inscrição da Extecamp (dados pessoais, endereço,
  dados acadêmicos/profissionais, forma de pagamento).
- **Matriculado**: inscrito que pagou a taxa de inscrição, submeteu os documentos na área
  do aluno e pagou o primeiro boleto de mensalidade (emitido quando o curso é confirmado).

## Fora de escopo (v1)
- Treinar/fine-tunar modelos próprios (a Atena orquestra modelos via API).
- Garantir ausência absoluta de alucinação (o objetivo é mitigação mensurável).
- Gestão de campanhas de mídia paga (criação/orçamento) de forma autônoma.
