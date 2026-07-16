# Spec 15 — Growth Engine (RevOps: pacing, Meta Ads, CAPI e recuperação)

> Motor autônomo de crescimento para as 4 turmas-alvo: monitora o ritmo de inscrições
> contra a meta de segurança, otimiza mídia (CPL + realocação de verba) e recupera
> checkouts abandonados via WABA. **Sugere sempre; só aplica mudança externa (pausar
> anúncio, mover verba) com `GROWTH_AUTO_APPLY=1`** — coerente com a governança híbrida
> (spec 07). Código em `atena/growth/`.

## 1. Cursos-alvo e metas

| Curso/Turma | Meta real | Meta segurança (+30%) | Prazo (ontologia) |
|---|---:|---:|---|
| FEQ-0480/009 | 50 | 65 | inscrições até 16/08/2026 |
| FEQ-0580/001 | 30 | 39 | inscrições até 22/07/2026 |
| FEQ-0333/001 | 25 | 33 | (sincronizado da ontologia) |
| FEQ-1000/018 | 70 | 91 | inscrições até 05/08/2026 |

Metas ficam em `cursos_metas` (seed no primeiro uso). O **prazo é sincronizado da
ontologia** (`inscricoes_fim`) a cada passada — a ontologia governada continua sendo a
única fonte de datas; a coluna `prazo` é só cache/fallback.

## 2. Arquitetura e fluxo

```
Meta Lead Ads ──webhook──► Ingestão (spec 04) ── opt-in LGPD registrado ──► leads (estado)
                                   │ ≤2 min                                     │
                                   ▼                                            ▼
                     Orquestrador + Cérebro + Revisor (specs 05/12) ──► WABA (spec 09)
                                   │  boas-vindas, dúvidas, nutrição            ▲
   eventos de checkout             ▼                                            │
   (Extecamp: iniciou /   ┌── atena/growth ─────────────────────────────┐       │
   boleto / confirmou)──► │ funil.py   scoring + CAPI + recuperação 24h │───────┘ (templates)
   POST /api ou CLI       │ pacing.py  ritmo vs meta · regra CPL ·      │
                          │            realocação de verba              │
   Meta Marketing API ◄───│ meta_ads.py insights · pause · budget · CAPI│──► Pixel (CAPI)
                          │ db.py      cursos_metas · ad_desempenho ·   │
                          │            eventos_funil · acoes_orcamento  │
                          └── motor.py (cron: pacing 1×/dia ·           │
                                        recuperar a cada 30 min) ───────┘
```

Reuso deliberado (nada disto é reimplementado aqui):
- **Boas-vindas ≤2 min**: já é o fluxo ingestão→orquestrador→WABA (specs 04/12/09).
- **Interações dinâmicas** (cronograma, PDF, link de inscrição): cérebro + Revisor (spec 05).
- **Envio WABA** (janela 24h, opt-out, horário, dry-run sem token): `transporte/whatsapp.py`.
- **Cadência fria genérica**: `agentes/nutricao.py`. O growth só adiciona a régua
  específica de checkout abandonado.

## 3. Schema SQL (SQLite, mesmo arquivo `atena/data/atena.db`, WAL)

```sql
-- metas e prazos por turma (seed das 4 turmas-alvo)
CREATE TABLE cursos_metas(
  curso TEXT PRIMARY KEY,            -- FEQ-0480
  turma TEXT, url TEXT,
  meta_real INTEGER, meta_seg INTEGER,
  prazo TEXT,                        -- ISO; cache da ontologia (inscricoes_fim)
  inscritos_extra INTEGER DEFAULT 0, -- inscrições fora do funil (ajuste manual)
  atualizado TEXT);

-- métricas de mídia por dia × conjunto (espelho local dos Insights)
CREATE TABLE ad_desempenho(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  dia TEXT, objeto_id TEXT, nome TEXT, campanha TEXT, curso TEXT,
  gasto REAL, impressoes INTEGER, cliques INTEGER, leads INTEGER,
  UNIQUE(dia, objeto_id));

-- eventos de conversão (alimentam inscritos, CAPI e a régua de recuperação)
CREATE TABLE eventos_funil(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  card_id TEXT, curso TEXT,
  tipo TEXT,                         -- inicio_inscricao | boleto_gerado |
                                     -- inscricao_confirmada | recuperacao_enviada
  ts TEXT, capi TEXT);               -- event_id enviado à CAPI (NULL = não enviado)

-- trilha de decisões de mídia (sugestões e aplicações)
CREATE TABLE acoes_orcamento(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT, tipo TEXT,                -- pausa_cpl | realocacao
  objeto_id TEXT, detalhe TEXT, aplicado INTEGER DEFAULT 0);
```

No `leads` (estado.py) entram `optin_ts` e `optin_fonte` — **registro explícito do
opt-in LGPD** na ingestão (formulário Lead Ads com consentimento = opt-in).

`inscritos(curso)` = `COUNT(DISTINCT card_id)` de `inscricao_confirmada` + `inscritos_extra`.

## 4. Pacing (pacing.py)

Por curso, diariamente:

```
faltam          = max(0, meta_seg − inscritos)
dias_restantes  = max(1, prazo − hoje)
vel_necessaria  = faltam / dias_restantes          # inscrições/dia exigidas
vel_7d          = confirmadas nos últimos 7 dias / 7
razao           = vel_7d / vel_necessaria
status: concluido (faltam=0) · sem_prazo · atrasado (razao<0,9) ·
        no_ritmo (0,9–1,25) · adiantado (>1,25)
```

