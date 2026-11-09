# Restores a backup made by backup-server.ps1: replaces the whole database with the backup's.
#   restore-server.ps1                    (newest backup in deploy\backups)
#   restore-server.ps1 C:\path\to\backup  (a backup folder, e.g. from another computer)
# Also takes the face-data key from the backup, so photos and two-step sign-in keep working.
# Scripted use: FEXOR_UNATTENDED=1 and FEXOR_RESTORE_CONFIRM=RESTORE.
param([string]$From = "")
$ErrorActionPreference = "Continue"
$deploy = $PSScriptRoot
$envFile = Join-Path $deploy ".env"
$unattended = [bool]$env:FEXOR_UNATTENDED

function Done($msg, $color, $code) {
    Write-Host $msg -ForegroundColor $color
    if (-not $unattended) { [void](Read-Host "Press Enter to close this window") }
    exit $code
}
function Compose { & docker compose --env-file "$envFile" @args 2>&1 | ForEach-Object { "$_" } | Out-Host; return $LASTEXITCODE }

if (-not (Test-Path $envFile)) { Done "Start the server once first ('Start Server (Windows)'), then restore into it." Red 1 }
if (-not $From) {
    $latest = Get-ChildItem (Join-Path $deploy "backups") -Directory -ErrorAction SilentlyContinue | Sort-Object Name -Descending | Select-Object -First 1
    if ($latest) { $From = $latest.FullName }
}
if (-not $From -or -not (Test-Path (Join-Path $From "fexor.dump")) -or -not (Test-Path (Join-Path $From "env.txt"))) {
    Done "No backup found (need fexor.dump and env.txt in a folder)." Red 1
}

Write-Host "Restore the Fexor server from: $From" -ForegroundColor Yellow
Write-Host "Everything in the current database is replaced by that backup. There is no undo,"
Write-Host "but a backup of the current state is made first."
$answer = if ($unattended) { $env:FEXOR_RESTORE_CONFIRM } else { Read-Host "Type RESTORE to continue, or anything else to cancel" }
if ($answer -cne "RESTORE") { Done "Cancelled. Nothing was changed." Green 0 }

$env:FEXOR_UNATTENDED = "1"
& powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $deploy "backup-server.ps1")
$env:FEXOR_UNATTENDED = if ($unattended) { "1" } else { "" }
if ($LASTEXITCODE -ne 0) { Done "Could not back up the current state, so nothing was restored." Red 1 }

Push-Location $deploy
try {
    [void](Compose stop server worker)
    if ((Compose cp (Join-Path $From "fexor.dump") db:/tmp/restore.dump) -ne 0) { Done "Could not copy the backup into the database container." Red 1 }
    # --clean drops what is there first. Ownership/grants come from this server's own database user.
    $rc = Compose exec -T db pg_restore -U fexor -d fexor --clean --if-exists --no-owner /tmp/restore.dump
    [void](Compose exec -T db rm -f /tmp/restore.dump)
    if ($rc -ne 0) { Write-Host "pg_restore reported problems (see above). Check the admin panel after the start." -ForegroundColor Yellow }

    $key = (Get-Content (Join-Path $From "env.txt") | Where-Object { $_ -like "FEXOR_DATA_KEY=*" } | Select-Object -First 1)
    $lines = @(Get-Content $envFile)
    if ($key -and -not ($lines -contains $key)) {
        $lines = @($lines | Where-Object { $_ -notlike "FEXOR_DATA_KEY=*" }) + $key
        [System.IO.File]::WriteAllLines($envFile, $lines)
        Write-Host "Took the face-data key from the backup."
    }
    [void](Compose up -d)
    Done "Restored. The server is starting again with the backup's data." Green 0
} finally {
    Pop-Location
}
