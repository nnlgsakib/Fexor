# Fexor server installer for Windows. Run in PowerShell:
#
#   irm https://github.com/nnlgsakib/Fexor/releases/latest/download/install-server.ps1 | iex
#
# Downloads the server files (no source code) to %USERPROFILE%\Fexor Server, installs Docker
# Desktop if needed, adds desktop shortcuts and starts the server. Run it again to update:
# deploy\.env (settings and keys) is kept.
#
# FEXOR_DIR       install folder (default: %USERPROFILE%\Fexor Server)
# FEXOR_DOWNLOAD  where fexor-server.zip comes from (default: latest GitHub release)
# FEXOR_NO_START  1 = only download/update the files, don't start
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"  # Windows PowerShell downloads crawl with the progress bar on
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$dir = if ($env:FEXOR_DIR) { $env:FEXOR_DIR } else { Join-Path $env:USERPROFILE "Fexor Server" }
$source = if ($env:FEXOR_DOWNLOAD) { $env:FEXOR_DOWNLOAD } else {
    "https://github.com/nnlgsakib/Fexor/releases/latest/download" }

Write-Host "Installing the Fexor server to $dir" -ForegroundColor Cyan
$update = Test-Path (Join-Path $dir "deploy\.env")
if ($update -and $env:FEXOR_NO_START -ne "1" -and (Get-Command docker -ErrorAction SilentlyContinue) -and (Test-Path (Join-Path $dir "deploy\backup-server.ps1"))) {
    # Updating an existing server: keep a backup of the database from before the update.
    Write-Host "Backing up the current server before updating..."
    $env:FEXOR_UNATTENDED = "1"
    & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $dir "deploy\backup-server.ps1")
    $env:FEXOR_UNATTENDED = ""
}
$tmp = Join-Path ([IO.Path]::GetTempPath()) ("fexor-" + [guid]::NewGuid())
New-Item -ItemType Directory -Path $tmp | Out-Null
try {
    Invoke-WebRequest -UseBasicParsing "$source/fexor-server.zip" -OutFile "$tmp\fexor-server.zip"
    Expand-Archive "$tmp\fexor-server.zip" -DestinationPath $tmp
    New-Item -ItemType Directory -Force -Path $dir | Out-Null
    # Overwrites the program files; deploy\.env is not in the zip, so settings and keys stay.
    Copy-Item -Recurse -Force "$tmp\fexor-server\*" $dir
} finally {
    Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
}

$shell = New-Object -ComObject WScript.Shell
$desktop = [Environment]::GetFolderPath("Desktop")
foreach ($name in "Start Server", "Stop Server") {
    $link = $shell.CreateShortcut((Join-Path $desktop "Fexor - $name.lnk"))
    $link.TargetPath = Join-Path $dir "$name (Windows).cmd"
    $link.WorkingDirectory = $dir
    $link.Save()
}
Write-Host "Done. Desktop shortcuts: 'Fexor - Start Server' and 'Fexor - Stop Server'." -ForegroundColor Green

if ($env:FEXOR_NO_START -eq "1") { return }

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        Write-Host "Installing Docker Desktop (Windows asks for permission)..." -ForegroundColor Cyan
        winget install -e --id Docker.DockerDesktop --accept-package-agreements --accept-source-agreements
        Write-Host ""
        Write-Host "Docker Desktop is installed. Restart the computer if it asks, open Docker Desktop once," -ForegroundColor Yellow
        Write-Host "accept its terms, then double-click 'Fexor - Start Server' on the desktop." -ForegroundColor Yellow
    } else {
        Write-Host "Install Docker Desktop, open it once, then double-click 'Fexor - Start Server'." -ForegroundColor Yellow
        Start-Process "https://www.docker.com/products/docker-desktop/"
    }
    return
}

& powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $dir "deploy\start-server.ps1")
