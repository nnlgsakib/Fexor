# Starts the Fexor server with Docker. The first run asks how the server will be reached and
# writes deploy\.env. Normally launched by double-clicking "Start Server (Windows).cmd".
#
# FEXOR_UNATTENDED=1: no questions, no browser, no "press Enter" (deploy\.env must exist).
#
# Docker prints progress and warnings on stderr. Windows PowerShell 5.1 turns that into errors,
# so this script never uses "Stop" and checks $LASTEXITCODE after every docker call instead.
$ErrorActionPreference = "Continue"
$deploy = $PSScriptRoot
$envFile = Join-Path $deploy ".env"
$unattended = [bool]$env:FEXOR_UNATTENDED

function Pause-Window {
    if (-not $unattended) { [void](Read-Host "Press Enter to close this window") }
}

function Fail($msg) {
    Write-Host ""
    Write-Host $msg -ForegroundColor Red
    Pause-Window
    exit 1
}

function RandomBase64([int]$bytes) {
    $b = New-Object byte[] $bytes
    [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($b)
    return [Convert]::ToBase64String($b)
}

function LanAddress {
    $ip = Get-NetIPConfiguration -ErrorAction SilentlyContinue |
        Where-Object { $_.IPv4DefaultGateway -and $_.NetAdapter.Status -eq "Up" } |
        ForEach-Object { $_.IPv4Address.IPAddress } | Select-Object -First 1
    if (-not $ip) { $ip = "localhost" }
    return $ip
}

function Compose {
    # Runs `docker compose --env-file .env <args>`; its output is shown as plain text and is not
    # part of the return value (only the exit code is).
    & docker compose --env-file "$envFile" @args 2>&1 | ForEach-Object { "$_" } | Out-Host
    return $LASTEXITCODE
}

function Compose-Quiet {
    & docker compose --env-file "$envFile" @args 2>&1 | Out-Null
    return $LASTEXITCODE
}

function Ensure-Docker {
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
        Write-Host "Fexor runs inside Docker Desktop, which is not installed yet."
        Write-Host "Opening the download page. Install it, start it once, then run Start Server again."
        if (-not $unattended) { Start-Process "https://www.docker.com/products/docker-desktop/" }
        Fail "Docker Desktop is needed."
    }
    & docker info 2>&1 | Out-Null
    if ($LASTEXITCODE -eq 0) { return }
    $dd = "$env:ProgramFiles\Docker\Docker\Docker Desktop.exe"
    if (Test-Path $dd) { Write-Host "Starting Docker Desktop..."; Start-Process $dd }
    for ($i = 0; $i -lt 90; $i++) {
        Start-Sleep 2
        & docker info 2>&1 | Out-Null
        if ($LASTEXITCODE -eq 0) { return }
    }
    Fail "Docker Desktop did not start. Open it yourself, wait until it says it is running, then try again."
}

function Ensure-Settings {
    if (Test-Path $envFile) { return }
    if ($unattended) { Fail "deploy\.env is missing; run once without FEXOR_UNATTENDED to create it." }
    Write-Host "First-time setup" -ForegroundColor Cyan
    Write-Host "  1) Office network only  - simplest; kiosks and admins on the same network"
    Write-Host "  2) Internet, own domain - domain points to this computer, ports 80 and 443 open (HTTPS)"
    Write-Host "  3) Internet, Cloudflare - HTTPS through a Cloudflare Tunnel; no router changes needed"
    $mode = Read-Host "Choose 1, 2 or 3 (default 1)"
    $lines = @("POSTGRES_PASSWORD=$((RandomBase64 24) -replace '[^A-Za-z0-9]', '')", "FEXOR_DATA_KEY=$(RandomBase64 32)")
    if ($mode -eq "3") {
        Write-Host "In the Cloudflare dashboard: Zero Trust > Networks > Tunnels > Create a tunnel (Cloudflared)."
        Write-Host "Add a public hostname (e.g. attendance.example.com) with service http://caddy:80,"
        Write-Host "then copy the tunnel token shown in the install command."
        $hostName = (Read-Host "Public hostname, e.g. attendance.example.com").Trim()
        $token = (Read-Host "Tunnel token").Trim()
        if (-not $hostName -or -not $token) { Fail "The hostname and the tunnel token are both needed." }
        $lines += @("COMPOSE_PROFILES=tunnel", "CF_TUNNEL_TOKEN=$token", "FEXOR_CADDYFILE=Caddyfile.http",
                    "FEXOR_SECURE_COOKIES=true", "FEXOR_PUBLIC_URL=https://$hostName")
    } elseif ($mode -eq "2") {
        $domain = (Read-Host "Domain name, e.g. attendance.example.com").Trim()
        $email = (Read-Host "Your email (for certificate notices)").Trim()
        if (-not $domain -or -not $email) { Fail "Domain and email are both needed for internet mode." }
        $lines += @("FEXOR_CADDYFILE=Caddyfile", "FEXOR_DOMAIN=$domain", "FEXOR_TLS=$email",
                    "FEXOR_SECURE_COOKIES=true", "FEXOR_PUBLIC_URL=https://$domain")
    } else {
        $lines += @("FEXOR_CADDYFILE=Caddyfile.http", "FEXOR_SECURE_COOKIES=false", "FEXOR_PUBLIC_URL=http://$(LanAddress)")
    }
    [System.IO.File]::WriteAllLines($envFile, $lines)
    Write-Host ""
    Write-Host "Settings saved in deploy\.env. Keep a copy of this file somewhere safe:" -ForegroundColor Yellow
    Write-Host "it holds the key that protects the stored face data." -ForegroundColor Yellow
    Write-Host ""
}

