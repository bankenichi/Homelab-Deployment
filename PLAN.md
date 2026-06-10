# Homelab Integration — Status & Final Architecture (VPN + Vision)

**Status date:** 2026-05-29
**Status: COMPLETE.** Both the VPN/WireGuard egress and the vision stack are fully
implemented, wired into `Deploy-Homelab.ps1`, and verified working (OpenCode launches,
vision round-trips, SearXNG searches with and without VPN). This file is now a record of
the **final** design and how to verify it — not a roadmap of pending work. Where this
conflicts with older notes, this file and the per-component docs win.

---

## 0. The stack (current)

Windows + Docker Desktop + WSL2, deployed by `Deploy-Homelab.ps1`.

| Component | Where | Notes |
|-----------|-------|-------|
| Caddy reverse proxy | container | `http://find` → SearXNG, `http://draw` → Excalidraw |
| SearXNG | container(s) | base = no VPN; optional VPN variant (see §1) |
| Excalidraw | container | whiteboard on `:5000` |
| llama.cpp coding server | native `llama-server.exe` :8081 | `run-llama`; MTP speculative decoding |
| OpenCode CLI | native (Bun) | config symlinked from `opencode and skills/opencode` |
| Coding-assistant MCP | python | `mcp-server/mcp_server.py` |
| Proton Suite MCP | node/python | `proton-mcp/` |
| Vision MCP + VLM | python + native :8083 | `opencode-vision/` (see §2) |

---

## 1. VPN/WireGuard egress for SearXNG — DONE (opt-in)

**Decision (final): VPN is OPT-IN, not always-on.** Bare-metal repos never ship a
`vpn.env`, so the default must work without it. SearXNG runs VPN-free by default and
degrades gracefully — it never hard-depends on a Proton key.

- `searxng/docker-compose.yml` — **base, no VPN**. Port published on `core`; services:
  `core`, `logo-rotator`. This is what the deploy script boots.
- `searxng/docker-compose.vpn.yml` — **complete VPN variant**: `gluetun` (Proton
  WireGuard, `SERVER_COUNTRIES=United States`, **`WIREGUARD_MTU: 1320`** — required on
  WSL2 or the tunnel times out) + `core`/routed-through-tunnel + an hourly `vpn-rotator`
  sidecar that cycles the US exit IP.
- `Enable-SearxngVpn.ps1` (repo root) — opt-in helper: writes the gitignored `vpn.env`
  from a Proton key/`.conf`, switches base→VPN; `-Disable` reverts.
- **Valkey was removed** — it only backed the limiter (public-instance bot protection)
  and provides no caching benefit. Re-enable instructions are in `VPN-EGRESS.md`.
- Deploy `§15` boots the base stack and includes a **VPN-aware re-run guard**: if the VPN
  variant is already active, a redeploy re-ups `docker-compose.vpn.yml` instead of
  clobbering it with the base file.

**Verify:** `docker ps` shows `searxng-core` (base) or `searxng-gluetun` healthy +
`searxng-core` + `searxng-vpn-rotator` (VPN). With VPN on,
`docker exec searxng-core wget -qO- https://ipinfo.io/ip` → a US IP, and Google image
search reliability > 0. Full design/troubleshooting: `searxng/VPN-EGRESS.md`.

---

## 2. Vision stack — DONE (integrated)

The local coding model gets vision via two pieces, both wired in and deployed:

- **Plugin** `opencode and skills/opencode/plugins/opencode-vision.ts` — vendored from the
  `bankenichi/opencode-vision` fork, loaded **raw by Bun (no build step)**. Rewrites
  image messages to call `vision_analyze`.
