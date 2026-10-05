# Spec 17 — Fluxo consolidado: LP inteligente no domínio FEQ

> **05/10/2026** — a LP passou a variar por três dados da ontologia, sem mudar nada nas páginas
> existentes: documento emitido (certificado × declaração de frequência), plano de 1 parcela
> exibido como à vista, e oferecimento contínuo (passos e período próprios). Ver spec 08
> § "Difusão de oferecimento contínuo" e `atena/tests/test_continuo.py`.

> **Esta spec consolida o funil definitivo** (refinamento de 15/07/2026) e prevalece
> onde conflitar com as specs 15/16. O operador cria campanhas de geração de leads
> com sinal de conversão offline; o sistema tem visibilidade total via Marketing API,
> gera e publica as LPs, rastreia cada sessão, conversa pelo WhatsApp e devolve as
> conversões offline à Meta.

## 1. O fluxo, de ponta a ponta

```
Meta Ads (lead gen + conversão offline)            visibilidade: meta_ads.insights/campanhas
      │ clique
      ▼
LP do curso — página órfã em https://cursos-extensao.feq.unicamp.br/  (WordPress)
      │  · copy ancorada na ontologia, estilo PUC Online (spec §3)
      │  · telemetria de sessão: onde para, onde sai, o que não lê (§5)
      │  · botão WhatsApp flutuante -> conversa livre com os agentes (§6)
      │  CTA "Inscreva-se" -> modal Nome + WhatsApp (opt-in LGPD)
      ▼
https://ins.extecamp.unicamp.br/curso/inscricao/<SIGLA>/<OFERECIMENTO>   (formulário oficial)
      │                                    ▲ destino SEMPRE /curso/inscricao/ (não /dados/)
      │ (sem webhook da Extecamp)
      ▼
Régua de reengajamento: 5 toques (§7) até o operador informar a inscrição
      ▼
Central de Leads: operador move o card p/ Inscrito/Matriculado  ──►  CAPI offline (§8)
```

## 2. Destino: formulário /curso/inscricao/

O destino do modal é o formulário oficial `https://ins.extecamp.unicamp.br/curso/
inscricao/<SIGLA>/<OFERECIMENTO>`. Fonte da URL: `url_extecamp` da ontologia
(governada); URLs antigas `/curso/dados/...` são normalizadas trocando `dados` →
`inscricao` (`ponte.url_inscricao`). Turmas-alvo: FEQ-0480/009 · FEQ-0580/001 ·
FEQ-0333/001 · FEQ-1000/018.

## 3. A LP (sequência das páginas antigas da FEQ, 100% ancorada)

Seções, na ordem (todas com `data-secao` para a telemetria) — a mesma sequência de
blocos das páginas antigas de `cursos-extensao.feq.unicamp.br`:

0. **Cabeçalho codado** — UMA faixa sticky: logo + menu real do site + busca + CTA
   (no mobile, logo + sanduíche). Não vem mais do tema do WP.
1. **Hero** (invertido) — tipo+modalidade, título, objetivo, selos, **preço**,
   urgência legítima ("inscrições até <prazo>"), CTA primário.
2. **Logos de empresas** onde os formados atuam (`empresasJson`).
3. **Seu currículo no futuro** — desenhado como uma **folha de currículo**: foto
   (imagem de engenharia que a FEQ já publica), formação complementar (o curso),
   competências (`habilidadesJson`) e referência de mercado (`salarioRef`), ao lado
   do certificado. Curso **sem modelo publicado** (FEQ-0331, FEQ-0499, FEQ-0580):
   mostramos o **teor da declaração** (nome do curso + carga horária), nunca uma
   imagem fabricada de documento oficial da Unicamp.
4. **Depoimentos** reais (`depoimentosJson`).
5. **Por que a Extensão FEQ** — 4 cards: certificado, formato de aulas, corpo
   docente, carga horária.
6. **Professores** — responsável + docentes em destaque (foto, bio, Lattes).
7. **Conteúdo programático** — accordion `<details>` módulo→subtópicos.
8. **FAQ** — requisito, inscrição, formato, **objetivos**, **período de
   oferecimento**, valores, prazo, avaliação, certificação/MEC.
9. **Investimento** (invertido) — parcelas, à vista, taxa, descontos, campanha,
   passo a passo da Extecamp e CTA de fechamento.
