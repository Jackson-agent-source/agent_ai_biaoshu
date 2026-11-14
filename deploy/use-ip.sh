#!/bin/sh
# Switch an already-built standalone deployment to trusted HTTPS on a public IP.
set -eu
if [ "$#" -ne 1 ]; then
    echo "Usage: bash deploy/use-ip.sh PUBLIC_IPV4" >&2
    exit 1
fi
cd "$(dirname "$0")/.."
for tool in python3 docker curl; do
    command -v "$tool" >/dev/null || { echo "Missing required tool: $tool" >&2; exit 1; }
done
dc() { docker compose -f docker-compose.yml -f compose.standalone.yml "$@"; }
dc version >/dev/null
python3 deploy/configure_ip.py "$1"
dc config --quiet
dc pull https
dc run --rm --no-deps https caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
# Existing backend/web images are reused; do not rebuild on a 2GB server just to change its address.
dc up -d --no-build --wait --wait-timeout 180
# nginx resolves its backend on startup; the recreated backend may have a new IP.
dc restart web
dc ps
echo "Waiting for the IP certificate (ports 80 and 443 must be reachable)..."
attempt=0
while [ "$attempt" -lt 24 ]; do
    if curl -fsS --connect-timeout 5 --max-time 10 "https://$1/health/" >/dev/null 2>&1 &&
       curl -fsS --connect-timeout 5 --max-time 10 "https://$1/login" >/dev/null 2>&1; then
        code=$(curl -sS -o /dev/null -w '%{http_code}' --connect-timeout 5 --max-time 10 "https://$1/api/v1/auth/me")
        if [ "$code" = "401" ]; then
            echo "READY: https://$1/login"
            echo "Trusted TLS, frontend, backend health and unauthenticated API protection passed."
            exit 0
        fi
    fi
    attempt=$((attempt + 1))
    sleep 5
done
echo "Certificate or application is not ready. Recent Caddy logs:" >&2
dc logs --tail=60 https
echo "Check inbound TCP 80/443 and retry. Existing database volumes were preserved." >&2
exit 1
