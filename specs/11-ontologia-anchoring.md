# 11 — Ancoragem Ontológica de Alto Nível

**Artefatos:** `atena/ontologia/{vocabulario.ttl, catalogo.jsonld, shapes.ttl, validar.py}`
**Status:** fundação implementada e **validada** (`python3 atena/ontologia/validar.py`, jun/2026).
**Stack:** JSON-LD + SHACL via `rdflib` + `pyshacl` (decisão fixada — sem reasoner OWL completo).

## Por que ontologia (e não JSON plano)
O grounding v1 valida **presença** de um literal na base (regex sobre dígitos). Isso falha em três
frentes reais (ver `feq-achados`): aprova data de curso **encerrado** (consta na base), confunde
**carga horária** com **hora-de-relógio**, e não valida a **coerência** de um plano de parcelas.
A ancoragem ontológica eleva o grounding de "a string existe?" para "a afirmação **resolve** a um
nó/aresta tipado do grafo **e** satisfaz as restrições?".

## Três camadas
| Camada | Classes | Papel |
|---|---|---|
| **Superior** | `Entidade`, `Assercao`, `Ancora`, `Acao`, `NivelDeRisco`, `Guardrail`, `Agente` | Todo dado tem proveniência; toda afirmação resolve a uma Âncora; toda ação tem tier de risco. Grounding e governança viram propriedades estruturais. |
| **Domínio** | `Curso`(↳`Especializacao`/`Extensao`), `RequisitoAcademico`, `JanelaTemporal`, `PlanoPagamento`, `Duracao`, `NivelEscolaridade`, `Lead` | Modela o catálogo FEQ e as relações antes implícitas. |
| **Operacional** | (em `12-orquestracao-estado`) `EstadoConversa`, `Decisao`, `Interacao` | Estado persistido e auditável. |

## Divisão de responsabilidade (modelagem correta)
- **SHACL (`shapes.ttl`) = integridade + coerência do catálogo.** Roda na carga/CI. Verifica
  estrutura (todo curso tem requisito/janela/plano), tipos (data/decimal/inteiro), `inicio ≤ fim`,
  `nivelMinimo ∈ {medio,…,pos}`, e a **coerência de pagamento** (`nº×valor ≈ à_vista`, tolerância
  `nº×0,01 + 0,05`) via `sh:SPARQLConstraint`.
- **SPARQL de runtime (`servico.py`) = inferência que depende de contexto.** Um curso encerrado é
  **dado válido**, só não é captável → isso NÃO é violação de integridade, é consulta:
  - **Captabilidade:** `status(janelaInscricao, hoje) ∈ {futura, aberta, encerrada}`.
  - **Elegibilidade:** `ordem(lead.escolaridade) ≥ ordem(curso.requisito.nivelMinimo)` (ordinal).

## Disciplina de tipos (mata o bug carga × relógio)
`feq:cargaHoraria` é uma `feq:Duracao` (`feq:horas` inteiro). `feq:horarioAula` é string de
hora-de-relógio. São **tipos distintos** — o Revisor classifica o token numérico pelo contexto e
nunca compara "aulas das 9h" contra a carga (398h). Campo ausente (ex.: carga do FEQ-0499) =
"não sei" → claim de carga é **bloqueado** (fail-closed), não inventado.

## O Revisor como validador ontológico (substitui o regex)
1. Extrai da mensagem candidata **asserções tipadas** (preço, data, carga, requisito, modalidade…).
2. Cada asserção tenta **unificar** com uma tripla do grafo do curso (com proveniência da âncora).
3. Asserção factual **sem âncora** ou que **viola constraint** → bloqueia. **Fail-closed**:
   na dúvida ou sem verificação possível, bloqueia (inverte o fail-open da v1).
4. Aprovação carrega a lista de Âncoras → alimenta o painel de transparência.

## Gotcha de manutenção (documentado para não regredir)
No JSON-LD, decimais devem ser **strings** (`"valorParcela": "866.09"`), não números nativos: o
parser do `rdflib` tipa número nativo como `float` e o literal fica *ill-typed* para `xsd:decimal`,
reprovando no SHACL. Datas e inteiros podem ser nativos. O `validar.py` é a rede de segurança.

## Resultado validado (jun/2026, `hoje=26/06/2026`)
- Integridade SHACL dos 8 cursos: **conforme**.
- Teste negativo (`30x R$ 866,09` no 0480): trava **pegou** (reprovou).
- Captabilidade: **encerradas** hoje = 0333, 0360, 0499, 0655; **abertas** = 0331, 0480, 0580, 1000.
- Elegibilidade: lead "ensino médio" → inelegível para todos os 8.

## Manutenção
- Mudou preço/data → editar `catalogo.jsonld` (decimais como string) e rodar `validar.py`.
- Curso novo com gate diferente → muda o `nivelMinimo` no dado, **não** o prompt.
- `validar.py` deve passar no CI antes de qualquer deploy (é o critério de aceitação da camada).

## Promoções de campanha (✅ implementado)
O modelo tinha UM preço por curso (`feq:planoPagamento`), e o Revisor bloqueia qualquer
valor fora da ontologia — então campanha promocional precisou virar DADO, não texto solto.

`feq:Promocao` = janela + lista de `feq:Oferta`. Cada oferta tem `rotulo`, `condicao` e
parcelas e/ou à vista. O preço de tabela **não** é substituído: continua sendo a base sobre
a qual o desconto é anunciado (e o que a LP mostra riscado).

SHACL trava: (a) parcelas × valor fecham com o à vista da própria oferta; (b) a promoção
**não pode terminar depois do fim das inscrições** — o lead chegaria na Extecamp e o preço
não existiria; (c) toda oferta declara sua condição (anunciar "19x de R$ 736,18" sem dizer
que exige grupo de 3+ seria propaganda enganosa).

`Ontologia.promocao(codigo, hoje)` filtra por data **no serviço**, não em quem chama. Com isso
a campanha some sozinha de todos os consumidores no dia seguinte ao fim: âncoras do Revisor,
prompt do cérebro, LP e templates de recuperação.

Consumidores: `ancoras()` (o Revisor passa a aceitar vários planos por curso),
`cerebro._bloco_promo`, `ponte._investimento_itens`, `funil.valor_curso` (Purchase usa o
valor promocional — reportar tabela numa venda com desconto infla o ROAS) e
`whatsapp.template_promocional` (template de campanha, com duplo gate: promoção vigente
E status APPROVED na Meta).

- AC8 Valor promocional é aprovado pelo Revisor; valor inventado ou desconto somado por
  conta própria continua bloqueado.
- AC9 Encerrada a janela, nenhum consumidor consegue mais anunciar o preço da campanha.
