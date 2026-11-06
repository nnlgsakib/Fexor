# Starts the Fexor server with Docker. First run asks two questions and writes deploy\.env.
# Normally launched by double-clicking "Start Server (Windows).cmd" in the project folder.
$ErrorActionPreference = "Stop"
$deploy = $PSScriptRoot
$envFile = Join-Path $deploy ".env"

function Done($msg, $color = "Red") {
    Write-Host ""
    Write-Host $msg -ForegroundColor $color
    Read-Host "Press Enter to close this window"
    exit
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

Write-Host "Fexor attendance server" -ForegroundColor Cyan
Write-Host ""

# 1. Docker
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Write-Host "Fexor runs inside Docker Desktop, which is not installed yet."
    Write-Host "Opening the download page. Install it, start it once, then run Start Server again."
    Start-Process "https://www.docker.com/products/docker-desktop/"
    Done "Docker Desktop is needed."
}
docker info *> $null
if ($LASTEXITCODE -ne 0) {
    $dd = "$env:ProgramFiles\Docker\Docker\Docker Desktop.exe"
    if (Test-Path $dd) { Write-Host "Starting Docker Desktop..."; Start-Process $dd }
    for ($i = 0; $i -lt 90; $i++) {
        Start-Sleep 2
        docker info *> $null
        if ($LASTEXITCODE -eq 0) { break }
    }
    if ($LASTEXITCODE -ne 0) { Done "Docker Desktop did not start. Open it yourself, wait until it says it is running, then try again." }
}

# 2. First-time settings
if (-not (Test-Path $envFile)) {
    Write-Host "First-time setup" -ForegroundColor Cyan
    Write-Host "  1) Office network only  - simplest; kiosks and admins on the same network"
    Write-Host "  2) Internet             - needs a domain name pointing to this computer (HTTPS)"
    $mode = Read-Host "Choose 1 or 2 (default 1)"
    $lines = @("POSTGRES_PASSWORD=$((RandomBase64 24) -replace '[^A-Za-z0-9]', '')", "FEXOR_DATA_KEY=$(RandomBase64 32)")
    if ($mode -eq "2") {
        $domain = (Read-Host "Domain name, e.g. attendance.example.com").Trim()
        $email = (Read-Host "Your email (for certificate notices)").Trim()
        if (-not $domain -or -not $email) { Done "Domain and email are both needed for internet mode." }
        $lines += @("FEXOR_CADDYFILE=Caddyfile", "FEXOR_DOMAIN=$domain", "FEXOR_TLS=$email",
                    "FEXOR_SECURE_COOKIES=true", "FEXOR_PUBLIC_URL=https://$domain")
    } else {
        $ip = LanAddress
        $lines += @("FEXOR_CADDYFILE=Caddyfile.http", "FEXOR_SECURE_COOKIES=false", "FEXOR_PUBLIC_URL=http://$ip")
    }
    [System.IO.File]::WriteAllLines($envFile, $lines)
    Write-Host ""
    Write-Host "Settings saved in deploy\.env. Keep a copy of this file somewhere safe:" -ForegroundColor Yellow
    Write-Host "it holds the key that protects the stored face data." -ForegroundColor Yellow
    Write-Host ""
}
$publicUrl = ((Get-Content $envFile | Where-Object { $_ -like "FEXOR_PUBLIC_URL=*" }) -replace "^FEXOR_PUBLIC_URL=", "").Trim()

# 3. Build and start (the first time downloads and builds for a few minutes)
Push-Location $deploy
try {
    Write-Host "Starting... the first time this takes a few minutes." -ForegroundColor Cyan
    docker compose --env-file .env up -d --build
    if ($LASTEXITCODE -ne 0) { Done "Starting failed. See the messages above." }

    Write-Host "Waiting for the server to be ready..."
    $ready = $false
    for ($i = 0; $i -lt 120; $i++) {
        docker compose --env-file .env exec -T server python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health')" *> $null
        if ($LASTEXITCODE -eq 0) { $ready = $true; break }
        Start-Sleep 2
    }
    if (-not $ready) { Done "The server did not become ready. Run 'docker compose logs server' in the deploy folder to see why." }
    $code = (docker compose --env-file .env exec -T server fexor-server setup-code | Out-String).Trim()
} finally {
    Pop-Location
}

# 4. Open the admin panel
Write-Host ""
Write-Host "Fexor is running at $publicUrl" -ForegroundColor Green
if ($code) {
    Write-Host "Setup code (already filled in for you): $code" -ForegroundColor Green
    Start-Process "$publicUrl/?setup=$code"
} else {
    Start-Process $publicUrl
}
Write-Host "Kiosks and other computers use this same address."
Write-Host "The server keeps running after you close this window, and starts again with Docker Desktop."
Done "To stop it, run 'Stop Server (Windows)'." "Gray"
