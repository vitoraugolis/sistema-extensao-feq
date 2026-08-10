#!/bin/zsh
# Deploy do Sistema Atena FEQ em um VPS (Lightsail) — roda NO MAC.
# Uso: zsh atena/ops/deploy_vps.sh <IP_DO_VPS> [usuario_ssh]
# Copia o código (PRESERVA .env e banco do VPS), instala dependências, cria
# serviços systemd (app + caddy com HTTPS automático via <IP>.sslip.io).
set -e
IP="$1"
SSHUSER="${2:-auto}"
KEY="$HOME/.ssh/Ext-FEQ.pem"
DIR="$(cd "$(dirname "$0")/../.." && pwd)"
[ -z "$IP" ] && { echo "uso: zsh atena/ops/deploy_vps.sh <IP> [usuario]"; exit 1; }

# 1) Descobre o usuário SSH (ubuntu ou ec2-user)
if [ "$SSHUSER" = "auto" ]; then
  for u in ubuntu ec2-user admin; do
    if ssh -i "$KEY" -o ConnectTimeout=6 -o StrictHostKeyChecking=accept-new "$u@$IP" true 2>/dev/null; then
      SSHUSER=$u; break
    fi
  done
fi
[ "$SSHUSER" = "auto" ] && { echo "não consegui logar (testei ubuntu/ec2-user/admin)"; exit 1; }
echo "==> usuário SSH: $SSHUSER"
SSH=(ssh -i "$KEY" -o StrictHostKeyChecking=accept-new "$SSHUSER@$IP")

# 2) Copia o projeto (código + specs; sem lixo, sem .env, sem banco)
echo "==> copiando arquivos"
"${SSH[@]}" "mkdir -p ~/atena-feq"
rsync -az -e "ssh -i $KEY" \
  --exclude '__pycache__' --exclude '.DS_Store' --exclude 'atena/backups' \
  --exclude 'atena/ops/logs' --exclude '.git' \
  --exclude 'atena/data' --exclude '.env' \
  "$DIR/" "$SSHUSER@$IP:~/atena-feq/"
# atena/data e .env EXCLUÍDOS: banco vivo e segredos moram no VPS — o .env do Mac
# pode estar parcial/desatualizado; sobrescrever apagaria tokens de produção.
# .env só é semeado no BOOTSTRAP (se ainda não existir no VPS):
rsync -az --ignore-existing -e "ssh -i $KEY" "$DIR/.env" "$SSHUSER@$IP:~/atena-feq/.env" 2>/dev/null || true

# 3) Prepara o VPS: python, deps, swap (instâncias pequenas), serviços
echo "==> configurando o VPS"
"${SSH[@]}" 'bash -s' <<'REMOTE'
set -e
# pacotes (apt=Ubuntu/Debian · dnf=Amazon Linux)
if command -v apt-get >/dev/null; then
  # libpango/gdk-pixbuf/libffi: exigidas pelo WeasyPrint (guia do curso em PDF, spec 08)
  sudo apt-get update -qq && sudo apt-get install -y -qq python3-pip python3-venv sqlite3 curl \
    libpango-1.0-0 libpangocairo-1.0-0 libgdk-pixbuf2.0-0 libffi-dev
elif command -v dnf >/dev/null; then
  sudo dnf install -y -q python3-pip sqlite curl pango gdk-pixbuf2 libffi-devel
fi
# swap de 1G se RAM < 1GB (pyshacl/rdflib pesam na instalação)
if [ "$(awk '/MemTotal/{print $2}' /proc/meminfo)" -lt 1000000 ] && [ ! -f /swapfile ]; then
  sudo fallocate -l 1G /swapfile && sudo chmod 600 /swapfile
  sudo mkswap /swapfile && sudo swapon /swapfile
  echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab >/dev/null
fi
# venv do app
cd ~/atena-feq
python3 -m venv .venv
./.venv/bin/pip install -q --upgrade pip
./.venv/bin/pip install -q flask requests rdflib pyshacl waitress weasyprint
# caddy (binário estático — HTTPS automático)
if [ ! -x /usr/local/bin/caddy ]; then
  curl -fsSL -o /tmp/caddy "https://caddyserver.com/api/download?os=linux&arch=$(uname -m | sed -e 's/x86_64/amd64/' -e 's/aarch64/arm64/')"
  sudo install -m755 /tmp/caddy /usr/local/bin/caddy
  sudo setcap cap_net_bind_service=+ep /usr/local/bin/caddy
fi
IP_PUB=$(curl -4 -fsS ifconfig.me)
sudo mkdir -p /etc/caddy
# feq.vitoraugolis.com.br: domínio definitivo (registro A -> este IP). O bloco
# sslip.io fica como fallback de acesso direto por IP (útil p/ diagnóstico e
# enquanto o DNS do domínio não propagou). Caddy emite/renova os dois
# certificados via ACME sozinho; se o domínio ainda não resolver pra cá, só a
# tentativa dele falha e tenta de novo depois — o site em sslip.io não é afetado.
printf '%s.sslip.io {\n  reverse_proxy localhost:8095\n}\n\nfeq.vitoraugolis.com.br {\n  reverse_proxy localhost:8095\n}\n' "$IP_PUB" | sudo tee /etc/caddy/Caddyfile >/dev/null
# systemd: app
sudo tee /etc/systemd/system/atena.service >/dev/null <<EOF
[Unit]
Description=Atena FEQ (Central de Operacao)
After=network.target
[Service]
User=$USER
WorkingDirectory=/home/$USER/atena-feq
ExecStart=/home/$USER/atena-feq/.venv/bin/python3 atena/api/app.py
Restart=always
RestartSec=3
[Install]
WantedBy=multi-user.target
EOF
# systemd: caddy
sudo tee /etc/systemd/system/caddy.service >/dev/null <<'EOF'
[Unit]
Description=Caddy (HTTPS reverso)
After=network.target
[Service]
ExecStart=/usr/local/bin/caddy run --config /etc/caddy/Caddyfile
Restart=always
RestartSec=3
[Install]
WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload
sudo systemctl enable --now atena caddy
sudo systemctl restart atena          # re-deploys: garante que o código novo carregue
sudo systemctl restart caddy          # idem p/ o Caddyfile: já rodando não recarrega sozinho
sleep 4
echo "--- saúde local:" && curl -fsS localhost:8095/health && echo
echo "--- URL pública: https://$IP_PUB.sslip.io"
echo "--- domínio definitivo: https://feq.vitoraugolis.com.br (precisa do registro A -> $IP_PUB)"
REMOTE
echo "==> deploy concluído"
