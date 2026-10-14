# Runbook — migração da Atena para um novo VPS

> **Executado em 13/10/2026**: Lightsail `3.218.159.36` → Oracle Cloud `144.33.22.236`
> (parada de ~35 s, sha256 do banco conferido). Diferenças em relação ao texto abaixo:
> as LPs já usavam o domínio (o passo 7 não foi necessário); os **webhooks da Meta** é que
> ainda apontavam para `sslip.io` e foram trocados pela API (`POST /{app}/subscriptions`);
> na Oracle é preciso liberar 80/443 também no `iptables` do host, não só na Security
> List; e as versões dos pacotes foram fixadas com o `pip freeze` do servidor antigo.
> O Lightsail foi apagado no mesmo dia. Numa próxima migração, confira os webhooks da
> Meta **antes** de desligar o servidor antigo.

Escrito em 13/10/2026 a partir do estado real do VPS atual (Lightsail `3.218.159.36`).
Todos os comandos rodam **no Mac**, na raiz do repositório, salvo indicação.

## Como a migração funciona (leia antes)

- O **código** vem do repositório (`deploy_vps.sh` faz rsync da pasta local). Em 13/10/2026
  o VPS estava idêntico ao `origin/main`.
- O **estado** só existe no VPS e é copiado direto do servidor antigo para o novo:

  | Caminho (em `~/atena-feq/`) | O que é |
  |---|---|
  | `.env` | 19 segredos de produção (WhatsApp, Meta, WordPress, LLMs, sessão) |
  | `atena/data/` (~50 MB) | `atena.db` (leads, conversas, funil, growth, config), `media/`, `guias/`, `bridge_pages/` |
  | `atena/ontologia/catalogo.jsonld` e `versoes/` | catálogo vivo e histórico do Injetor |
  | `atena/backups/` (~115 MB) | 14 backups diários (opcional, mas barato levar) |

- **Nunca deixe os dois servidores com o serviço `atena` ativo e o `.env` de produção ao
  mesmo tempo.** O agendador interno (`_operacoes` em `atena/api/app.py`, tick de 60 s) roda
  nutrição, cadência de toques, pacing e republicação de LP: duas instâncias mandariam
  mensagens duplicadas aos leads. Não há cron nem timer no host; tudo é agendado dentro do
  processo, então "serviço parado" = "nada dispara".
- Com o `atena` antigo parado, o Caddy antigo responde 502 e a Meta **reenvia** os webhooks
  (WhatsApp e Lead Ads) depois. A janela de indisponibilidade não perde mensagem, só atrasa.

## Referência do servidor atual

| Item | Valor |
|---|---|
| SO / arquitetura | Ubuntu 24.04 LTS, x86_64, Python 3.12.3 |
| Tamanho | 2 vCPU, 412 MB RAM + 1 GB swap, disco 19 GB (4,8 GB usados) |
| Serviços systemd | `atena` (waitress em `0.0.0.0:8095`), `caddy` (80/443 → 8095) |
| Pacotes Python | Flask 3.1.3, requests 2.34.2, rdflib 7.6.0, pyshacl 0.31.0, waitress 3.0.2, weasyprint 69.0 |
| Domínio | `feq.vitoraugolis.com.br` (registro A, DNS no registro.br) |
| Fallback | `3.218.159.36.sslip.io` — **embutido nas LPs publicadas** (ver passo 7) |

O deploy instala as versões mais recentes dos pacotes (não há pin). Se algo quebrar no
servidor novo, fixe as versões da tabela com `pip install pacote==versão`.

## 0. Preparação (dias antes)

1. Contratar o VPS: **Ubuntu 24.04**, ≥ 1 GB de RAM (o atual vive em swap), ≥ 20 GB de
   disco, IP fixo. x86_64 ou arm64 servem.
2. Firewall do provedor: liberar **só a porta 22** por enquanto. 80 e 443 abrem no passo 5.
   A 8095 nunca deve ficar aberta para fora.
