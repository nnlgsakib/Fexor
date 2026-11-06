# Install the Fexor kiosk on a Windows PC or laptop.
# Run in PowerShell from a copy of the repository:  .\deploy\kiosk\install-windows.ps1 [-Camera 0]
# Starts the kiosk and opens Edge full screen every time this user signs in.
param([string]$Camera = "0")
$ErrorActionPreference = "Stop"

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
$env:FEXOR_MODEL_DIR = "$data\models"
& "$app\.venv\Scripts\fexor-kiosk.exe" models
Pop-Location

$launcher = "$data\start-kiosk.cmd"
@"
@echo off
set FEXOR_KIOSK_DATA=$data
set FEXOR_MODEL_DIR=$data\models
start "" /min "$app\.venv\Scripts\fexor-kiosk.exe" run --camera $Camera
timeout /t 8 /nobreak >nul
start "" msedge --kiosk http://127.0.0.1:8300/ --edge-kiosk-type=fullscreen --no-first-run
"@ | Set-Content -Encoding ascii $launcher

$action = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c `"$launcher`""
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
Register-ScheduledTask -TaskName "Fexor Kiosk" -Action $action -Trigger $trigger -Force | Out-Null

Write-Host "Installed. Starting now..."
Start-Process cmd.exe -ArgumentList "/c `"$launcher`""
Write-Host "Enter the server address and the code from Admin > Kiosks > Add kiosk on the kiosk screen."
Write-Host "Speed check: $app\.venv\Scripts\fexor-kiosk.exe bench --camera $Camera"
