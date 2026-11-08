# Fexor

Attendance for any place that needs it: offices, schools, shifts, branches. People check in at a
kiosk by showing a signed QR card and their face. The kiosk checks that the face is live and matches
the card, on the device itself, and keeps working when the network is down. Admins manage everything
from a web panel.

**New here? Follow the [setup manual](docs/SETUP.md)**: one computer, a few commands, about 20 minutes.

```
[Admin browser] --HTTPS--> [Caddy] --> [Fexor server (FastAPI)] --> [PostgreSQL]
                                             |   [worker: absences, recalculation, webhooks]
                              device token   |
[Kiosk: camera + face models + local queue] <+   pulls roster, pushes check-ins
        ^ http://127.0.0.1:8300 (full-screen browser)
```

## What it does

- **Spaces.** Any number: "Head office", "Night shift", "School morning". Each has its own people,
  schedule, holidays, kiosks, dashboard and reports.
- **Two kinds of attendance.** *Check in and out* (offices: hours, late, early leave, overtime,
  half days) or *one check per day* (schools: present or late).
- **Schedules.** Shifts with working days, grace period and check-in windows, including overnight
  shifts (22:00–06:00). Per-person shift changes with a start date, public holiday presets for
  ~150 countries, custom holidays, leave.
- **People.** Add by hand or import Excel/CSV with dry-run checking. Custom fields per space. Face
  photos one by one or as a zip, with automatic quality checks. Printable ID-size QR cards.