10. **Cursos relacionados** + rodapé institucional.

**Repetições eliminadas (11/08/2026):** a seção "O que você vai dominar" (mesmas
habilidades do currículo), a faixa de chips de empresas (mesmos nomes dos logos),
os 3 "cards de valor" (repetiam benefícios e investimento), "Para quem é" (já é
item do FAQ e campo da etapa 2) e o CTA final solto (o bloco de investimento já
fecha com CTA). Objetivos e período viraram itens do FAQ — mantendo o `data-secao`
no `<details>`, então a telemetria de interesse não se perde.

"Fricção positiva": a LP entrega as informações essenciais sem esconder nada —
quem clica no CTA já se autoqualificou. O modal (Nome + WhatsApp + LGPD) é a única
fricção, e o redirect à Extecamp nunca espera o backend (timeout 1,5 s, spec 16 §2).



### 2b. LP para TODO o portfólio + modo LISTA DE ESPERA (20/07/2026)

Toda turma do catálogo tem LP, inclusive **sem inscrição aberta**. `ponte.render()`
consulta `ont.captavel()` e propaga `aberto` para os tokens e para `window.BRIDGE`:

| | Turma aberta | Sem turma (lista de espera) |
|---|---|---|
| CTA | "Inscreva-se →" | "Quero ser avisado →" |
| Hero | "Inscrições abertas até X · turma Y" | "Inscrições encerradas no momento — entre na lista de espera" |
| Modal | contato → **Extecamp** | contato → **confirma na própria página** (`confirmarEspera`) |
| Seção "Como se inscrever" | presente | some (e o atalho na nav-âncora também) |
| Investimento | "Inscrições abertas até X" | "Valores da última turma · sem inscrições abertas", com a ressalva de reajuste |
| Evento de funil | `inicio_inscricao` (CAPI InitiateCheckout) | **`lista_espera`** (CAPI Lead) |

O tipo de evento distinto é o que impede o efeito em cadeia: um lead de espera com
`inicio_inscricao` cairia na aba **Verificação** (pedindo ao operador conferir na
Extecamp uma inscrição que não existe) e na **régua de recuperação de checkout**.
Com `lista_espera` ele fica fora das duas, apenas capturado no CRM
(`aguardando_revisao`, pois o gate de captabilidade não engaja curso fechado).

**Requisito por nível**: o texto vem de `_REQUISITO_TEXTO[nivelMinimo]` — havia um
bug que afirmava "ensino superior completo" para qualquer curso com requisito, o
que é FALSO no FEQ-0299 e FEQ-0670 (pedem ensino médio).

**Cross-sell**: com 9 LPs no ar, `_cursos_relacionados_html` limita a 3 cards e
prioriza cursos ABERTOS (sort estável preserva a ordem do `cursos_metas`, que
começa pelas turmas-alvo da campanha).

### 3b. Conteúdo rico importado das páginas oficiais preexistentes (20/07/2026)

As páginas antigas de `cursos-extensao.feq.unicamp.br` são fonte LEGÍTIMA (material
oficial já publicado pela FEQ) e seu conteúdo foi importado LITERALMENTE para a
ontologia (`objetivosJson`, `habilidadesJson`, `programaDetalheJson`,
`depoimentosJson`, `empresasJson`, `avaliacaoTexto`, `salarioRef`, `garantia7dias`,
`plataformaAula` — strings JSON no grafo, objetos via `servico.curso()`), virando
seções novas da LP no padrão MBA USP/Esalq: objetivos, habilidades em chips,
programa em ACCORDION módulo→subtópicos, bios do corpo docente (dict
`PROFESSOR_BIO`, transcrito), depoimentos reais, prova social de empresas,
garantia de 7 dias (só onde publicada — FEQ-1000), salário de referência (com a
ressalva original), passo a passo de inscrição da Extecamp e FAQ de
certificação/MEC. Regras: seção sem dado oficial SOME (nunca inventa; a nav-âncora
de Depoimentos idem); dado DESATUALIZADO da página antiga (planos de pagamento
antigos do 0480, datas passadas do 0333) NÃO é importado — pagamento/datas/carga
vêm sempre da Extecamp via ontologia, que é quem o Revisor ancora.

