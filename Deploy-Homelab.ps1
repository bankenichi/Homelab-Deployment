# === 1. CONFIGURATION & DYNAMIC PATHING ===
$scriptDir = $PSScriptRoot
$repoUrl = "https://github.com/bankenichi/Homelab-Deployment"
$isStandalone = $false

# Post-reboot resume state
$resumeUserFile = "C:\ProgramData\HomelabBootstrap\resume-user.txt"

# Llama.cpp & Model Config
# $llamaInstallDir defaults to C:\Program Files\llamacpp but honors a pre-set LLAMACPP_ROOT
# env var if present — useful for putting the 20+ GB GGUF weights on a non-system drive.
# IMPORTANT: the override must be persisted at User or Machine scope BEFORE running this
# script. A transient `$env:LLAMACPP_ROOT = ...` set in the same shell will NOT survive the
# UAC self-elevation below (the elevated process reads env vars fresh from the registry).
# See the README "Optional: Installing llama.cpp to a different drive" section for the exact
# command. The resolved value is persisted back to machine scope later (section 11), so any
# subsequent run picks it up automatically without re-setting.
$llamaInstallDir = if ($env:LLAMACPP_ROOT) { $env:LLAMACPP_ROOT } else { "C:\Program Files\llamacpp" }
$llamaRepoUrl = "https://github.com/bankenichi/llamacpp-turboquant-mtp-executables-for-cuda-12.8"
$llamaModelRepo = "mudler/Qwen3.6-35B-A3B-Claude-4.7-Opus-Reasoning-Distilled-APEX-MTP-GGUF"
$llamaModelFile = "Qwen3.6-35B-A3B-Claude-4.7-Opus-Reasoning-Distilled-APEX-MTP-I-Compact.gguf"
$llamaVisionRepo = "mudler/Qwen3.6-35B-A3B-Claude-4.7-Opus-Reasoning-Distilled-APEX-GGUF"
$llamaVisionFile = "mmproj.gguf"

# opencode-vision VLM (dedicated :8083 vision server, lazily spawned by vision_mcp.py).
# These two files are downloaded into <repo>/opencode-vision/ and referenced by the
# VISION_LLAMA_MODEL / VISION_LLAMA_MMPROJ env vars in opencode.json. Keep these filenames
# in sync with that opencode.json `vision` mcp entry — if they drift, the MCP can't spawn.
$visionModelRepo  = "mradermacher/CyberNeurova-Qwen2.5-VL-3B-Instruct-abliterated-GGUF"
$visionModelFile  = "CyberNeurova-Qwen2.5-VL-3B-Instruct-abliterated.Q4_K_M.gguf"
$visionMmprojFile = "CyberNeurova-Qwen2.5-VL-3B-Instruct-abliterated.mmproj-Q8_0.gguf"

# Non-fatal dependency issue collector. Optional-but-not-critical steps (Python MCP deps,
# the OpenCode plugin npm install, the vision VLM download) append a human-readable entry
# with a manual fix command here instead of aborting the whole deploy via Exit-Fatal. The
# list is printed as a "DEPENDENCY ISSUES" section in the final summary (§17). The core
# stack (Docker apps, llama.cpp + main model, OpenCode) still hard-fails on real errors.
$script:depIssues = [System.Collections.Generic.List[string]]::new()
function Add-DepIssue { param([string]$What, [string]$Fix)
    $script:depIssues.Add("- $What`n    Fix: $Fix")
    Write-Warning "$What (continuing; will be summarized at the end)"
}

# === HELPER: FATAL ERROR ===
function Exit-Fatal {
    param([string]$Message)
    Write-Error $Message
    Pause
    exit 1
}

