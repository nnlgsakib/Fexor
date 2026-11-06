# Fexor setup manual

This manual sets up Fexor on one computer with a few commands: the server, the admin panel and a
kiosk. It takes about 20 minutes. Commands work in PowerShell (Windows) and in a terminal (Linux,
macOS) unless marked.

## What you need

- A computer running Windows 10/11, Linux or macOS, with internet for the first install.
- A camera: the laptop camera, a USB webcam, or a phone with the DroidCam app.
- These tools:

| Tool | Install |
| --- | --- |
| Git | https://git-scm.com/downloads |
| uv (runs Python) | Windows: `powershell -ExecutionPolicy Bypass -c "irm https://astral.sh/uv/install.ps1 \| iex"`<br>Linux/macOS: `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| Node.js 20 or newer | https://nodejs.org (only to build the admin panel once) |
| Docker Desktop | https://www.docker.com/products/docker-desktop (only runs the database) |

## 1. Download and install

```bash
git clone https://github.com/nnlgsakib/Fexor.git
cd Fexor
uv sync
cd web/admin
npm install
npm run build
cd ../..
```

## 2. Start the database

Open Docker Desktop first, then:

```bash
docker run -d --name fexor-pg --restart unless-stopped -e POSTGRES_USER=fexor -e POSTGRES_PASSWORD=fexor -e POSTGRES_DB=fexor -p 5432:5432 postgres:16-alpine
```

It starts again by itself whenever Docker Desktop runs. You only do this once.

## 3. Create the settings file

Windows (PowerShell):

```powershell
Set-Content -Encoding ascii .env "FEXOR_SECURE_COOKIES=false`nFEXOR_WEB_DIR=web/admin/dist"
```

Linux / macOS:

```bash
printf 'FEXOR_SECURE_COOKIES=false\nFEXOR_WEB_DIR=web/admin/dist\n' > .env
```

The first start creates the encryption key in `data/data.key`. **Back up that file.** It protects
the stored face data; without it the data cannot be read.

## 4. Start the server

Use two terminal windows, both in the `Fexor` folder, and leave them open.

Terminal 1, the server and admin panel:

```bash
uv run fexor-server serve --host 127.0.0.1
```

Terminal 2, background jobs (marks absences, recalculates days):

```bash
uv run fexor-server worker
```

Open http://127.0.0.1:8000. The first time it shows **Welcome to Fexor**: enter your name, email
and a password (10 characters or more) and click **Create account**.

## 5. Set up a space

In the admin panel:

1. Click **All spaces**, then **New space**. Give it a name, pick **Office** (check in and out) or
   **School** (one check per day), choose the time zone and click **Create space**.
2. Follow **Get this space ready** on the space's overview:
   - **People:** **Add employee** / **Add student**, or **Bulk actions, Import from Excel or CSV**.
   - **Face photos:** open a person, **Face photos** tab, add 2 or 3 clear front-facing photos.
   - **Working hours:** **Schedule & holidays**, check the shift times and add holidays.
   - **QR cards:** open a person, **QR card**, then **Print card** or **Download PNG**.

## 6. Set up the kiosk

1. In the admin panel, open the space, go to **Kiosks**, click **Add kiosk**, type a name and click
   **Continue**. Note the code under the QR, for example `ABCDE-FGHJK`.
2. Terminal 3, pair the kiosk with that code:

   ```bash
   uv run fexor-kiosk enroll --server http://127.0.0.1:8000 --code ABCDE-FGHJK
   ```

   It prints `enrolled as '<name>' in space '<space>'`.
3. Start the kiosk, choosing the camera number:

   ```bash
   uv run fexor-kiosk run --camera 0
   ```

4. Open http://127.0.0.1:8300. You should see the camera picture and **Online** at the top.

**Wrong or black camera?** Stop the kiosk with Ctrl+C and try `--camera 1`, `--camera 2` and so
on. For a phone with DroidCam, use the address the app shows:
`uv run fexor-kiosk run --camera http://192.168.1.5:4747/video`.

## 7. Check in

1. Show the QR card to the camera. The kiosk reads it by itself.
2. Look straight at the camera, then do what the screen asks: blink, turn your head, smile.
3. A green **Checked in** with the time means it worked. Scan again after 2 minutes or more for
   **Checked out**.
4. See the result in the admin panel under **Overview**, **Check-ins** and **Register**.

## Every day

Start in this order, each in its own terminal (the database starts with Docker Desktop):

```bash
uv run fexor-server serve --host 127.0.0.1
uv run fexor-server worker
uv run fexor-kiosk run --camera 0
```

Stop any of them with Ctrl+C. Pairing, people and records are kept.

## Kiosks on other computers

`--host 127.0.0.1` only accepts this computer. To let kiosks on other computers connect:

1. Find this computer's network address: `ipconfig` (Windows) or `hostname -I` (Linux), for
   example `192.168.1.20`.
2. Add `FEXOR_PUBLIC_URL=http://192.168.1.20:8000` to `.env`.
3. Start the server with `uv run fexor-server serve --host 0.0.0.0`.
4. Allow port 8000 through the firewall. Windows (as administrator):
   `New-NetFirewallRule -DisplayName "Fexor" -Direction Inbound -Protocol TCP -LocalPort 8000 -Action Allow`
5. On the kiosk computer, do step 1 (skip the `npm` lines) and step 6 with
   `--server http://192.168.1.20:8000`.

## If something goes wrong

| Problem | Fix |
| --- | --- |
| `connection refused` when the server starts | The database isn't running: open Docker Desktop, then `docker start fexor-pg`. |
| Kiosk shows **Offline** | The server terminal was closed. Start `fexor-server serve` again. |
| `invalid or expired enrollment code` | The code is used or older than 30 minutes. Click **Show pairing QR** under **Kiosks** for a new one. |
| `server URL must start with https://` | The address isn't on your local network. Use the local address, or add `--allow-http` for testing. |
| "Face did not match" | Add 2 or 3 clearer photos of that person, taken in normal light. |
| "Live face check failed" | Light the face from the front; avoid a bright window behind the person. |
| Lost or shared QR card | Open the person, **QR card**, **Issue new code**, and print the new card. |

## More

- Production install with Docker, HTTPS and Raspberry Pi kiosks: see the [README](../README.md).
- Testing without a second screen: start the kiosk with `FEXOR_ENV=dev` to upload a QR image
  instead of scanning one (testing only).
