# run-vision.ps1 — launch the dedicated vision VLM server on :8083 (no MTP, no context-shift).
#
# WHY a separate server: the main :8081 coding server uses `--spec-type mtp`, and MTP
# speculative decoding is incompatible with multimodal image input in llama.cpp
# (find_slot "non-consecutive token position" crash). This server omits those flags.
#
# Edit the two paths below to where you downloaded the VLM weights (see README.md),
# then run:  .\run-vision.ps1

# --- Portable path resolution (in line with the rest of the homelab project) ---
# 1. Prefer $env:HOMELAB_ROOT (machine-scope, set by Deploy-Homelab.ps1).
# 2. Fall back to deriving the repo root from this script's location:
#    .../<repo>/opencode-vision/vision-server/run-vision.ps1 -> repo root is two parents up.
$RepoRoot = if ($env:HOMELAB_ROOT) { $env:HOMELAB_ROOT } else {
    (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
}
$VisionDir = Join-Path $RepoRoot "opencode-vision"

# --- EDIT THESE FILENAMES IF YOU USE A DIFFERENT VLM ---
$VisionModel  = Join-Path $VisionDir "CyberNeurova-Qwen2.5-VL-3B-Instruct-abliterated.Q4_K_M.gguf"
$VisionMMProj = Join-Path $VisionDir "CyberNeurova-Qwen2.5-VL-3B-Instruct-abliterated.mmproj-Q8_0.gguf"
# --------------------------------------------------------

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ArgsFile  = Join-Path $ScriptDir "vision-args.txt"

# Resolve llama-server.exe (on PATH via Deploy-Homelab, else under LLAMACPP_ROOT).
$llamaExe = (Get-Command "llama-server.exe" -ErrorAction SilentlyContinue).Source
if (-not $llamaExe) {
    $root = $env:LLAMACPP_ROOT; if (-not $root) { $root = "C:\Program Files\llamacpp" }
    $llamaExe = Join-Path $root "llama-server.exe"
}
if (-not (Test-Path $llamaExe))     { throw "llama-server.exe not found ($llamaExe)" }
if (-not (Test-Path $VisionModel))  { throw "Vision model GGUF not found: $VisionModel" }
if (-not (Test-Path $VisionMMProj)) { throw "Vision mmproj GGUF not found: $VisionMMProj" }

# Extra flags from vision-args.txt (ignore blanks/comments). MUST NOT contain --spec-type
# or --context-shift — those are what break multimodal inference.
$extra = @()
if (Test-Path $ArgsFile) {
    foreach ($line in Get-Content $ArgsFile) {
        $t = $line.Trim()
        if ($t -and -not $t.StartsWith("#")) { $extra += ($t -split "\s+") }
    }
}
$banned = $extra | Where-Object { $_ -in @("--spec-type", "--context-shift") }
if ($banned) { throw "vision-args.txt contains MTP/context-shift flags that break vision: $($banned -join ', ')" }

$allArgs = @("-m", $VisionModel, "--mmproj", $VisionMMProj) + $extra

# Force this server fully onto the CPU: hide all GPUs so it never allocates any VRAM or
# competes with the 35B coding server on :8081. (Belt-and-suspenders with --n-gpu-layers 0
# and --no-mmproj-offload in vision-args.txt.)
$env:CUDA_VISIBLE_DEVICES = "-1"
$env:GGML_CUDA_VISIBLE_DEVICES = "-1"

Write-Host "Launching vision VLM server on :8083 (CPU-only, GPUs hidden)" -ForegroundColor Cyan
Write-Host "  exe   : $llamaExe"   -ForegroundColor DarkGray
Write-Host "  model : $VisionModel" -ForegroundColor DarkGray
Write-Host "  mmproj: $VisionMMProj" -ForegroundColor DarkGray
Write-Host "  args  : $($allArgs -join ' ')" -ForegroundColor DarkGray

& $llamaExe @allArgs
