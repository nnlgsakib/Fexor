"""Make the server download for customers: launchers, deploy scripts and compose file. No source code.

The server itself comes from the published image (ghcr.io/nnlgsakib/fexor-server), which holds
compiled code only. Output: packaging/dist/fexor-server.zip (fixed name, so the installers'
".../releases/latest/download/fexor-server.zip" always gets the newest one)

    python packaging/server_bundle.py          (version from FEXOR_VERSION, else "dev")
"""

import os
import re
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VERSION = os.environ.get("FEXOR_VERSION", "dev")

FILES = [
    "Start Server (Windows).cmd",
    "Stop Server (Windows).cmd",
    "Reset Server (Windows).cmd",
    "deploy/docker-compose.yml",
    "deploy/Caddyfile",
    "deploy/Caddyfile.http",
    "deploy/start-server.ps1",
    "deploy/start-server.sh",
    "deploy/stop-server.ps1",
    "deploy/stop-server.sh",
    "deploy/reset-server.ps1",
    "deploy/reset-server.sh",
]

README = """Fexor server {version}
====================

1. Install Docker Desktop (https://www.docker.com/products/docker-desktop/) and open it once.
   On a Linux server, install Docker Engine instead.
2. Windows: double-click "Start Server (Windows).cmd".
   Mac/Linux: open a terminal in this folder and run ./deploy/start-server.sh
3. The first time, it asks how the server will be reached, downloads the server, and opens
   "Welcome to Fexor" in your browser. Create the admin account there.

Settings and keys are saved in deploy/.env. Keep a copy of that file somewhere safe: it holds the
key that protects stored face data. Without it, a restored backup cannot be read.

Stop the server:   "Stop Server (Windows).cmd"   or ./deploy/stop-server.sh
Delete everything: "Reset Server (Windows).cmd"  or ./deploy/reset-server.sh  (asks first, no undo)
Update:            run the start script again; it downloads the newest server.

Kiosks: install the Fexor Kiosk app on each verification computer, open it, choose
"New session" and scan the pairing QR from the admin panel (Kiosks, Add kiosk).
"""


def main() -> None:
    out = ROOT / "packaging" / "dist" / "fexor-server.zip"
    out.parent.mkdir(parents=True, exist_ok=True)
    top = "fexor-server"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for name in FILES:
            data = (ROOT / name).read_bytes()
            if name.endswith("docker-compose.yml"):
                # No source here, so no "build from source" option: always use the published image.
                data, n = re.subn(rb"\r?\n[ \t]*(# Published image;|build: \{)[^\n]*", b"", data)
                assert n == 2, "docker-compose.yml: build lines not found"
            info = zipfile.ZipInfo(f"{top}/{name}", date_time=(2020, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            # Unix permissions so the .sh scripts stay runnable after unzip on Mac/Linux.
            info.external_attr = (0o755 if name.endswith(".sh") else 0o644) << 16
            z.writestr(info, data)
        info = zipfile.ZipInfo(f"{top}/README.txt", date_time=(2020, 1, 1, 0, 0, 0))
        info.external_attr = 0o644 << 16
        z.writestr(info, README.format(version=VERSION).replace("\n", "\r\n"), zipfile.ZIP_DEFLATED)
    print(out)


if __name__ == "__main__":
    main()
