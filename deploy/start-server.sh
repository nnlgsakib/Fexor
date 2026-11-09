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
  echo "  2) Internet, own domain - domain points to this computer, ports 80 and 443 open (HTTPS)"
  echo "  3) Internet, Cloudflare - HTTPS through a Cloudflare Tunnel; no router changes needed"
  read -rp "Choose 1, 2 or 3 (default 1): " mode
  cfg="POSTGRES_PASSWORD=$(head -c 24 /dev/urandom | base64 | tr -dc 'A-Za-z0-9')
FEXOR_DATA_KEY=$(head -c 32 /dev/urandom | base64)"
  if [[ "$mode" == "3" ]]; then
    echo "In the Cloudflare dashboard: Zero Trust > Networks > Tunnels > Create a tunnel (Cloudflared)."
    echo "Add a public hostname (e.g. attendance.example.com) with service http://caddy:80,"
    echo "then copy the tunnel token shown in the install command."
    read -rp "Public hostname, e.g. attendance.example.com: " host
    read -rp "Tunnel token: " token
    [[ -n "$host" && -n "$token" ]] || need "The hostname and the tunnel token are both needed."
    cfg+="
COMPOSE_PROFILES=tunnel
CF_TUNNEL_TOKEN=$token
FEXOR_CADDYFILE=Caddyfile.tunnel
FEXOR_SECURE_COOKIES=true
FEXOR_PUBLIC_URL=https://$host"
  elif [[ "$mode" == "2" ]]; then
    read -rp "Domain name, e.g. attendance.example.com: " domain
    read -rp "Your email (for certificate notices): " email
    [[ -n "$domain" && -n "$email" ]] || need "Domain and email are both needed for internet mode."
    cfg+="
FEXOR_CADDYFILE=Caddyfile
FEXOR_DOMAIN=$domain
FEXOR_TLS=$email
FEXOR_SECURE_COOKIES=true
FEXOR_PUBLIC_URL=https://$domain"
  else
    cfg+="
FEXOR_CADDYFILE=Caddyfile.http
FEXOR_SECURE_COOKIES=false
FEXOR_PUBLIC_URL=http://$(lan_ip || echo localhost)"
  fi
  (umask 077 && printf '%s
' "$cfg" > .env)
  say "Settings saved in deploy/.env. Keep a copy somewhere safe: it holds the key that protects stored face data."
fi
# Older tunnel installs used Caddyfile.http, which now trusts no proxy: switch them over.
if grep -q '^COMPOSE_PROFILES=.*tunnel' .env && grep -qx 'FEXOR_CADDYFILE=Caddyfile.http' .env; then
  sed -i.bak 's/^FEXOR_CADDYFILE=Caddyfile\.http$/FEXOR_CADDYFILE=Caddyfile.tunnel/' .env && rm -f .env.bak
fi
url=$(grep '^FEXOR_PUBLIC_URL=' .env | cut -d= -f2-)

say "Starting... the first time this takes a few minutes."
if docker compose --env-file .env pull server; then
  docker compose --env-file .env up -d
elif [ ! -f ../Dockerfile ]; then
  echo "Could not download the Fexor server. Check this computer's internet connection and try again." >&2
  exit 1
else
  echo "No ready-made server image available; building it here instead."
  docker compose --env-file .env up -d --build
fi
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
