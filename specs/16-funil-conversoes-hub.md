# Spec 16 — Hub do Funil de Conversões (ponte, conversões offline, ferramentas p/ agentes)

> Evolução da spec 15: o Growth Engine vira um **controlador de funil** com rastreamento
> full-funnel (Pixel + CAPI com desduplicação), feedback de matrículas (offline
> conversions) e uma camada de gestão de campanhas exposta como **ferramentas que um
> agente LLM aciona** (tool calling). O hub central é a Central de Operação existente
> (Flask, `atena/api/app.py`) — **não** se cria um segundo backend (FastAPI/Node);
> convenção da casa é stdlib + flask + requests (CLAUDE.md §7).

## 1. Os 3 pilares no hub

```
                         ┌──────────────── HUB (Flask :8095) ────────────────┐
 Anúncio Meta ──clique──►│ GET /ponte?curso=FEQ-XXXX   (bridge page pública)  │
                         │   modal Nome+WhatsApp+opt-in → Pixel IC (eventID)  │
                         │   POST /webhook/ponte ──► growth/ponte.py          │
                         │      lead (ingestão) + evento inicio_inscricao     │──► Extecamp
                         │      CAPI InitiateCheckout (MESMO event_id=dedup)  │    (redirect)
                         │                                                    │
 Operador FEQ ──CLI/CSV─►│ growth/matriculas.py  (Pilar 2: offline)           │
                         │   SHA-256 email/telefone → CAPI Purchase c/ valor  │
                         │   lead → fase Matriculado · evento matricula_conf. │
                         │                                                    │
 Agente IA (futuro) ────►│ growth/ferramentas.py  (Pilar 3: tool calling)     │
                         │   listar_campanhas · ritmo_cursos ·                │
                         │   custo_por_matricula/ROAS · aumentar_orcamento ·  │
                         │   pausar_ad — escrita passa pelo gate AUTO_APPLY   │
                         └──────────────────────────┬─────────────────────────┘
                                                    ▼
                              Meta Marketing API + Conversions API (meta_ads.py)
```

## 2. Pilar 1 — Bridge Page (LP two-step) com rastreamento full-funnel

A LP é uma **página ponte**: não tem formulário completo — ela "vende o clique"
e qualifica; o formulário oficial é o da Extecamp. Arquivos:
`atena/web/bridge-template.html` (Tailwind CDN) + `atena/web/bridge-logic.js`
(vanilla). Servida em `GET /ponte?curso=...` (dev/local) e publicada como
página órfã no WordPress (§7). **Toda a copy é ancorada na ontologia**
(objetivo, professor, carga, horário, plano, descontos, prazo) — a LP obedece
à mesma regra anti-alucinação do Revisor: sem fato sem âncora.

Jornada (two-step opt-in):

1. Lead chega do Meta Ads → Pixel dispara **`ViewContent`** no load.
2. Copy persuasiva (headline, benefícios ancorados, professor, FAQ). Os CTAs
   **não são links**: abrem o **modal** (overlay escuro, centrado, responsivo)
   com Nome + WhatsApp (máscara `(##) #####-####`) + consentimento LGPD.
3. No submit: valida (10–11 dígitos), gera `event_id` (UUID) — **regra de
   ouro**: o MESMO id vai no Pixel (`fbq('track','InitiateCheckout',{...},
   {eventID})`) e no `POST /webhook/ponte` → a Meta desduplica browser×servidor.
4. `fetch()` assíncrono (payload `{nome, whatsapp|telefone, curso_id|curso,
   event_id, fbp, fbc, url}`) com **timeout de 1,5 s**: resposta, falha ou
   estouro → `window.location.href` para a Extecamp **de qualquer forma**
   (a venda nunca espera o nosso backend; `keepalive` mantém o POST vivo).
