# 14 — Central de Operação (Frontend)

**Módulos-alvo:** `atena/api/app.py` (REST + SSE) + `atena/web/` (SPA).
**Status:** protótipo navegável implementado (auth/RBAC/auditoria; captabilidade e Revisor reais). **Estética:** **iOS-like, glassmorphism** (vidro fosco,
vibrância, cantos arredondados, tipografia tipo SF). Mockup validado com o operador.

## Propósito
Central de comando do Sistema Extensão FEQ: **visibilidade total** das conversas, **intervenção
humana** quando necessário, **estatísticas** do funil e a porta do **Injetor** (`13`). Materializa o
"painel de transparência" do SPEC original.

## Seções
1. **Dashboard / Estatísticas**
   - Contadores do funil: Lead · Prospect · Inscrito · Matriculado (+ Em espera, Perdido).
   - Taxas de conversão por etapa e **por curso**; leads no tempo (série); SLA/`watch-late`.
   - Saúde dos agentes: % mensagens bloqueadas pelo Revisor, opt-out rate, escalonamentos abertos.
2. **Conversas ao vivo**
   - Lista de conversas (filtro por curso/estado/atribuição) + thread completa por lead.
   - **Intervir:** assumir a conversa (pausa o agente), enviar mensagem manual, ou
     **aprovar/editar/rejeitar** a mensagem que o agente propôs.
3. **Fila de aprovação humana** — escalonamentos, gates de captabilidade, propostas do Injetor/Curador.
   Desde 19/10/2026 vive dentro de Notificações (caixa única, ver §6/7).
4. **Injetor** (`13`) — submeter update → ver diff + relatório de impacto → aprovar/rejeitar.
5. **Transparência** (`/inspecao`) — para qualquer mensagem: o trace de decisão + **âncoras** que a
   fundamentaram (preço/data/requisito → tripla do grafo).
6. **Notificações** (11/08/2026) — caixa de entrada única do agente comercial.
7. **Inscrições** (11/08/2026) — fila de fichas da etapa 2, prontas para transcrever.
8. **Captabilidade** (25/09/2026, painel inicial) — por curso: status, inscritos Extecamp, turma,
   janela de inscrição (com dias até fechar), boleto, aulas, meta/meta segura (`cursos_metas`,
   com % atingido), vagas, modalidade, horário, carga, preço à vista, professor. Botão
   **Copiar relatório** gera o mesmo conteúdo em texto (+ link de inscrição) para reporte.
8. **Otimização** (dentro de Medição, 11/08/2026) — objetivo de controle, bandit e gate.

### 6/7. Notificações e Inscrições — o que mudou e por quê (11/08/2026)

**A aba Verificação saiu.** Ela existia para o operador ir conferir no sistema da
Extecamp se o lead tinha concluído a inscrição — a única fonte de verdade disponível
quando o formulário era de lá. Com a ficha da etapa 2 dentro do sistema (spec 17 §7),
a pergunta deixou de ser *"ele se inscreveu?"* e passou a ser *"já transcrevi a ficha
dele?"*. Trocar a aba, e não apenas removê-la, foi a decisão: remover deixaria o
agente sem fila de trabalho.

| | Verificação (saiu) | Inscrições (entrou) |
|---|---|---|
| Pergunta | ele se inscreveu na Extecamp? | já transcrevi a ficha? |
| Fonte | operador conferindo em outro sistema | `inscricoes.dados_json` |
| Ação | Inscreveu / Não inscreveu | ver ficha · copiar campo · Transcrita |
| Conversão | veredito manual | o gesto de transcrever reporta à Meta |

A ficha aparece **em dois lugares**, de propósito: na aba (fila de trabalho) e dentro
do card da conversa (o agente é perguntado "o que eu preenchi?" durante o atendimento
e não pode ter de largar o chat). Cada campo tem botão de **copiar** — transcrever é
digitar campo a campo na Extecamp, e é aí que nasce o erro de digitação que faz a
inscrição do lead sair errada lá.