3. Baixar o TTL do registro A de `feq.vitoraugolis.com.br` no registro.br para o mínimo.
4. Definir as variáveis usadas no resto do runbook:

```bash
OLD=ubuntu@3.218.159.36;      KOLD=~/.ssh/Ext-FEQ.pem
NEW=ubuntu@<IP_NOVO>;         KNEW=~/.ssh/<chave_nova>.pem
```

## 1. Pré-instalar o servidor novo (sem downtime)

Instala pacotes, venv, Caddy e os serviços, e deixa o app **parado**.

```bash
# .env vazio impede o deploy de semear o .env do Mac (que pode estar desatualizado)
ssh -i $KNEW $NEW 'mkdir -p ~/atena-feq && touch ~/atena-feq/.env'
ATENA_SSH_KEY=$KNEW zsh atena/ops/deploy_vps.sh <IP_NOVO> ubuntu
ssh -i $KNEW $NEW 'sudo systemctl disable --now atena && systemctl is-active atena'   # esperado: inactive
```

O deploy termina com `{"ok":true,...}` no health local. O app chega a subir por alguns
segundos com `.env` vazio (tudo em dry-run, sem porta pública aberta) e cria um banco
vazio, que o passo 3 apaga.

## 2. Cutover — parar o servidor antigo (início do downtime)

Escolha um horário de pouco movimento. Avise quem opera a Central.

```bash
ssh -i $KOLD $OLD 'sudo systemctl disable --now atena
cd ~/atena-feq
sqlite3 atena/data/atena.db "PRAGMA wal_checkpoint(TRUNCATE); PRAGMA integrity_check; SELECT count(*) FROM leads;"
sha256sum atena/data/atena.db'
```

Esperado: `ok` no integrity_check. **Anote a contagem de leads e o sha256.** O checkpoint é
obrigatório: o banco roda em WAL e, em 13/10, havia 4 MB de dados só no `atena.db-wal`.

## 3. Copiar o estado do antigo para o novo

Stream direto entre os servidores, passando pelo Mac sem gravar nada em disco:

```bash
ssh -i $KNEW $NEW 'rm -rf ~/atena-feq/atena/data'     # banco vazio criado no passo 1 (e seu -wal)
ssh -i $KOLD $OLD 'tar -C ~/atena-feq -czf - .env atena/data atena/ontologia/catalogo.jsonld atena/ontologia/versoes atena/backups' \
  | ssh -i $KNEW $NEW 'tar -C ~/atena-feq -xzf - && chmod 600 ~/atena-feq/.env'
```

Conferir:

```bash
ssh -i $KNEW $NEW 'cd ~/atena-feq && sha256sum atena/data/atena.db && grep -c = .env && ls atena/ontologia/versoes | wc -l'
```

O sha256 tem de ser igual ao anotado no passo 2; `.env` com 19 chaves.

## 4. Subir o servidor novo

```bash
ssh -i $KNEW $NEW 'sudo systemctl enable --now atena && sleep 5 && curl -fsS localhost:8095/health && ~/atena-feq/.venv/bin/python3 ~/atena-feq/atena/ontologia/validar.py | tail -2'
```

Esperado: `{"cursos":13,"leads":<mesma contagem>,"ok":true}` e SHACL verde.
Se falhar: `ssh -i $KNEW $NEW 'journalctl -u atena -n 50 --no-pager'`.

## 5. Virar o tráfego

1. Abrir as portas **80 e 443** no firewall do provedor novo.
2. Testar pelo fallback antes do DNS: `curl -fsS https://<IP_NOVO>.sslip.io/health`
   (o Caddy emite o certificado na primeira chamada; pode levar alguns segundos).
