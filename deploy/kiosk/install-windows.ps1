# Install the Fexor kiosk on a Windows PC or laptop.
# Easiest: double-click "Install Kiosk (Windows).cmd" in the project folder.
# The kiosk starts, and opens Edge full screen, every time this user signs in. The camera and the
# server are chosen on the kiosk screen; -Camera is only for unattended installs.
param([string]$Camera = "")
$ErrorActionPreference = "Stop"
Write-Host "Installing the Fexor kiosk. The first time this downloads about 300 MB." -ForegroundColor Cyan

$src = (Resolve-Path "$PSScriptRoot\..\..").Path
$app = "$env:LOCALAPPDATA\Fexor\app"
$data = "$env:LOCALAPPDATA\Fexor\kiosk"
New-Item -ItemType Directory -Force $app, $data | Out-Null
Copy-Item -Recurse -Force "$src\pyproject.toml", "$src\uv.lock", "$src\core", "$src\kiosk", "$src\server" $app

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression
    $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
}
Push-Location $app
uv sync --frozen --no-dev --package fexor-kiosk
if ($LASTEXITCODE -ne 0) { Pop-Location; throw "Installing the kiosk app failed (see messages above)." }
$env:FEXOR_MODEL_DIR = "$data\models"
& "$app\.venv\Scripts\fexor-kiosk.exe" models
Pop-Location

$cameraArg = ""
if ($Camera) { $cameraArg = "--camera $Camera" }
$launcher = "$data\start-kiosk.cmd"
@"
@echo off
set FEXOR_KIOSK_DATA=$data
set FEXOR_MODEL_DIR=$data\models
start "" /min "$app\.venv\Scripts\fexor-kiosk.exe" run $cameraArg
timeout /t 8 /nobreak >nul
start "" msedge --kiosk http://127.0.0.1:8300/ --edge-kiosk-type=fullscreen --no-first-run
"@ | Set-Content -Encoding ascii $launcher

$action = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c `"$launcher`""
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
Register-ScheduledTask -TaskName "Fexor Kiosk" -Action $action -Trigger $trigger -Force | Out-Null

Write-Host ""
Write-Host "Installed. The kiosk screen opens now, and again every time you sign in." -ForegroundColor Green
Start-Process cmd.exe -ArgumentList "/c `"$launcher`""
Write-Host "On the kiosk screen: choose the camera, then show it the pairing QR from the"
Write-Host "admin panel (Kiosks > Add kiosk). To leave full screen press Alt+F4."
