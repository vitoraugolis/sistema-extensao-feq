# Spec 17 — Fluxo consolidado: LP inteligente no domínio FEQ

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

## 3. A LP (inspiração: PUC Online, 100% ancorada)

Seções, na ordem (todas com `data-secao` para a telemetria):

1. **Hero** — tipo+modalidade, título, objetivo (ontologia), urgência legítima
   ("inscrições até <prazo>"), CTA primário.
2. **Cards de valor** — carga horária, horário ("pensado para quem trabalha"),
   parcelamento/desconto (plano da ontologia).
3. **Conteúdo programático** — expansível (`<details>`), direto de
   `conteudoProgramatico` da ontologia (o equivalente às "disciplinas" da PUC).
4. **Para quem é** — requisito acadêmico + público-alvo.
5. **Professor responsável** — nome + vínculo FEQ/Unicamp.
6. **Investimento** — parcelas, à vista, taxa de inscrição, descontos (ex.: 10%
   alunos/ex-alunos Unicamp), com urgência do prazo real e CTA.
7. **FAQ** — respostas ancoradas (requisito, formato, valores, prazo, jornada
   Extecamp).
8. **CTA final** + rodapé institucional.

"Fricção positiva": a LP entrega as informações essenciais sem esconder nada —
quem clica no CTA já se autoqualificou. O modal (Nome + WhatsApp + LGPD) é a única
fricção, e o redirect à Extecamp nunca espera o backend (timeout 1,5 s, spec 16 §2).

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
respondeu após o checkout (a conversa passa ao cérebro). Boleto continua esperando
24 h para o 1º toque. Teto de 25 envios/passada.

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
