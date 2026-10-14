# CLAUDE.md — Projeto Atena × FARO CRM (Captação de Alunos FEQ Unicamp)

> Este arquivo é o contexto-mestre do projeto. O Claude Code o carrega automaticamente.
> As **specs em `specs/` são a fonte da verdade**: ao implementar, siga a spec; se a spec
> estiver errada ou incompleta, atualize-a *antes* de codar. Código sem spec correspondente
> é dívida técnica.

## 1. O que é este projeto

Um CRM operado por agentes de IA para captar alunos dos cursos da Extensão FEQ Unicamp.
Leads vêm de campanhas Meta/Google, e agentes autônomos (com guardrails) fazem contato,
qualificação, nutrição e suporte à conversão pelo WhatsApp, movendo cada lead por:

```
Lead → Prospect → Inscrito → Taxa aceita → Taxa paga → Documento entregue → Matriculado
                                        (saídas: Em espera · Perdido · Cancelado)
```
Os quatro degraus entre Inscrito e Matriculado são o que a coleta do ADM Cursos da
Extecamp deixa ver de dentro do processo (spec 20). Nunca teste fase por lista de
strings: use `estado.alcancou(fase, "Inscrito")` — a esteira cresce.

A orquestradora se chama **Atena**. O sistema de registro (system of record) é o
**FARO CRM** (já existente, API REST sobre Supabase). A Atena NÃO armazena dados de lead —
ela lê/escreve no FARO via API e conversa pelo WhatsApp Business Cloud.

## 2. Decisões de projeto (fixadas)

| Tema | Decisão |
|---|---|
| Modelo de acesso | **Híbrido**: leitura livre; ação externa/irreversível exige aprovação humana. |
| Autonomia dos agentes | **Autônomo com guardrails** (ver `specs/07`). |
| WhatsApp | **API oficial WhatsApp Business Cloud**. |
| System of record | **FARO CRM**, pipeline `[FEQ] CRM`. Não criamos banco próprio. |
| Infra inicial | Máquina local; deploy gradual depois (VPS a definir). |
| Gate acadêmico | **Os cursos da FEQ exigem ensino superior completo** (dado por curso, não regra fixa — ver `specs/08`). ECO-1100 (Instituto de Economia, fora da FEQ) não exige. |

## 3. Arquitetura em uma tela

```
Meta/Google Lead Ads
        │ (webhook)
        ▼
[Fase 1] Ingestão  ──►  FARO CRM (fase "Lead")     feq_ingestao_meta_faro.py  :8080
        │                     ▲
        ▼                     │ api-cards-*
[Fase 2] Cérebro + Revisor ───┘                    feq_cerebro_revisor.py     :8090
        │  (WhatsApp Business Cloud — transporte a construir, Fase 2b)
        ▼
[Fase 3] Inscrição (Extecamp)  →  [Fase 4] Matrícula (aprovação humana)
```

Detalhes em `specs/01-arquitetura.md`.

## 4. Estrutura do repositório

```
CLAUDE.md                       # este arquivo
specs/                          # ESPECIFICAÇÕES (fonte da verdade)
  00-visao-geral.md
  01-arquitetura.md
  02-modelo-de-dados-faro.md
  03-integracao-faro-api.md
  04-fase1-ingestao.md
  05-fase2-cerebro-revisor.md
  06-fase3-inscricao-fase4-matricula.md
  07-guardrails-lgpd-governanca.md
  08-base-conhecimento.md
  09-transporte-whatsapp.md
  10-testes-criterios-aceitacao.md
  11-ontologia-anchoring.md · 12-orquestracao-estado.md · 13-injetor-evolucao-ontologia.md · 14-central-operacao-frontend.md
  15-growth-engine.md           # RevOps: pacing, Meta Ads/CAPI, recuperação de checkout
  19-funil-professores.md       # módulo RESTRITO (master concede acesso): prospecção de docentes
  16-funil-conversoes-hub.md    # bridge page (dedup Pixel×CAPI), matrículas offline, ferramentas p/ agentes
  17-fluxo-consolidado-lp.md    # FLUXO DEFINITIVO: LP órfã no WP FEQ, telemetria de sessão, 5 toques, conversão offline via Central
atena/                          # SISTEMA v2 (monólito modular) — ver atena/README.md
  ontologia/  core/  agentes/  revisor/  transporte/  api/  web/  growth/  tests/
feq_ingestao_meta_faro.py       # legado v1 (referência)
feq_cerebro_revisor.py          # legado v1 (referência)
faro_probe.py                   # utilitário: sonda fases/campos do FARO
cursos_feq.json                 # base de conhecimento (grounding do Revisor)
Base_Conhecimento_Cursos_FEQ_v1.md
README_ingestao.md
config.example.env              # modelo de variáveis de ambiente
Atena_*.docx                    # documentos de design (referência humana)
```

