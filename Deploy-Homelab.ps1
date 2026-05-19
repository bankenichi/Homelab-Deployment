# === 1. CONFIGURATION & DYNAMIC PATHING ===
$scriptDir = $PSScriptRoot
$repoUrl = "https://github.com/bankenichi/Homelab-searxng-plus-proxy" # Update this to your actual repo URL
$isStandalone = $false

# Post-reboot resume state
$resumeUserFile = "C:\ProgramData\HomelabBootstrap\resume-user.txt"

# Llama.cpp & Model Config
$llamaInstallDir = "C:\Program Files\llamacpp"
$llamaRepoUrl = "https://github.com/bankenichi/llamacpp-turboquant-mtp-executables-for-cuda-12.8"
$llamaModelRepo = "mudler/Qwen3.6-35B-A3B-Claude-4.7-Opus-Reasoning-Distilled-APEX-MTP-GGUF"
$llamaModelFile = "Qwen3.6-35B-A3B-Claude-4.7-Opus-Reasoning-Distilled-APEX-MTP-I-Compact.gguf"
$llamaVisionRepo = "mudler/Qwen3.6-35B-A3B-Claude-4.7-Opus-Reasoning-Distilled-APEX-GGUF"
$llamaVisionFile = "mmproj.gguf"

# === HELPER: FATAL ERROR ===
function Exit-Fatal {
    param([string]$Message)
    Write-Error $Message
    Pause
    exit 1
}

# === 1b. CLEAN UP POST-REBOOT SCHEDULED TASK (if resuming) ===
$resumeTaskName = "ResumeHomelabBootstrap"
$resumingAsSystem = $false
schtasks /query /tn $resumeTaskName 2>$null | Out-Null
if ($LASTEXITCODE -eq 0) {
    schtasks /delete /tn $resumeTaskName /f | Out-Null
    $resumingAsSystem = $true
    Write-Host "Resumed from post-reboot scheduled task. Task cleaned up." -ForegroundColor DarkGray
}

