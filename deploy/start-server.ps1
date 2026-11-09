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
    # The address of the network Windows prefers for outgoing traffic (lowest default-route metric),
    # so a PC with both Ethernet and Wi-Fi gets the one the rest of the office can reach.
    $route = Get-NetRoute -DestinationPrefix "0.0.0.0/0" -ErrorAction SilentlyContinue |
        Sort-Object { $_.RouteMetric + (Get-NetIPInterface -InterfaceIndex $_.ifIndex -AddressFamily IPv4).InterfaceMetric } |
        Select-Object -First 1
    $ip = $null
    if ($route) {
        $ip = Get-NetIPAddress -InterfaceIndex $route.ifIndex -AddressFamily IPv4 -ErrorAction SilentlyContinue |
            Select-Object -First 1 -ExpandProperty IPAddress
    }
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
    # Its own Docker project name: otherwise every install folder called "deploy" is the same project
    # and a new install would reuse (and fail to open) an older install's database.
    $suffix = -join ((1..6) | ForEach-Object { "{0:x}" -f (Get-Random -Maximum 16) })
    $lines = @("COMPOSE_PROJECT_NAME=fexor-$suffix",
               "POSTGRES_PASSWORD=$((RandomBase64 24) -replace '[^A-Za-z0-9]', '')", "FEXOR_DATA_KEY=$(RandomBase64 32)")
    if ($mode -eq "3") {
        Write-Host "In the Cloudflare dashboard: Zero Trust > Networks > Tunnels > Create a tunnel (Cloudflared)."
        Write-Host "Add a public hostname (e.g. attendance.example.com) with service http://caddy:80,"
        Write-Host "then copy the tunnel token shown in the install command."
        $hostName = (Read-Host "Public hostname, e.g. attendance.example.com").Trim()
        $token = (Read-Host "Tunnel token").Trim()
        if (-not $hostName -or -not $token) { Fail "The hostname and the tunnel token are both needed." }
        $lines += @("COMPOSE_PROFILES=tunnel", "CF_TUNNEL_TOKEN=$token", "FEXOR_CADDYFILE=Caddyfile.tunnel",
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

function Update-ProxyConfig {
    # Older tunnel installs used Caddyfile.http, which now trusts no proxy: switch them over.
    $lines = Get-Content $envFile
    if (-not ($lines -match "^COMPOSE_PROFILES=.*tunnel") -or -not ($lines -contains "FEXOR_CADDYFILE=Caddyfile.http")) { return }
    $lines = $lines | ForEach-Object { if ($_ -eq "FEXOR_CADDYFILE=Caddyfile.http") { "FEXOR_CADDYFILE=Caddyfile.tunnel" } else { $_ } }
    [System.IO.File]::WriteAllLines($envFile, $lines)
}

function Update-OfficeAddress {
    # Office network mode stores this computer's IP address. Routers can hand out a new one; then
    # the admin panel and the pairing QR would point to an address that is no longer this computer.
    $lines = Get-Content $envFile
    $urlLine = $lines | Where-Object { $_ -like "FEXOR_PUBLIC_URL=http://*" } | Select-Object -First 1
    if (-not $urlLine -or ($lines -match "^COMPOSE_PROFILES=.*tunnel")) { return }
    $oldHost = ([uri]($urlLine -replace "^FEXOR_PUBLIC_URL=", "")).Host
    if ($oldHost -notmatch "^\d{1,3}(\.\d{1,3}){3}$") { return }  # a name, not an IP: leave it
    $mine = @(Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue | ForEach-Object { $_.IPAddress })
    if ($mine -contains $oldHost) { return }
    $newHost = LanAddress
    if ($newHost -eq "localhost") { return }
    $lines = $lines | ForEach-Object { if ($_ -eq $urlLine) { "FEXOR_PUBLIC_URL=http://$newHost" } else { $_ } }
    [System.IO.File]::WriteAllLines($envFile, $lines)
    Write-Host ""
    Write-Host "This computer's network address changed from $oldHost to $newHost." -ForegroundColor Yellow
    Write-Host "Kiosks paired with the old address: open their settings (Settings QR) and enter" -ForegroundColor Yellow
    Write-Host "http://$newHost as the server address. To stop this happening, reserve this" -ForegroundColor Yellow
    Write-Host "computer's address in your router (often called DHCP reservation)." -ForegroundColor Yellow
    Write-Host ""
}

function Image-Present {
    # This install's server image (pinned to a version in the downloaded server files).
    $img = (& docker compose --env-file "$envFile" config --images 2>$null | Where-Object { $_ -like "*fexor-server*" } | Select-Object -First 1)
    if (-not $img) { return $false }
    & docker image inspect "$img" 2>&1 | Out-Null
    return $LASTEXITCODE -eq 0
}

function Register-Backup {
    # A daily backup at 02:00 (Docker Desktop must be running, which it is while you are signed in).
    $id = [BitConverter]::ToString([Security.Cryptography.SHA1]::Create().ComputeHash([Text.Encoding]::UTF8.GetBytes($deploy))).Replace("-", "").Substring(0, 8)
    $script = Join-Path $deploy "backup-server.ps1"
    try {
        $action = New-ScheduledTaskAction -Execute "powershell.exe" `
            -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$script`" -Unattended"
        $trigger = New-ScheduledTaskTrigger -Daily -At 2am
        # Runs later if the computer was off at 02:00.
        $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable
        Register-ScheduledTask -TaskName "Fexor daily backup $id" -Action $action -Trigger $trigger `
            -Settings $settings -Force -ErrorAction Stop | Out-Null
        Write-Host "Daily backup at 02:00 is set up (deploy\backups)."
    } catch {
        Write-Host "Could not set up the daily backup ($($_.Exception.Message)). Use 'Backup Server' now and then." -ForegroundColor Yellow
    }
}

function Update-Stack {
    # Run by the installer on an existing server (FEXOR_UPDATE=1): back up, download the new version
    # while the old one keeps running, then restart on it. A failure stops before anything changes.
    Write-Host "Backing up before the update..." -ForegroundColor Cyan
    # Out-Host: anything printed inside a function becomes part of its return value in PowerShell.
    & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $deploy "backup-server.ps1") -Unattended | Out-Host
    if ($LASTEXITCODE -ne 0) { Fail "The backup failed, so nothing was updated. The current version keeps running." }
    Write-Host "Downloading the new version (the current one keeps running meanwhile)..." -ForegroundColor Cyan
    if ((Compose pull server worker) -ne 0) {
        Fail "Could not download the new version. Check the internet connection and run the installer again. The current version keeps running."
    }
    Write-Host "Stopping the old version and starting the new one (a few seconds)..." -ForegroundColor Cyan
    $rc = Compose up -d --remove-orphans
    if ($rc -ne 0) { return $rc }
    # Compose keeps a running container when only the image behind its tag changed: replace them.
    return (Compose up -d --no-deps --force-recreate server worker)
}

function Start-Stack {
    if ($env:FEXOR_UPDATE -eq "1") {
        $rc = Update-Stack
        if ($rc -ne 0) { Fail "Starting the new version failed. See the messages above; 'Restore Server' brings back the backup." }
        Wait-Ready
        return
    }
    Write-Host "Starting... the first time this takes a few minutes." -ForegroundColor Cyan
    # Already downloaded: start without going online, so a restart works without internet.
    if ((Image-Present) -or (Compose-Quiet pull server) -eq 0) {
        $rc = Compose up -d
    } elseif (-not (Test-Path (Join-Path $deploy "..\Dockerfile"))) {
        Fail "Could not download the Fexor server. Check this computer's internet connection and try again."
    } else {
        Write-Host "No ready-made server image to download; building it on this computer instead."
        $rc = Compose up -d --build
    }
    if ($rc -ne 0) { Fail "Starting failed. See the messages above." }
    Wait-Ready
}

function Wait-Ready {
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
    Update-OfficeAddress
    Update-ProxyConfig
    Push-Location $deploy
    try { Start-Stack; Register-Backup; Show-Result } finally { Pop-Location }
} catch {
    Fail "Something went wrong: $($_.Exception.Message)"
}
Pause-Window
exit 0
