#!/usr/bin/env bash
# Deletes the Fexor server completely so you can start a new one from scratch: containers, the
# database (all people, faces, attendance), the certificate store, deploy/.env (passwords and
# encryption key) and the old development server's data. Program files and the Docker image
# (code only) are kept.   Usage: ./deploy/reset-server.sh   (scripted: FEXOR_RESET_CONFIRM=DELETE)
cd "$(dirname "$0")"
root=$(cd .. && pwd)
# Compose project of THIS folder, the way Compose works it out: COMPOSE_PROJECT_NAME if set, else
# the folder name. Never assume "deploy": a copy in another folder is another server.
project=${COMPOSE_PROJECT_NAME:-$(grep -s '^COMPOSE_PROJECT_NAME=' .env | cut -d= -f2-)}
project=${project:-$(basename "$PWD" | tr 'A-Z' 'a-z' | tr -cd 'a-z0-9_-')}

if ! command -v docker >/dev/null || ! docker info >/dev/null 2>&1; then
  echo "Docker is not running, so the database cannot be deleted. Start Docker and try again."; exit 1
fi
containers=$(docker ps -aq --filter "label=com.docker.compose.project=$project")
volumes=$(docker volume ls -q --filter "label=com.docker.compose.project=$project")
files=()
for f in "$PWD/.env" "$root/.env" "$root/data"; do [[ -e "$f" ]] && files+=("$f"); done

echo "Reset the Fexor server"
echo
echo "Server folder: $PWD  (Docker project '$project')"
echo
echo "This permanently deletes:"
echo "  - every person, face photo, QR card, schedule and attendance record"
echo "  - all admin accounts and kiosk pairings (kiosks must be paired again)"
[[ -n "$containers" ]] && echo "  - $(echo "$containers" | wc -l | tr -d ' ') server containers"
for v in $volumes; do echo "  - Docker volume $v"; done
for f in "${files[@]}"; do echo "  - $f"; done
echo
if [[ -z "$containers$volumes" && ${#files[@]} -eq 0 ]]; then echo "Nothing to delete: there is no Fexor server here."; exit 0; fi
echo "There is no undo. Back up deploy/.env and the database first if you might need them."
answer=${FEXOR_RESET_CONFIRM:-}
[[ -z "$answer" ]] && read -rp "Type DELETE to continue, or anything else to cancel: " answer
[[ "$answer" == "DELETE" ]] || { echo "Cancelled. Nothing was deleted."; exit 0; }

[[ -n "$containers" ]] && docker rm -f $containers >/dev/null && echo "Removed the server containers."
for v in $volumes; do docker volume rm "$v" >/dev/null && echo "Deleted volume $v."; done
for f in "${files[@]}"; do rm -rf "$f" && echo "Deleted $f."; done
echo
echo "The old server is gone. Run ./deploy/start-server.sh to set up a new one."