# Resolve the target user profile -- either the saved resume user (SYSTEM context) or current user
if ($resumingAsSystem -and (Test-Path $resumeUserFile)) {
    $resumeUsername = (Get-Content $resumeUserFile -Raw).Trim()
    $script:resolvedHomeDir = "C:\Users\$resumeUsername"
    Write-Host "Resolved user home directory: $($script:resolvedHomeDir)" -ForegroundColor DarkGray
    Remove-Item -Path $resumeUserFile -Force -ErrorAction SilentlyContinue
    $resumeUserDir = Split-Path $resumeUserFile
    if ((Get-ChildItem $resumeUserDir -ErrorAction SilentlyContinue | Measure-Object).Count -eq 0) {
        Remove-Item -Path $resumeUserDir -Force -ErrorAction SilentlyContinue
    }
} else {
    $script:resolvedHomeDir = $env:USERPROFILE
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
    
    # --- GRACEFUL REBOOT SEQUENCE ---
    # Save the current username so the SYSTEM-context resume can resolve the correct user profile
    $resumeUserDir = Split-Path $resumeUserFile
    if (!(Test-Path $resumeUserDir)) { New-Item -ItemType Directory -Path $resumeUserDir -Force | Out-Null }
    $env:USERNAME | Set-Content -Path $resumeUserFile -Force
    Write-Host "Saved resume username ($env:USERNAME) to $resumeUserFile." -ForegroundColor DarkGray

    # Register a one-shot scheduled task running as SYSTEM so it resumes fully elevated after reboot with no UAC prompt
    $resumeTaskName = "ResumeHomelabBootstrap"
    $resumeCommand = "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Normal -File `"$PSCommandPath`""
    schtasks /create /tn $resumeTaskName /sc ONLOGON /rl HIGHEST /ru "SYSTEM" /tr $resumeCommand /f | Out-Null
    if ($LASTEXITCODE -ne 0) { Exit-Fatal "Failed to register post-reboot resume task via schtasks." }
    
    Write-Host ""
    Write-Host "=======================================================" -ForegroundColor Red
    Write-Host "                SYSTEM REBOOT REQUIRED                 " -ForegroundColor Red
    Write-Host "=======================================================" -ForegroundColor Red
    Write-Host "Docker requires your Windows user account to be added  " -ForegroundColor Yellow
    Write-Host "to the 'docker-users' group. This permission change    " -ForegroundColor Yellow
    Write-Host "only takes effect after a full logout/reboot.          " -ForegroundColor Yellow
    Write-Host ""
    Write-Host "The Homelab deployment will AUTOMATICALLY RESUME       " -ForegroundColor Cyan
    Write-Host "exactly where it left off after you log back in.       " -ForegroundColor Cyan
    Write-Host ""
    Write-Host "PLEASE SAVE ALL YOUR WORK NOW." -ForegroundColor Red
    Write-Host "=======================================================" -ForegroundColor Red
    Write-Host ""

    for ($i = 30; $i -gt 0; $i--) {
        Write-Progress -Activity "System Reboot Imminent" -Status "Please save your work. Restarting in $i seconds..." -PercentComplete (($i/30)*100)
        Start-Sleep -Seconds 1
    }
    Write-Progress -Activity "System Reboot Imminent" -Completed
    Write-Host "Rebooting now..." -ForegroundColor Red
    Restart-Computer -Force
    exit 
} else {
    Write-Host "Docker is ready." -ForegroundColor Green
}

# === 5. DEPENDENCY CHECK: GIT ===
if (!(Get-Command git -ErrorAction SilentlyContinue)) {
    Write-Host "Git not found. Installing via Winget..." -ForegroundColor Yellow
    $LASTEXITCODE = 0
    winget install --id Git.Git -e --source winget --accept-package-agreements --accept-source-agreements
    if ($LASTEXITCODE -ne 0) { Exit-Fatal "Git installation via winget failed." }
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path","User")
}

if (!(Get-Command git -ErrorAction SilentlyContinue)) {
    Exit-Fatal "Git installation failed. Please install Git manually and re-run."
}
Write-Host "Git is ready." -ForegroundColor Green

# === 6. DEPENDENCY CHECK: OPENCODE ===
if (!(Get-Command opencode -ErrorAction SilentlyContinue)) {
    Write-Host "OpenCode not found. Installing via npm..." -ForegroundColor Yellow
    if (!(Get-Command npm -ErrorAction SilentlyContinue)) {
        Write-Host "npm not found. Installing Node.js via Winget..." -ForegroundColor Yellow
        $LASTEXITCODE = 0
        winget install --id OpenJS.NodeJS -e --source winget --accept-package-agreements --accept-source-agreements
        if ($LASTEXITCODE -ne 0) { Exit-Fatal "Node.js installation via winget failed." }
        $env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path","User")
    }
    if (!(Get-Command npm -ErrorAction SilentlyContinue)) {
        Exit-Fatal "npm is required to install opencode-ai. Please install Node.js manually and re-run."
    }
    
    $LASTEXITCODE = 0
    npm install -g opencode-ai@latest
    if ($LASTEXITCODE -ne 0) { Exit-Fatal "Failed to install opencode-ai via npm." }
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path","User")
    
    if (!(Get-Command opencode -ErrorAction SilentlyContinue)) {
        Write-Warning "OpenCode command not found after install, but continuing (you may need to restart your terminal)."
    } else {
        Write-Host "OpenCode installed successfully." -ForegroundColor Green
    }
} else {
    Write-Host "OpenCode is ready." -ForegroundColor Green
}

# === 7. DEPENDENCY CHECK: PYTHON & HUGGINGFACE CLI ===
$pythonCmd = if (Get-Command python -ErrorAction SilentlyContinue) { "python" } elseif (Get-Command python3 -ErrorAction SilentlyContinue) { "python3" } else { $null }

$needsPythonInstall = $false
if ($pythonCmd) {
    $pyVersionOutput = & $pythonCmd --version 2>&1
    if ($pyVersionOutput -match "Python (\d+)\.(\d+)") {
        $pyMajor = [int]$Matches[1]
        $pyMinor = [int]$Matches[2]
        if ($pyMajor -lt 3 -or ($pyMajor -eq 3 -and $pyMinor -lt 14)) {
            Write-Host "Python $pyMajor.$pyMinor detected — upgrading to Python 3.14..." -ForegroundColor Yellow
            $needsPythonInstall = $true
        } else {
            Write-Host "Python $pyVersionOutput confirmed." -ForegroundColor DarkGray
        }
    } else {
        Write-Warning "Could not parse Python version from: $pyVersionOutput. Will attempt to install Python 3.14."
        $needsPythonInstall = $true
    }
} else {
    Write-Host "Python not found. Installing Python 3.14..." -ForegroundColor Yellow
    $needsPythonInstall = $true
}

if ($needsPythonInstall) {
    $LASTEXITCODE = 0
    winget install --id Python.Python.3.14 -e --source winget --accept-package-agreements --accept-source-agreements
    if ($LASTEXITCODE -ne 0) { Exit-Fatal "Python 3.14 installation via winget failed." }
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path","User")
    # Re-resolve python command after install/upgrade
    $pythonCmd = if (Get-Command python -ErrorAction SilentlyContinue) { "python" } elseif (Get-Command python3 -ErrorAction SilentlyContinue) { "python3" } else { $null }
    if (!$pythonCmd) { Exit-Fatal "Python installation could not be verified after install. Please check manually and re-run." }
}

# Ensure pip is installed
if (!(Get-Command pip -ErrorAction SilentlyContinue)) {
    Write-Host "pip not found. Bootstrapping pip via Python..." -ForegroundColor Yellow
    $LASTEXITCODE = 0
    python -m ensurepip --upgrade
    if ($LASTEXITCODE -ne 0) { Exit-Fatal "Failed to bootstrap pip using Python ensurepip." }
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path","User")
}

if (!(Get-Command huggingface-cli -ErrorAction SilentlyContinue)) {
    Write-Host "Hugging Face CLI not found. Installing via pip..." -ForegroundColor Yellow
    $LASTEXITCODE = 0
    pip install huggingface_hub[cli]
    if ($LASTEXITCODE -ne 0) { Exit-Fatal "Failed to install huggingface-cli via pip." }
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path","User")
}

Write-Host "Python & Hugging Face CLI are ready." -ForegroundColor Green

# === 8. SELF-ACTUALIZATION (CLONE OR PULL) ===
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

Write-Host "Repository is ready at $targetDir." -ForegroundColor Green

# === 9. CONFIGURE OPENCODE SYMLINKS ===
Write-Host "Configuring OpenCode symlinks..." -ForegroundColor Cyan

$opencodeAndSkillsDir = "$targetDir\opencode and skills"
if (!(Test-Path $opencodeAndSkillsDir)) {
    Write-Host "Creating 'opencode and skills' directory structure in repo..." -ForegroundColor DarkGray
    New-Item -ItemType Directory -Path $opencodeAndSkillsDir -Force | Out-Null
}

Write-Host "Backing up existing OpenCode configurations and creating symlinks to the repository..." -ForegroundColor Cyan

# === HELPER: SAFE PROCESS KILLER ===
$handleExe = "$env:TEMP\handle.exe"
function Stop-LockingProcesses {
    param([string]$Path)

    if (!(Test-Path $script:handleExe)) {
        Write-Host "Downloading Sysinternals handle.exe..." -ForegroundColor DarkGray
        try {
            Invoke-WebRequest -Uri "https://live.sysinternals.com/handle.exe" -OutFile $script:handleExe -ErrorAction Stop
        } catch {
            Write-Warning "Could not download handle.exe: $_. Skipping lock detection."
            return
        }
    }

    $output = & $script:handleExe -accepteula -nobanner $Path 2>&1
    $pids = $output |
        Where-Object { $_ -match 'pid:\s*(\d+)' } |
        ForEach-Object { [int]($Matches[1]) } |
        Sort-Object -Unique

    if (!$pids) {
        Write-Host "No locking processes detected for $Path." -ForegroundColor DarkGray
        return
    }

    # Protected processes list to prevent catastrophic desktop/IDE crashes
    $protectedProcesses = @(
        "explorer", "Code", "cursor", "WindowsTerminal", "pwsh", "powershell", "cmd", 
        "idea64", "pycharm64", "studio64", "devenv", 
        "WINWORD", "EXCEL", "POWERPNT"
    )

    foreach ($procId in $pids) {
        $proc = Get-Process -Id $procId -ErrorAction SilentlyContinue
        if ($proc) {
            if ($protectedProcesses -contains $proc.Name) {
                Write-Warning "Skipping '$($proc.Name)' (PID $procId) - Protected system/editor process holding a lock!"
            } else {
                Write-Host "Stopping '$($proc.Name)' (PID $procId) which is locking $Path..." -ForegroundColor Yellow
                Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
            }
        }
    }
}

Write-Host "Ensuring symlink paths are clear and backed up if necessary..." -ForegroundColor DarkGray

function Initialize-Symlink {
    param([string]$LinkPath, [string]$TargetDir)

    $backupPath = "$LinkPath.backup"

    if (Test-Path $LinkPath) {
        $item = Get-Item $LinkPath -Force
        if ($item.LinkType -eq 'SymbolicLink') {
            Write-Host "Symlink already exists for $LinkPath. Skipping." -ForegroundColor DarkGray
            return
        } else {
            Write-Host "Existing directory found at $LinkPath. Backing up to $backupPath..." -ForegroundColor Yellow
            if (Test-Path $backupPath) { Remove-Item -Path $backupPath -Recurse -Force }
            Copy-Item -Path $LinkPath -Destination $backupPath -Recurse -Force

            if (!(Test-Path $TargetDir)) {
                Write-Host "Target $TargetDir not found. Seeding from backup..." -ForegroundColor Cyan
                Copy-Item -Path $backupPath -Destination $TargetDir -Recurse -Force
            } else {
                Write-Host "Target $TargetDir already exists. Repo version takes precedence; local backup kept at $backupPath." -ForegroundColor DarkGray
            }

            try {
                Remove-Item -Path $LinkPath -Recurse -Force -ErrorAction Stop
            } catch {
                Write-Host "Folder is locked. Attempting to free it safely..." -ForegroundColor Yellow
                Stop-LockingProcesses -Path $LinkPath
                Start-Sleep -Seconds 2
                try {
                    Remove-Item -Path $LinkPath -Recurse -Force -ErrorAction Stop
                } catch {
                    Write-Warning "Still could not remove $LinkPath after clearing safe processes. You may need to manually close IDEs/Terminals using it."
                    return
                }
            }
        }
    } elseif (!(Test-Path $TargetDir)) {
        Write-Host "Creating empty target directory $TargetDir..." -ForegroundColor DarkGray
        New-Item -ItemType Directory -Path $TargetDir -Force | Out-Null
    }

    Write-Host "Creating symlink: $LinkPath -> $TargetDir" -ForegroundColor Green
    New-Item -ItemType SymbolicLink -Path $LinkPath -Target $TargetDir -Force | Out-Null
}

$homeDir = $script:resolvedHomeDir
$agentsLink = "$homeDir\.agents"
$agentsTarget = "$opencodeAndSkillsDir\.agents"

$opencodeConfigLink = "$homeDir\.config\opencode"
$opencodeConfigTarget = "$opencodeAndSkillsDir\opencode"

if (!(Test-Path "$homeDir\.config")) {
    New-Item -ItemType Directory -Path "$homeDir\.config" -Force | Out-Null
}

Initialize-Symlink -LinkPath $agentsLink -TargetDir $agentsTarget
Initialize-Symlink -LinkPath $opencodeConfigLink -TargetDir $opencodeConfigTarget

Write-Host "Symlinks configured successfully." -ForegroundColor Green

# === 10. INJECT LOCAL DNS ===
$hostsPath = "$env:windir\System32\drivers\etc\hosts"
$hostsBlock = @"

# --- Added by Homelab Bootstrap ---
127.0.0.1 find
127.0.0.1 draw
# 127.0.0.1 coder     # (Reserved for future OpenCode deployment)
# 127.0.0.1 assistant # (Reserved for future OpenCode deployment)
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

Write-Host "System configuration complete. Proceeding with application deployments..." -ForegroundColor Green

# === 11. DEPLOY LLAMACPP & LOCAL MODELS ===
Write-Host "=== DEPLOYING LLAMACPP ===" -ForegroundColor Cyan
if (Test-Path $llamaInstallDir) {
    if (Test-Path "$llamaInstallDir\.git") {
        Write-Host "Directory $llamaInstallDir already exists and is a git repo. Pulling latest updates..." -ForegroundColor Yellow
        Set-Location $llamaInstallDir
        $LASTEXITCODE = 0
        git pull
        if ($LASTEXITCODE -ne 0) { Exit-Fatal "Failed to pull updates for llamacpp." }
    } else {
        Write-Host "Directory $llamaInstallDir exists but is NOT a git repo. Removing and re-cloning..." -ForegroundColor Yellow
        Remove-Item -Path $llamaInstallDir -Recurse -Force -ErrorAction Stop
        $LASTEXITCODE = 0
        git clone $llamaRepoUrl $llamaInstallDir
        if ($LASTEXITCODE -ne 0) { Exit-Fatal "Failed to clone llamacpp repository." }
    }
} else {
    Write-Host "Cloning llama executables directly to $llamaInstallDir..." -ForegroundColor Cyan
    $LASTEXITCODE = 0
    git clone $llamaRepoUrl $llamaInstallDir
    if ($LASTEXITCODE -ne 0) { Exit-Fatal "Failed to clone llamacpp repository." }
}

Write-Host "Verifying System PATH for llamacpp..." -ForegroundColor Cyan
$pathKey = [Microsoft.Win32.Registry]::LocalMachine.OpenSubKey("SYSTEM\CurrentControlSet\Control\Session Manager\Environment", $true)
$currentPath = $pathKey.GetValue("Path", "", [Microsoft.Win32.RegistryValueOptions]::DoNotExpandEnvironmentNames)

if ($currentPath -notlike "*$llamaInstallDir*") {
    $newPath = $currentPath + ";" + $llamaInstallDir
    $pathKey.SetValue("Path", $newPath, [Microsoft.Win32.RegistryValueKind]::ExpandString)
    $env:Path += ";$llamaInstallDir"
    Write-Host "Successfully added $llamaInstallDir to the system PATH." -ForegroundColor Green
} else {
    Write-Host "$llamaInstallDir is already in the system PATH." -ForegroundColor DarkGray
}
$pathKey.Close()

Write-Host "Downloading AI Models via Hugging Face..." -ForegroundColor Cyan
if (Get-Command huggingface-cli -ErrorAction SilentlyContinue) {
    Write-Host "Downloading Main Model GGUF..." -ForegroundColor Cyan
    $LASTEXITCODE = 0
    huggingface-cli download $llamaModelRepo $llamaModelFile --local-dir $llamaInstallDir
    if ($LASTEXITCODE -ne 0) { Exit-Fatal "Failed to download the main GGUF model." }
    
    Write-Host "Downloading Vision MMProj GGUF..." -ForegroundColor Cyan
    $LASTEXITCODE = 0
    huggingface-cli download $llamaVisionRepo $llamaVisionFile --local-dir $llamaInstallDir
    if ($LASTEXITCODE -ne 0) { Exit-Fatal "Failed to download the vision MMProj model." }
    
    Write-Host "Model downloads complete." -ForegroundColor Green
} else {
    Exit-Fatal "huggingface-cli is missing after installation step. Cannot proceed with model downloads."
}

Write-Host "LLama.cpp deployment complete." -ForegroundColor Green

# === 12. GENERATE RUN-LLAMA COMMAND & CONFIG ===
Write-Host "Creating global run-llama command and external configuration file..." -ForegroundColor Cyan

$llamaArgsFile = "$llamaInstallDir\llama-args.txt"
$llamaWrapperScript = "$llamaInstallDir\run-llama.ps1"

$initialArgs = '-m "C:\Program Files\llamacpp\Qwen3.6-35B-A3B-Claude-4.7-Opus-Reasoning-Distilled-APEX-MTP-I-Compact.gguf" --mmproj "C:\Program Files\llamacpp\mmproj.gguf" --n-gpu-layers 999 --no-mmap --cache-type-k turbo4 --cache-type-v turbo3 --jinja -c 262144 --mlock --n-cpu-moe 28 --context-shift --keep -1 -np 1 --port 8081 --spec-type mtp --spec-draft-n-max 2'
Set-Content -Path $llamaArgsFile -Value $initialArgs -Force

$wrapperContent = @"
`$exePath = "C:\Program Files\llamacpp\llama-server.exe"
`$argsFile = "C:\Program Files\llamacpp\llama-args.txt"

if (Test-Path `$argsFile) {
    `$argsText = (Get-Content `$argsFile -Raw).Trim()
    # Split on whitespace but respect quoted strings (e.g. paths with spaces)
    `$argsList = [System.Collections.Generic.List[string]]::new()
    [regex]::Matches(`$argsText, '(?:\"[^\"]*\"|[^\s]+)') | ForEach-Object { `$argsList.Add(`$_.Value) }
    Write-Host "Booting llama-server with parameters from `$argsFile..." -ForegroundColor Cyan
    Start-Process -FilePath `$exePath -ArgumentList `$argsList -NoNewWindow -Wait
} else {
    Write-Error "Configuration file not found: `$argsFile"
}
"@
Set-Content -Path $llamaWrapperScript -Value $wrapperContent -Force

