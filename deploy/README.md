# Deploying the demo on a Contabo VPS

Native setup: **gunicorn + systemd + nginx**, served over **HTTP on the server IP**
(no domain / no HTTPS). Target OS: **Ubuntu 24.04 LTS**. Run everything as `root`
(or with `sudo`).

> The contracts are already live on Horizen L3 mainnet, so the server only *reads*
> chain state and serves the UI. No private key goes on the server, and the admin
> console is locked.

---

### 1. Create the server
Contabo's cheapest Cloud VPS is plenty. Storage: **75 GB NVMe** (faster; capacity
is irrelevant for this app). Image: **Ubuntu 24.04**. Then SSH in: `ssh root@<server-ip>`.

### 2. Base packages
```bash
apt update && apt -y upgrade
apt -y install python3 python3-venv python3-pip nginx git curl ufw
adduser --system --group --home /opt/horizen-staking horizen
```
> No Node/solc here: the compiled contract ABIs ship in `build/`, so the server
> never runs the Solidity toolchain (one less network dependency to fail on).

### 3. Get the code
```bash
cd /opt
git clone https://github.com/NVladan/horizen-staking.git
cd horizen-staking
```

### 4. Python venv + dependencies (incl. gunicorn)
```bash
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt gunicorn
```

### 5. Create `.env` (live mainnet contracts; admin LOCKED; HTTP so secure-cookie off)
```bash
cat > .env <<'EOF'
CHAIN_ID=26514
CHAIN_NAME=Horizen
RPC_URL=https://horizen.calderachain.xyz/http
EXPLORER_URL=https://horizen.calderaexplorer.xyz

# Live mainnet contracts (public)
TSTZEN_ADDRESS=0xF5574BC04D18DAe1939066d1D52C7fCCC93112b6
STAKING_ADDRESS=0x3656Aa266082fdDedbdDD44e387704351F5a5199

# No deployer key on a public demo. Admin console locked (manage from your laptop).
DEPLOYER_PRIVATE_KEY=
ADMIN_ADDRESS=
ADMIN_OPEN=0

REWARD_PER_YEAR=50000
EPOCH_DURATION_SECONDS=1800

FLASK_DEBUG=0
SESSION_COOKIE_SECURE=0
FLASK_SECRET_KEY=
EOF

# generate a strong secret key in place
SECRET=$(.venv/bin/python -c "import secrets; print(secrets.token_hex(32))")
sed -i "s|^FLASK_SECRET_KEY=.*|FLASK_SECRET_KEY=$SECRET|" .env

# permissions: app user owns the repo, .env readable only by it
chown -R horizen:horizen /opt/horizen-staking
chmod 600 .env
```

### 6. systemd service (auto-start + restart)
```bash
cp deploy/horizen-staking.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now horizen-staking
systemctl status horizen-staking --no-pager     # should be "active (running)"
```

### 7. nginx reverse proxy
```bash
cp deploy/nginx-horizen.conf /etc/nginx/sites-available/horizen
ln -sf /etc/nginx/sites-available/horizen /etc/nginx/sites-enabled/horizen
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl reload nginx
```

### 8. Firewall
```bash
ufw allow OpenSSH
ufw allow 'Nginx HTTP'
ufw --force enable
```

### Done
Open **`http://<your-server-ip>`** — the dashboard loads live mainnet stats, and
visitors can connect MetaMask, use the faucet, and stake.

---

## Updating the demo later
```bash
cd /opt/horizen-staking
sudo -u horizen git pull
.venv/bin/pip install -r requirements.txt gunicorn   # if deps changed
systemctl restart horizen-staking
```
> Contract changes? Recompile **on your laptop** (`python -m scripts.compile`),
> commit the updated `build/*.json`, then `git pull` on the server.

## Notes / caveats (HTTP-only demo)
- **No HTTPS** (no domain), so `SESSION_COOKIE_SECURE=0`. The public staking flow
  is unaffected (it uses MetaMask + a client-side session). The admin console is
  **locked** (`ADMIN_ADDRESS` empty) — do deploys/top-ups from your own machine,
  which has the full `.env`.
- Want HTTPS later? Point a domain at the IP, set `server_name` in the nginx
  config, run `certbot --nginx`, and set `SESSION_COOKIE_SECURE=1` in `.env`.
- Logs: `journalctl -u horizen-staking -f` (gunicorn) and `logs/app.log` (app).
