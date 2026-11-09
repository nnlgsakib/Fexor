#!/usr/bin/env bash
# Restores a backup made by backup-server.sh: replaces the whole database with the backup's.
#   Usage: ./deploy/restore-server.sh                  (newest backup in deploy/backups)
#          ./deploy/restore-server.sh path/to/backup   (a backup folder, e.g. from another computer)
# Also takes the face-data key from the backup, so photos and two-step sign-in keep working.
# Scripted use: FEXOR_RESTORE_CONFIRM=RESTORE
set -euo pipefail
cd "$(dirname "$0")"
[[ -f .env ]] || { echo "Start the server once first (./deploy/start-server.sh), then restore into it."; exit 1; }
src=${1:-$(ls -1d backups/*/ 2>/dev/null | sort -r | head -1)}
src=${src%/}
[[ -n "$src" && -f "$src/fexor.dump" && -f "$src/env.txt" ]] || { echo "No backup found (need fexor.dump and env.txt in a folder)."; exit 1; }
compose() { docker compose --env-file .env "$@"; }

echo "Restore the Fexor server from: $src"
echo "Everything in the current database is replaced by that backup. There is no undo,"
echo "but a backup of the current state is made first."
answer=${FEXOR_RESTORE_CONFIRM:-}
[[ -z "$answer" ]] && read -rp "Type RESTORE to continue, or anything else to cancel: " answer
[[ "$answer" == "RESTORE" ]] || { echo "Cancelled. Nothing was changed."; exit 0; }

./backup-server.sh
compose stop server worker >/dev/null 2>&1 || true
compose cp "$src/fexor.dump" db:/tmp/restore.dump >/dev/null
# --clean drops what is there first. Ownership/grants come from this server's own database user.
compose exec -T db pg_restore -U fexor -d fexor --clean --if-exists --no-owner /tmp/restore.dump
compose exec -T db rm -f /tmp/restore.dump

key=$(grep '^FEXOR_DATA_KEY=' "$src/env.txt" | cut -d= -f2-)
if [[ -n "$key" ]] && ! grep -qxF "FEXOR_DATA_KEY=$key" .env; then
  grep -v '^FEXOR_DATA_KEY=' .env > .env.new
  echo "FEXOR_DATA_KEY=$key" >> .env.new
  chmod 600 .env.new && mv .env.new .env
  echo "Took the face-data key from the backup."
fi
compose up -d >/dev/null
echo "Restored. The server is starting again with the backup's data."