Write-Host "run-llama configured successfully." -ForegroundColor Green

# === 13. DOCKER READINESS CHECK ===
Write-Host "Checking Docker daemon..." -ForegroundColor Cyan

$dockerExe = (Get-ItemProperty -Path "HKLM:\SOFTWARE\Docker Inc.\Docker Desktop" -ErrorAction SilentlyContinue).InstallPath
if ($dockerExe) { $dockerExe = Join-Path $dockerExe "Docker Desktop.exe" }

if (!$dockerExe -or !(Test-Path $dockerExe)) {
    $dockerExe = "C:\Program Files\Docker\Docker\Docker Desktop.exe"
}

if (!(Get-Process "Docker Desktop" -ErrorAction SilentlyContinue) -and (Test-Path $dockerExe)) {
    Write-Host "Starting Docker Desktop application..." -ForegroundColor Yellow
    Start-Process -FilePath $dockerExe -WindowStyle Hidden
}

$timeout = 90
$elapsed = 0

$LASTEXITCODE = 1
docker info *>$null

while ($LASTEXITCODE -ne 0 -and $elapsed -lt $timeout) {
    Write-Host "Waiting for Docker to start... ($elapsed/$timeout seconds)" -ForegroundColor Yellow
    Start-Sleep -Seconds 3
    $elapsed += 3

    $LASTEXITCODE = 1
    docker info *>$null
}