# === HELPER: CONTAINER HEALTH CHECK ===
function Test-ContainerHealth {
    param(
        [string]$ContainerName,
        [int]$MaxRetries = 3,
        [int]$TimeoutSeconds = 30
    )
    $retry = 0
    while ($retry -lt $MaxRetries) {
        try {
            $status = docker inspect --format='{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Running}}{{end}}' $ContainerName 2>$null
            if ($LASTEXITCODE -eq 0) {
                if ($status -eq 'healthy' -or $status -eq 'true') {
                    Write-Host "  Container '$ContainerName' is healthy." -ForegroundColor Green
                    return
                }
            }
        } catch {
            Write-Host "  Health check for '$ContainerName' failed: $_" -ForegroundColor DarkGray
        }

        Write-Host "  Container '$ContainerName' not yet healthy (attempt $($retry + 1)/$MaxRetries). Waiting ${TimeoutSeconds}s..." -ForegroundColor DarkGray
        Start-Sleep -Seconds $TimeoutSeconds
        $retry++
    }
    Exit-Fatal "Container '$ContainerName' failed to become healthy after $MaxRetries retries x ${TimeoutSeconds}s each."
}

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

# === HELPER: SYMLINK INITIALIZER ===
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

# === 1b. CLEAN UP POST-REBOOT SCHEDULED TASK (if resuming) ===
$resumeTaskName = "ResumeHomelabBootstrap"
$resumingAsUser = $false
schtasks /query /tn $resumeTaskName 2>$null | Out-Null
if ($LASTEXITCODE -eq 0) {
    schtasks /delete /tn $resumeTaskName /f | Out-Null
    $resumingAsUser = $true
    Write-Host "Resumed from post-reboot scheduled task. Task cleaned up." -ForegroundColor DarkGray
}