**Realocação**: havendo ao menos um `adiantado` e um `atrasado`, mover
`GROWTH_SHIFT_PCT`% (default 20) da verba diária do mais adiantado para o mais
atrasado. A verba do doador é lida ao vivo (adsets ativos do curso); piso de
`GROWTH_BUDGET_MIN_CENTS` por adset. Sem `GROWTH_AUTO_APPLY=1` a ação fica só
registrada em `acoes_orcamento` (aplicado=0) + alerta na Central.

**Regra de CPL**: por adset, janela de 3 dias — se `gasto ≥ GROWTH_CPL_MAX` e
(`leads = 0` ou `gasto/leads > GROWTH_CPL_MAX`) → pausar (mesma regra
sugerir/aplicar). Não repete a mesma sugestão em <3 dias.

Curso ↔ campanha: código `FEQ-XXXX` no nome da campanha/conjunto
(`ingestao.curso_de_texto`, padrão já acordado na spec 04).

## 5. CAPI e funil (funil.py + meta_ads.py)

| Evento local | Evento CAPI | Quando |
|---|---|---|
| `inicio_inscricao` | InitiateCheckout | lead abriu a inscrição (ponte/Extecamp) |
| `boleto_gerado` | InitiateCheckout | boleto emitido |
| `inscricao_confirmada` | CompleteRegistration | inscrição efetivada |
| `matricula_confirmada` | Purchase (valor real, BRL) | matrícula offline (spec 16) |

- `user_data.ph` = SHA-256 do telefone E.164 (digits-only, DDI 55). Sem e-mail no estado.
- `event_id = "<tipo>-<card_id>"` (dedup na Meta). `action_source=system_generated`.
- `GROWTH_TEST_EVENT_CODE` roteia para Test Events durante homologação.
- Extecamp não tem webhook: eventos entram por `motor.py evento <card_id> <tipo>`
  (operador/integração futura). Purchase com value 0: Extecamp não expõe valor pago.

**Lead scoring** (`funil.score`, computado do histórico — nada a sincronizar):
respondeu +30 · ≥3 msgs +10 · perguntou preço/cronograma/inscrição +20 ·
`inicio_inscricao` +40 · `boleto_gerado` +60 · `inscricao_confirmada` = 100 ·
opt-out = 0. Classes: quente ≥70 · morno ≥40 · frio <40.

**Recuperação (carrinho abandonado)**: checkout sem `inscricao_confirmada` → régua de
templates aprovados. Espera por tipo (spec 16 §6): `inicio_inscricao` (ponte) 2 h ·
`boleto_gerado` 24 h. Escalonamento: 1º toque `feq_lead` (ajuda), 2º+ `feq_lembrete_inscricao`
(nome, curso, prazo — urgência legítima: o prazo é real). Lead que respondeu no WhatsApp
após o checkout sai da régua (a conversa é do cérebro). Máx. 3 toques por lead, intervalo
≥48 h, teto de 25 envios/passada, opt-out/captabilidade/dry-run (guardrails do transporte).

## 6. Resiliência (zero intervenção)

- Meta Graph: backoff exponencial (1→2→4→8 s) em HTTP 429/5xx e códigos de rate
  limit (4, 17, 32, 613, 8000x). Falha final loga e **não derruba a passada**.
- Sem `META_ADS_TOKEN`/`META_PIXEL_ID`/`WHATSAPP_TOKEN`: tudo vira dry-run
  (leitura vazia, escrita simulada) — o motor roda de ponta a ponta em dev.
- SQLite WAL compartilhado com o estado; escrita idempotente
  (`INSERT OR REPLACE` por `(dia, objeto_id)`).

## 7. Variáveis de ambiente (adicionar ao .env)

```
META_ADS_TOKEN=            # token com ads_read + ads_management
META_AD_ACCOUNT_ID=act_... # conta de anúncios
META_PIXEL_ID=             # pixel/dataset p/ CAPI
META_CAPI_TOKEN=           # token da CAPI (default: META_ADS_TOKEN)
GROWTH_CPL_MAX=60          # R$/lead — teto crítico (pausa)
GROWTH_SHIFT_PCT=20        # % de verba movida na realocação
GROWTH_BUDGET_MIN_CENTS=1000  # piso diário por adset (R$10)
GROWTH_AUTO_APPLY=0        # 1 = aplica pausas/realocação sem humano
GROWTH_TEST_EVENT_CODE=    # homologação da CAPI
```

## 8. Como rodar

```bash
python3 -m atena.growth.motor status      # ritmo por curso, sem efeito colateral
python3 -m atena.growth.motor pacing      # insights + ritmo + CPL + realocação
python3 -m atena.growth.motor recuperar   # régua de checkout abandonado
python3 -m atena.growth.motor evento <card_id> <tipo>   # registra evento de funil (+CAPI)
python3 atena/tests/test_growth.py        # testes offline
```

Cron (crontab -e):
```
30 7 * * *    cd <repo> && set -a && . .env && set +a && python3 -m atena.growth.motor pacing
*/30 * * * *  cd <repo> && set -a && . .env && set +a && python3 -m atena.growth.motor recuperar
```

## 9. Critérios de aceitação

1. `status` mostra, por curso: inscritos, faltam, vel. necessária, vel. 7d, status.
2. Curso atrasado + curso adiantado → ação `realocacao` registrada; aplicada só com AUTO_APPLY.
3. Adset com CPL > teto → ação `pausa_cpl`; não repete em <3 dias.
4. `evento ... inscricao_confirmada` → Purchase na CAPI (ph = SHA-256) e inscritos +1.
5. Boleto há 25 h sem confirmação → 1 template de recuperação; nada antes de 48 h do toque.
6. Lead ingerido com consentimento → `optin_ts`/`optin_fonte` preenchidos.
7. Sem tokens no ambiente, todos os comandos rodam em dry-run sem erro.
