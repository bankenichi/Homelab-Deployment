# === 1. CONFIGURATION & DYNAMIC PATHING ===
$scriptDir = $PSScriptRoot
$repoUrl = "https://github.com/bankenichi/Homelab-searxng-plus-proxy" # Update this to your actual repo URL
$isStandalone = $false

# === HELPER: FATAL ERROR ===
function Exit-Fatal {
    param([string]$Message)
    Write-Error $Message
    Pause
    exit 1
}

# === 2. FORCE ADMINISTRATOR ===
if (!([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Warning "Requesting Administrator privileges to configure DNS and install dependencies..."
    Start-Process powershell.exe "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`"" -Verb RunAs
    exit
}

Write-Host "=== SELF-ACTUALIZING HOMELAB BOOTSTRAP ===" -ForegroundColor Cyan

# === 3. DEPENDENCY CHECK: WSL2 ===
Write-Host "Updating WSL2 to latest version..." -ForegroundColor Cyan
wsl --update
if ($LASTEXITCODE -ne 0) {
    Exit-Fatal "WSL2 update failed. Please run 'wsl --update' manually and re-run."
}
wsl --set-default-version 2
if ($LASTEXITCODE -ne 0) {
    Exit-Fatal "Failed to set WSL default version to 2."
}
Write-Host "WSL2 is up to date." -ForegroundColor Green

# === 4. DEPENDENCY CHECK: DOCKER ===
if (!(Get-Command docker -ErrorAction SilentlyContinue)) {
    Write-Host "Docker not found. Downloading..." -ForegroundColor Yellow
    $installerPath = "$env:TEMP\DockerDesktopInstaller.exe"
    try {
        Invoke-WebRequest -Uri "https://desktop.docker.com/win/main/amd64/Docker%20Desktop%20Installer.exe" -OutFile $installerPath
    } catch {
        Exit-Fatal "Failed to download Docker installer: $_"
    }

    Write-Host "Installing Docker Desktop..." -ForegroundColor Yellow
    Start-Process -FilePath $installerPath -ArgumentList "install", "--quiet", "--accept-license" -Wait -NoNewWindow
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path","User")
    Write-Host "Docker installed. A reboot may be required if this is a first-time install." -ForegroundColor Green
} else {
    Write-Host "Docker is ready." -ForegroundColor Green
}

# === 5. DEPENDENCY CHECK: GIT ===
if (!(Get-Command git -ErrorAction SilentlyContinue)) {
    Write-Host "Git not found. Installing via Winget..." -ForegroundColor Yellow
    winget install --id Git.Git -e --source winget --accept-package-agreements --accept-source-agreements
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path","User")
}

if (!(Get-Command git -ErrorAction SilentlyContinue)) {
    Exit-Fatal "Git installation failed. Please install Git manually and re-run."
}
Write-Host "Git is ready." -ForegroundColor Green

# === 6. SELF-ACTUALIZATION (CLONE OR PULL) ===
if (Test-Path "$scriptDir\.git") {
    $targetDir = $scriptDir
    Write-Host "Script is running inside the repository. Pulling latest updates..." -ForegroundColor Cyan
    Set-Location $targetDir
    git pull
    if ($LASTEXITCODE -ne 0) { Exit-Fatal "Failed to pull repository." }
} else {
    $isStandalone = $true
    $targetDir = "$scriptDir\Homelab"
    Write-Host "Standalone script detected. Carving out $targetDir..." -ForegroundColor Cyan
    if (!(Test-Path $targetDir)) {
        git clone $repoUrl $targetDir
        if ($LASTEXITCODE -ne 0) { Exit-Fatal "Failed to clone repository." }
    } else {
        Set-Location $targetDir
        git pull
        if ($LASTEXITCODE -ne 0) { Exit-Fatal "Failed to pull repository." }
    }
}

# === 7. INJECT LOCAL DNS ===
$hostsPath = "$env:windir\System32\drivers\etc\hosts"
$hostsBlock = @"

# --- Added by Homelab Bootstrap ---
127.0.0.1 find
127.0.0.1 coder
127.0.0.1 assistant
# ----------------------------------
"@
$currentHosts = Get-Content -Path $hostsPath -Raw
if ($currentHosts -notmatch "127\.0\.0\.1 find") {
    Write-Host "Injecting DNS routing into Windows hosts file..." -ForegroundColor Yellow
    try {
        Add-Content -Path $hostsPath -Value $hostsBlock
        Write-Host "DNS routing injected successfully." -ForegroundColor Green
    } catch {
        Exit-Fatal "Failed to write to hosts file: $_"
    }
} else {
    Write-Host "DNS routing already exists. Skipping." -ForegroundColor DarkGray
}

# === 8. DOCKER READINESS CHECK ===
Write-Host "Checking Docker daemon..." -ForegroundColor Cyan

# Dynamically locate Docker Desktop via the Registry
$dockerExe = (Get-ItemProperty -Path "HKLM:\SOFTWARE\Docker Inc.\Docker Desktop" -ErrorAction SilentlyContinue).InstallPath
if ($dockerExe) { $dockerExe = Join-Path $dockerExe "Docker Desktop.exe" }

# Fallback to default path if registry query fails
if (!$dockerExe -or !(Test-Path $dockerExe)) {
    $dockerExe = "C:\Program Files\Docker\Docker\Docker Desktop.exe"
}

# Attempt to start Docker Desktop in the background if it's not running
if (!(Get-Process "Docker Desktop" -ErrorAction SilentlyContinue) -and (Test-Path $dockerExe)) {
    Write-Host "Starting Docker Desktop application..." -ForegroundColor Yellow
    Start-Process -FilePath $dockerExe -WindowStyle Hidden
}

$timeout = 90
$elapsed = 0

# Force failure state to prevent stale success codes
$LASTEXITCODE = 1
docker info *>$null

while ($LASTEXITCODE -ne 0 -and $elapsed -lt $timeout) {
    Write-Host "Waiting for Docker to start... ($elapsed/$timeout seconds)" -ForegroundColor Yellow
    Start-Sleep -Seconds 3
    $elapsed += 3

    # Force failure state again before checking
    $LASTEXITCODE = 1
    docker info *>$null
}

if ($LASTEXITCODE -ne 0) {
    Exit-Fatal "Docker daemon did not start in time. If Docker was just installed, a reboot may be required before re-running."
}
Write-Host "Docker daemon is up." -ForegroundColor Green

# === 9. VALIDATE REPO STRUCTURE ===
if (!(Test-Path "$targetDir\proxy")) {
    Exit-Fatal "Expected folder '$targetDir\proxy' not found. Did the clone succeed? Check the repo structure."
}
if (!(Test-Path "$targetDir\searxng")) {
    Exit-Fatal "Expected folder '$targetDir\searxng' not found. Did the clone succeed? Check the repo structure."
}

# === 10. SPIN UP CONTAINERS ===
Write-Host "Booting Caddy Proxy..." -ForegroundColor Cyan
Set-Location "$targetDir\proxy"
$LASTEXITCODE = 1 # Reset
docker compose up -d
if ($LASTEXITCODE -ne 0) { Exit-Fatal "Failed to start Caddy Proxy. Check docker compose output above." }

Write-Host "Booting SearXNG..." -ForegroundColor Cyan
Set-Location "$targetDir\searxng"
$LASTEXITCODE = 1 # Reset
docker compose up -d
if ($LASTEXITCODE -ne 0) { Exit-Fatal "Failed to start SearXNG. Check docker compose output above." }

# === 11. SELF-DESTRUCT ===
if ($isStandalone) {
    Write-Host "Removing standalone bootstrap script..." -ForegroundColor DarkGray
    Remove-Item -Path $PSCommandPath -Force -ErrorAction SilentlyContinue
}

Write-Host "=== DEPLOYMENT COMPLETE ===" -ForegroundColor Green
Pause