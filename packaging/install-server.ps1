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

function Get-Version($readme) {
    if (Test-Path $readme) {
        $m = Select-String -Path $readme -Pattern "^Fexor server (\S+)" | Select-Object -First 1
        if ($m) { return $m.Matches[0].Groups[1].Value }
    }
    return "an older version"
}

# Already installed here? Then this run updates it: settings and data stay.
$update = Test-Path (Join-Path $dir "deploy\.env")
$current = Get-Version (Join-Path $dir "README.txt")
if ($update) {
    Write-Host "Fexor is already installed in $dir ($current). Updating it to the latest version." -ForegroundColor Cyan
} else {
    Write-Host "Installing the Fexor server to $dir" -ForegroundColor Cyan
}
$tmp = Join-Path ([IO.Path]::GetTempPath()) ("fexor-" + [guid]::NewGuid())
New-Item -ItemType Directory -Path $tmp | Out-Null
try {
    Invoke-WebRequest -UseBasicParsing "$source/fexor-server.zip" -OutFile "$tmp\fexor-server.zip"
    Expand-Archive "$tmp\fexor-server.zip" -DestinationPath $tmp
    $latest = Get-Version "$tmp\fexor-server\README.txt"
    if ($update) {
        if ($latest -eq $current) { Write-Host "Already on the latest version ($latest): reinstalling it and restarting." }
        else { Write-Host "Updating from $current to $latest." }
    }
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

# On an update the start script backs up, downloads the new version, then restarts the server.
$env:FEXOR_UPDATE = if ($update) { "1" } else { "" }
& powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $dir "deploy\start-server.ps1")
$env:FEXOR_UPDATE = ""