## 5. Variáveis de ambiente (.env — nunca versionar)

```
FARO_KEY=sk_live_...            # chave do FARO escopada ao pipeline [FEQ] CRM
FARO_PIPELINE_ID=49d65f66-ec1d-4c10-a5ff-71aef07f9279
ANTHROPIC_API_KEY=sk-ant-...    # cérebro e passada semântica do Revisor
ANTHROPIC_MODEL=claude-sonnet-4-6
WHATSAPP_TOKEN=...              # token permanente (System User) do WABA
PHONE_NUMBER_ID=991579807382963
META_VERIFY_TOKEN=...           # handshake do webhook Meta
META_APP_SECRET=...             # verifica X-Hub-Signature-256 dos webhooks (v2)
ATENA_USERS=nome:senha:supervisor,...   # Central v2 (sem isto: operador/operador DEV)
ATENA_SECRET=...                # chave de sessão da Central v2
ATENA_MASTER=vitor              # dono dos módulos restritos (spec 19) — só ele concede acesso
OPENAI_API_KEY=sk-...           # Whisper: transcrição dos áudios do lead (spec 09)
PORT=8080                       # 8090 cérebro v1 · 8095 Central v2
# Growth Engine (spec 15) — sem estes tokens, tudo roda em dry-run:
META_ADS_TOKEN=... META_AD_ACCOUNT_ID=act_... META_PIXEL_ID=... META_CAPI_TOKEN=...
GROWTH_CPL_MAX=60 GROWTH_SHIFT_PCT=20 GROWTH_AUTO_APPLY=0 GROWTH_TEST_EVENT_CODE=...
```

## 6. Como rodar e testar

```bash
pip install flask requests rdflib pyshacl
set -a && source .env && set +a

# Sistema v2 (atena/) — PRODUÇÃO no VPS Oracle Cloud 144.33.22.236 (systemd atena+caddy),
# migrado do Lightsail em 13/10/2026 (atena/ops/RUNBOOK_migracao_vps.md). Chave: ~/.ssh/oracle-b.key
# https://feq.vitoraugolis.com.br (domínio definitivo) · https://144.33.22.236.sslip.io (fallback por IP)
# NUNCA rode o deploy no Lightsail antigo (3.218.159.36): ele é só um proxy Caddy para o
# servidor novo e o deploy religaria o serviço atena lá (mensagens duplicadas aos leads).
zsh atena/ops/deploy_vps.sh 144.33.22.236 ubuntu   # deploy (rsync; NÃO sobrescreve
#   .env, atena/data nem a ontologia — catalogo.jsonld/versoes são dado de produção,
#   escritos pelo Injetor; sobrescrevê-los desfazia mudanças aprovadas em silêncio)
# (launchd local br.unicamp.feq.atena foi desativado no cutover de 07/07/2026)
python3 atena/api/app.py            # dev manual · http://localhost:8095 (bootstrap: operador/operador até criar usuários reais)
for t in revisor orquestrador injetor agentes growth; do python3 atena/tests/test_$t.py; done
python3 atena/ontologia/validar.py  # valida a ontologia (SHACL)

# Growth Engine (spec 15) — cron: pacing 07:30 · recuperar */30
python3 -m atena.growth.motor status      # ritmo por curso (sem efeito colateral)
python3 -m atena.growth.motor pacing      # insights + ritmo + CPL + realocação
python3 -m atena.growth.motor recuperar   # régua de checkout abandonado (24h)
python3 -m atena.growth.motor evento <card_id> inscricao_confirmada   # funil + CAPI
python3 -m atena.growth.matriculas add <tel|email> --curso FEQ-0480 --valor 3900  # offline -> Purchase
# Bridge page (spec 16): GET /ponte?curso=FEQ-0480 · POST /webhook/ponte (públicas, CORS)
python3 -m atena.growth.wp_deploy_bridge   # publica as 4 LPs no WordPress (sem creds = dry-run)

# --- Legado v1 (referência) ---
# Fase 1 — ingestão (porta 8080)
python3 feq_ingestao_meta_faro.py
curl -X POST localhost:8080/simulate -H 'Content-Type: application/json' \
  -d '{"nome":"Teste","telefone":"+55 (19) 99999-0001","curso":"FEQ-0480"}'

# Fase 2 — cérebro + revisor (porta 8090)
python3 feq_cerebro_revisor.py
curl -X POST localhost:8090/reviewer -H 'Content-Type: application/json' \
  -d '{"curso":"FEQ-0480","texto":"O curso custa R$ 999,00."}'   # deve bloquear

# Sonda da estrutura do FARO
python3 faro_probe.py
```

