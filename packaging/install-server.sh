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

version_of() { sed -n 's/^Fexor server \([^ ]*\).*/\1/p' "$1" 2>/dev/null | head -1 | tr -d '\r'; }

# Already installed here? Then this run updates it: settings and data stay.
update=""
if [ -f "$dir/deploy/.env" ]; then
  update=1
  current=$(version_of "$dir/README.txt"); current=${current:-an older version}
  echo "Fexor is already installed in $dir ($current). Updating it to the latest version."
else
  echo "Installing the Fexor server to $dir"
fi
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
curl -fsSL "$source/fexor-server.zip" -o "$tmp/fexor-server.zip"
if command -v unzip >/dev/null; then
  unzip -q "$tmp/fexor-server.zip" -d "$tmp"
else
  python3 -m zipfile -e "$tmp/fexor-server.zip" "$tmp"
fi
if [ -n "$update" ]; then
  latest=$(version_of "$tmp/fexor-server/README.txt")
  if [ "$latest" = "$current" ]; then echo "Already on the latest version ($latest): reinstalling it and restarting."
  else echo "Updating from $current to ${latest:-the latest version}."; fi
fi
mkdir -p "$dir"
# Overwrites the program files; deploy/.env is not in the zip, so settings and keys stay.
cp -R "$tmp/fexor-server/." "$dir/"
chmod +x "$dir"/deploy/*.sh
echo "Done. Start: $dir/deploy/start-server.sh   Stop: $dir/deploy/stop-server.sh"

[ "${FEXOR_NO_START:-}" = "1" ] && exit 0

# stdin is this script (curl | sh): give the setup questions the terminal, when there is one
# (none when run by another program; then there are no questions on an update anyway).
with_tty() { if ( : </dev/tty ) 2>/dev/null; then "$@" </dev/tty; else "$@"; fi; }

if ! command -v docker >/dev/null; then
  if [ "$(uname)" = "Linux" ]; then
    echo "Installing Docker (asks for your password)..."
    curl -fsSL https://get.docker.com | sudo sh
    sudo usermod -aG docker "$(id -un)"
    # This login session doesn't have the new group yet; sg runs the start script with it.
    with_tty sg docker -c "\"$dir/deploy/start-server.sh\""
    exit 0
  fi
  echo "Install Docker Desktop (https://www.docker.com/products/docker-desktop/), open it once,"
  echo "then run: $dir/deploy/start-server.sh"
  exit 1
fi

export FEXOR_UPDATE="$update"  # update mode: back up, download the new version, restart
with_tty "$dir/deploy/start-server.sh"