**Notificações** compõe a fila na LEITURA a partir do que já existe (alertas, fichas
pendentes, aprovações) — não há um segundo caminho de escrita para desincronizar.
Alerta de tipo `inscricao` é filtrado: a fila de fichas já representa o mesmo fato, e
com estado melhor (some quando o trabalho é feito, em vez de depender de alguém
marcar como lido). `lido` é coluna nova em `alertas`, separada de `notificado` — uma
é a marca do humano na Central, a outra é o disparo externo ao operador.

**Dispensa (06/10/2026).** Todo item da fila de intervenção pode ser limpo: ✕ por item e
"Limpar tudo" (Aprovações e Notificações), via `POST /api/aprovacoes/dispensar`
(`{chave}` ou `{todas: true}`). Dispensar **não resolve nem apaga nada** — lead, proposta e
dúvida ficam como estão; a ação entra na auditoria. O servidor guarda a `marca` do item
(config `disp:<tipo>:<id>`): última fala do lead (manual), id da proposta/dúvida, status
do curso (captabilidade), `atualizado` (escalonamento). Mudou a marca, o item **volta
sozinho**. Fichas de inscrição não são dispensáveis (saem ao serem transcritas). O painel
"Alertas do sistema" mostra só alertas não lidos e tem o próprio "Limpar".

**Caixa única (19/10/2026).** Com mais gente usando a Central, as abas **Aprovações** e
**Dúvidas** foram absorvidas por Notificações — uma porta só para o trabalho humano. Cada
item de `GET /api/notificacoes` traz `categoria` (`aprovacao` · `duvida` · `ficha` ·
`alerta`, com filtros por categoria e contagem em `por_fonte`) e o que é preciso para agir
na própria linha: proposta com o texto completo (`mensagem`) → Aprovar e enviar / Editar;
dúvida com `contexto` e `ref` → resposta escrita ali mesmo, "É dado de catálogo → Injetor"
ou Descartar; escalonamento → Abrir / Resolvido; ontologia e curso encerrado → Injetor.
Captabilidade (prioridade 4) entrou: sem a aba antiga, era o único lugar onde ela sumiria.
O re-render do polling respeita foco — não recria a caixa de resposta de quem está digitando.
**Pacing fora da caixa**: `pacing_atrasado` (um alerta por curso por dia, ~90% da caixa em
produção) deixou de ser gravado (`growth/pacing.py`) e o legado é filtrado
(`ALERTAS_FORA_DA_CAIXA`); o ritmo segue no Painel/Medição. CPL estourado e realocação de
verba continuam alertando — são decisões de verba. `/api/aprovacoes` e `/api/duvidas`
continuam existindo (testes e integrações), só sem aba própria.

**Templates e campanhas completos (19/10/2026).** O marketing cria tudo na aba Templates:
cabeçalho (nenhum · texto com até 1 variável · imagem · vídeo · PDF), corpo com variáveis e
formatação do WhatsApp, rodapé e até 10 botões — resposta rápida, link (fixo ou dinâmico com
`{{1}}` no final), ligar e copiar cupom (só Marketing) —, com prévia no formato do WhatsApp e
validação dos limites da Meta antes da chamada (`whatsapp.validar_componentes`). Respostas
rápidas vão antes dos botões de ação (a Meta recusa intercalados). O arquivo do cabeçalho:
sobe para a Meta como amostra da revisão (Resumable Upload API do app, `META_APP_ID`) e fica
em `atena/data/tpl_midia`, servido em `/tplmidia/<nome>.<ext>` (público: a Meta busca o link
a cada envio). Os exemplos de cabeçalho/link/cupom viram o **padrão de envio**
(`config tpl_padrao:<nome>`). Na aba Campanhas, ao escolher o template, cada variável do corpo/
cabeçalho e cada botão dinâmico ganha uma fonte (primeiro nome, curso, código, fim das
inscrições ou texto fixo), a mídia do cabeçalho vem preenchida com o padrão, e a prévia usa o
1º lead do público. O disparo é recusado (400) se algum valor sair vazio para esse lead
(`_componentes_para`) — antes, template criado na Central saía com variável vazia e a Meta
recusava o lote inteiro. A configuração fica em `campanhas.config_json`. O envio avulso pela
conversa usa o mesmo caminho, com os padrões da criação.

