# Backs up the Fexor server: the whole database plus deploy\.env (it holds the key that
# protects face data; a backup without it can't be read). Saved in deploy\backups\<date>\,
# newest 14 kept. Copy that folder somewhere else (USB drive, cloud storage) now and then.
# Runs every day by itself once the server was started with "Start Server (Windows)".
# FEXOR_UNATTENDED=1: no "press Enter" at the end (used by the daily task).
$ErrorActionPreference = "Continue"
$deploy = $PSScriptRoot
$envFile = Join-Path $deploy ".env"
$unattended = [bool]$env:FEXOR_UNATTENDED

function Done($msg, $color, $code) {
    Write-Host $msg -ForegroundColor $color
    if (-not $unattended) { [void](Read-Host "Press Enter to close this window") }
    exit $code
}
function Compose { & docker compose --env-file "$envFile" @args 2>&1 | Out-Null; return $LASTEXITCODE }

if (-not (Test-Path $envFile)) { Done "No Fexor server here (deploy\.env is missing)." Red 1 }
& docker info 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) { Done "Docker is not running. Start Docker Desktop, then try again." Red 1 }

Push-Location $deploy
try {
    [void](Compose up -d db)  # the database must run; the rest of the server may be stopped
    for ($i = 0; $i -lt 60; $i++) {
        if ((Compose exec -T db pg_isready -U fexor) -eq 0) { break }
        Start-Sleep 1
    }
    $dir = Join-Path $deploy ("backups\" + (Get-Date -Format "yyyy-MM-dd_HHmmss"))
    New-Item -ItemType Directory -Force -Path $dir | Out-Null
    # Dumped inside the container and copied out: piping binary output through PowerShell corrupts it.
    if ((Compose exec -T db pg_dump -U fexor -Fc -f /tmp/fexor.dump fexor) -ne 0) { Done "Backup failed: could not dump the database." Red 1 }
    if ((Compose cp db:/tmp/fexor.dump (Join-Path $dir "fexor.dump")) -ne 0) { Done "Backup failed: could not copy the dump." Red 1 }
    [void](Compose exec -T db rm -f /tmp/fexor.dump)
    Copy-Item $envFile (Join-Path $dir "env.txt")
    $dump = Get-Item (Join-Path $dir "fexor.dump")
    if ($dump.Length -eq 0) { Done "Backup failed: the database dump is empty." Red 1 }

    Get-ChildItem (Join-Path $deploy "backups") -Directory | Sort-Object Name -Descending |
        Select-Object -Skip 14 | Remove-Item -Recurse -Force
    Done ("Backup saved in $dir ({0:N1} MB)." -f ($dump.Length / 1MB)) Green 0
} finally {
    Pop-Location
}