5. Backend (`growth/ponte.py`): valida (telefone, curso no `cursos_metas`,
   `event_id` sane), ingere o lead (dedupe por telefone, `optin_fonte=ponte`),
   registra `inicio_inscricao` com o `event_id` + sinais de match
   (`fbp`/`fbc`, IP, user-agent, URL) e reenvia via CAPI
   (`action_source=website`).

Rotas públicas (fora de `/api`): `GET /ponte`, `POST /webhook/ponte` (com
CORS liberado + `OPTIONS`, porque a LP publicada no WordPress vive em outra
origem).

## 3. Pilar 2 — conversões offline (feedback loop)

Extecamp não tem webhook; quem sabe quem matriculou é o operador. CLI:

```bash
python3 -m atena.growth.matriculas add "+5519988880001" --curso FEQ-0480 --valor 3900
python3 -m atena.growth.matriculas add aluno@email.com --curso FEQ-1000 --valor 4200
python3 -m atena.growth.matriculas csv matriculados.csv   # colunas: telefone|email[,curso][,valor]
```

- **Hash SHA-256** antes de qualquer envio (email: trim+lowercase; telefone: E.164
  digits-only) — PII nunca sai em claro (`meta_ads._user_data`).
- Telefone casa o lead local (tolerante ao 9º dígito) → fase **Matriculado** +
  evento `matricula_confirmada` (com `valor`). E-mail sem lead local ainda envia
  a conversão (o estado não guarda e-mail hoje — YAGNI; o hash vai só à Meta).
- CAPI `Purchase` com `currency=BRL`, `value=<valor>`, `event_id=matricula_confirmada-<card>`
  (idempotente em reenvio). Match quality: em + ph quando ambos existem.

## 4. Pilar 3 — gestão de campanhas e fundação p/ agentes

`meta_ads.py` (Marketing API): `campanhas()`, `conjuntos()`, `anuncios()` (leitura),
`orcamento()`, `definir_orcamento()`, `pausar()` (escrita). Dry-run sem token.

`ferramentas.py` — contrato de tool calling (formato Claude `input_schema`):

| Ferramenta | Tipo | Guardrail |
|---|---|---|
| `listar_campanhas` | leitura | — |
| `ritmo_cursos` | leitura | — |
| `custo_por_matricula` | leitura | gasto ÷ matrículas offline; ROAS = Σvalor ÷ gasto |
| `aumentar_orcamento(objeto_id, percentual)` | escrita | clamp ±50%/chamada; piso `GROWTH_BUDGET_MIN_CENTS`; **gate `GROWTH_AUTO_APPLY`** |
| `pausar_ad(ad_id, motivo)` | escrita | `motivo` obrigatório; **gate `GROWTH_AUTO_APPLY`** |

`executar(nome, args, g, ads, est)` despacha; **toda escrita sem
`GROWTH_AUTO_APPLY=1` vira sugestão** em `acoes_orcamento` (aplicado=0) + alerta —
mesma governança híbrida da spec 15. Tudo fica auditável em `acoes_orcamento`.

## 5. Mudanças de schema e eventos

- `eventos_funil` ganha `valor REAL` (migração idempotente). O `event_id` enviado
  à CAPI já é persistido na coluna `capi` — é o vínculo lead↔evento↔Meta.
- **Status do funil é derivado, não duplicado** (`funil.status_funil`):
  `enrolled` (fase Matriculado ou `matricula_confirmada`) · `initiated_checkout`
  (qualquer evento de checkout) · `lead` (resto). A coluna `fase` do lead já é o
  status de negócio; criar outra coluna seria estado duplicado.
- Mapa evento→CAPI revisado (atualiza a spec 15 §5):

| Evento local | Evento CAPI | Origem |
|---|---|---|
| `inicio_inscricao` | InitiateCheckout | ponte (website) ou operador |
| `boleto_gerado` | InitiateCheckout | operador |
| `inscricao_confirmada` | CompleteRegistration | operador / motor |
| `matricula_confirmada` | Purchase (valor real) | CLI de matrículas |

- `optin_fonte` agora reflete a origem real (`meta_lead_ad` ou `ponte`).

