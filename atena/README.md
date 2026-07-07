# Atena v2 — monólito modular

Sistema de captação FEQ com ancoragem ontológica. Specs em `../specs/` são a fonte da verdade.

## Rodar (produção 24/7 — macOS, launchd)
O sistema roda como serviço (waitress + caffeinate; reinicia em queda e no boot):
```bash
launchctl list | grep feq                 # br.unicamp.feq.atena (app) · br.unicamp.feq.ngrok (túnel)
launchctl kickstart -k gui/$UID/br.unicamp.feq.atena   # reiniciar (ex.: após editar código)
tail -f atena/ops/logs/atena.err.log      # logs
```
Acesso: `http://localhost:8095` (local) ou a URL pública do ngrok (domínio estático da conta).
**Login**: sem usuários cadastrados vale o bootstrap `operador/operador` (supervisor); crie os
usuários reais em Configurações → Usuários — aí o bootstrap desliga sozinho. Senhas com hash
PBKDF2 no SQLite; rate limit de 5 falhas/min no login. Backup diário 03:30 (interno ao app;
manual: `POST /api/backup`) em `atena/backups/`.

Dev manual (sem serviço):
```bash
pip install flask requests rdflib pyshacl waitress
python3 atena/api/app.py        # http://localhost:8095
```

## Testar (offline, sem rede)
```bash
for t in revisor orquestrador injetor agentes; do python3 atena/tests/test_$t.py; done
python3 atena/ontologia/validar.py     # SHACL + captabilidade + elegibilidade
```

## Estrutura
- `ontologia/` — grafo (JSON-LD) + SHACL + `servico.py` (única porta de grounding) + `versionamento.py`.
- `core/` — `estado.py` (SQLite), `orquestrador.py` (máquina de estados), `contratos.py`, `guardrails.py`, `auth.py`.
- `agentes/` — `cerebro.py` (LLM+fallback), `ingestao.py` (Variante B), `curador.py`, `ontologista.py` (Injetor), `bibliotecario.py`.
- `revisor/ontologico.py` — anti-alucinação fail-closed. `transporte/whatsapp.py` — envio guardado.
- `api/app.py` + `web/index.html` — Central glass.

## Guardas
- Nada envia mensagem nem gasta dinheiro sem token/aprovação (envio = dry-run sem `WHATSAPP_TOKEN`).
- Mensagem (de agente ou humano) passa pelo Revisor antes de virar proposta.
- Webhooks verificam `X-Hub-Signature-256` quando `META_APP_SECRET` está setado.
- Aprovar mudança de ontologia exige papel `supervisor`; ações humanas são auditadas.