## 7. Convenções de código

- Python 3, stdlib + `flask` + `requests`. Sem framework pesado nesta fase.
- Toda chamada ao FARO passa por um helper com backoff exponencial em 5xx.
- Segredos só via `os.environ`. Nunca logar chave, token ou PII em texto.
- Dedupe de lead **sempre por telefone** (digits-only) antes de criar card.
- Mensagem ao lead **sempre** passa pelo Revisor antes de "enviar".
- Datas: entrada/saída humana em DD/MM/AAAA; sistema em ISO 8601.
- Fuso: **America/Sao_Paulo (BRT)** sempre. `atena/__init__.py` fixa `TZ` no import do
  pacote, então `datetime.now()` ingênuo já sai em BRT mesmo com o host em UTC (caso do
  VPS). Script que rode fora do pacote precisa `import atena` para herdar isso.

## 8. Definition of Done (por entrega)

1. Atende a spec correspondente em `specs/`.
2. Tem teste reproduzível (endpoint de simulação ou unitário).
3. Respeita os guardrails de `specs/07` e a governança híbrida.
4. Não introduz caminho que envie mensagem/gaste dinheiro sem aprovação.
5. Atualiza a spec se o comportamento mudou.

## 9. Status atual (jun/2026)

Reescrita **v2** (monólito modular `atena/`, ancoragem ontológica) implementada e testada
— 29 checks: revisor 12 · orquestrador 7 · injetor 4 · agentes 6.
- ✅ Ontologia JSON-LD + SHACL + serviço de consulta (`specs/11`); `validar.py` verde.
- ✅ Estado SQLite + orquestrador (máquina de estados, Revisor fail-closed, escalonamento real) (`specs/12`).
- ✅ Cérebro LLM com fallback determinístico offline; ingestão Variante B; transporte WhatsApp (dry-run sem token).
- ✅ Injetor de ontologia: diff + impacto + SHACL + aprovação + rollback (`specs/13`).
- ✅ Central de Operação (Flask + SPA glass) com auth/RBAC/auditoria (`specs/14`).
- ✅ Growth Engine (`atena/growth/`): pacing 4 turmas, regra de CPL, realocação de verba,
  CAPI, scoring, recuperação de checkout 24h, opt-in LGPD (`specs/15`).
  Aplicação externa (pausar/mover verba) só com `GROWTH_AUTO_APPLY=1`; default sugere.
- ✅ Hub do funil (`specs/16`): bridge page `/ponte` (Pixel×CAPI com desduplicação por
  event_id), CLI de matrículas offline (SHA-256 → Purchase c/ valor, ROAS), ferramentas
  de tool calling p/ agentes (`growth/ferramentas.py`, mesmo gate AUTO_APPLY).
- ✅ Fluxo consolidado (`specs/17`) — 84 checks: LP completa estilo PUC (copy 100%
  ancorada na ontologia, destino `/curso/inscricao/`), telemetria de sessão
  (`lp_sessoes`, `/api/lp/analytics`, ferramenta `analise_lp`), botão WhatsApp,
  cadência de 5 toques, conversão offline ao mover card na Central (CAPI), deploy
  WP em bloco `wp:html` com validação pós-publicação.
- ✅ Coleta Extecamp (`specs/20`) **conferida contra o sistema real** (30/08/2026, 86 checks):
  nos 9 oferecimentos operados, total lido = total declarado pela página, e o relatório de
  contatos bate com a listagem. Corrigido bug que fazia a listagem *em andamento*
  (`cursoand.asp`) devolver zero em silêncio. Painel mostra inscritos+matriculados da turma
  corrente contra o mínimo de alunos.
- ⏳ Fases 3 e 4 (inscrição/matrícula) (`specs/06`); envio WhatsApp e cérebro LLM reais dependem de chaves.
- ⏳ Legado v1 (`feq_*.py`) mantido só como referência.
- ✅ Catálogo multi-instituto (16/09/2026, `specs/08` § "Cursos fora da FEQ"): **ECO-1100**
  (Instituto de Economia, não-FEQ) inserido na ontologia — sem gate acadêmico, landing page
  externa (`eco-1100-landing.vercel.app`), fora de `cursos_metas` (logo fora do deploy WP, do
  Growth Engine e da varredura da coleta Extecamp, que já são todos escopados a `cursos_metas`).
  `atena.ontologia.servico.CODE_RE` virou a fonte única do regex de código de curso (antes
  hardcoded `FEQ[-\s]?(\d{3,4})` em 5 arquivos — nenhum deles reconheceria "ECO-1100").

