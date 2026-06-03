#!/usr/bin/env bash
#
# One-shot demo deploy for the Horizen staking dApp on a fresh Ubuntu 24.04 box.
# Run as root (or with sudo):
#
#   curl -fsSL https://raw.githubusercontent.com/NVladan/horizen-staking/main/deploy/setup.sh | sudo bash
#
# Idempotent: safe to re-run. Serves over HTTP on the server IP; admin locked.
set -euo pipefail

REPO_URL="https://github.com/NVladan/horizen-staking.git"
APP_DIR="/opt/horizen-staking"
TSTZEN="0xF5574BC04D18DAe1939066d1D52C7fCCC93112b6"
STAKING="0x3656Aa266082fdDedbdDD44e387704351F5a5199"

echo ">>> [1/8] Base packages + Node"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get -y -qq install python3 python3-venv python3-pip nginx git curl ufw >/dev/null
if ! command -v node >/dev/null; then
  curl -fsSL https://deb.nodesource.com/setup_20.x | bash - >/dev/null
  apt-get -y -qq install nodejs >/dev/null
fi

echo ">>> [2/8] App user + code"
id horizen &>/dev/null || adduser --system --group --home "$APP_DIR" horizen
if [ -d "$APP_DIR/.git" ]; then
  git -C "$APP_DIR" pull --ff-only
else
  git clone --depth 1 "$REPO_URL" "$APP_DIR"
fi
cd "$APP_DIR"

echo ">>> [3/8] Python venv + dependencies"
python3 -m venv .venv
.venv/bin/pip install --upgrade pip -q
.venv/bin/pip install -q -r requirements.txt gunicorn

echo ">>> [4/8] Compile contract ABIs"
npm install --silent
.venv/bin/python -m scripts.compile

echo ">>> [5/8] .env (live mainnet contracts; admin locked; HTTP demo)"
if [ ! -f .env ]; then
  SECRET="$(.venv/bin/python -c 'import secrets; print(secrets.token_hex(32))')"
  cat > .env <<EOF
CHAIN_ID=26514
CHAIN_NAME=Horizen
RPC_URL=https://horizen.calderachain.xyz/http
EXPLORER_URL=https://horizen.calderaexplorer.xyz
TSTZEN_ADDRESS=$TSTZEN
STAKING_ADDRESS=$STAKING
DEPLOYER_PRIVATE_KEY=
ADMIN_ADDRESS=
ADMIN_OPEN=0
REWARD_PER_YEAR=50000
EPOCH_DURATION_SECONDS=1800
FLASK_DEBUG=0
SESSION_COOKIE_SECURE=0
FLASK_SECRET_KEY=$SECRET
EOF
  echo "    wrote new .env"
else
  echo "    .env already exists — left as-is"
fi
chown -R horizen:horizen "$APP_DIR"
chmod 600 .env

echo ">>> [6/8] systemd service"
cp deploy/horizen-staking.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now horizen-staking
systemctl restart horizen-staking

echo ">>> [7/8] nginx reverse proxy"
cp deploy/nginx-horizen.conf /etc/nginx/sites-available/horizen
ln -sf /etc/nginx/sites-available/horizen /etc/nginx/sites-enabled/horizen
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl reload nginx

echo ">>> [8/8] Firewall"
ufw allow OpenSSH >/dev/null
ufw allow 'Nginx HTTP' >/dev/null
ufw --force enable >/dev/null

sleep 3
echo ">>> Health check:"
curl -s -o /dev/null -w "    local app  -> HTTP %{http_code}\n" http://127.0.0.1:8000/api/health || true
curl -s -o /dev/null -w "    via nginx  -> HTTP %{http_code}\n" http://127.0.0.1/ || true

IP="$(curl -s https://api.ipify.org || echo '<server-ip>')"
echo
echo ">>> Done. Open:  http://$IP"