function Start-Stack {
    Write-Host "Starting... the first time this takes a few minutes." -ForegroundColor Cyan
    if ((Compose-Quiet pull server) -eq 0) {
        $rc = Compose up -d
    } else {
        Write-Host "No ready-made server image to download; building it on this computer instead."
        $rc = Compose up -d --build
    }
    if ($rc -ne 0) { Fail "Starting failed. See the messages above." }

    Write-Host "Waiting for the server to be ready..."
    for ($i = 0; $i -lt 120; $i++) {
        $rc = Compose-Quiet exec -T server python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health')"
        if ($rc -eq 0) { return }
        Start-Sleep 2
    }
    Fail "The server did not become ready. In the deploy folder run 'docker compose logs server' to see why."
}

function Show-Result {
    $publicUrl = ((Get-Content $envFile | Where-Object { $_ -like "FEXOR_PUBLIC_URL=*" }) -replace "^FEXOR_PUBLIC_URL=", "").Trim()
    $code = (& docker compose --env-file "$envFile" exec -T server fexor-server setup-code 2>$null | Out-String).Trim()
    $codeOk = $LASTEXITCODE -eq 0

    Write-Host ""
    Write-Host "Fexor is running at $publicUrl" -ForegroundColor Green
    Write-Host "Kiosks and other computers use this same address."
    Write-Host ""
    if ($code) {
        Write-Host "  +----------------------------------------------+" -ForegroundColor Yellow
        Write-Host "  |  Setup code for the first admin account:     |" -ForegroundColor Yellow
        Write-Host ("  |  {0,-44}|" -f $code) -ForegroundColor Yellow
        Write-Host "  +----------------------------------------------+" -ForegroundColor Yellow
        try { Set-Clipboard -Value $code; Write-Host "  (copied to the clipboard)" } catch { }
        Write-Host ""
        Write-Host "The browser opens with the code filled in. If it doesn't, go to"
        Write-Host "$publicUrl/?setup=$code"
        if (-not $unattended) { Start-Process "$publicUrl/?setup=$code" }
    } elseif ($codeOk) {
        Write-Host "An admin account already exists. Sign in at $publicUrl"
        if (-not $unattended) { Start-Process $publicUrl }
    } else {
        Write-Host "Could not read the setup code. In the deploy folder run:" -ForegroundColor Yellow
        Write-Host "  docker compose exec server fexor-server setup-code"
    }
    Write-Host ""
    Write-Host "The server keeps running after you close this window, and starts again with Docker Desktop."
    Write-Host "To stop it, run 'Stop Server (Windows)'."
}

Write-Host "Fexor attendance server" -ForegroundColor Cyan
Write-Host ""
try {
    Ensure-Docker
    Ensure-Settings
    Push-Location $deploy
    try { Start-Stack; Show-Result } finally { Pop-Location }
} catch {
    Fail "Something went wrong: $($_.Exception.Message)"
}
Pause-Window
exit 0