3. No registro.br, apontar o registro A de `feq.vitoraugolis.com.br` para `<IP_NOVO>`.
4. Acompanhar: `dig +short feq.vitoraugolis.com.br` até devolver o IP novo, depois
   `curl -fsS https://feq.vitoraugolis.com.br/health`.

Fim do downtime.

## 6. Verificação funcional

- [ ] Login na Central em `https://feq.vitoraugolis.com.br` com um usuário real; conversas e funil aparecem.
- [ ] Mandar uma mensagem de WhatsApp de um número de teste para o número da FEQ: aparece em Conversas e o agente responde.
- [ ] `https://feq.vitoraugolis.com.br/ponte?curso=FEQ-0480` abre.
- [ ] Gerar/abrir um guia em PDF pela Central (valida WeasyPrint e a fonte Inter no host novo).
- [ ] Meta for Developers → app → Webhooks (WhatsApp e Página): a URL de callback usa o
      **domínio**. Se estiver com `3.218.159.36.sslip.io`, trocar para
      `https://feq.vitoraugolis.com.br/webhook/whatsapp` e `/webhook/meta`
      (token de verificação = `META_VERIFY_TOKEN` do `.env`).
- [ ] No dia seguinte: existe `atena/backups/atena_<data>_0600.db` novo (backup das 06h BRT).

## 7. LPs que apontam para o IP antigo

As LPs no WordPress embutem o webhook em `https://3.218.159.36.sslip.io`. Enquanto não
forem republicadas, a telemetria e as fichas de inscrição delas vão para o IP antigo.
Duas saídas, nesta ordem:

**a) Ponte imediata (logo após o passo 5):** o servidor antigo vira só um proxy para o novo.

```bash
ssh -i $KOLD $OLD 'printf "3.218.159.36.sslip.io {\n  reverse_proxy https://feq.vitoraugolis.com.br {\n    header_up Host {upstream_hostport}\n  }\n}\n" | sudo tee /etc/caddy/Caddyfile >/dev/null && sudo systemctl restart caddy'
curl -fsS https://3.218.159.36.sslip.io/health      # deve devolver o health do servidor NOVO
```

**b) Definitivo:** republicar as LPs (o `.env` já tem `ATENA_PUBLIC_URL` no domínio, então
saem com a URL certa). É publicação em página pública: fazer sob decisão explícita.

```bash
ssh -i $KNEW $NEW 'cd ~/atena-feq && set -a && . ./.env && set +a && ./.venv/bin/python3 -m atena.growth.wp_deploy_bridge'
```

Conferir que nenhuma LP no ar ainda contém `sslip.io` antes de desligar o servidor antigo.

## Rollback

- **Antes de o servidor novo receber dado novo** (até o passo 5.3): parar o novo e religar o antigo.

```bash
ssh -i $KNEW $NEW 'sudo systemctl disable --now atena'
ssh -i $KOLD $OLD 'sudo systemctl enable --now atena && curl -fsS localhost:8095/health'
```

- **Depois** (o novo já recebeu mensagens): parar o novo, repetir os passos 2 e 3 com os
  papéis invertidos (novo → antigo), religar o antigo e voltar o DNS. Se a ponte do passo
  7a já foi aplicada, restaurar o Caddyfile antigo (basta rodar o `deploy_vps.sh` nele).

## 8. Encerramento (após ~7 dias estável)

1. LPs republicadas e sem `sslip.io` (passo 7b).
2. Snapshot final da instância Lightsail; depois apagar a instância e liberar o IP estático.
3. Atualizar `CLAUDE.md` § 6 (IP e comando de deploy) e trocar o default de `KEY` em
   `atena/ops/deploy_vps.sh` para a chave nova.
4. Subir o TTL do DNS de volta.
5. Aproveitar para rotacionar os segredos do `.env` (pendência de `CLAUDE.md` § 10).

Deploys seguintes: `ATENA_SSH_KEY=$KNEW zsh atena/ops/deploy_vps.sh <IP_NOVO> ubuntu`.