# Resolve the target user profile -- either the saved resume user or current user
if ($resumingAsUser -and (Test-Path $resumeUserFile)) {
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
Write-Host "Ensuring WSL is installed and enabled..." -ForegroundColor Cyan
wsl --install --no-distribution
Start-Sleep -Seconds 3 # Give the hypervisor a moment to register

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
    # Save the current username so the resume task can resolve the correct user profile
    $resumeUserDir = Split-Path $resumeUserFile
    if (!(Test-Path $resumeUserDir)) { New-Item -ItemType Directory -Path $resumeUserDir -Force | Out-Null }
    $env:USERNAME | Set-Content -Path $resumeUserFile -Force
    Write-Host "Saved resume username ($env:USERNAME) to $resumeUserFile." -ForegroundColor DarkGray

    # Register a one-shot scheduled task running as the interactive user with highest privileges.
    # Running as SYSTEM would break winget, Docker Desktop (Session 0 isolation), and WSL profile mapping.
    # The user will see one UAC prompt on login before the script resumes — this is expected and required.
    $resumeTaskName = "ResumeHomelabBootstrap"
    $resumeCommand = "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Normal -File `"$PSCommandPath`""
    schtasks /create /tn $resumeTaskName /sc ONLOGON /rl HIGHEST /ru $env:USERNAME /tr $resumeCommand /f | Out-Null
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
    Write-Host "after you log back in. You will see ONE UAC prompt     " -ForegroundColor Cyan
    Write-Host "asking for administrator privileges -- please accept   " -ForegroundColor Cyan
    Write-Host "it to allow the deployment to complete.                " -ForegroundColor Cyan
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
        # Require a reasonably modern Python (3.10+). We do NOT force-upgrade an existing
        # newer Python (e.g. 3.13/3.14) — users can manage that themselves. Fresh installs
        # target 3.12, which has the broadest wheel support for the heavy parsing stack
        # (textract, pdfplumber, pdfminer.six, etc.). 3.13/3.14 may lack some wheels;
        # any resulting dep failures are collected and surfaced at the end (non-fatal).
        if ($pyMajor -lt 3 -or ($pyMajor -eq 3 -and $pyMinor -lt 10)) {
            Write-Host "Python $pyMajor.$pyMinor is too old — installing Python 3.12..." -ForegroundColor Yellow
            $needsPythonInstall = $true
        } else {
            Write-Host "Python $pyVersionOutput confirmed." -ForegroundColor DarkGray
            if ($pyMinor -ge 13) {
                Write-Warning "Python $pyMajor.$pyMinor is newer than the tested 3.12; some MCP deps (e.g. textract) may lack wheels. Failures will be summarized at the end."
            }
        }
    } else {
        Write-Warning "Could not parse Python version from: $pyVersionOutput. Will attempt to install Python 3.12."
        $needsPythonInstall = $true
    }
} else {
    Write-Host "Python not found. Installing Python 3.12..." -ForegroundColor Yellow
    $needsPythonInstall = $true
}

if ($needsPythonInstall) {
    $LASTEXITCODE = 0
    winget install --id Python.Python.3.12 -e --source winget --accept-package-agreements --accept-source-agreements
    if ($LASTEXITCODE -ne 0) { Exit-Fatal "Python 3.12 installation via winget failed." }
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
    # hf_xet enables fast native downloads from xet-backed repos (the vision VLM repo is
    # xet); without it the CLI falls back to slower plain HTTP. cli pulls the entrypoint.
    pip install "huggingface_hub[cli,hf_xet]" --break-system-packages
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

# === 8b. INSTALL PYTHON DEPS FOR MCP SERVERS ===
Write-Host "Installing Python dependencies for MCP servers..." -ForegroundColor Cyan
$codingMcpReq = "$targetDir\mcp-server\requirements.txt"
$protonMcpReq = "$targetDir\proton-mcp\requirements.txt"
$visionMcpReq = "$targetDir\opencode-vision\mcp\requirements.txt"

# MCP dep installs are NON-FATAL: a single bad/unbuildable package (e.g. textract on a
# too-new Python) must not abort the whole deploy. Failures are collected and surfaced in
# the final summary with the exact command to re-run by hand. The Docker apps, llama.cpp,
# and OpenCode itself do not depend on these Python packages.
if (Test-Path $codingMcpReq) {
    $LASTEXITCODE = 0
    pip install -r $codingMcpReq
    if ($LASTEXITCODE -ne 0) { Add-DepIssue "coding-assistant MCP Python deps failed to install." "pip install -r `"$codingMcpReq`"" }
} else {
    Add-DepIssue "Missing $codingMcpReq — coding-assistant MCP deps not installed." "Verify the repo cloned fully, then: pip install -r `"$codingMcpReq`""
}

if (Test-Path $protonMcpReq) {
    $LASTEXITCODE = 0
    pip install -r $protonMcpReq
    if ($LASTEXITCODE -ne 0) { Add-DepIssue "proton-suite MCP Python deps failed to install." "pip install -r `"$protonMcpReq`"" }
} else {
    Add-DepIssue "Missing $protonMcpReq — proton-suite MCP deps not installed." "Verify the repo cloned fully, then: pip install -r `"$protonMcpReq`""
}

# opencode-vision MCP server (vision_mcp.py) — deps: mcp, requests. Spawns the :8083
# vision llama-server on demand; without these the `vision` MCP entry fails to start.
if (Test-Path $visionMcpReq) {
    $LASTEXITCODE = 0
    pip install -r $visionMcpReq
    if ($LASTEXITCODE -ne 0) { Add-DepIssue "opencode-vision MCP Python deps failed to install." "pip install -r `"$visionMcpReq`"" }
} else {
    Add-DepIssue "Missing $visionMcpReq — opencode-vision MCP deps not installed." "Verify the repo cloned fully, then: pip install -r `"$visionMcpReq`""
}

Write-Host "Python MCP dependency install step complete." -ForegroundColor Green

# === 8c. SET HOMELAB_ROOT ENVIRONMENT VARIABLE ===
# opencode.json references this path via {env:HOMELAB_ROOT} for its MCP server commands,
# so wherever this repo lives on disk, opencode can find mcp-server and proton-mcp without
# any hard-coded absolute paths. Forward slashes keep the value portable across shells and
# safe to drop into JSON without escaping backslashes.
Write-Host "Setting HOMELAB_ROOT environment variable..." -ForegroundColor Cyan
$homelabRoot = ($targetDir -replace '\\', '/')
$currentHomelabRoot = [Environment]::GetEnvironmentVariable("HOMELAB_ROOT", [EnvironmentVariableTarget]::Machine)
if ($currentHomelabRoot -ne $homelabRoot) {
    [Environment]::SetEnvironmentVariable("HOMELAB_ROOT", $homelabRoot, [EnvironmentVariableTarget]::Machine)
    Write-Host "HOMELAB_ROOT set to '$homelabRoot' (machine scope)." -ForegroundColor Green
} else {
    Write-Host "HOMELAB_ROOT already set to '$homelabRoot'. Skipping." -ForegroundColor DarkGray
}
# Mirror into the current process so any subsequent step in this run sees it without a reboot.
$env:HOMELAB_ROOT = $homelabRoot

# === 9. CONFIGURE OPENCODE SYMLINKS ===
Write-Host "Configuring OpenCode symlinks..." -ForegroundColor Cyan

$opencodeAndSkillsDir = "$targetDir\opencode and skills"
if (!(Test-Path $opencodeAndSkillsDir)) {
    Write-Host "Creating 'opencode and skills' directory structure in repo..." -ForegroundColor DarkGray
    New-Item -ItemType Directory -Path $opencodeAndSkillsDir -Force | Out-Null
}

Write-Host "Backing up existing OpenCode configurations and creating symlinks to the repository..." -ForegroundColor Cyan
Write-Host "Ensuring symlink paths are clear and backed up if necessary..." -ForegroundColor DarkGray

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

# Install the OpenCode plugin dependencies (incl. the opencode-vision plugin's
# @opencode-ai/plugin + @types/node). OpenCode runs the .ts plugin directly via Bun, so
# these are primarily for type resolution / tooling, but npm install keeps the vendored
# plugin's package.json honored and avoids editor/type drift. Idempotent.
$opencodePkgDir = "$opencodeConfigTarget"
if (Test-Path "$opencodePkgDir\package.json") {
    if (Get-Command npm -ErrorAction SilentlyContinue) {
        Write-Host "Installing OpenCode plugin dependencies (npm install)..." -ForegroundColor Cyan
        Push-Location $opencodePkgDir
        $LASTEXITCODE = 0
        npm install
        Pop-Location
        # Non-fatal: the vision plugin runs as raw .ts via Bun with zero runtime deps;
        # these packages are for type resolution/tooling only, so a failure shouldn't abort.
        if ($LASTEXITCODE -ne 0) { Add-DepIssue "OpenCode plugin npm install failed (type tooling only; plugin still runs)." "cd `"$opencodePkgDir`"; npm install" }
    } else {
        Add-DepIssue "npm not found — OpenCode plugin dev deps not installed (plugin still runs via Bun)." "Install Node.js, then: cd `"$opencodePkgDir`"; npm install"
    }
} else {
    Write-Warning "Missing $opencodePkgDir\package.json — skipping OpenCode plugin dependency install."
}

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

# Persist LLAMACPP_ROOT to machine scope so downstream tools (and future runs of this script)
# can locate the install dir without re-deriving it. Mirrors the HOMELAB_ROOT pattern from
# section 8c. Idempotent — only writes if the resolved value differs from what's already set.
$currentLlamacppRoot = [Environment]::GetEnvironmentVariable("LLAMACPP_ROOT", [EnvironmentVariableTarget]::Machine)
if ($currentLlamacppRoot -ne $llamaInstallDir) {
    [Environment]::SetEnvironmentVariable("LLAMACPP_ROOT", $llamaInstallDir, [EnvironmentVariableTarget]::Machine)
    Write-Host "LLAMACPP_ROOT set to '$llamaInstallDir' (machine scope)." -ForegroundColor Green
} else {
    Write-Host "LLAMACPP_ROOT already set to '$llamaInstallDir'. Skipping." -ForegroundColor DarkGray
}
$env:LLAMACPP_ROOT = $llamaInstallDir

if (Test-Path $llamaInstallDir) {
    if (Test-Path "$llamaInstallDir\.git") {
        Write-Host "Directory $llamaInstallDir already exists and is a git repo. Pulling latest updates..." -ForegroundColor Yellow
        Set-Location $llamaInstallDir
        $LASTEXITCODE = 0
        git pull
        if ($LASTEXITCODE -ne 0) { Exit-Fatal "Failed to pull updates for llamacpp." }

        Write-Host "Initializing llamacpp submodules..." -ForegroundColor Cyan
        $LASTEXITCODE = 0
        git submodule init
        if ($LASTEXITCODE -ne 0) { Exit-Fatal "Failed to initialize llamacpp submodules. Check your network connection and repo access." }

        Write-Host "Updating llamacpp submodules..." -ForegroundColor Cyan
        $LASTEXITCODE = 0
        git submodule update
        if ($LASTEXITCODE -ne 0) { Exit-Fatal "Failed to update llamacpp submodules. Check your network connection and repo access." }

        Write-Host "Llamacpp submodules are up to date." -ForegroundColor Green
    } else {
        Write-Host "Directory $llamaInstallDir exists but is NOT a git repo. Backing up and re-cloning..." -ForegroundColor Yellow
        $backupName = "llamacpp.backup"
        $backupPath = "$llamaInstallDir.backup"
        if (Test-Path $backupPath) {
            Write-Host "Removing old backup from previous deployment..." -ForegroundColor DarkGray
            Remove-Item -Path $backupPath -Recurse -Force -ErrorAction SilentlyContinue
        }
        try {
            Rename-Item -Path $llamaInstallDir -NewName $backupName -Force
            Write-Host "Original folder renamed to $backupName for safety." -ForegroundColor DarkGray
        } catch {
            Exit-Fatal "Could not rename $llamaInstallDir (file may be locked by another process). Please close any applications using files in that folder, then rerun this script."
        }
        $LASTEXITCODE = 0
        git clone $llamaRepoUrl $llamaInstallDir
        if ($LASTEXITCODE -ne 0) { Exit-Fatal "Failed to clone llamacpp repository." }

        Write-Host "Initializing llamacpp submodules..." -ForegroundColor Cyan
        Set-Location $llamaInstallDir
        $LASTEXITCODE = 0
        git submodule init
        if ($LASTEXITCODE -ne 0) { Exit-Fatal "Failed to initialize llamacpp submodules. Check your network connection and repo access." }

        Write-Host "Updating llamacpp submodules..." -ForegroundColor Cyan
        $LASTEXITCODE = 0
        git submodule update
        if ($LASTEXITCODE -ne 0) { Exit-Fatal "Failed to update llamacpp submodules. Check your network connection and repo access." }

        Write-Host "Llamacpp submodules are up to date." -ForegroundColor Green
    }
} else {
    Write-Host "Cloning llama executables directly to $llamaInstallDir..." -ForegroundColor Cyan
    $LASTEXITCODE = 0
    git clone $llamaRepoUrl $llamaInstallDir
    if ($LASTEXITCODE -ne 0) { Exit-Fatal "Failed to clone llamacpp repository." }

    Write-Host "Initializing llamacpp submodules..." -ForegroundColor Cyan
    Set-Location $llamaInstallDir
    $LASTEXITCODE = 0
    git submodule init
    if ($LASTEXITCODE -ne 0) { Exit-Fatal "Failed to initialize llamacpp submodules. Check your network connection and repo access." }

    Write-Host "Updating llamacpp submodules..." -ForegroundColor Cyan
    $LASTEXITCODE = 0
    git submodule update
    if ($LASTEXITCODE -ne 0) { Exit-Fatal "Failed to update llamacpp submodules. Check your network connection and repo access." }

    Write-Host "Llamacpp submodules are up to date." -ForegroundColor Green
}

# FIX 3: Use [Environment]::SetEnvironmentVariable instead of direct registry write.
# This broadcasts WM_SETTINGCHANGE so running apps pick up the new PATH immediately.
Write-Host "Verifying System PATH for llamacpp..." -ForegroundColor Cyan
$llamaConfigUiDir = "$llamaInstallDir\llama-config-ui"
$currentPath = [Environment]::GetEnvironmentVariable("Path", [EnvironmentVariableTarget]::Machine)

if ($currentPath -notlike "*$llamaInstallDir*") {
    [Environment]::SetEnvironmentVariable("Path", $currentPath + ";" + $llamaInstallDir, [EnvironmentVariableTarget]::Machine)
    $env:Path += ";$llamaInstallDir"
    Write-Host "Successfully added $llamaInstallDir to the system PATH." -ForegroundColor Green
} else {
    Write-Host "$llamaInstallDir is already in the system PATH." -ForegroundColor DarkGray
}

if (Test-Path $llamaConfigUiDir) {
    if ($currentPath -notlike "*$llamaConfigUiDir*") {
        $currentPath = [Environment]::GetEnvironmentVariable("Path", [EnvironmentVariableTarget]::Machine)
        [Environment]::SetEnvironmentVariable("Path", $currentPath + ";" + $llamaConfigUiDir, [EnvironmentVariableTarget]::Machine)
        $env:Path += ";$llamaConfigUiDir"
        Write-Host "Successfully added $llamaConfigUiDir to the system PATH." -ForegroundColor Green
    } else {
        Write-Host "$llamaConfigUiDir is already in the system PATH." -ForegroundColor DarkGray
    }
} else {
    Write-Warning "Submodule folder '$llamaConfigUiDir' not found after submodule update. Skipping PATH registration — verify the submodule populated correctly."
}

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

    # opencode-vision dedicated VLM (Qwen2.5-VL-3B) + its mmproj, into <repo>/opencode-vision/.
    # Lazily spawned on :8083 by vision_mcp.py. Idempotent: skip files already on disk so
    # re-runs don't re-pull ~2.8 GB. Filenames MUST match the VISION_LLAMA_* paths in opencode.json.
    $visionDir = "$targetDir\opencode-vision"
    if (!(Test-Path $visionDir)) { New-Item -ItemType Directory -Path $visionDir -Force | Out-Null }

    # Vision VLM download is NON-FATAL: it's optional (only the `vision` MCP needs it) and
    # large. A failure is collected and surfaced at the end with the manual command, rather
    # than aborting the deploy of the core stack.
    if (!(Test-Path "$visionDir\$visionModelFile")) {
        Write-Host "Downloading opencode-vision VLM GGUF..." -ForegroundColor Cyan
        $LASTEXITCODE = 0
        huggingface-cli download $visionModelRepo $visionModelFile --local-dir $visionDir
        if ($LASTEXITCODE -ne 0) { Add-DepIssue "opencode-vision VLM model download failed (vision MCP won't spawn until present)." "huggingface-cli download $visionModelRepo $visionModelFile --local-dir `"$visionDir`"" }
    } else {
        Write-Host "opencode-vision VLM already present. Skipping." -ForegroundColor DarkGray
    }

    if (!(Test-Path "$visionDir\$visionMmprojFile")) {
        Write-Host "Downloading opencode-vision mmproj GGUF..." -ForegroundColor Cyan
        $LASTEXITCODE = 0
        huggingface-cli download $visionModelRepo $visionMmprojFile --local-dir $visionDir
        if ($LASTEXITCODE -ne 0) { Add-DepIssue "opencode-vision mmproj download failed (vision MCP won't spawn until present)." "huggingface-cli download $visionModelRepo $visionMmprojFile --local-dir `"$visionDir`"" }
    } else {
        Write-Host "opencode-vision mmproj already present. Skipping." -ForegroundColor DarkGray
    }

    Write-Host "Model downloads complete." -ForegroundColor Green
} else {
    Exit-Fatal "huggingface-cli is missing after installation step. Cannot proceed with model downloads."
}

Write-Host "LLama.cpp deployment complete." -ForegroundColor Green

# # === 12. GENERATE RUN-LLAMA COMMAND & CONFIG ===
Write-Host "Creating global run-llama command and external configuration file..." -ForegroundColor Cyan

$llamaArgsFile = "$llamaInstallDir\llama-args.txt"
$llamaWrapperScript = "$llamaInstallDir\run-llama.ps1"
$llamaCmdWrapper = "$llamaInstallDir\run-llama.cmd"


# Write the initial args file. This is the only file users need to edit to change
# llama-server launch parameters. Keep all args on a single line.
$initialArgs = '--n-gpu-layers 999 --no-mmap --cache-type-k turbo4 --cache-type-v turbo3 --jinja -c 262144 --mlock --n-cpu-moe 28 --context-shift --keep -1 -np 1 --port 8081 --spec-type mtp --spec-draft-n-max 2 -m "' + $llamaInstallDir + '\Qwen3.6-35B-A3B-Claude-4.7-Opus-Reasoning-Distilled-APEX-MTP-I-Compact.gguf" --mmproj "' + $llamaInstallDir + '\mmproj.gguf"'
Set-Content -Path $llamaArgsFile -Value $initialArgs -Force

# The .ps1 wrapper reads llama-args.txt and correctly parses quoted paths (e.g. paths
# with spaces) before passing them to llama-server. CMD cannot reliably do this parsing,
# which is why a PowerShell wrapper is required rather than a pure .cmd solution.
$wrapperContent = @"
`$exePath = "$llamaInstallDir\llama-server.exe"
`$argsFile = "$llamaInstallDir\llama-args.txt"
if (!(Test-Path `$argsFile)) { Write-Error "Config not found: `$argsFile"; exit 1 }
`$argsText = (Get-Content `$argsFile -Raw).Trim()
`$argsList = [regex]::Matches(`$argsText, '(?:"[^"]*"|[^\s]+)') | ForEach-Object { `$_.Value }
Write-Host "Booting llama-server..." -ForegroundColor Cyan
& `$exePath @argsList
"@
Set-Content -Path $llamaWrapperScript -Value $wrapperContent -Force

# The .cmd shim exists solely to bypass PowerShell execution policy restrictions.
# Without it, typing 'run-llama' in a fresh terminal would fail on systems where
# .ps1 execution is disabled. The .cmd is what PATH resolves 'run-llama' to.
$cmdContent = "@echo off`r`npowershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$llamaWrapperScript`""
Set-Content -Path $llamaCmdWrapper -Value $cmdContent -Force

# Unblock both files so Windows doesn't prompt users to confirm execution.
# Files written by a script that originated from the internet inherit a Zone.Identifier
# alternate data stream marking them as untrusted — Unblock-File removes that mark.
Unblock-File -Path $llamaWrapperScript
Unblock-File -Path $llamaCmdWrapper

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
if (!(Test-Path "$targetDir\opencode-vision\mcp\vision_mcp.py")) {
    Exit-Fatal "Expected '$targetDir\opencode-vision\mcp\vision_mcp.py' not found. Did the clone succeed? Check the repo structure."
}

Write-Host "Repository structure validated." -ForegroundColor Green

# === 15. SPIN UP CONTAINERS ===
Write-Host "Booting Caddy Proxy..." -ForegroundColor Cyan
Push-Location "$targetDir\proxy"
$LASTEXITCODE = 1
docker compose up -d
Pop-Location
if ($LASTEXITCODE -ne 0) { Exit-Fatal "Failed to start Caddy Proxy. Check docker compose output above." }
Test-ContainerHealth -ContainerName "caddy"

# SearXNG boots WITHOUT a VPN by default (base docker-compose.yml) — it works standalone
# and never depends on a Proton key. To route SearXNG's egress through a Proton VPN US node
# (fixes Google's 403 image-search blocks), run Enable-SearxngVpn.ps1 after deploy; it
# switches the stack to docker-compose.vpn.yml. See searxng\VPN-EGRESS.md.
#
# VPN-aware re-run guard: if a previous run of Enable-SearxngVpn.ps1 already switched this
# box to the VPN variant (gluetun container present), DON'T boot the base file — that would
# silently drop the tunnel and orphan gluetun/vpn-rotator on searxng_default. Re-up the VPN
# variant instead so a plain redeploy preserves the user's VPN choice.
Push-Location "$targetDir\searxng"
$vpnActive = (docker ps -a --filter "name=searxng-gluetun" --format "{{.Names}}" 2>$null)
if ($vpnActive) {
    Write-Host "Existing VPN stack detected — booting SearXNG via docker-compose.vpn.yml..." -ForegroundColor Cyan
    $LASTEXITCODE = 1
    docker compose -f docker-compose.vpn.yml up -d
    Pop-Location
    if ($LASTEXITCODE -ne 0) { Exit-Fatal "Failed to start the VPN SearXNG stack. Check docker compose output above." }
    Test-ContainerHealth -ContainerName "searxng-gluetun"
    Test-ContainerHealth -ContainerName "searxng-core"
} else {
    Write-Host "Booting SearXNG (no VPN — opt in later with Enable-SearxngVpn.ps1)..." -ForegroundColor Cyan
    $LASTEXITCODE = 1
    docker compose up -d
    Pop-Location
    if ($LASTEXITCODE -ne 0) { Exit-Fatal "Failed to start SearXNG. Check docker compose output above." }
    Test-ContainerHealth -ContainerName "searxng-core"
}

Write-Host "Booting Excalidraw..." -ForegroundColor Cyan
Push-Location "$targetDir\excalidraw"
$LASTEXITCODE = 1
docker compose up -d
Pop-Location
if ($LASTEXITCODE -ne 0) { Exit-Fatal "Failed to start Excalidraw. Check docker compose output above." }
Test-ContainerHealth -ContainerName "excalidraw"

Write-Host "All containers are up." -ForegroundColor Green

# === 16. SELF-DESTRUCT ===
if ($isStandalone) {
    Write-Host "Removing standalone bootstrap script..." -ForegroundColor DarkGray
    $cmd = "cmd.exe /c timeout /t 2 /nobreak >nul & del `"$PSCommandPath`""
    Start-Process -FilePath cmd.exe -ArgumentList "/c", $cmd -WindowStyle Hidden
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
Write-Host "   * HOMELAB_ROOT env var (machine scope) = $homelabRoot" -ForegroundColor DarkGray
Write-Host "     Referenced by opencode.json as {env:HOMELAB_ROOT} for MCP server paths." -ForegroundColor DarkGray
Write-Host ""
Write-Host "3. LLAMA.CPP (Local LLM Server)" -ForegroundColor Cyan
Write-Host "   Executables, models, and a global launch command were installed." -ForegroundColor Gray
Write-Host "   - To Run: Open any terminal and type 'run-llama'" -ForegroundColor White
Write-Host "   * Edit Server Flags in: $llamaInstallDir\llama-args.txt" -ForegroundColor DarkGray
Write-Host "   * Update Executables:  cd into $llamaInstallDir and run 'git pull'" -ForegroundColor DarkGray
Write-Host "   * LLAMACPP_ROOT env var (machine scope) = $llamaInstallDir" -ForegroundColor DarkGray
Write-Host "     To relocate llama.cpp on a future redeploy, pre-set this env var; see README." -ForegroundColor DarkGray
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

# --- Deferred dependency issues (non-fatal steps that didn't complete) ---
if ($script:depIssues.Count -gt 0) {
    Write-Host ""
    Write-Host "================================================================================" -ForegroundColor Yellow
    Write-Host "  DEPENDENCY ISSUES ($($script:depIssues.Count)) — the core stack deployed, but these optional" -ForegroundColor Yellow
    Write-Host "  components need a manual step. Run the fix command(s) below, then re-open OpenCode." -ForegroundColor Yellow
    Write-Host "================================================================================" -ForegroundColor Yellow
    foreach ($issue in $script:depIssues) {
        Write-Host ""
        Write-Host $issue -ForegroundColor Gray
    }
    Write-Host ""
    Write-Host "  Tip: if a Python package fails to build on a very new Python (e.g. textract on" -ForegroundColor DarkGray
    Write-Host "  3.13/3.14), install Python 3.12 and re-run the pip command against it." -ForegroundColor DarkGray
    Write-Host "================================================================================" -ForegroundColor Yellow
} else {
    Write-Host "All optional dependencies installed cleanly." -ForegroundColor Green
}

Write-Host ""
Pause