if ($LASTEXITCODE -ne 0) {
    Exit-Fatal "Docker daemon did not start in time. If Docker was just installed, a reboot may be required before re-running."
}
Write-Host "Docker daemon is up." -ForegroundColor Green

# === 14. VALIDATE REPO STRUCTURE ===
if (!(Test-Path "$targetDir\proxy")) {
    Exit-Fatal "Expected folder '$targetDir\proxy' not found. Did the clone succeed? Check the repo structure."
}
if (!(Test-Path "$targetDir\searxng")) {
    Exit-Fatal "Expected folder '$targetDir\searxng' not found. Did the clone succeed? Check the repo structure."
}
if (!(Test-Path "$targetDir\excalidraw")) {
    Exit-Fatal "Expected folder '$targetDir\excalidraw' not found. Did the clone succeed? Check the repo structure."
}

Write-Host "Repository structure validated." -ForegroundColor Green

# === 15. SPIN UP CONTAINERS ===
Write-Host "Booting Caddy Proxy..." -ForegroundColor Cyan
Push-Location "$targetDir\proxy"
$LASTEXITCODE = 1
docker compose up -d
Pop-Location
if ($LASTEXITCODE -ne 0) { Exit-Fatal "Failed to start Caddy Proxy. Check docker compose output above." }

