#!/usr/bin/env bash
# Starts the Fexor server with Docker (Linux / macOS). First run asks two questions and
# writes deploy/.env.   Usage:  ./deploy/start-server.sh
set -euo pipefail
cd "$(dirname "$0")"

say() { printf '\n%s\n' "$*"; }
need() { say "$*"; exit 1; }

command -v docker >/dev/null || need "Fexor needs Docker. Install Docker Desktop (Mac) or Docker Engine (Linux), then run this again: https://docs.docker.com/get-docker/"
docker info >/dev/null 2>&1 || need "Docker is installed but not running. Start it, then run this again."

lan_ip() {
  if command -v ip >/dev/null; then ip route get 1.1.1.1 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="src") print $(i+1)}' | head -1
  elif command -v ipconfig >/dev/null; then ipconfig getifaddr en0 || ipconfig getifaddr en1
  fi
}

if [[ ! -f .env ]]; then
  echo "First-time setup"
  echo "  1) Office network only  - simplest; kiosks and admins on the same network"
  echo "  2) Internet             - needs a domain name pointing to this computer (HTTPS)"
  read -rp "Choose 1 or 2 (default 1): " mode
  {
    echo "POSTGRES_PASSWORD=$(head -c 24 /dev/urandom | base64 | tr -dc 'A-Za-z0-9')"
    echo "FEXOR_DATA_KEY=$(head -c 32 /dev/urandom | base64)"
    if [[ "$mode" == "2" ]]; then
      read -rp "Domain name, e.g. attendance.example.com: " domain
      read -rp "Your email (for certificate notices): " email
      [[ -n "$domain" && -n "$email" ]] || need "Domain and email are both needed for internet mode."
      echo "FEXOR_CADDYFILE=Caddyfile"; echo "FEXOR_DOMAIN=$domain"; echo "FEXOR_TLS=$email"
      echo "FEXOR_SECURE_COOKIES=true"; echo "FEXOR_PUBLIC_URL=https://$domain"
    else
      echo "FEXOR_CADDYFILE=Caddyfile.http"; echo "FEXOR_SECURE_COOKIES=false"
      echo "FEXOR_PUBLIC_URL=http://$(lan_ip || echo localhost)"
    fi
  } > .env
  chmod 600 .env
  say "Settings saved in deploy/.env. Keep a copy somewhere safe: it holds the key that protects stored face data."
fi
url=$(grep '^FEXOR_PUBLIC_URL=' .env | cut -d= -f2-)

say "Starting... the first time this takes a few minutes."
docker compose --env-file .env up -d --build
echo "Waiting for the server to be ready..."
for _ in $(seq 120); do
  docker compose --env-file .env exec -T server python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health')" >/dev/null 2>&1 && break
  sleep 2
done
code=$(docker compose --env-file .env exec -T server fexor-server setup-code | tr -d '\r\n')

open_url() { (xdg-open "$1" || open "$1") >/dev/null 2>&1 || true; }
say "Fexor is running at $url"
if [[ -n "$code" ]]; then
  echo "Setup code (already filled in for you): $code"
  open_url "$url/?setup=$code"
else
  open_url "$url"
fi
echo "Kiosks and other computers use this same address. To stop: docker compose -f deploy/docker-compose.yml down"
