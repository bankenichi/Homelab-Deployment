# vision-server — dedicated VLM on :8083

The main coding server on `:8081` runs with `--spec-type mtp` (Multi-Token-Prediction
speculative decoding). **MTP speculative decoding is incompatible with multimodal image
input in llama.cpp** — when an image batch is inserted, slot/position tracking breaks:

```
srv  process_chun: processing image...
decoding image batch 1/1, n_tokens_batch = 1681
find_slot: non-consecutive token position 10 after 9 for sequence 0 with 512 new tokens
```

So vision runs on a **separate, dedicated llama-server instance on port 8083** with a
small VLM and **no MTP / no context-shift**. The 35B coding server keeps MTP untouched.

> Port note: 8082 is used elsewhere in the stack, so this server uses **8083**.

## Who starts it (usually: the MCP)

The Python `vision_mcp.py` MCP server **lazily spawns this llama-server on first use** and
**auto-shuts it down after idle**, so by default you don't run anything in this folder by
hand. Lifecycle in one diagram:

```
OpenCode boots             → MCP starts                       (zero RAM/VRAM)
First vision_analyze call  → MCP spawns llama-server on :8083 (visible console window)
                              ↓
                            tool call runs ─────────────────► description returned
                              ↓
                            idle timer resets on each call
                              ↓
VISION_LLAMA_IDLE_TIMEOUT  → MCP terminates llama-server      (RAM/VRAM released)
                              ↓
Next vision_analyze call   → re-spawn (cold start ~5–15 s)
OpenCode closes            → Windows Job Object reaps any
                              child instantly (no orphans)
```

To kill it manually mid-session: Ctrl+C in the visible console window, or just close it.
The MCP detects the loss on the next call and re-spawns.

Configured via the `vision` MCP `environment` block in `opencode.json` — see those env
vars below.

## When to run it manually (`run-vision.ps1`)

Use the manual launcher only when:

- you want to **benchmark** the server independently of OpenCode,
- you want to **watch the boot log** without it being tied to an OpenCode session, or
- you want to **disable the MCP spawn** (set `VISION_SPAWN_SERVER=0` in `opencode.json`)
  and run the server as a long-lived process you control.

```powershell
.\vision-server\run-vision.ps1
```

The script resolves paths portably: it prefers `$env:HOMELAB_ROOT`, falling back to its
own location two parents up. Edit just the *filenames* near the top if you swap VLMs.

The MCP probes `:8083` before spawning; if you already have `run-vision.ps1` running it
will reuse that and not start a duplicate.

## Portability convention

All paths in this module follow the homelab convention (see `opencode and skills/AGENTS.md`):

- **Repo-internal paths** use `{env:HOMELAB_ROOT}` in `opencode.json` (forward slashes only)
  and `$env:HOMELAB_ROOT` in PowerShell.
- **External paths** (`C:/Program Files/llamacpp/...`) may be absolute since they're
  fixed by the deploy script.
- Don't hardcode `C:\Users\<name>\...` anywhere in this folder.

VLM files live at `$HOMELAB_ROOT/opencode-vision/<vlm>.gguf` and
`$HOMELAB_ROOT/opencode-vision/<vlm>-mmproj.gguf`.

## Picking a VLM

| Model | Approx VRAM (Q4) | Notes |
| --- | --- | --- |
| **Qwen2.5-VL-3B-Instruct** (current) | ~3–4 GB | Sweet spot: fits next to the 35B without starving it, ~2× faster than 7B |
| Qwen2.5-VL-7B-Instruct | ~6–8 GB | Better quality; may starve the 35B for VRAM |
| Gemma 3 4B (vision) | ~4–6 GB | Alternative family if Qwen architecture isn't supported |

Each needs **two files**: the model GGUF and its matching `mmproj` GGUF. Download into
`$HOMELAB_ROOT/opencode-vision/` and update the filenames in `VISION_LLAMA_MODEL` /
`VISION_LLAMA_MMPROJ` (in `opencode.json`) — and in `run-vision.ps1` only if you intend
to launch manually.

```powershell
# Example — verify exact filenames on the HF page before downloading
$dir = Join-Path $env:HOMELAB_ROOT "opencode-vision"
huggingface-cli download <repo-id> <model.gguf>  --local-dir $dir
huggingface-cli download <repo-id> <mmproj.gguf> --local-dir $dir
```

## Env vars (from `opencode.json` → vision MCP `environment`)

| Var | Default | Purpose |
| --- | --- | --- |
| `VISION_SPAWN_SERVER` | `1` | `0` to disable MCP spawn (use external `run-vision.ps1` instead). |
| `VISION_LLAMA_MODEL` | *(required)* | Absolute path to the VLM GGUF. Use `{env:HOMELAB_ROOT}/opencode-vision/...`. |
| `VISION_LLAMA_MMPROJ` | *(required)* | Absolute path to the mmproj GGUF. |
| `VISION_LLAMA_ARGS_FILE` | `$HOMELAB_ROOT/opencode-vision/vision-server/vision-args.txt` | Extra llama-server flags, one per line. Banned: `--spec-type`, `--context-shift`. |
| `VISION_LLAMA_IDLE_TIMEOUT` | `300` | Seconds of inactivity before auto-shutdown. `0` = stay alive until OpenCode exits. |
| `VISION_LLAMA_STARTUP_TIMEOUT` | `60` | Seconds the MCP waits for `/v1/models` to respond after spawn. |
| `VISION_LLAMA_VISIBLE_CONSOLE` | `1` | `0` to spawn hidden and log to `%TEMP%\opencode-vision\llama-server.log` instead of a visible window. |
| `VISION_LLAMA_EXE` | — | Override `llama-server.exe` discovery (else: PATH, then `$LLAMACPP_ROOT`). |

## Tuning (`vision-args.txt`)

One flag per line. Banned flags (`--spec-type`, `--context-shift`) will refuse to spawn
with a clear error. Typical knobs: `--n-gpu-layers`, `--no-mmproj-offload`, `-fa`, `-c`,
`-b`, `-ub`, `--mlock`, `--no-mmap`. See PLAN.md for the tuning history we settled on.

## Verifying

- `curl http://127.0.0.1:8083/v1/models` should return JSON when the server is up
  (during an active OpenCode vision session, or when `run-vision.ps1` is running).
- The MCP writes a spawn banner + the launch command to
  `%TEMP%\opencode-vision\llama-server.log` on every spawn.
- `python ../tests/test_vision.py <some-image.png>` validates the backend end-to-end;
  it'll trigger the MCP's lazy spawn if no server is up yet.

## Promotion to the deploy stack (Phase 6)

See `../docs/PLAN.md`. Summary: the deploy script downloads the VLM into
`$HOMELAB_ROOT/opencode-vision/`, drops `--mmproj` from the `:8081` `llama-args.txt`,
and lets the MCP handle the rest — no extra service or autostart needed.
