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
Os cursos de especialização/extensão da FEQ exigem **ensino superior completo** → no grafo,
`requisito/nivelMinimo = feq:superiorCompleto`. A elegibilidade é a comparação ordinal `≥`
(ver `11`). Trocar o gate de um curso é editar o dado — não é uma regra fixa no prompt, e por
isso o catálogo já tem exceções reais: **ECO-1100** (§ "Cursos fora da FEQ") declara
`nivelMinimo = feq:nenhum` porque a landing page do curso é explícita ("não há pré-requisito").

## Cursos fora da FEQ
O catálogo não é exclusivo da Extensão FEQ: **ECO-1100** ("Desvendando a Economia Global") é do
**Instituto de Economia (IE/CERI)** — outro instituto da Unicamp, com sua própria landing page
(`eco-1100-landing.vercel.app`) e sem pré-requisito acadêmico. É tratado pelos mesmos agentes
(ontologia, Revisor, orquestrador) que os cursos da FEQ, mas com três diferenças deliberadas:
- **Não entra na página `cursos-extensao.feq.unicamp.br`** — tem landing page própria, fora do
  WordPress da FEQ. Nunca é adicionado a `cursos_metas` (`atena/growth/db.py`), o que já basta
  para excluí-lo do deploy de bridge pages (`wp_deploy_bridge.py`, que só publica turmas do
  `cursos_metas`) e do Growth Engine (pacing/CAPI/recuperação de checkout).
- **Fora da varredura da coleta Extecamp (spec 20)** — sem acesso ao Instituto de Economia no
  ADM Cursos por ora. A varredura já só processa códigos presentes no `cursos_metas`
  (`app.py::coleta_pagina`, tipo `"lista"`), então a mesma ausência acima já exclui o ECO-1100
  sem precisar de nenhuma trava adicional.
- **`requisito/nivelMinimo = feq:nenhum`** — sem gate acadêmico; o orquestrador não pergunta
  "você concluiu o ensino superior?" para este curso (mesmo mecanismo do teste
  `test_lead_com_medio_e_elegivel_no_curso_de_medio`).

