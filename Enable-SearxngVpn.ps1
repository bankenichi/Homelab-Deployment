<#
.SYNOPSIS
    Opt in (or out) of routing SearXNG through a Proton VPN US node.

.DESCRIPTION
    The homelab deploys SearXNG WITHOUT a VPN by default (base docker-compose.yml).
    This helper switches the running SearXNG stack to the VPN-routed variant
    (docker-compose.vpn.yml), which sends all of SearXNG's outbound traffic through
    a Proton VPN US node via a gluetun gateway — fixing Google's 403 image-search
    blocks. It writes the required searxng\vpn.env (gitignored) from your Proton
    WireGuard private key, then recreates the stack.

    -Disable reverts to the base (no-VPN) stack.

    Requires a paid Proton plan. Generate a WireGuard config at
    account.protonvpn.com → Downloads → WireGuard configuration (any US server),
    then pass the [Interface] PrivateKey via -Key, point -ConfPath at the .conf,
    or run with neither and you'll be prompted.

.PARAMETER Key
    The Proton WireGuard private key (the [Interface] PrivateKey value).

.PARAMETER ConfPath
    Path to a downloaded Proton WireGuard .conf file; the PrivateKey is extracted.

.PARAMETER Interval
    VPN exit-IP rotation interval in seconds (written to searxng\.env as
    VPN_ROTATE_INTERVAL). Default 3600 (hourly). Ignored with -Disable.

.PARAMETER Disable
    Tear down the VPN stack and bring the base (no-VPN) SearXNG stack back up.

.EXAMPLE
    .\Enable-SearxngVpn.ps1 -ConfPath .\searxng-us.conf

.EXAMPLE
    .\Enable-SearxngVpn.ps1 -Key "gAUU8Qmiv...=="

.EXAMPLE
    .\Enable-SearxngVpn.ps1 -Disable
#>
[CmdletBinding()]
param(
    [string]$Key,
    [string]$ConfPath,
    [int]$Interval = 3600,
    [switch]$Disable
)

$ErrorActionPreference = "Stop"

$RepoRoot   = $PSScriptRoot
$SearxngDir = Join-Path $RepoRoot "searxng"
$BaseFile   = Join-Path $SearxngDir "docker-compose.yml"
$VpnFile    = Join-Path $SearxngDir "docker-compose.vpn.yml"
$VpnEnv     = Join-Path $SearxngDir "vpn.env"

function Log($msg, $color = "Cyan") { Write-Host $msg -ForegroundColor $color }

if (!(Test-Path $SearxngDir)) { throw "searxng folder not found at $SearxngDir" }
if (!(Get-Command docker -ErrorAction SilentlyContinue)) { throw "docker not found on PATH." }

Push-Location $SearxngDir
try {
    # ---- DISABLE: revert to the base (no-VPN) stack ----
    if ($Disable) {
        Log "Tearing down the VPN stack..."
        docker compose -f $VpnFile down
        Log "Bringing up the base (no-VPN) SearXNG stack..."
        $LASTEXITCODE = 0
        docker compose -f $BaseFile up -d
        if ($LASTEXITCODE -ne 0) { throw "Failed to start the base SearXNG stack." }
        Log "SearXNG is back on the base (no-VPN) stack." "Green"
        Log "(searxng\vpn.env was left in place; delete it if you want the key gone.)" "DarkGray"
        return
    }

    # ---- ENABLE: obtain the WireGuard private key ----
    if (-not $Key -and $ConfPath) {
        if (!(Test-Path $ConfPath)) { throw "ConfPath not found: $ConfPath" }
        $m = Select-String -Path $ConfPath -Pattern '^\s*PrivateKey\s*=\s*(\S+)' | Select-Object -First 1
        if (-not $m) { throw "No 'PrivateKey =' line found in $ConfPath" }
        $Key = $m.Matches[0].Groups[1].Value
        Log "Extracted PrivateKey from $ConfPath." "DarkGray"
    }
    if (-not $Key) {
        $secure = Read-Host "Paste your Proton WireGuard PrivateKey" -AsSecureString
        $Key = [Runtime.InteropServices.Marshal]::PtrToStringAuto(
            [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure))
    }
    $Key = $Key.Trim()
    if ([string]::IsNullOrWhiteSpace($Key)) { throw "No private key provided." }

    # ---- Write vpn.env (gitignored) ----
    $vpnEnvContent = @"
# vpn.env — SECRET, gitignored. Written by Enable-SearxngVpn.ps1.
# Proton WireGuard private key used by the gluetun gateway. Rotate by re-running
# this script with a new key, or edit the line below and recreate the stack.
WIREGUARD_PRIVATE_KEY=$Key
"@
    Set-Content -Path $VpnEnv -Value $vpnEnvContent -Encoding ascii -Force
    Log "Wrote $VpnEnv (gitignored)." "Green"

    # ---- Persist the rotation interval into searxng\.env (optional knob) ----
    if ($Interval -ne 3600) {
        $envFile = Join-Path $SearxngDir ".env"
        $line = "VPN_ROTATE_INTERVAL=$Interval"
        if (Test-Path $envFile) {
            $existing = Get-Content $envFile | Where-Object { $_ -notmatch '^\s*VPN_ROTATE_INTERVAL\s*=' }
            Set-Content -Path $envFile -Value ($existing + $line) -Encoding ascii -Force
        } else {
            Set-Content -Path $envFile -Value $line -Encoding ascii -Force
        }
        Log "Set VPN_ROTATE_INTERVAL=$Interval in searxng\.env." "DarkGray"
    }

    # ---- Switch the stack: base down, VPN up ----
    Log "Stopping the base (no-VPN) stack (if running)..."
    docker compose -f $BaseFile down
    Log "Starting the VPN-routed stack..."
    $LASTEXITCODE = 0
    docker compose -f $VpnFile up -d
    if ($LASTEXITCODE -ne 0) { throw "Failed to start the VPN stack. Check 'docker logs searxng-gluetun'." }

    # ---- Wait for the tunnel to come up, then report the exit IP ----
    Log "Waiting for gluetun to become healthy (up to ~90s)..."
    $healthy = $false
    for ($i = 0; $i -lt 30; $i++) {
        Start-Sleep -Seconds 3
        $status = (docker inspect -f '{{.State.Health.Status}}' searxng-gluetun 2>$null)
        if ($status -eq "healthy") { $healthy = $true; break }
    }
    if (-not $healthy) {
        Write-Warning "gluetun did not report healthy in time. Check 'docker logs searxng-gluetun'."
        Write-Warning "Common cause: a bad/expired WireGuard key. Re-run with a fresh key."
        return
    }
    # Query from searxng-core (the searxng image has wget; the gluetun image is minimal).
    # core shares gluetun's netns, so its egress IS the tunnel's exit IP.
    $ip = (docker exec searxng-core wget -qO- https://ipinfo.io/ip 2>$null)
    if ($ip) { $ip = $ip.Trim() }
    if ([string]::IsNullOrWhiteSpace($ip)) { $ip = "(could not read — check 'docker exec searxng-core wget -qO- https://ipinfo.io/ip')" }
    Log "VPN enabled. SearXNG now exits via: $ip (expected: a US IP)." "Green"
    Log "To revert: .\Enable-SearxngVpn.ps1 -Disable" "DarkGray"
}
finally {
    Pop-Location
}