### 8. Otimização — estrutura de RL (spec 18 §3.2/3.3) em modo sombra

Objetivo de controle explícito: **maximizar inscritos por curso** e **minimizar CPL**,
em `atena/avaliacao/otimizacao.py`. O que destravou a estrutura foi o rótulo: até
agora "inscrito" era digitado à mão na Verificação, e rótulo digitado não fecha ciclo
de aprendizado. A conclusão da etapa 2 é automática, datada e por curso.

- **Método**: bandit contextual (Thompson, Beta-Bernoulli) por variante de template
  da cadência. Não é deep RL porque o espaço de ação tem 3 templates e o dado é
  escasso — condições em que bandit responde com dezenas de amostras e PPO não
  responderia com milhares.
- **Gate (§3.4)**: ≥100 episódios com desfecho e ≥30 conversões. Abaixo disso o
  módulo roda em **sombra** — calcula e mostra a escolha, sem tocar na operação.
- **Corte do rótulo**: episódios só contam a partir do primeiro `etapa1` observado.
  Antes disso `inicio_inscricao` significava "clicou para ir à Extecamp"; contar os
  dois juntos envenenaria o treino com eventos de mesmo nome e sentido diferente.
- **CPL pode estar INDISPONÍVEL**: `ad_desempenho` é populado pela API da Meta e fica
  vazio sem `META_ADS_TOKEN`. Nesse caso o termo de custo é declarado indisponível na
  tela, em vez de virar zero — zero silencioso faria o objetivo parecer ótimo
  justamente quando se está cego.

### 9. Criar card manualmente no Funil + nome do perfil do WhatsApp (11/08/2026)

**Criar card** (`POST /api/kanban/criar`, botão "+ Novo lead" na aba Funil): cobre o
lead que não veio por ingestão automática (indicação, contato feito fora do
WhatsApp). Só telefone é obrigatório — nome e curso são opcionais (curso "a
descobrir" já é um estado normal do sistema, o mesmo de um lead que chega pelo
WhatsApp sem citar curso). **Idempotente por telefone**, a mesma regra de sempre
(CLAUDE.md §7): número que já tem card não ganha um segundo —
`EST.lead_por_telefone()` decide, exatamente a função que todo caminho de ingestão
já usa. Card duplicado tentado vira **atualização** do existente: nome só entra se
estava vazio (nunca sobrescreve o que já foi confirmado/corrigido), curso novo soma
como interesse adicional (`EST.add_curso_interesse`) em vez de substituir o ativo. A
tela mostra isso explicitamente — "este telefone já tinha um card" — e abre a
conversa existente, nunca finge que criou algo novo.

**Nome do perfil do WhatsApp** (`transporte/whatsapp.py::receber()`): a Meta manda,
junto do lote de mensagens, um array `contacts` (`wa_id` → `profile.name`) — o nome
que a PESSOA colocou no próprio perfil. É a única fonte de nome para quem chega
sozinho pelo WhatsApp, sem formulário. Sem isso o card nascia "wa-554199515077" e
ficava assim para sempre. Duas situações:
- **Card novo**: nasce com `nome` já preenchido pelo perfil.
- **Card já existente** (criado na mão, por outra integração, ou por uma mensagem
  anterior sem `contacts`): a próxima mensagem completa o nome — **só se ainda
  estiver vazio**. Essa guarda é a idempotência inteira: não existe tabela nova de
  "já processado", o próprio estado (nome vazio ou não) é a marca. Roda de novo com
  o mesmo resultado sempre, e **nunca** sobrescreve um nome que o operador já leu na
  conversa e corrigiu — mesmo que o perfil do WhatsApp mude ou traga outro texto.

**Fallback de exibição**: card sem nome nenhum (nem perfil, nem cadastro manual) não
mostra mais o `card_id` cru na tela — mostra o telefone formatado
(`/api/kanban`, `/api/conversas`, `/api/conversas/<id>` passaram a incluir
`telefone` para isso); só cai para o id se nem telefone houver. O `card_id`
("wa-...", "op-...", "meta-...") é chave interna — nunca é o que o operador lê como
nome de alguém.

### 10. Iniciar/reiniciar conversa a qualquer momento (16/08/2026)

