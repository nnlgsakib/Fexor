# Fexor

Attendance for any place that needs it: offices, schools, shifts, branches. People check in at a
kiosk by showing a signed QR card and their face. The kiosk checks that the face is live and matches
the card, on the device itself, and keeps working when the network is down. Admins manage everything
from a web panel.

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

## Quick start (development)

Requirements: [uv](https://docs.astral.sh/uv/), Node 20+, Docker.

```bash
docker run -d --name fexor-pg -e POSTGRES_USER=fexor -e POSTGRES_PASSWORD=fexor -e POSTGRES_DB=fexor -p 5432:5432 postgres:16-alpine
uv sync
cat > .env <<EOF
FEXOR_DATA_KEY=$(uv run fexor-server gen-key)
FEXOR_SECURE_COOKIES=false
FEXOR_WEB_DIR=web/admin/dist
EOF
uv run fexor-server migrate
uv run fexor-server create-admin --email you@example.com
(cd web/admin && npm install && npm run build)
uv run fexor-server serve --host 127.0.0.1      # http://127.0.0.1:8000
uv run fexor-server worker                       # second terminal
```

For frontend work, run `npm run dev` in `web/admin` (port 5173; it proxies `/api` to port 8000).

Try a kiosk on the same machine: in the admin panel open a space, go to **Kiosks**, then
**Add kiosk**, and:

```bash
uv run fexor-kiosk enroll --server http://127.0.0.1:8000 --code ABCDE-FGHJK --allow-http
uv run fexor-kiosk run --camera 0               # open http://127.0.0.1:8300
```

## Production

### Server (Docker)

```bash
cd deploy
cp .env.example .env              # set domain, TLS mode, DB password
docker build -t fexor-server:latest ..
docker run --rm fexor-server:latest fexor-server gen-key    # put this in FEXOR_DATA_KEY
docker compose up -d
docker compose exec server fexor-server create-admin --email you@example.com
```

- **Public domain:** set `FEXOR_TLS` to your email. Caddy gets a Let's Encrypt certificate.
- **Office network only:** set `FEXOR_TLS=internal`. Caddy issues its own certificate. Copy its
  root CA to each kiosk (`docker compose cp caddy:/data/caddy/pki/authorities/local/root.crt .`)
  and set `FEXOR_KIOSK_CA` to that file on the kiosk.
- **Back up** the `db` volume (`pg_dump`) and keep `FEXOR_DATA_KEY` safe and separate from the
  backups. Without the key, stored faces and two-step sign-in secrets can't be read.
- The `server` container runs migrations on start. Scale API processes with `FEXOR_WORKERS`.

### Kiosk

Any computer with a camera. A Raspberry Pi 5 (4 GB+) with a USB webcam and a touch screen is a good
low-cost kiosk. Use 64-bit Raspberry Pi OS.

```bash
sudo CAMERA=0 ./deploy/kiosk/install-linux.sh          # Raspberry Pi / Debian / Ubuntu
sudo CSI_CAMERA=1 ./deploy/kiosk/install-linux.sh      # Pi ribbon-cable camera
.\deploy\kiosk\install-windows.ps1 -Camera 0           # Windows laptop / PC
```

This installs a service that starts on boot and opens the kiosk page full screen at login. On first
start, enter the server address and the setup code from **Admin, Kiosks, Add kiosk**. Then:

- **Check-ins while offline** are stored on the kiosk and uploaded when the network is back.
- **Kiosk clock:** the kiosk corrects its clock offset from the server. The dashboard warns when a
  kiosk drifts by more than 2 minutes. A Raspberry Pi has no battery-backed clock, so keep NTP on.
- **Encryption:** the local roster and token are encrypted with a device key stored next to the
  data. Turn on full-disk encryption on kiosks in public places.

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
