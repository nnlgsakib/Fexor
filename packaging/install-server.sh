#!/bin/sh
# Fexor server installer for Linux and macOS. Run:
#
#   curl -fsSL https://github.com/nnlgsakib/Fexor/releases/latest/download/install-server.sh | sh
#
# Downloads the server files (no source code) to ~/fexor-server, installs Docker on Linux if
# needed, and starts the server. Run it again to update: deploy/.env (settings and keys) is kept.
#
# FEXOR_DIR       install folder (default: ~/fexor-server)
# FEXOR_DOWNLOAD  where fexor-server.zip comes from (default: latest GitHub release)
# FEXOR_NO_START  1 = only download/update the files, don't start
set -eu

dir="${FEXOR_DIR:-$HOME/fexor-server}"
source="${FEXOR_DOWNLOAD:-https://github.com/nnlgsakib/Fexor/releases/latest/download}"

echo "Installing the Fexor server to $dir"
if [ -f "$dir/deploy/.env" ] && [ "${FEXOR_NO_START:-}" != "1" ] && command -v docker >/dev/null \
   && [ -x "$dir/deploy/backup-server.sh" ]; then
  # Updating an existing server: keep a backup of the database from before the update.
  echo "Backing up the current server before updating..."
  "$dir/deploy/backup-server.sh" || true
fi
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
curl -fsSL "$source/fexor-server.zip" -o "$tmp/fexor-server.zip"
if command -v unzip >/dev/null; then
  unzip -q "$tmp/fexor-server.zip" -d "$tmp"
else
  python3 -m zipfile -e "$tmp/fexor-server.zip" "$tmp"
fi
mkdir -p "$dir"
# Overwrites the program files; deploy/.env is not in the zip, so settings and keys stay.
cp -R "$tmp/fexor-server/." "$dir/"
chmod +x "$dir"/deploy/*.sh
echo "Done. Start: $dir/deploy/start-server.sh   Stop: $dir/deploy/stop-server.sh"

[ "${FEXOR_NO_START:-}" = "1" ] && exit 0

if ! command -v docker >/dev/null; then
  if [ "$(uname)" = "Linux" ]; then
    echo "Installing Docker (asks for your password)..."
    curl -fsSL https://get.docker.com | sudo sh
    sudo usermod -aG docker "$(id -un)"
    # This login session doesn't have the new group yet; sg runs the start script with it.
    sg docker -c "\"$dir/deploy/start-server.sh\"" </dev/tty
    exit 0
  fi
  echo "Install Docker Desktop (https://www.docker.com/products/docker-desktop/), open it once,"
  echo "then run: $dir/deploy/start-server.sh"
  exit 1
fi

# stdin is this script (curl | sh), so give the setup questions the terminal.
"$dir/deploy/start-server.sh" </dev/tty
