# Deletes the Fexor server completely so you can start a new one from scratch:
# containers, the database (all people, faces, attendance), the web certificates store,
# deploy\.env (passwords and encryption key) and the old development server's data.
# Program files and the Docker image (code only, no data) are kept.
# Normally launched by double-clicking "Reset Server (Windows).cmd".
# Scripted use: FEXOR_UNATTENDED=1 and FEXOR_RESET_CONFIRM=DELETE.
$ErrorActionPreference = "Continue"
$deploy = $PSScriptRoot
$root = Split-Path -Parent $deploy
$unattended = [bool]$env:FEXOR_UNATTENDED

function Pause-Window {
    if (-not $unattended) { [void](Read-Host "Press Enter to close this window") }
}

# Docker Compose project of THIS folder, worked out the way Compose does it: COMPOSE_PROJECT_NAME
# if set, else the folder name. Never assume "deploy": a copy in another folder is another server.
$envFile = Join-Path $deploy ".env"
$project = $env:COMPOSE_PROJECT_NAME
if (-not $project -and (Test-Path $envFile)) {
    $line = Get-Content $envFile | Where-Object { $_ -like "COMPOSE_PROJECT_NAME=*" } | Select-Object -First 1
    if ($line) { $project = ($line -replace "^COMPOSE_PROJECT_NAME=", "").Trim() }
}
if (-not $project) { $project = (Split-Path -Leaf $deploy).ToLower() -replace "[^a-z0-9_-]", "" }

$dockerUp = $false
if (Get-Command docker -ErrorAction SilentlyContinue) {
    & docker info 2>&1 | Out-Null
    $dockerUp = $LASTEXITCODE -eq 0
}
$containers = @(); $volumes = @()
if ($dockerUp) {
    $containers = @(& docker ps -aq --filter "label=com.docker.compose.project=$project" 2>$null)
    $volumes = @(& docker volume ls -q --filter "label=com.docker.compose.project=$project" 2>$null)
}
$files = @($envFile, (Join-Path $root ".env"), (Join-Path $root "data")) | Where-Object { Test-Path $_ }

Write-Host "Reset the Fexor server" -ForegroundColor Red
Write-Host ""
Write-Host "Server folder: $deploy  (Docker project '$project')"
Write-Host ""
Write-Host "This permanently deletes:"
Write-Host "  - every person, face photo, QR card, schedule and attendance record"
Write-Host "  - all admin accounts and kiosk pairings (kiosks must be paired again)"
if ($containers.Count) { Write-Host "  - $($containers.Count) server containers" }
foreach ($v in $volumes) { Write-Host "  - Docker volume $v" }
foreach ($f in $files) { Write-Host "  - $f" }
Write-Host ""
if (-not $dockerUp) {
    Write-Host "Docker is not running, so the database cannot be deleted. Start Docker Desktop and try again." -ForegroundColor Yellow
    Pause-Window
    exit 1
}
if (-not $containers.Count -and -not $volumes.Count -and -not $files.Count) {
    Write-Host "Nothing to delete: there is no Fexor server here." -ForegroundColor Green
    Pause-Window
    exit 0
}
Write-Host "There is no undo. A final backup is saved in deploy\backups first (that folder is kept)." -ForegroundColor Yellow
$answer = if ($unattended) { $env:FEXOR_RESET_CONFIRM } else { Read-Host "Type DELETE to continue, or anything else to cancel" }
if ($answer -cne "DELETE") {
    Write-Host "Cancelled. Nothing was deleted." -ForegroundColor Green
    Pause-Window
    exit 0
}

if ($containers.Count -and (Test-Path $envFile)) {
    Write-Host "Saving a final backup first..."
    $env:FEXOR_UNATTENDED = "1"
    & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $deploy "backup-server.ps1")
    $env:FEXOR_UNATTENDED = if ($unattended) { "1" } else { "" }
    if ($LASTEXITCODE -ne 0) {
        $go = if ($unattended) { "yes" } else { Read-Host "The backup failed. Delete anyway? Type yes to continue" }
        if ($go -ne "yes") { Write-Host "Cancelled. Nothing was deleted." -ForegroundColor Green; Pause-Window; exit 0 }
    }
}
if ($containers.Count) {
    & docker rm -f $containers 2>&1 | Out-Null
    Write-Host "Removed the server containers."
}
foreach ($v in $volumes) {
    & docker volume rm $v 2>&1 | Out-Null
    if ($LASTEXITCODE -eq 0) { Write-Host "Deleted volume $v." } else { Write-Host "Could not delete volume $v." -ForegroundColor Yellow }
}
foreach ($f in $files) {
    Remove-Item -Recurse -Force $f -ErrorAction SilentlyContinue
    if (Test-Path $f) { Write-Host "Could not delete $f." -ForegroundColor Yellow } else { Write-Host "Deleted $f." }
}
Write-Host ""
Write-Host "The old server is gone. Run 'Start Server (Windows)' to set up a new one." -ForegroundColor Green
Pause-Window