Antes, o único jeito de abrir conversa com um lead sem histórico (ex.: quem só
entregou a ficha da etapa 2 pela LP e nunca passou pelo WhatsApp — spec 17 §7b) era
o operador achar sozinho o ícone de template no compositor, escolher `feq_lead`/
`feq_abertura_curso` na mão e preencher os parâmetros. `POST /api/conversas/<id>/nova`
reaproveita o caminho único do 1º contato (`_primeiro_contato`, o mesmo da ingestão
automática do Meta) atrás de **um botão só**, em dois lugares:

- **Tela vazia da conversa** ("Sem mensagens ainda") ganha um CTA "Iniciar conversa".
- **Cabeçalho da conversa** ganha um ícone sempre disponível (exceto opt-out) para
  recomeçar o atendimento **mesmo havendo histórico** — "sempre que quiser".

Não há conceito de thread separada por sessão (`conversas` continua um log único
por `card_id`, spec 12): "nova conversa" é reenviar a mensagem de abertura na MESMA
linha do tempo, sem apagar nada — o histórico anterior fica visível acima. Não exige
histórico prévio nem checa captabilidade do curso (ato deliberado do operador, mesmo
raciocínio de `conversa_enviar_template`); opt-out continua bloqueando (checado
dentro de `enviar_template`). `estado_conversa` volta para `aguardando_resposta`.

## Tempo real
SSE (ou WebSocket) para novas mensagens, mudança de contadores e novos itens na fila. Servido pelo
próprio monólito (sem segundo serviço nesta fase). **Implementado por polling (5s)**: a aba Conversas
separa a "moldura" (cabeçalho + campo de escrita, montada 1× ao abrir) das "mensagens" (atualizadas
no polling) — o `<textarea>` nunca é recriado, então foco/cursor do operador não se perdem.

## Múltiplos cursos de interesse (✅ implementado)
Um lead tem **um curso ativo** (`leads.curso` — o que o agente conversa / gate / template) e um
**conjunto de cursos de interesse** (`leads.cursos_interesse`, CSV; o ativo sempre incluído). Acumula
por: 2º formulário do mesmo telefone (ingest não troca o ativo), troca de interesse na conversa
(orquestrador), e adição manual (`POST /api/leads/<id>/interesse {curso, ativar?}`). Na conversa,
chips por curso (ativo com ★, clique em outro = tornar ativo). No **Funil**, filtro por curso
(`#kbFiltro`) casa contra o conjunto de interesse; o card mostra o ativo + `+N`.
`estado.cursos_interesse(lead)` devolve a lista.

## Stack recomendada
SPA leve servida pela API Flask (HTML/CSS/JS com glassmorphism; charts via SVG/lib leve). Mantém o
monólito coeso; evita um build pesado de frontend no estágio local. React/Vite fica como evolução se
a UI crescer.

## Segurança, LGPD e auditoria (não-negociável — a central expõe PII)
- **Autenticação + RBAC**: a central mostra telefone e conteúdo de conversa (PII) → login obrigatório
  e papéis (operador, supervisor).
- **Auditoria das ações humanas**: quem leu, enviou, interveio, aprovou — registrado (a trilha não é
  só dos agentes).
- **Mensagem manual passa pelo Revisor** por padrão (com override explícito e auditado do supervisor).
- **Opt-out e janela de horário** valem também para envio manual.

## Critérios de aceitação
- AC1 Operador vê a thread completa de qualquer lead em tempo (quase) real.
- AC2 Operador consegue pausar o agente e assumir/editar a próxima mensagem.
- AC3 Contadores do funil batem com o FARO/estado (fonte única).
- AC4 `/inspecao` mostra as âncoras de uma afirmação factual enviada.
- AC5 Acesso exige autenticação; ações humanas ficam auditadas.
- AC6 Envio manual respeita Revisor (salvo override auditado), opt-out e horário.
- AC7 Lead sem nenhuma mensagem (ex.: só ficha da etapa 2) pode ter a conversa
  iniciada pelo operador a qualquer momento; lead com histórico pode ser reiniciado
  do mesmo jeito, sem perder o que já foi conversado. Opt-out bloqueia os dois casos.
