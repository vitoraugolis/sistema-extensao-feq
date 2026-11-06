# Guia — subir um novo sistema no VPS da FEQ (Oracle)

Para o desenvolvedor que vai hospedar outro sistema ao lado da Atena.
**Leia tudo antes de começar: a máquina é pequena e a Atena está em produção.**

## 1. A máquina

| | |
|---|---|
| IP | `144.33.22.236` (Oracle Cloud, Ubuntu, x86_64) |
| CPU | 2 vCPU (formato Micro: capacidade garantida baixa, aguenta picos, não carga contínua) |
| RAM | **1 GB** total, ~600 MB livres hoje · 1 GB de swap |
| Disco | 45 GB, ~40 GB livres |
| Já rodando | `atena.service` (Python, porta 8095, ~100 MB) · `caddy.service` (HTTPS, portas 80/443) |
| Firewall | iptables só aceita 22, 80 e 443 vindos de fora. Qualquer outra porta fica interna. |

**Orçamento para o seu sistema: até 250 MB de RAM em regime normal.** Para Postgres/MySQL,
Docker com vários containers, Chromium/Playwright ou builds de front-end, peça uma VM
separada: não cabe aqui.

## 2. O que você precisa nos entregar ANTES do acesso

1. **Acesso**: você recebe a chave `.pem` da empresa e entra como `ubuntu`
   (`ssh -i chave.pem ubuntu@144.33.22.236`). Esse usuário tem **sudo e é o mesmo que roda a
   Atena**: as regras da seção 3 não são opcionais. Guarde a chave com `chmod 600` e não a
   copie para outras máquinas nem para repositórios.
2. **Domínio ou subdomínio** do sistema, com DNS (registro A) apontando para `144.33.22.236`.
3. **Porta interna** que o app vai escutar, em `127.0.0.1` (não `0.0.0.0`). Livres: qualquer uma
   menos 8095, 2019, 53, 111.
4. **O unit do systemd** (modelo abaixo) e **o comando de start**.
5. **Consumo esperado** de RAM e disco, e se grava arquivos (onde e quanto por mês).

## 3. Regras da casa

- Tudo seu fica em **`/home/ubuntu/<seu-app>/`** (pasta própria). Não toque em `/home/ubuntu/atena-feq/`
  (o deploy da Atena faz rsync ali e apagaria o que for seu), nem em `~/.ssh/authorized_keys`.
- Não rode `apt upgrade`, não troque a versão do Python do sistema e não altere `atena.service`
  nem os blocos existentes do Caddyfile. Use um venv (`python3 -m venv .venv`) ou o runtime
  da sua linguagem dentro da sua home.
- Não reinicie a máquina sem combinar: a Atena atende leads por WhatsApp 24/7.
- Segredos só no seu `.env`, com permissão `600`. Nunca em commit e nunca em log.
- Logs vão para o journald (`journalctl -u <seu-servico>`), que já faz rotação. Se gravar
  arquivo de log próprio, configure rotação.

## 4. Modelo de unit do systemd (obrigatório usar o limite de memória)

`/etc/systemd/system/<seu-servico>.service`:

```ini
[Unit]
Description=<Nome do sistema>
After=network.target

[Service]
User=ubuntu
WorkingDirectory=/home/ubuntu/<seu-app>
EnvironmentFile=/home/ubuntu/<seu-app>/.env
ExecStart=/home/ubuntu/<seu-app>/.venv/bin/python app.py
Restart=always
RestartSec=5
# Limites: se o seu app vazar memória, ELE é morto e reiniciado, não a Atena.
MemoryHigh=250M
MemoryMax=300M
CPUQuota=60%
Nice=5

[Install]
WantedBy=multi-user.target
```

## 5. HTTPS (Caddy)

Acrescente um bloco (sem alterar os existentes) e rode `sudo systemctl reload caddy` ao `/etc/caddy/Caddyfile`; o certificado sai automaticamente:

```
seu.dominio.com.br {
  reverse_proxy localhost:<sua-porta>
}
```

## 6. Avisos automáticos de recursos

A Atena vigia a máquina a cada 10 min (`atena/ops/recursos.py`) e alerta na Central e no
WhatsApp do gestor, no máximo 1 aviso por tipo a cada 6h, quando:

| Aviso | Limite |
|---|---|
| `recurso_disco` | disco ≥ 85% |
| `recurso_memoria` | RAM disponível < 120 MB |
| `recurso_swap` | swap ≥ 50% |
| `recurso_cpu` | carga média de 15 min > 1,5 × nº de CPUs |

Se um aviso disparar depois que o seu sistema subir, o primeiro passo é medir o seu processo
(`systemctl status <seu-servico>` mostra a memória). Para conferir na hora:
`python3 -m atena.ops.recursos` (dentro de `/home/ubuntu/atena-feq`).

## 7. Checklist de entrega (quando terminar)

- [ ] `systemctl status <seu-servico>` ativo; reinício testado (`sudo systemctl restart`)
- [ ] App escuta em `127.0.0.1:<porta>` (`ss -tlnp | grep <porta>`)
- [ ] `https://seu.dominio` abre com certificado válido
- [ ] `free -h` com mais de 300 MB "available" com tudo rodando
- [ ] `https://feq.vitoraugolis.com.br` continua abrindo (a Atena não foi afetada)
- [ ] README curto na sua home: como fazer deploy, como reiniciar, onde ficam os dados
