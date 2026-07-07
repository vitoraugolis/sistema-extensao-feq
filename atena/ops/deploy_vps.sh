#!/bin/zsh
# Deploy do Sistema Atena FEQ em um VPS (Lightsail) — roda NO MAC.
# Uso: zsh atena/ops/deploy_vps.sh <IP_DO_VPS> [usuario_ssh]
# Copia código + .env + banco, instala dependências, cria serviços systemd
# (app + caddy com HTTPS automático via <IP>.sslip.io).
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

# 2) Copia o projeto (código + specs + .env + banco; sem lixo)
echo "==> copiando arquivos"
"${SSH[@]}" "mkdir -p ~/atena-feq"
rsync -az -e "ssh -i $KEY" \
  --exclude '__pycache__' --exclude '.DS_Store' --exclude 'atena/backups' \
  --exclude 'atena/ops/logs' --exclude '.git' \
  --exclude 'atena/data' \
  "$DIR/" "$SSHUSER@$IP:~/atena-feq/"
# atena/data EXCLUÍDO: o banco vivo mora no VPS — nunca sobrescrever com o do Mac

# 3) Prepara o VPS: python, deps, swap (instâncias pequenas), serviços
echo "==> configurando o VPS"
"${SSH[@]}" 'bash -s' <<'REMOTE'
set -e
# pacotes (apt=Ubuntu/Debian · dnf=Amazon Linux)
if command -v apt-get >/dev/null; then
  sudo apt-get update -qq && sudo apt-get install -y -qq python3-pip python3-venv sqlite3 curl
elif command -v dnf >/dev/null; then
  sudo dnf install -y -q python3-pip sqlite curl
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
./.venv/bin/pip install -q flask requests rdflib pyshacl waitress
# caddy (binário estático — HTTPS automático)
if [ ! -x /usr/local/bin/caddy ]; then
  curl -fsSL -o /tmp/caddy "https://caddyserver.com/api/download?os=linux&arch=$(uname -m | sed -e 's/x86_64/amd64/' -e 's/aarch64/arm64/')"
  sudo install -m755 /tmp/caddy /usr/local/bin/caddy
  sudo setcap cap_net_bind_service=+ep /usr/local/bin/caddy
fi
IP_PUB=$(curl -4 -fsS ifconfig.me)
sudo mkdir -p /etc/caddy
printf '%s.sslip.io {\n  reverse_proxy localhost:8095\n}\n' "$IP_PUB" | sudo tee /etc/caddy/Caddyfile >/dev/null
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
sleep 4
echo "--- saúde local:" && curl -fsS localhost:8095/health && echo
echo "--- URL pública: https://$IP_PUB.sslip.io"
REMOTE
echo "==> deploy concluído"