Write-Host "Booting SearXNG..." -ForegroundColor Cyan
Push-Location "$targetDir\searxng"
$LASTEXITCODE = 1
docker compose up -d
Pop-Location
if ($LASTEXITCODE -ne 0) { Exit-Fatal "Failed to start SearXNG. Check docker compose output above." }

Write-Host "Booting Excalidraw..." -ForegroundColor Cyan
Push-Location "$targetDir\excalidraw"
$LASTEXITCODE = 1
docker compose up -d
Pop-Location
if ($LASTEXITCODE -ne 0) { Exit-Fatal "Failed to start Excalidraw. Check docker compose output above." }

Write-Host "All containers are up." -ForegroundColor Green

# === 16. SELF-DESTRUCT ===
if ($isStandalone) {
    Write-Host "Removing standalone bootstrap script..." -ForegroundColor DarkGray
    Remove-Item -Path $PSCommandPath -Force -ErrorAction SilentlyContinue
}

Write-Host "Bootstrap process complete." -ForegroundColor Green

# === 17. FINAL SUMMARY ===
Write-Host ""
Write-Host "================================================================================" -ForegroundColor Green
Write-Host "                      HOMELAB DEPLOYMENT COMPLETE                               " -ForegroundColor Green
Write-Host "================================================================================" -ForegroundColor Green
Write-Host ""
Write-Host "Here is what this script set up and how to use it:" -ForegroundColor White
Write-Host ""
Write-Host "1. DOCKER SERVICES (Background Web Apps)" -ForegroundColor Cyan
Write-Host "   These are running in Docker Desktop and routed via local DNS." -ForegroundColor Gray
Write-Host "   - SearXNG Search: Open your browser to http://find" -ForegroundColor White
Write-Host "   - Excalidraw:     Open your browser to http://draw" -ForegroundColor White
Write-Host "   * Edit Compose Files in: $targetDir" -ForegroundColor DarkGray
Write-Host ""
Write-Host "2. OPENCODE-AI (Agentic CLI)" -ForegroundColor Cyan
Write-Host "   Your system configs were automatically backed up and symlinked to your repo." -ForegroundColor Gray
Write-Host "   - To Run: Open any terminal and type 'opencode'" -ForegroundColor White
Write-Host "   * Edit Agents/Skills in: $targetDir\opencode and skills" -ForegroundColor DarkGray
Write-Host ""
Write-Host "3. LLAMA.CPP (Local LLM Server)" -ForegroundColor Cyan
Write-Host "   Executables, models, and a global launch command were installed." -ForegroundColor Gray
Write-Host "   - To Run: Open any terminal and type 'run-llama'" -ForegroundColor White
Write-Host "   * Edit Server Flags in: C:\Program Files\llamacpp\llama-args.txt" -ForegroundColor DarkGray
Write-Host "   * Update Executables:  cd into C:\Program Files\llamacpp and run 'git pull'" -ForegroundColor DarkGray
Write-Host ""
Write-Host "4. SYSTEM DEPENDENCIES INSTALLED" -ForegroundColor Cyan
Write-Host "   - WSL2: Windows Subsystem for Linux, required as the backend for Docker containers." -ForegroundColor Gray
Write-Host "   - Docker Desktop: The container engine hosting SearXNG, Caddy Proxy, and Excalidraw." -ForegroundColor Gray
Write-Host "   - Git: Version control tool used to clone/pull the repositories." -ForegroundColor Gray
Write-Host "   - Node.js & npm: JavaScript runtime environment required to run OpenCode-AI locally." -ForegroundColor Gray
Write-Host "   - Python & pip: Required to install and run the Hugging Face CLI." -ForegroundColor Gray
Write-Host "   - Hugging Face CLI: Tool used to cleanly download the large GGUF model files directly." -ForegroundColor Gray
Write-Host ""
Write-Host "NOTE: You may need to restart your current terminal window for the new commands" -ForegroundColor Yellow
Write-Host "(opencode and run-llama) to be recognized in your system PATH." -ForegroundColor Yellow
Write-Host "================================================================================" -ForegroundColor Green
Write-Host ""
Pause