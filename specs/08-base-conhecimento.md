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

## Guia do curso em PDF (✅ implementado — `atena/growth/pdf_guia.py`)
Documento formal por curso para leads que pedem material além da conversa/LP — referência de
estrutura: "Guia do Curso FEQ-0480.pdf" (documento oficial da Extensão FEQ), com a paleta visual
da própria bridge page (navy `#020617`/`#0f172a` + âmbar `#fbbf24`) em vez da paleta do
documento de referência, mantendo uma identidade única em toda a jornada (LP → WhatsApp → PDF).

**Fonte**: 100% ontologia (`catalogo.jsonld`) + `atena/core/contatos.py` — zero texto gerado por
LLM, mesmo princípio anti-alucinação do Revisor. Seção sem dado **some**, nunca inventa (mesma
regra de `ponte.py`): um curso sem `empresasJson` não mostra a página de empresas; um professor
sem bio/foto/link cadastrado aparece só com o nome. **Sem página de investimento/preço** — preço
muda por campanha (spec 15) e o PDF fica salvo no celular do lead sem se atualizar sozinho.

**Cache**: `atena/data/guias/<CURSO>.pdf` + `.hash` sidecar (sha256 do HTML renderizado, mesma
ideia do `lp_auto.py` — spec 13 §"Auto-republicação"). Gera sob demanda; só refaz quando a
ontologia daquele curso mudaria o HTML.

**Motor**: WeasyPrint (HTML/CSS → PDF). Exige no host `libpango-1.0-0 libpangocairo-1.0-0
libgdk-pixbuf2.0-0 libffi-dev` (Ubuntu/apt) — import adiado em `pdf_guia.obter()`, só quem gera
de fato precisa das libs.

**Envio — dois caminhos**:
1. **Agente**: o cérebro marca `dados.anexo_guia=true` quando o lead pede material/PDF/
   apresentação formal (`atena/agentes/cerebro.py::_FERRAMENTA`). O flag precisa sobreviver até a
   aprovação humana (pode vir bem depois) → `decisoes.anexo` (coluna nova), gravado por
   `Orquestrador._aplicar` só quando a proposta é aprovada pelo Revisor (`status="proposta"`).
   `_aprovar_e_enviar` (app.py) manda o texto e, se `anexo=="guia_curso"`, o PDF em seguida via
   `WhatsApp.enviar_midia`.
2. **Operador**: botão no composer da conversa (`POST /api/conversas/<id>/guia`) — envio direto,
   sem passar pelo cérebro/Revisor (documento fixo gerado da ontologia, não texto livre).

- AC10 Seção sem dado no curso nunca aparece em branco nem com placeholder — some inteira.
- AC11 PDF nunca inclui preço/parcela (evita desatualização depois de salvo no dispositivo do lead).
- AC12 `anexo_guia` só vira anexo de fato se a proposta correspondente foi aprovada pelo Revisor.