### 3c. Redesign monochrome com a IDV da FEQ (11/08/2026)

A LP era escura com acento âmbar (paleta genérica de LP). Passou a usar a identidade
visual do manual da marca (v2, pág. 14), na disciplina monochrome — uma tinta só,
ênfase por inversão:

| Papel | Cor | Uso |
|---|---|---|
| Tinta institucional | `#065BAA` | títulos, réguas, bordas, CTA — faz o papel do "preto" |
| Leitura / inversão | `#094567` | corpo de texto e **fundo** das seções invertidas |
| Acento gráfico | `#19ABB5` | filetes, marcadores, sinais do accordion — **nunca** texto pequeno sobre branco (2,6:1) |
| Dinheiro e relógio | `#FBBF24` | preço, desconto, selo de campanha e contagem regressiva — só sobre fundo azul (5,6:1) |
| Apoio | `#A2DADB` · Erro `#DC4E41` · Papel `#FFFFFF`/`#F5F7F9` | |

- **Tipografia**: Montserrat (substituto livre da Gotham do manual), display com
  tracking negativo e escala grande. O `<link>` da fonte é criado pelo
  `bridge-logic.js` — em `@import` no CSS o `convert_chars()` do WordPress
  transformaria o `&` de `&display=swap` em `&#038;` e quebraria a URL. **Por isso
  `&` é proibido em `bridge.css`.**
- **Disciplina**: raio 0, zero sombra, régua de 4px entre seções, negativo generoso,
  transições ≤100ms. Duas exceções deliberadas: retratos (professores e depoentes)
  são **circulares, coloridos, com anel na tinta** — quem ensina é argumento de
  venda; e o botão do WhatsApp é redondo no **verde oficial** (`#25D366`), marca de
  terceiro que precisa ser reconhecível.
- **Logos de empresas**: coloridos e grandes — a seção existe para o lead
  *reconhecer* as marcas.
- **Cabeçalho codado** (`.lp-head`): **uma faixa só** — logo + menu real do site
  (Home · Projetos · Legislações · Cursos · Comissão · Equipe · Contato + busca),
  conferido no HTML de `cursos-extensao.feq.unicamp.br`, + CTA. Havia duas faixas
  (institucional e atalhos da LP) e, empilhadas, liam como cabeçalho duplicado. A LP
  deixa de depender do header do tema e funciona em qualquer URL.
  **Mobile**: só logo + sanduíche (~50px); menu, busca e CTA vão para o painel. O
  toggle é **CSS puro** (checkbox + label, checkbox invisível mas focável) — o
  bootstrap da página vai em base64 e já foi bloqueado por plugin do WP uma vez, o
  menu não pode depender de JS.
- **Auditoria**: `test_growth.py` varre todo `class="…"` do template + das LPs
  renderizadas e exige definição no `bridge.css` — classe órfã não passa.

### 3d. Bloco de cursos da HOME (11/08/2026)

`ponte.home_cursos_html()` gera, da ontologia, os cards de TODO o catálogo — um por
curso do `cursos_metas`, com o estado real de captação (`ont.captavel`):

| Estado | Selo | O que o card promete |
|---|---|---|
| aberto | verde-água | preço vigente + "Inscrições até DD/MM" → LP |
| `recuperavel` | amarelo | "prazo encerrado, a turma não começou — peça vaga remanescente" → LP (etapa 1 escala para humano) |
| fechado | apoio | "entre na lista de espera" → LP em modo espera |

Curso sem turma aberta **não anuncia preço** (valor de turma encerrada não é oferta).
O link sai de `ponte.slug_lp()` — a MESMA função que o deploy usa para publicar, e que
o cross-sell da LP também passou a usar (eram duas implementações, e o cross-sell
apontava para um slug que o deploy não publicava).

**Publicação** (`wp_deploy_bridge --home` ou `--so-home`): a home é uma página
Elementor (id 8157). O bloco de cursos ocupa VÁRIAS seções — dois títulos e duas
grades de imagens ligando às páginas antigas — e vira UMA seção com um widget HTML.
Guardas: só recorta entre a seção com a âncora `#cursos` e a seção de diferenciais
("Saia na frente"), e aborta se houver no meio qualquer seção que não seja grade,
título de curso ou espaçador. O `_elementor_data` original é salvo em
`atena/data/bridge_pages/home_elementor_antes.json` antes da primeira escrita.