- **Kiosk verification.** Signed QR, then a face check on the device that includes a test for
  photos and screens, a random movement test (blink, turn, smile, look up) and a 1:1 face match.
  See [How a check-in is verified](#how-a-check-in-is-verified).
- **Failures don't block the line.** A failed check shows a clear reason and offers *Ask admin to
  review*. After 3 failures that QR card is paused for 5 minutes; everyone else carries on.
- **Admin panel.** Live dashboard, register (people × days grid), check-in log, review queue, Excel
  and CSV reports, kiosk health, admins with per-space roles (owner, manager, viewer), two-step
  sign-in, audit log.
- **Integrations.** Read-only REST API keys, signed webhooks for every check-in, Excel/CSV export.
  OpenAPI docs at `/api/docs`.

## How a check-in is verified

All of it runs on the kiosk; camera frames never leave the device.

1. **QR card.** The token is `FX1.<payload>.<Ed25519 signature>`. The kiosk checks the signature
   with the server's public key, then checks that it is the person's *current* card (reissuing a
   card cancels the old one on the next sync, within about 15 seconds).
2. **One tracked face.** Exactly one person in frame, close enough. If the face jumps or changes
   mid-check, the check fails.
3. **Photo and screen detection** (MiniFASNet) on every frame, averaged.
4. **Movement test.** 2 random actions in random order (configurable 0–4), measured against the
   person's own neutral face from a 478-point face mesh. A replayed video can't know the order; an
   opposite head turn fails at once.
5. **Face match** (SFace) against that person's enrolled photos only, on several sharp,
   front-facing frames. Then a cross-check: if the face looks more like someone else in the
   space, it fails.

| Stage | Model | License | Speed (this dev PC) |
|---|---|---|---|
| Face detection | YuNet | MIT | ~11 ms |
| Face embedding | SFace | Apache-2.0 | ~22 ms |
| Photo/screen detection | MiniFASNetV2-SE (int8) | Apache-2.0 | ~9 ms |
| Face mesh | MediaPipe Face Mesh V2 (ONNX) | Apache-2.0 | ~5 ms |

Every model can be used commercially. They run through OpenCV and ONNX Runtime on Windows, macOS
and Linux (x86 and ARM), so the same kiosk runs on a Raspberry Pi, a laptop or a PC. Run
`fexor-kiosk bench` on the target machine to measure. Models are pinned by SHA-256 and checked on
download.

**Limit:** a normal camera stops printed photos, phone/tablet screens, replayed videos and simple
masks. It does not reliably stop a high-quality 3D mask. Where that matters, add a depth or IR
camera (not built in yet).

## Getting started (no command line)

### 1. Start the server

On the computer or cloud server that will hold the data, run one line. It downloads the server
(compiled, no source code), installs Docker if needed and starts it. Run it again to update.

- Windows (PowerShell):
  `irm https://github.com/nnlgsakib/Fexor/releases/latest/download/install-server.ps1 | iex`
- Linux / Mac:
  `curl -fsSL https://github.com/nnlgsakib/Fexor/releases/latest/download/install-server.sh | sh`

Or by hand, from a copy of this repository or the release's `fexor-server.zip`:

1. Install [Docker Desktop](https://www.docker.com/products/docker-desktop/) (or Docker Engine on
   a Linux server) and open it once.
2. Double-click **Start Server (Windows).cmd** in this folder. On Mac or Linux, run
   `./deploy/start-server.sh`.
3. The first time, it asks how the server will be reached:
   - **Office network only:** simplest. The admin panel and the kiosks must be on the same network.
     Traffic is not encrypted, so use this only on a network you trust.
   - **Internet, own domain:** a domain pointing to this computer, ports 80 and 443 open. Gets an
     HTTPS certificate automatically.
   - **Internet, Cloudflare:** HTTPS through a free Cloudflare Tunnel. Nothing to open on the
     router; needs a Cloudflare account with your domain on it. The script tells you where to get
     the tunnel token.
4. It downloads the ready-made server image (or builds it if none is published yet), then your
   browser opens on **Welcome to Fexor**. Create your admin account; the setup code is already
   filled in.

All passwords and keys are generated for you and saved in `deploy/.env`. **Keep a copy of that
file somewhere safe:** it holds the key that protects stored face data. The server keeps running
in the background and starts again with Docker. **Stop Server (Windows).cmd** (Mac/Linux:
`./deploy/stop-server.sh`) stops it completely, also any development server; your data stays, and
it remains stopped until you start it again.

To delete a server completely and start over, run **Reset Server (Windows).cmd** (Mac/Linux:
`./deploy/reset-server.sh`). It shows the server folder and everything it will delete (database,
`deploy/.env` with its keys, old development data) and only goes ahead after you type `DELETE`.
There is no undo.

### 2. Set up a space

Open a space and follow **Get this space ready** on its overview: add people (or import a
spreadsheet), add face photos, set working hours, print QR cards.

### 3. Set up a kiosk with the desktop app

On the kiosk computer, download the installer from the
[Releases page](https://github.com/nnlgsakib/Fexor/releases):

| Kiosk computer | File | Install |
| --- | --- | --- |
| Windows 10/11 PC or laptop | `FexorKiosk-Setup-<version>-windows-x64.exe` | Double-click. Leave **Start automatically when this user signs in** ticked. |
| Raspberry Pi 5 (64-bit Raspberry Pi OS) | `fexor-kiosk_<version>_arm64.deb` | Double-click to open it in the package installer, or `sudo apt install ./fexor-kiosk_<version>_arm64.deb` |
| Linux PC (Debian, Ubuntu) | `fexor-kiosk_<version>_amd64.deb` | As above |
| Mac (Apple silicon) | `FexorKiosk-<version>-macos-arm64.dmg` | Drag **Fexor Kiosk** to Applications. The first time, right-click it and choose **Open**. |

The app opens full screen on **Set up this kiosk**:

1. Pick the camera from the preview pictures, or type a phone camera address (DroidCam).
2. In the admin panel, go to **Kiosks → Add kiosk**. Hold the QR code it shows in front of the
   kiosk camera. The kiosk connects by itself.

The app starts by itself after every sign-in. To change the camera, move the kiosk to another
space or close the app, click **Settings QR** next to the kiosk in the admin panel and show it to
the kiosk camera. The code works for 10 minutes and only on that kiosk.

## Development

Requirements: [uv](https://docs.astral.sh/uv/), Node 20+, Docker.

```bash
docker run -d --name fexor-pg -e POSTGRES_USER=fexor -e POSTGRES_PASSWORD=fexor -e POSTGRES_DB=fexor -p 5432:5432 postgres:16-alpine
uv sync
printf 'FEXOR_SECURE_COOKIES=false\nFEXOR_WEB_DIR=web/admin/dist\n' > .env
(cd web/admin && npm install && npm run build)
uv run fexor-server serve --host 127.0.0.1      # http://127.0.0.1:8000, runs migrations first
uv run fexor-server worker                       # second terminal
```

The first visit shows **Welcome to Fexor**. Requests from the same machine don't need the setup
code. The encryption key is created in `data/data.key` (set `FEXOR_DATA_KEY` to manage it
yourself). For frontend work, run `npm run dev` in `web/admin` (port 5173; it proxies `/api` to
port 8000).

Run a kiosk on the same machine with `uv run fexor-kiosk run` and open http://127.0.0.1:8300.
Pair it from **Kiosks → Add kiosk**. Command-line pairing also works:
`uv run fexor-kiosk enroll --server http://127.0.0.1:8000 --code ABCDE-FGHJK`.

**Testing without a second screen:** start the kiosk with `FEXOR_ENV=dev` and the kiosk page shows
*Test: upload a QR image instead of scanning*. Download the card with **People, QR card, Download
PNG** and upload it. The token still goes through the full signature and revocation check; only
the camera step is skipped. The upload endpoint does not exist unless `FEXOR_ENV=dev`, so never set
it on a real kiosk.

## Production notes

- **Manual server setup:** copy `deploy/.env.example` to `deploy/.env`, fill it in and run
  `docker compose up -d` in `deploy/`. Then open the admin panel; the setup code comes from
  `docker compose exec server fexor-server setup-code`.
- **Office network mode** serves plain HTTP on port 80. Kiosks accept plain HTTP only for
  local-network addresses (private IPs, `localhost`, `.local`/`.lan` names); a server on the
  internet must use HTTPS (own domain or Cloudflare Tunnel mode).
- **Cloudflare Tunnel by hand:** in `deploy/.env` set `COMPOSE_PROFILES=tunnel`,
  `CF_TUNNEL_TOKEN=<token>`, `FEXOR_CADDYFILE=Caddyfile.http`, `FEXOR_SECURE_COOKIES=true` and
  `FEXOR_PUBLIC_URL=https://<your hostname>`. The tunnel's public hostname points to
  `http://caddy:80`.
- **Back up** the `db` volume (`pg_dump`) and keep `FEXOR_DATA_KEY` safe and separate from the
  backups. Without the key, stored faces and two-step sign-in secrets can't be read.
- The `server` container runs migrations on start. Scale API processes with `FEXOR_WORKERS`.

### Kiosk notes

A Raspberry Pi 5 (4 GB+) with a USB webcam and a touch screen is a good low-cost kiosk. Use 64-bit
Raspberry Pi OS with the desktop, set to sign in automatically. For the ribbon-cable camera module,
set `PI_CAMERA_MODULE=1` in `/etc/default/fexor-kiosk` and install `libcamera-v4l2`.

- **Check-ins while offline** are stored on the kiosk and uploaded when the network is back.
- **Kiosk clock:** the kiosk corrects its clock offset from the server. The dashboard warns when a
  kiosk drifts by more than 2 minutes. A Raspberry Pi has no battery-backed clock, so keep NTP on.
- **Encryption:** the local roster and token are encrypted with a device key stored next to the
  data. Turn on full-disk encryption on kiosks in public places.
- **Moving a kiosk** to another space (Settings QR → Disconnect) first uploads what it can; check-ins
  that still can't be uploaded are deleted, and the screen says how many.
- **App data and log:** `%LOCALAPPDATA%\Fexor Kiosk` (Windows), `~/Library/Application Support/Fexor Kiosk`
  (Mac), `~/.local/share/Fexor Kiosk` (Linux). `kiosk.log` and `crash.log` are there.
- **Script installs** (`deploy/kiosk/install-linux.sh`, `install-windows.ps1`) still work for
  machines where you prefer running from source.

### Releasing

Push a version tag to build everything:

```bash
git tag v0.2.0 && git push origin v0.2.0
```

GitHub Actions then publishes the server image `ghcr.io/nnlgsakib/fexor-server` (PC and ARM
servers) and attaches these files to a release:

- `fexor-server.zip`: the server download for customers. Start/Stop/Reset launchers, the
  compose file and a short README. No source code.
- `install-server.ps1` / `install-server.sh`: the one-line installers, which fetch that zip.
- Kiosk installers (Windows, macOS, Linux, Raspberry Pi).

After the first run, open the package on GitHub (**Packages**, **fexor-server**, **Package
settings**) and make it **Public** so servers can download it without signing in. To build a kiosk
installer locally: `uv sync --extra desktop --group build && uv run python packaging/build.py`.

**What customers get:** the server image and the kiosk app contain compiled Python only (`.pyc`),
not the `.py` source. The admin panel is minified JavaScript, as on any website. Give customers the
server zip and the kiosk installers, never this repository.

## Security

- Admin passwords: Argon2id. Sessions: server-side, `HttpOnly` + `Secure` + `SameSite=Strict`
  cookies, CSRF token on every change. Login rate limits and account lockout. Optional TOTP
  two-step sign-in.
- Every endpoint checks the caller's role in that space. Other spaces return 404, so their IDs
  don't leak.
- Face embeddings, photos, failure snapshots, TOTP secrets, webhook secrets and the QR signing key
  are encrypted at rest with AES-256-GCM. Each person's data is bound to their ID.
- Kiosks use single-use setup codes and revocable tokens (stored as hashes). A kiosk can only
  write to its own space; check-in uploads are idempotent.
- Strict security headers (CSP, HSTS, frame denial). Every admin change is in the audit log.
- Privacy: consent is recorded per person, enrollment photos can be discarded after processing,
  failure snapshots are off by default, and **Erase data** removes a person's biometrics.

## Project layout

```
core/     shared face, liveness, movement-test, QR signing and encryption code
server/   FastAPI app, database models and migrations, rules engine, worker, CLI
kiosk/    camera, verification state machine, offline store, sync, kiosk web page
web/admin React + Mantine admin panel
packaging/ desktop kiosk app build (PyInstaller spec, Windows/Linux/macOS installers)
deploy/   Dockerfile companions: compose file, Caddyfile, kiosk installers
```

## Tests

```bash
uv run pytest        # needs the Postgres container from Quick start
uv run ruff check .
```

The tests cover the rules engine (late, half day, overnight shifts, daylight-saving changes,
holidays, leave), QR forgery and revocation, encryption, the movement test, the full kiosk flow
with scripted inputs (impostor, photo, video replay, face swap, lockout, review request), a
real-model check that a printed photo is rejected, and the API end to end (permissions, import,
enrollment, kiosk sync, idempotent uploads, review, reports, webhooks).