- **`vision` MCP** `opencode-vision/mcp/vision_mcp.py` — registered in `opencode.json`.
  On first use it **lazily spawns a dedicated `llama-server` on :8083** (Qwen2.5-VL-3B,
  CPU-only per `vision-server/vision-args.txt`), idles it out after
  `VISION_LLAMA_IDLE_TIMEOUT` (300s), and the OS reaps it via a Windows Job Object.
  **No autostart, no scheduled task, no `run-vision` PATH command** — the MCP owns the
  lifecycle. `run-vision.ps1` remains a manual/debug launcher (the MCP reuses :8083 if
  it's already responding — handy for performance tuning with live console output).
- **Why a dedicated :8083 server** (not the :8081 coding server): :8081 runs
  `--spec-type mtp`, which is incompatible with multimodal input in llama.cpp.

**Canonical model (settled):** `mradermacher/CyberNeurova-Qwen2.5-VL-3B-Instruct-abliterated-GGUF`
→ `...Q4_K_M.gguf` + `...mmproj-Q8_0.gguf`. The 7B / f16 files are alternatives, not used.

**Deploy integration (`Deploy-Homelab.ps1`):**
- `§8b` installs `opencode-vision/mcp/requirements.txt` (non-fatal — see §3).
- `§9` symlinks the whole `opencode` config dir (plugin + `opencode-vision.json` +
  `opencode.json` ride along) and `npm install`s plugin deps (non-fatal).
- `§11` downloads the VLM + mmproj into `opencode-vision/` (idempotent, non-fatal).
- `§14` structure-checks `opencode-vision/mcp/vision_mcp.py`.

**Verify:** in OpenCode on a `llama.cpp/*` model, paste an image → plugin logs
`Model matched` → model calls `vision_analyze` → :8083 returns a description.
`curl http://127.0.0.1:8083/v1/models` returns JSON while a session is active.

---

## 3. Cross-cutting deploy hardening — DONE

- **Python 3.12** for fresh installs (broadest wheel support for textract/pdfplumber/etc.);
  an existing 3.10+ is left as-is (3.13/3.14 gets a heads-up warning).
- **MCP/plugin/VLM installs are non-fatal** — failures are collected and printed as a
  **"DEPENDENCY ISSUES"** section at the end with the exact manual fix command, so one
  bad package can't abort the whole deploy. The core stack still hard-fails on real errors.
- **`huggingface_hub[cli,hf_xet]`** for fast xet downloads.
- **`.gitignore`** uses patterns (`*.gguf`, `*.conf`, `searxng/vpn.env`).
- **DuckDuckGo dep** futureproofed (`ddgs` with a `duckduckgo_search` fallback import).

---

## 4. Gotchas / lessons (don't regress these)

- **Never put `tsconfig.json` or `@types/*` in the runtime OpenCode config dir**
  (`opencode and skills/opencode/`, symlinked to `~/.config/opencode`). Bun reads any
  tsconfig there when loading the `.ts` plugin and it can break OpenCode startup
  (config/provider/agent load failures). Type-check in the fork instead.
- **WSL2 + WireGuard:** pin `WIREGUARD_MTU: 1320`. PMTUD is broken there; the tunnel
  connects but larger packets (TLS, DNS, healthcheck dials) silently time out → gluetun
  restart-loops as "unhealthy".
- **Two compose files share `name: searxng`:** tear down with the same file you brought
  up with (or the VPN superset), else orphaned `gluetun`/`vpn-rotator` hold the network.
- **Visible vision console:** on Windows, do not set any std handle when spawning with
  `CREATE_NEW_CONSOLE` (it forces `STARTF_USESTDHANDLES` and routes output to the MCP
  pipe, blanking the window). Fixed in `vision_mcp.py`.

## 5. Global done criteria (all met)
- `docker ps` → caddy, searxng-core, excalidraw, logo-rotator (base). VPN adds
  searxng-gluetun (healthy) + searxng-vpn-rotator.
- SearXNG image search works; with VPN, no Google 403.
- OpenCode launches clean; vision round-trip works on the local model.
- A fresh `Deploy-Homelab.ps1` reproduces the above (VPN is a separate opt-in step).
- No secrets tracked: `git ls-files` shows only `vpn.env.example`, never `vpn.env`/`*.conf`.