- ✅ **FEQ-1150 e FEQ-0295 com LP e card na home** (05/10/2026, `specs/08` § "Difusão de
  oferecimento contínuo"): FEQ-1150 ("Processos sob Controle: do PID ao MPC") é difusão de
  oferecimento contínuo — declaração de frequência (não certificado), à vista, sem turma a
  formar; LP e guia derivam isso da ontologia (`continuo`, `certificacao`). FEQ-0295 ganhou a
  taxa de inscrição (R$ 24) e o link de inscrição em `cursos_metas`. `test_continuo.py` (20 checks).

- ✅ **Descontos e contato da secretaria** (05/10/2026, `specs/08`): FEQ-0480 dá 15% no pagamento
  à vista (campo `descontos`; aplicado no boleto, na linha de descontos). **Descontos nunca
  acumulam** (regra no prompt do cérebro). Contato da Secretaria FEQ — extfeq@unicamp.br ou
  WhatsApp (19) 3521-3911 — agora entra no prompt (`contatos.secretaria_texto`); o antigo
  WhatsApp (19) 98262-0072 foi removido de tudo.

## 10. Riscos e pendências abertas

- ✅ FEQ-0499 confirmado em inscrição (turma **006**, até 06/02/2027) na coleta de
  30/08/2026 — mas `cursos_metas` ainda diz turma `001`: corrigir o cadastro.
- FEQ-0333 e FEQ-0655 não têm oferecimento em inscrição nem em andamento (coleta 30/08/2026).
- ✅ Identidade institucional do agente por curso (16/09/2026, `atena/ontologia/servico.py::
  instituicao_do_codigo`): system prompt do cérebro, respostas fixas do orquestrador e cartão de
  contato do WhatsApp (`atena/core/contatos.py`, chave `secretaria_ie` — Rafaela Sanches/Solange
  Gonçalves, (19) 3521-5728 e (19) 3521-0119, iecoext@unicamp.br, 14h-22h, confirmado por Vitor)
  agora variam por instituto (FEQ vs Instituto de Economia). Sem confirmação de WhatsApp nos
  números do IE — `whatsapp: False` até alguém testar (mesma trava que evita repetir o bug de
  10/08/2026 na secretaria FEQ).
- ✅ **Guia em PDF por instituto** (26/09/2026, `atena/growth/pdf_guia.py::INSTITUTOS`): incidente —
  o lead Giuliana (ECO-1100, `wa-5511940463127`) recebeu às 13:07 o guia com marca e contatos da
  FEQ. Agora logo (`web/assets/ie.png`), textos, contatos e selos vêm do instituto; ECO-1100 sai
  com "Declaração de frequência" (não certificado) e sem os selos da FEQ (aulas ao vivo, turmas
  reduzidas). Prefixo sem entrada em `INSTITUTOS` não gera guia (fail-closed).
- Chave FARO atual enxerga vários pipelines — migrar para chave escopada só ao FEQ.
- ✅ Persistência de conversa em SQLite (`atena/core/estado.py`); integração real com FARO (SoR externo) ainda pendente.
- ✅ O VPS já tem as credenciais do WordPress (31/08/2026): aprovação do Injetor e tick de
  deriva publicam de verdade. **Consequência a vigiar**: o tick horário (`app.py`,
  `lp_auto.rodar`) republica sozinho toda LP cuja renderização mude. As baselines
  (`config lp_hash_<curso>`) foram congeladas no estado atual para nada ir ao ar sem
  revisão; os valores anteriores estão em `config lp_hash_backup_31ago`.
- As LPs no ar ainda embutem o webhook em `3.218.159.36.sslip.io`. Desde a migração de
  13/10/2026 esse host é o Lightsail antigo fazendo proxy para o servidor novo, então o
  **Lightsail não pode ser desligado** enquanto houver LP com `sslip.io`. Republicá-las
  passa todas para `feq.vitoraugolis.com.br` — é publicação em página pública, então só
  sob decisão explícita.
- As versões dos pacotes Python em produção foram fixadas iguais às do Lightsail
  (pyshacl 0.31.0, weasyprint 69.0); o deploy não as atualiza. Não há `requirements.txt`.
- FEQ-0499: turma 006 aberta no ADM Cursos, aguardando abrir o link público de inscrição.
  Quando sair: `POST /api/injetor/oferecimento` (turma + URL) — atualiza `cursos_metas` e
  republica a LP num passo só.
- **FEQ-1000 turma 019** (06/10/2026): declarada com inscrições até 13/02/2027, mas o link
  público da Extecamp (`/FEQ-1000/019`) ainda não abre. O agente manda a LP
  (`urlInscricaoLead`, `specs/08`); quando o link abrir, remover esse campo do catálogo de
  produção. A LP antiga `inscricao-feq-1000-018` é espelho da nova.
- Rotacionar quaisquer segredos que tenham circulado em texto.