**Identidade institucional**: o agente se apresenta como "Vinícius, atendente do Instituto de
Economia" (não da FEQ) quando o curso do lead é ECO-1100 — `atena.ontologia.servico
.instituicao_do_codigo(codigo)` deriva isso do prefixo e alimenta o system prompt do cérebro
(`cerebro.py::_system_prompt`) e as respostas fixas do orquestrador (pergunta "é robô?"). O
cartão de contato do WhatsApp também varia: chave `secretaria_ie` em `atena/core/contatos.py`
tem telefone/e-mail reais da secretaria do IE (confirmado por Vitor em 16/09/2026), distintos
da secretaria da FEQ. Pendência conhecida: o Guia do Curso em PDF (`atena/growth/pdf_guia.py`)
ainda não segue essa regra — o branding (logo, contato) é 100% FEQ hardcoded; ver CLAUDE.md §10.

Prefixo de código novo (`ECO` vs `FEQ`) precisa estar em `atena.ontologia.servico.CODE_RE` —
é a única fonte do regex de código de curso; ingestão, orquestrador, Bibliotecário, Ontologista
e Revisor importam de lá. O mesmo módulo também é a única fonte de `instituicao_do_codigo`.

## Difusão de oferecimento contínuo (FEQ-1150)
**FEQ-1150** ("Processos sob Controle: do PID ao MPC", inserido em 05/10/2026 a partir da
página oficial `ins.extecamp.unicamp.br/curso/dados/FEQ-1150/001`) é o primeiro curso **da
FEQ** do tipo Difusão e de **oferecimento contínuo** (`oferecimentoContinuo = "true"` →
`curso()["continuo"]`): inscrição de 16/07/2026 a 01/07/2031, horário livre, sem turma a
formar. Entra em `cursos_metas`, tem LP (`/inscricao-feq-1150/`) e card na home. Quatro
consequências, todas derivadas do dado e não do código do curso:
- **Documento emitido**: `certificacao` começa com "Declaração" → LP (`ponte._documento`) e
  guia (`pdf_guia._certificado`) dizem "Declaração de frequência", nunca "Certificado".
- **Plano de 1 parcela = à vista**: `numParcelas = 1` continua no grafo (SHACL e Revisor
  dependem dele); `ponte._plano_exibicao` só impede a página de escrever "1x de"/"em até 1 parcelas".
- **`janelaOferecimento` é disponibilidade na plataforma**, não início/fim de aulas: a LP
  troca o FAQ de período e os 5 passos (que mandam "aguardar a abertura da turma ao
  encerramento das inscrições") por 3 passos; o guia omite "Início das aulas".
- **Sem os "3 motivos"/selos da FEQ no guia** (aulas ao vivo, turmas reduzidas): `pdf_guia._inst`.
- **Sem gate acadêmico** (`feq:nenhum`), como o ECO-1100.

Pendências do FEQ-1150: `fichaInscricao` não declarada (cai na ficha completa — confirmar com
a coordenação se a básica basta); meta 20/26 em `cursos_metas` é placeholder (a Extecamp não
publica mínimo de alunos); pacing contra um prazo de 2031 não informa nada.

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
  Política vigente (05/10/2026, confirmada por Vitor): **15% de desconto no pagamento à vista**,
  registrada no campo `descontos` do curso (o agente informa a % e o valor cheio; não calcula).
  O lead seleciona o pagamento à vista na inscrição e o desconto vem direto no boleto, na
  linha de descontos. **Descontos nunca acumulam** (regra geral, no prompt do cérebro).
- **Contato da Secretaria de Extensão da FEQ**: e-mail `extfeq@unicamp.br` ou WhatsApp
  +55 19 3521-3911. Fonte única em `atena/core/contatos.py`; o cérebro o recebe no prompt via
  `secretaria_texto(codigo)` (por instituto — o IE não tem WhatsApp confirmado). É o **único** WhatsApp informado: o antigo
  (19) 98262-0072 foi removido de `contatos.py`, do guia em PDF e da Central.
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

## Link que o agente envia (`urlInscricaoLead`, 06/10/2026)

`curso()["url_inscricao"]` é o link pronto para o lead. Por padrão deriva de `urlExtecamp`
(`/curso/inscricao/<curso>/<turma>`). O campo **opcional** `urlInscricaoLead` no curso vence
esse padrão e também vira âncora do Revisor — serve para quando o oferecimento já foi declarado
mas o link público da Extecamp ainda não abre. Removido o campo, o agente volta ao link da
Extecamp. O Injetor ainda não edita esse campo (mudança direta no catálogo, versionada).

- **FEQ-1000, turma 019** (declarada em 06/10/2026): inscrições 06/10/2026–13/02/2027, aulas
  13/03/2027–11/11/2028. O link `…/curso/inscricao/FEQ-1000/019` ainda redirecionava para a home
  da Extecamp, então `urlInscricaoLead` aponta para a LP
  `https://cursos-extensao.feq.unicamp.br/inscricao-feq-1000/` (slug perene; a LP antiga
  `inscricao-feq-1000-018` é espelhada com o mesmo conteúdo). **Pendência:** quando a
  Extecamp abrir o link, remover o campo.

### O que muda sozinho quando um oferecimento novo é declarado (revisão de 06/10/2026)

- **Contagem de inscritos (pacing, spec 15)**: `cursos_metas.turma_desde` marca o início da
  turma corrente; `Growth.inscritos()` conta só `inscricao_confirmada` a partir dali, e
  `set_oferecimento` zera `inscritos_extra`. Regravar a mesma turma não zera de novo.
- **Totais da Extecamp no Painel (spec 20)**: `totais_extecamp()` devolve `None` ("—") enquanto
  o total guardado for de outra turma — a coleta troca sozinha quando enxergar a nova.
- **LP (spec 17)**: publicada no slug perene e **espelhada na URL legada** de `LEGADO_TURMA`.
- **Precedentes do agente**: resposta antiga que cita data (`dd/mm`) não é mais usada como
  precedente — data é fato do oferecimento e vem do registro do curso.
- Os leads da turma anterior (matriculados, cancelados) **permanecem** no funil como histórico.
