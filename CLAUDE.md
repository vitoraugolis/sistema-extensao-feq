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
Lead → Prospect → Inscrito → Matriculado
```

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
| Gate acadêmico | **Todos os 8 cursos exigem ensino superior completo.** |

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
atena/                          # SISTEMA v2 (monólito modular) — ver atena/README.md
  ontologia/  core/  agentes/  revisor/  transporte/  api/  web/  tests/
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
PORT=8080                       # 8090 cérebro v1 · 8095 Central v2
```

## 6. Como rodar e testar

```bash
pip install flask requests rdflib pyshacl
set -a && source .env && set +a

# Sistema v2 (atena/) — roda 24/7 como serviço launchd (br.unicamp.feq.atena + .ngrok)
launchctl kickstart -k gui/$UID/br.unicamp.feq.atena   # reiniciar após mudar código
python3 atena/api/app.py            # dev manual · http://localhost:8095 (bootstrap: operador/operador até criar usuários reais)
for t in revisor orquestrador injetor agentes; do python3 atena/tests/test_$t.py; done
python3 atena/ontologia/validar.py  # valida a ontologia (SHACL)

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
- ⏳ Fases 3 e 4 (inscrição/matrícula) (`specs/06`); envio WhatsApp e cérebro LLM reais dependem de chaves.
- ⏳ Legado v1 (`feq_*.py`) mantido só como referência.

## 10. Riscos e pendências abertas

- Confirmar disponibilidade do FEQ-0499 na Extecamp (pode estar encerrado).
- Chave FARO atual enxerga vários pipelines — migrar para chave escopada só ao FEQ.
- ✅ Persistência de conversa em SQLite (`atena/core/estado.py`); integração real com FARO (SoR externo) ainda pendente.
- Rotacionar quaisquer segredos que tenham circulado em texto.
