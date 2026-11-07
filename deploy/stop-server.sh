#!/usr/bin/env bash
# Stops the Fexor server completely: the Docker containers and any development server
# started with `uv run fexor-server`. Your data is kept.   Usage: ./deploy/stop-server.sh
cd "$(dirname "$0")"
# Compose project of THIS folder, the way Compose works it out: COMPOSE_PROJECT_NAME if set, else
# the folder name. Never assume "deploy": a copy in another folder is another server.
project=${COMPOSE_PROJECT_NAME:-$(grep -s '^COMPOSE_PROJECT_NAME=' .env | cut -d= -f2-)}
project=${project:-$(basename "$PWD" | tr 'A-Z' 'a-z' | tr -cd 'a-z0-9_-')}

echo "Stopping the Fexor server..."
if command -v docker >/dev/null && docker info >/dev/null 2>&1; then
  ids=$(docker ps -aq --filter "label=com.docker.compose.project=$project")
  if [[ -n "$ids" ]]; then
    docker stop $ids >/dev/null && docker rm $ids >/dev/null   # the database volume stays
    echo "  Stopped $(echo "$ids" | wc -l | tr -d ' ') server containers."
  else
    echo "  No server containers were running."
  fi
else
  echo "  Docker is not running, so its containers are already stopped."
fi
if pkill -f 'fexor-server(\.exe)?"? +(serve|worker)' 2>/dev/null; then
  echo "  Stopped the development server."
fi
echo
echo "The Fexor server is stopped. Your data is kept."
echo "It stays stopped until you run ./deploy/start-server.sh"