Cada card leva a **imagem que a FEQ já usava** para aquele curso na grade antiga
(extraída do backup do Elementor). Duas exceções: FEQ-0299 e FEQ-0670 apontavam para
servidores de TERCEIROS (`agencia.fapesp.br`, `ictq.com.br`) — hotlink que quebra
quando o outro site mexe no arquivo; foram trocadas por equivalentes da própria
biblioteca de mídia (microscópio eletrônico e modelo molecular). Teste trava as duas
coisas: todo curso tem imagem, e nenhuma vem de fora do domínio da FEQ.

**Gotcha do Elementor**: ele serve o HTML renderizado de um cache que a escrita pela
API não invalida — o bloco novo leva alguns minutos para aparecer, e às vezes a
página no ar ainda mostra a versão ANTERIOR do bloco. Por isso o marcador carrega um
carimbo de versão (`<!-- FEQ-CARDS-CURSOS v=<hash8> -->`): o deploy só responde
`publicado` quando **aquele** hash está no ar; senão devolve
`gravado_aguardando_cache` dizendo o que está na tela agora. Para não esperar: abrir
a home no Elementor e clicar em *Update* (ou Elementor › Ferramentas › Regenerar
arquivos e dados). Mexer no `post_content` não adianta — quem renderiza é o Elementor.

### 3e. Datas: prorrogação × turma nova

`Ontologista.propor_datas()` cobre o caso "a Extecamp esticou o prazo e empurrou o
oferecimento da MESMA turma": muda `janelaInscricao.fim`, `janelaOferecimento.*` e a
`dataBoleto`, **sem tocar em turma nem URL** — o slug da LP não pode mudar. Turma
NOVA continua em `propor_oferecimento()` (que exige URL e move a LP para o slug
perene). Os dois passam pelo mesmo caminho: diff explícito, SHACL e aprovação; ao
aprovar, a LP é republicada.

`dataBoleto` (novo campo, `feq:dataBoleto`) é a data do boleto da inscrição daquela
turma; aparece no passo 3 do "Como se inscrever" e **some** em curso que não a
declara — nunca é derivada do prazo de inscrição.

## 4. Publicação como página órfã no WordPress FEQ

`cursos-extensao.feq.unicamp.br` roda WordPress. Estratégia (wp_deploy_bridge.py):

1. **Fragmento, não documento**: para o WP vai só o miolo (seções + modal +
   scripts), embrulhado em `<!-- wp:html -->…<!-- /wp:html -->` (bloco HTML do
   Gutenberg — sem `wpautop` mastigando o JS). O `<!doctype>`/`<head>` ficam só na
   versão servida pelo hub (`/ponte`).
2. **Auth**: Application Password de usuário **administrador** (precisa de
   `unfiltered_html`, senão o WP remove os `<script>` silenciosamente).
3. **Validação pós-publicação**: depois do POST, o deploy relê a página publicada
   e confere que os `<script>` sobreviveram e que o modal está lá; senão, reporta
   FALHA (nada de página morta com status de sucesso).
4. **Órfã**: slug fora de menus + noindex (meta robots no fragmento não vale —
   usa-se o campo do Yoast quando existir; senão, anotar manualmente no SEO do WP).
5. Idempotente por slug (`inscricao-feq-XXXX-turma`); dry-run sem credenciais.
6. **Fallback sem acesso WP**: as LPs continuam servidas pelo hub em
   `/ponte?curso=...` — basta apontar DNS/proxy (ex.: lp.feq…) para o VPS.

## 5. Telemetria de sessão (inteligência da LP)

**Coleta (bridge-logic.js)**: `sessao_id` (UUID), IntersectionObserver nas seções
(`data-secao`) medindo alcance e tempo por seção, profundidade máxima de scroll,
cliques de CTA, modal aberto/enviado; na saída (`visibilitychange`→hidden),
`navigator.sendBeacon` manda o resumo para `POST /webhook/lp` (público, CORS,
rate-limit). Sem PII: a sessão só se liga ao lead se o modal for enviado
(`sessao_id` acompanha o POST do modal).

