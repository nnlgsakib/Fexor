#!/usr/bin/env bash
# Backs up the Fexor server: the whole database plus deploy/.env (it holds the key that
# protects face data; a backup without it can't be read). Saved in deploy/backups/<date>/,
# newest 14 kept. Copy that folder somewhere else (USB drive, cloud storage) now and then.
#   Usage: ./deploy/backup-server.sh
set -euo pipefail
cd "$(dirname "$0")"
export MSYS_NO_PATHCONV=1  # Git Bash on Windows: keep container paths like /tmp as they are
[[ -f .env ]] || { echo "No Fexor server here (deploy/.env is missing)."; exit 1; }
compose() { docker compose --env-file .env "$@"; }

compose up -d db >/dev/null 2>&1  # the database must run; the rest of the server may be stopped
for _ in $(seq 60); do compose exec -T db pg_isready -U fexor >/dev/null 2>&1 && break; sleep 1; done

dir="backups/$(date +%Y-%m-%d_%H%M%S)"
mkdir -p "$dir"
trap '[[ -s "$dir/fexor.dump" ]] || rm -rf "$dir"' EXIT  # no half-made backups
compose exec -T db pg_dump -U fexor -Fc -f /tmp/fexor.dump fexor
compose cp db:/tmp/fexor.dump "$dir/fexor.dump" >/dev/null
compose exec -T db rm -f /tmp/fexor.dump
(umask 077 && cp .env "$dir/env.txt")
[[ -s "$dir/fexor.dump" ]] || { echo "Backup failed: the database dump is empty." >&2; exit 1; }

ls -1d backups/*/ | sort -r | tail -n +15 | while read -r old; do rm -rf "$old"; done
echo "Backup saved in deploy/$dir ($(du -h "$dir/fexor.dump" | cut -f1))."
