# 08 — Base de Conhecimento (grounding) → Ontologia

**Fonte da verdade (v2):** `atena/ontologia/catalogo.jsonld` (+ `vocabulario.ttl`, `shapes.ttl`).
**Modelo formal:** ver `11-ontologia-anchoring`. **Legado:** `cursos_feq.json` /
`Base_Conhecimento_Cursos_FEQ_v1.md` permanecem até o Revisor migrar 100% para o grafo; manter
em sincronia ou marcar como derivados.

## Papel
É a **fonte da verdade** dos agentes. O Revisor só aprova afirmações factuais (preço, data, carga,
requisito, horário, modalidade) que **resolvam a uma âncora** no grafo e satisfaçam as restrições.
Campo ausente = "não sei" → o agente diz que vai confirmar; **nunca** inventa (fail-closed).

## Regra de qualificação (agora como dado, não prompt)
Os 8 cursos exigem **ensino superior completo** → no grafo, `requisito/nivelMinimo =
feq:superiorCompleto`. A elegibilidade é a comparação ordinal `≥` (ver `11`). Ensino médio não
qualifica para nenhum curso atual. Trocar o gate de um curso futuro é editar o dado.

## Esquema de cada curso (catalogo.jsonld)
`codigo, nome, @type(Especializacao|Extensao), modalidade, horarioAula, professorResponsavel,
requisito{nivelMinimo}, cargaHoraria{horas}?, vagasMin?, vagasMax?, publicoAlvo?, certificacao?,
janelaInscricao{inicio?,fim}, janelaOferecimento{inicio,fim},
planoPagamento{taxaInscricao,numParcelas,valorParcela,aVista}, urlExtecamp?, insightVenda,
alertaCatalogo?, wasDerivedFrom`.

## Cursos (resumo) — captabilidade é calculada vs. `hoje`, não fixa aqui
| Código | Tipo | Requisito | Inscrições até | À vista |
|---|---|---|---|---|
| FEQ-0360 | Especialista | Superior | 20/05/2026 | R$ 16.739,36 |
| FEQ-0480 | Especialista | Superior | 16/08/2026 | R$ 16.455,75 |
| FEQ-1000 | Especialista | Superior | 05/08/2026 | R$ 17.856,13 |
| FEQ-0499 | Extensão | Superior | 30/04/2026 (Extecamp indisponível) | R$ 3.799,74 |
| FEQ-0333 | Extensão | Superior | 11/05/2026 | R$ 3.000,37 |
| FEQ-0580 | Extensão | Superior | 22/07/2026 | R$ 9.533,46 |
| FEQ-0331 | Extensão | Superior | 15/08/2026 | R$ 5.815,95 |
| FEQ-0655 | Extensão | Superior (diploma — gargalo) | 10/05/2026 | R$ 13.576,09 |

## Insights de venda (campo `insightVenda`; usado por SDR/Nutrição, não pelo Revisor)
- **FEQ-0480** (playbook): desconto gradual a partir de 8% + workshop.
- **FEQ-0333**: esclarecer que são módulos (não curso pela metade) e que não é 100% em inglês.
- **FEQ-1000**: vende-se sozinho; cadência informativa (vaga máx. 90).
- **FEQ-0655**: qualificar diploma cedo (gargalo de elegibilidade).

## Campo `alertaCatalogo` (insumo do agente Curador de Catálogo)
Sinaliza dado a confirmar com humano antes de captar. Hoje: **FEQ-0499** (indisponível na Extecamp
+ carga horária desconhecida).

## Manutenção
- Preço/data muda → editar `catalogo.jsonld` (**decimais como string**) e rodar `validar.py`.
- Evolução: enriquecer com FAQs/objeções por curso; futuramente RAG híbrido (não bloqueante).
- Pendência de catálogo: confirmar disponibilidade e carga do FEQ-0499 na Extecamp.
