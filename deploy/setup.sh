#!/usr/bin/env bash
# UNTESTED on a real VM. Ubuntu 22.04+. Run from the project folder: sudo DOMAIN=name.duckdns.org bash deploy/setup.sh
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "run with sudo"; exit 1; }
: "${DOMAIN:?set DOMAIN=your.domain}"
SRC="$(cd "$(dirname "$0")/.." && pwd)"
APP=/opt/digest

apt-get update
apt-get install -y python3 debian-keyring debian-archive-keyring apt-transport-https curl gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' > /etc/apt/sources.list.d/caddy-stable.list
apt-get update && apt-get install -y caddy

id digest >/dev/null 2>&1 || useradd --system --home "$APP" digest
mkdir -p "$APP/data"
cp -r "$SRC/digest.py" "$SRC/textutil.py" "$SRC/sources.json" "$SRC/web" "$APP/"
chown -R digest "$APP"

if [ ! -f /etc/digest.env ]; then
  KEY="$(python3 -c 'import secrets;print(secrets.token_urlsafe(24))')"
  cat > /etc/digest.env <<ENV
HOST=127.0.0.1
PORT=8080
DIGEST_DB=$APP/data/digest.db
DIGEST_FETCH_HOURS=6
DIGEST_ADMIN_KEY=$KEY
# DIGEST_API_KEY=
ENV
  chmod 600 /etc/digest.env
  echo "Admin key (also in /etc/digest.env): $KEY"
fi

cp "$SRC/deploy/digest.service" /etc/systemd/system/digest.service
sed "s/DOMAIN/$DOMAIN/" "$SRC/deploy/Caddyfile" > /etc/caddy/Caddyfile
systemctl daemon-reload
systemctl enable --now digest
systemctl restart caddy

# Oracle Ubuntu images block most inbound traffic in iptables; open 80/443 before the REJECT rule.
iptables -I INPUT 6 -m state --state NEW -p tcp --dport 80 -j ACCEPT
iptables -I INPUT 6 -m state --state NEW -p tcp --dport 443 -j ACCEPT
apt-get install -y iptables-persistent && netfilter-persistent save
echo "Open https://$DOMAIN (review page: /review.html)"