**Armazenamento**: tabela `lp_sessoes` (upsert por `sessao_id`): curso, ts,
duração, scroll máx., última seção, tempo por seção (JSON), cta_cliques,
modal_aberto, modal_enviado.

**Leitura**:
- `GET /api/lp/analytics?curso=X&dias=N` (Central, autenticado): sessões, funil de
  alcance por seção (% que chegou), tempo mediano por seção, taxa CTA→modal→envio,
  **seção de maior abandono** (onde o lead para/sai/não entende).
- Ferramenta `analise_lp` em `growth/ferramentas.py` — os agentes de IA leem o
  mesmo funil para propor mudanças de copy/campanha.

## 6. WhatsApp na LP

Botão flutuante (canto inferior direito) → `https://wa.me/<numero>?text=<oi, vim
da página do curso X>` — cai no número WABA e a conversa é dos agentes (orquestrador,
spec 12). Número via `GROWTH_WA_NUMERO` (default 551931672313). O texto pré-preenchido
carrega o código do curso → o orquestrador já sabe o contexto.

## 7. Reengajamento: 5 toques

Lead com checkout (modal enviado) e **não informado como inscrito** pelo operador →
cadência de **5 toques** com templates aprovados, escalonando tom (boas práticas de
CRM: ajuda → lembrete → valor → urgência → última chamada):

| # | Espera desde o toque anterior | Template |
|---|---|---|
| 1 | 2 h do checkout | `feq_lead` (ajuda: "teve dificuldade?") |
| 2 | +24 h | `feq_lembrete_inscricao` (prazo) |
| 3 | +48 h | `feq_reengajamento` (valor/benefício) |
| 4 | +72 h | `feq_lembrete_inscricao` (urgência do prazo) |
| 5 | +96 h | `feq_lead` (última chamada humana) |

Param de prazo ausente → "breve" (nunca parâmetro vazio, a Meta rejeita). Saem da
régua: convertido (Inscrito/Matriculado), opt-out, curso não captável, lead que
respondeu após o checkout (a conversa passa ao cérebro) e — desde 11/08/2026 —
**quem entregou a ficha da etapa 2 e ainda não foi transcrito** (`inscricoes.status
= 'recebida'`): a bola está com a gente, não com ele. Boleto continua esperando 24 h
para o 1º toque. Teto de 25 envios/passada.

### 7b. O que cada etapa faz com o lead (11/08/2026)

| | Etapa 1 (contato) | Etapa 2 (ficha completa) |
|---|---|---|
| Guarda | card no CRM: nome, e-mail, telefone, opt-in, UTMs, sessão | tudo da etapa 1 **+ `inscricoes.dados_json`** (CPF, endereço, pagamento) |
| Evento de funil | `etapa1` (sem CAPI) | `inicio_inscricao` → CAPI InitiateCheckout, mesmo `event_id` do Pixel |
| Fase no kanban | Lead | **Inscrito** (move só a fase; não grava `inscricao_confirmada`) |
| Aba Verificação | fora (não há o que transcrever) | **entra** — é a fila de transcrição na Extecamp |
| Régua | 2 h → `feq_lead` ("teve dificuldade?") | fora enquanto a ficha está `recebida` |
| Fim da jornada | abre a etapa 2 | `/thank-you-page-lp/`: "o time entra em contato para concluir a matrícula" |

A distinção que sustenta isso: **fase ≠ evento de funil**. A etapa 2 move a fase para
Inscrito (o lead pediu a vaga e entregou os dados), mas quem reporta conversão à Meta
e tira o card da fila continua sendo o gesto humano na Verificação, depois de lançar
a inscrição na Extecamp de verdade (§8).

**Sem repasse à Extecamp**: a LP não manda mais o lead ao formulário oficial — ele
preencheria a mesma ficha duas vezes. Por isso o `select` de pagamento lista as
opções **já com o preço da campanha** (parceladas e à vista) e o aviso "o formulário
da Extecamp mostra o valor cheio" (`promocao.como_aplicar`) saiu da LP — o texto
segue na ontologia porque os agentes ainda o usam ao mandar o link oficial numa
inscrição fora do prazo.

