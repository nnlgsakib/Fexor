#!/usr/bin/env bash
# Install the Fexor kiosk on Raspberry Pi OS (64-bit) / Debian / Ubuntu.
# Run from a copy of the repository:  sudo ./deploy/kiosk/install-linux.sh
#
#   CAMERA=0              optional: camera index or stream URL (otherwise chosen on screen)
#   CSI_CAMERA=1          Raspberry Pi ribbon-cable camera (wraps the kiosk in libcamerify)
#   KIOSK_USER=pi         desktop user that should see the full-screen kiosk page
set -euo pipefail

[[ $EUID -eq 0 ]] || { echo "run with sudo"; exit 1; }
SRC="$(cd "$(dirname "$0")/../.." && pwd)"
APP=/opt/fexor
DATA=/var/lib/fexor-kiosk
CAMERA="${CAMERA:-}"
CAMERA_ARG=""
[[ -n "$CAMERA" ]] && CAMERA_ARG="--camera $CAMERA"
KIOSK_USER="${KIOSK_USER:-${SUDO_USER:-pi}}"

apt-get update -q
apt-get install -y -q curl ca-certificates
if [[ "${CSI_CAMERA:-0}" == "1" ]]; then apt-get install -y -q libcamera-v4l2 libcamera-tools; fi
command -v chromium >/dev/null || command -v chromium-browser >/dev/null || apt-get install -y -q chromium || true

id fexor >/dev/null 2>&1 || useradd --system --home "$DATA" --groups video fexor
install -d -o fexor -g fexor -m 700 "$DATA"
install -d "$APP"
cp -r "$SRC/pyproject.toml" "$SRC/uv.lock" "$SRC/core" "$SRC/kiosk" "$SRC/server" "$APP/"
chown -R fexor:fexor "$APP"

if ! command -v uv >/dev/null; then curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin sh; fi
sudo -u fexor -H bash -c "cd $APP && UV_PYTHON_INSTALL_DIR=$APP/.python uv sync --frozen --no-dev --package fexor-kiosk"
sudo -u fexor -H bash -c "cd $APP && FEXOR_MODEL_DIR=$DATA/models .venv/bin/fexor-kiosk models"

WRAP=""
[[ "${CSI_CAMERA:-0}" == "1" ]] && WRAP="/usr/bin/libcamerify "
cat >/etc/systemd/system/fexor-kiosk.service <<EOF
[Unit]
Description=Fexor attendance kiosk
After=network-online.target
Wants=network-online.target

[Service]
User=fexor
Group=fexor
Environment=FEXOR_KIOSK_DATA=$DATA
Environment=FEXOR_MODEL_DIR=$DATA/models
# Set if your server uses a private CA (Caddy "internal"):
# Environment=FEXOR_KIOSK_CA=$DATA/server-ca.crt
ExecStart=${WRAP}$APP/.venv/bin/fexor-kiosk run $CAMERA_ARG
Restart=always
RestartSec=3
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=$DATA
PrivateTmp=true

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable --now fexor-kiosk

# Full-screen browser for the desktop user on login.
HOME_DIR=$(getent passwd "$KIOSK_USER" | cut -d: -f6)
if [[ -n "$HOME_DIR" ]]; then
  BROWSER=$(command -v chromium || command -v chromium-browser || echo chromium)
  install -d -o "$KIOSK_USER" "$HOME_DIR/.config/autostart"
  cat >"$HOME_DIR/.config/autostart/fexor-kiosk.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Fexor kiosk
Exec=sh -c 'sleep 8; $BROWSER --kiosk --noerrdialogs --disable-infobars --incognito --check-for-update-interval=31536000 http://127.0.0.1:8300/'
EOF
  chown "$KIOSK_USER" "$HOME_DIR/.config/autostart/fexor-kiosk.desktop"
fi

echo
echo "Installed. Restart the device: the kiosk screen opens full screen."
echo "On it, choose the camera, then show it the pairing QR from the admin panel (Kiosks > Add kiosk)."
