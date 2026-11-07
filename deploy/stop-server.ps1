# Stops the Fexor server completely: the Docker containers (database, server, worker, web front)
# and any development server started with `uv run fexor-server`. Your data is kept.
# Normally launched by double-clicking "Stop Server (Windows).cmd". FEXOR_UNATTENDED=1: no pause.
$ErrorActionPreference = "Continue"
$deploy = $PSScriptRoot
$unattended = [bool]$env:FEXOR_UNATTENDED

function Pause-Window {
    if (-not $unattended) { [void](Read-Host "Press Enter to close this window") }
}

Write-Host "Stopping the Fexor server..." -ForegroundColor Cyan

# 1. Docker containers. Found by their Compose project, so this works even without deploy\.env.
$project = "deploy"
$envFile = Join-Path $deploy ".env"
if (Test-Path $envFile) {
    $line = Get-Content $envFile | Where-Object { $_ -like "COMPOSE_PROJECT_NAME=*" } | Select-Object -First 1
    if ($line) { $project = ($line -replace "^COMPOSE_PROJECT_NAME=", "").Trim() }
}
if (Get-Command docker -ErrorAction SilentlyContinue) {
    & docker info 2>&1 | Out-Null
    if ($LASTEXITCODE -eq 0) {
        $ids = @(& docker ps -aq --filter "label=com.docker.compose.project=$project" 2>$null)
        if ($ids.Count -gt 0) {
            & docker stop $ids 2>&1 | Out-Null
            & docker rm $ids 2>&1 | Out-Null  # containers only; the database volume stays
            Write-Host "  Stopped $($ids.Count) server containers."
        } else {
            Write-Host "  No server containers were running."
        }
    } else {
        Write-Host "  Docker is not running, so its containers are already stopped."
    }
}

# 2. Development server processes (uv run fexor-server serve / worker).
$dev = @(Get-CimInstance Win32_Process | Where-Object {
    $_.CommandLine -match 'fexor-server(\.exe)?"?\s+(serve|worker)' -and
    $_.Name -notin @("powershell.exe", "pwsh.exe", "cmd.exe", "bash.exe")
})
foreach ($p in $dev) { Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue }
if ($dev.Count -gt 0) { Write-Host "  Stopped $($dev.Count) development server processes." }

# 3. Check nothing is still serving on the server's ports.
Start-Sleep 1
$busy = @(Get-NetTCPConnection -State Listen -LocalPort 80, 443, 8000 -ErrorAction SilentlyContinue)
$ours = @($busy | Where-Object {
    $name = (Get-Process -Id $_.OwningProcess -ErrorAction SilentlyContinue).ProcessName
    $name -in @("com.docker.backend", "wslrelay", "python", "uvicorn", "fexor-server")
})
Write-Host ""
if ($ours.Count -eq 0) {
    Write-Host "The Fexor server is stopped. Your data is kept." -ForegroundColor Green
    Write-Host "It stays stopped, also after a restart, until you run 'Start Server (Windows)'."
} else {
    Write-Host "Something is still using port $(($ours.LocalPort | Sort-Object -Unique) -join ', ')." -ForegroundColor Yellow
    Write-Host "Close Docker Desktop, or restart the computer, to free it."
}
Pause-Window