## 6. Recuperação pós-ponte (WABA)

Extecamp não confirma pagamento por webhook; a régua cobre o vão
(`funil.recuperar`, cron a cada 30 min):

- Espera por tipo de checkout: **`inicio_inscricao` (ponte): 2 h** ·
  `boleto_gerado`: 24 h. Se o lead **respondeu no WhatsApp depois do
  checkout**, a régua NÃO toca — a conversa é do cérebro/orquestrador.
- Toques escalonados (máx. 3, ≥48 h entre eles, teto 25/passada):
  **1º = `feq_lead`** (nome, curso — botões "Receber o link"/"Quero tirar
  dúvidas", o equivalente aprovado de "teve alguma dificuldade? posso
  ajudar"); **2º+ = `feq_lembrete_inscricao`** (nome, curso, prazo real).
  Texto livre estilo "Oi [Nome], vi que você iniciou..." exigiria template
  novo aprovado pela Meta (business-initiated fora da janela de 24 h só
  com template) — se aprovado um `feq_recuperacao_inscricao`, basta trocar
  a constante.
- Guardrails de sempre: opt-out, captabilidade, dry-run sem token.

## 7. Deploy automatizado no WordPress (páginas órfãs)

`atena/growth/wp_deploy_bridge.py` publica as 4 LPs via **WP REST API**
(`POST /wp-json/wp/v2/pages`, auth Application Password):

```bash
python3 -m atena.growth.wp_deploy_bridge            # 4 cursos do cursos_metas + ontologia
python3 -m atena.growth.wp_deploy_bridge --config cursos.json   # override
python3 -m atena.growth.wp_deploy_bridge --saida out/          # só renderiza (sem WP = dry-run)
```

- Config default = `cursos_metas` (curso, turma, URL Extecamp) + copy da
  ontologia; JSON externo pode sobrescrever qualquer campo.
- Página órfã: slug não linkado + `<meta name="robots" content="noindex">` no
  próprio HTML + tentativa de `_yoast_wpseo_meta-robots-noindex` (ignorada se
  não houver Yoast).
- O template recebe `{{WEBHOOK_URL}}` absoluto (`ATENA_PUBLIC_URL` +
  `/webhook/ponte`) — a LP no WordPress fala com o hub no VPS (CORS §2).
- Sem `WP_URL`/`WP_USER`/`WP_APP_PASSWORD` no ambiente: dry-run (renderiza os
  HTMLs em disco e não publica nada).
- Idempotente: se já existe página com o mesmo slug, atualiza em vez de duplicar.

Env: `WP_URL`, `WP_USER`, `WP_APP_PASSWORD`, `ATENA_PUBLIC_URL`.

## 8. Critérios de aceitação

1. `POST /webhook/ponte` com event_id X → payload CAPI sai com `event_id=X` e
   `action_source=website` (desduplicação garantida) e resposta traz o redirect da turma.
2. Telefone inválido ou curso desconhecido na ponte → 4xx lógico, nada gravado.
2b. LP renderiza sem tokens `{{...}}` sobrando; CTA abre o modal; submit redireciona
   à Extecamp mesmo com o backend fora do ar (timeout 1,5 s). Checkout da ponte sem
   conversão nem resposta → `feq_lead` após 2 h; lead que respondeu não recebe régua.
   Deploy sem credenciais WP renderiza os 4 HTMLs e não publica.
3. `matriculas add` por telefone → lead Matriculado, Purchase com valor e hashes SHA-256.
4. `matriculas add` por e-mail nunca envia o e-mail em claro (só o hash).
5. `ferramentas.executar("aumentar_orcamento", {percentual: 200})` → clamp em +50%;
   sem AUTO_APPLY nada muda na Meta e a sugestão fica registrada.
6. `pausar_ad` sem motivo → recusado.
7. Sem tokens, ponte/CLI/ferramentas rodam de ponta a ponta em dry-run.