**Formulário simplificado (16/08/2026)**: a ficha da etapa 2 é mais curta que a da
Extecamp — **Dados pessoais, Endereço e Pagamento** apenas. A seção "Formação e
atuação" (escolaridade, instituição, cargo, empresa etc.) foi retirada: eram campos
informativos que só enfeitavam a ficha de quem vai transcrever; o *gate* acadêmico
(ensino superior completo, spec 11) não depende dela — ele é feito na conversa pelo
orquestrador (`coletando escolaridade`), que já cobre leads que chegam sem a
declaração. `CAMPOS_ETAPA2`/`_GRUPOS_FICHA` seguem aceitando e exibindo esses campos
para **fichas históricas** (whitelist + Central não descartam dado já capturado).

## 8. Conversão offline: Central → Meta

**O gesto do operador É o reporte**: mover o card na Central para **Inscrito**
dispara `inscricao_confirmada` (CAPI CompleteRegistration); para **Matriculado**,
`matricula_confirmada` (CAPI **Purchase** com valor da ontologia — parcelas×valor ou
à vista). Nada de tela extra: o kanban já era o lugar onde o operador registra a
verdade. A CLI (`matriculas.py`) segue para lotes/CSV e e-mails avulsos.

Por que CAPI (e não a antiga Offline Conversions API): a Meta **descontinuou** a
API antiga (migração para CAPI); o caminho recomendado é evento server-side com
`event_id` determinístico (`<tipo>-<card_id>`, dedup em reenvio), identificadores
em SHA-256 (telefone E.164; e-mail quando houver) e os sinais `fbp`/`fbc` capturados
na LP no momento do modal — é isso que liga a conversão à campanha original e
maximiza o Event Match Quality. Enviar em até 7 dias (janela de atribuição);
`GROWTH_TEST_EVENT_CODE` para homologar no Test Events antes de valer.

## 8b. Verificação humana do desfecho (fecha o loop da LP)

O lead da ponte declara intenção (modal) e é redirecionado à Extecamp — mas a
inscrição em si acontece num sistema que não emite webhook para nós. O loop fecha
com o operador: a Central ganha a aba **Verificação**, que lista todo lead vindo
da LP (`inicio_inscricao`/`boleto_gerado` registrado) ainda **sem** conversão
confirmada e **sem** veredito humano. Para cada um, o operador confere no sistema
da Extecamp se a inscrição foi concluída e registra o desfecho:

| Veredito | Efeito |
|---|---|
| **Inscreveu** | Mesmo caminho do kanban (§8): fase → **Inscrito**, espelho no FARO, `inscricao_confirmada` na CAPI (idempotente). Sai da fila e da régua. |
| **Não inscreveu** | Continua **Lead**; evento `verificacao_negativa` tira o card da fila (não da régua) e o agente dispara o **1º toque da cadência do §7 imediatamente** — sem esperar as 2h do relógio. Os toques seguintes continuam pelo relógio normal. |

A ativação imediata usa o MESMO código da régua (`funil._toque`): guardas de
opt-out, captabilidade e "lead já respondeu → cérebro cuida" valem igual; só os
gatilhos de tempo são pulados (o gatilho aqui é o gesto do operador, não o
relógio). Endpoints: `GET /api/verificacao` (fila com nome/telefone/curso/quando)
e `POST /api/leads/<id>/verificar` `{inscrito: true|false}`. Toda decisão é
auditada (`log_acao`).

## 9. Critérios de aceitação

1. LP renderiza com as 8 seções ancoradas, botão WhatsApp e destino `/curso/inscricao/`.
2. Sessão na LP gera registro em `lp_sessoes`; `/api/lp/analytics` mostra funil por
   seção e ponto de abandono; `analise_lp` devolve o mesmo aos agentes.
3. Checkout sem conversão recebe até 5 toques na cadência da tabela; convertido,
   opt-out e quem respondeu saem da régua.
4. Mover card para Inscrito/Matriculado dispara o evento CAPI com event_id
   determinístico e valor (Matriculado).
5. Deploy WP publica fragmento em bloco wp:html, valida os scripts publicados e
   falha ruidosamente se o WP os remover; sem credenciais, dry-run.
6. Tudo roda em dry-run sem tokens.
7. Lead da ponte sem conversão aparece na aba Verificação; "inscreveu" move a
   Inscrito (+CAPI); "não inscreveu" registra `verificacao_negativa`, mantém Lead
   e envia o 1º toque na hora (guardas de opt-out/captável/respondeu preservadas).
