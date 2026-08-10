# 19 — Funil comercial de professores (módulo restrito)

**Status:** implementado (`atena/core/estado.py`, `atena/core/auth.py`, `atena/api/app.py`,
seção `#professores` da Central).

## Papel
Prospecção de **docentes** para novos cursos — não de alunos. É trabalho comercial manual
do master: ele descobre professores, conversa, propõe e negocia. Nada aqui é automático.

**Por que tabela separada de `leads`:** professor não é lead de curso. Se compartilhassem
a tabela, todas as regras que valem para lead — captabilidade, gate acadêmico, nutrição,
régua de recuperação, opt-out/LGPD, Revisor, agente — passariam a valer para professores,
que não fazem parte de nenhuma delas. `professores` é uma tabela própria e o funil não se
cruza com o de captação em ponto nenhum.

## Fases (ordem das colunas)
`Lead · Aquecimento · Proposta enviada · Em negociação · Aceito · Recusado · Revisitar · Inviável`

Fonte única: `estado.FASES_PROFESSOR`. A API devolve a lista e a Central desenha a partir
dela — a ordem não é duplicada no front.

## Campos (todos manuais)
`nome` (obrigatório) · `telefone` · `email` · `instituto` · `cursos` (texto livre, vários
separados por vírgula) · `anotacoes` · `ultimo_contato` · `proposta` · `servicos` · `fase`.

`cursos` é texto livre, **não** vinculado à ontologia: a prospecção inclui professores de
fora da FEQ, para cursos que muitas vezes ainda nem existem no catálogo — amarrar ao
`Ontologia.cursos()` impediria cadastrar exatamente o caso mais comum deste funil.

`telefone` é normalizado para E.164 na gravação — é o que faz o link `wa.me` abrir.
`set_professor`/`add_professor` filtram por lista branca de colunas: campo desconhecido
vindo da API é ignorado, nunca vira coluna.

## Contato: WhatsApp pessoal, não o WABA
O botão "Conversar" abre `https://wa.me/<E.164>` numa aba nova. É **deliberadamente** fora
da API oficial: quem fala com o professor é o master, pelo WhatsApp pessoal dele. Por isso
nada nesta tela passa por Revisor, janela de 24h, template aprovado ou opt-out — esses
guardrails existem para o envio automatizado a leads, e aqui não há envio automatizado.

## Acesso (o ponto do módulo)
| conceito | regra |
|---|---|
| **Master** | usuário de `ATENA_MASTER` (padrão `vitor`). Único que concede e revoga. |
| **Concessão** | nominal, por usuário, na tabela `modulo_acesso` (guarda quem concedeu). |
| **Papel** | **irrelevante**. Ser supervisor NÃO dá acesso. |

Isso não é preciosismo: em produção há outros supervisores (`Miguel`, `extfeq`). Se o
acesso viesse do papel, a prospecção inteira ficaria visível para eles.

**Onde é barrado:** no `before_request` de `auth.py`, por PREFIXO de rota
(`MODULOS_RESTRITOS`). Uma rota nova sob `/api/professores` nasce fechada — esquecer um
decorador não vira vazamento. `/api/modulo-acesso` também é restrita, senão um usuário com
acesso poderia se promover ou liberar terceiros.

O item de menu some para quem não tem acesso, mas isso é conveniência de UI: quem barra é
o servidor, e forjar o menu não abre nada.

## Interação
Quadro kanban com arraste (Pointer Events), **o mesmo** do funil de leads. O arraste foi
generalizado em vez de duplicado: os listeners são globais e pegam qualquer `.kb-card`, então
o quadro se identifica por `data-board` — sem isso o kanban de leads sequestraria os cards de
professor e chamaria a API errada.

Diferença de gesto entre os dois quadros, de propósito:
- **leads**: exige a chave "modo mover" (clicar abre a conversa, ação de alto custo).
- **professores**: arrasta direto; o clique só conta se o ponteiro não passou de 5 px. Abrir
  a ficha e mover o card convivem sem chave nenhuma.

## Critérios de aceitação
- AC1 Supervisor sem concessão recebe 403 em toda rota do módulo e não vê o menu.
- AC2 Usuário com acesso não consegue conceder acesso a si mesmo nem a terceiros.
- AC3 Revogação tira o acesso imediatamente.
- AC4 Registro de professor nunca aparece no funil de leads (e vice-versa).
- AC5 Arrastar um card não abre a ficha; clicar não move o card.
- AC6 Mexer no arraste de professores não altera o comportamento do funil de leads.
