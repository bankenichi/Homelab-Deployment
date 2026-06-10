# PLAN — implementation checklist & testing criteria

> **STATUS (2026-05): integrated.** The plugin (`plugins/opencode-vision.ts`, loaded raw —
> **no build**), the `vision` MCP server, and the configs are placed in
> `opencode and skills/opencode/` and wired in `opencode.json`. `Deploy-Homelab.ps1` now
> downloads the VLM (§11), installs the MCP + plugin deps (§8b, §9), and validates structure
> (§14). The backend is a **dedicated `:8083`** server lazily spawned by `vision_mcp.py`
> (NOT the `:8081` reuse this plan was originally written against). Phase 2's "vendor + build
> + symlink the dist" steps are **superseded** — Bun runs the `.ts` directly from `plugins/`.
> Where this plan says `:8081` as the vision backend, read `:8083`. The T0–T5 matrix below
> remains the acceptance suite; re-run it after a clean deploy.

Authoritative, ordered build plan. Execute phases in order; do not start a phase until the
previous phase's exit test passes. Checkboxes are for the downstream coder.

**Environments:** all work targets the **local PC** first. The "Promote to homelab" phase is
last and is intentionally deferred until the local E2E test passes.

**Conventions:** paths inside this repo use `{env:HOMELAB_ROOT}` in `opencode.json` (forward
slashes only). The fork lives at `https://github.com/bankenichi/opencode-vision`.

---

## Phase 0 — Configure the vision backend (do NOT skip)

> ⚠️ **Do NOT point vision at the `:8081` coding server.** It runs `--spec-type mtp`, and MTP
> speculative decoding is incompatible with multimodal image input — it crashes with
> `find_slot: non-consecutive token position`. Vision runs on a separate `:8083` instance with
> a small VLM and no MTP / no context-shift. Full setup: `../vision-server/README.md`.

> Note: the `:8083` server is **lazy-spawned by `vision_mcp.py`** at first use and auto-shut
> down after idle. You normally do NOT launch it by hand — these Phase 0 steps are about
> *configuration* (so the MCP knows what to spawn). `run-vision.ps1` exists for benchmarking
> / debugging only; the MCP will probe `:8083` and reuse it if it's running.

- [ ] **0.1** Download a small VLM + its mmproj into `$HOMELAB_ROOT/opencode-vision/`.
      Qwen2.5-VL-3B (settled) — see vision-server README for HF commands.
- [ ] **0.2** Set `VISION_LLAMA_MODEL` and `VISION_LLAMA_MMPROJ` in the `vision` MCP
      `environment` block of `opencode.json` to those file paths
      (use `{env:HOMELAB_ROOT}/opencode-vision/<filename>` — forward slashes).
- [ ] **0.3** Sanity-launch once via `vision-server/run-vision.ps1`. Confirm
      `curl http://127.0.0.1:8083/v1/models` returns JSON and the `:8083` startup log
      shows the projector/`mtmd` loading. Then close the window — the MCP will spawn its
      own on demand.
- [ ] **0.4** Smoke-test vision over HTTP with a tiny known image (Test T0).

**Exit test T0 — dedicated server vision works:**
> **Given** a PNG of a red circle on white, base64-inlined in a `/v1/chat/completions`
> `image_url` request to **`:8083`**,
> **when** asked "What shape and color is in this image?",
> **then** the response text contains "circle" and "red" (case-insensitive), and the `:8083`
> log shows **no** `find_slot: non-consecutive token position` errors.

If T0 fails, STOP — nothing downstream can work. Check the VLM/mmproj filenames and that the
launch flags contain no `--spec-type` / `--context-shift`.

---

## Phase 1 — Build the `local_vision` MCP server (`mcp/vision_mcp.py`)

The file is already written in this repo. This phase is about installing deps and validating it
in isolation, before OpenCode is involved.

- [ ] **1.1** `pip install -r mcp/requirements.txt` (use `--break-system-packages` only on
      managed Linux; on the Windows host install normally / into the same Python that runs the
      existing `mcp-server`).
- [ ] **1.2** Confirm the module imports and lists its tool:
      `python -c "import mcp.vision_mcp as v; print(v.server.name)"` → prints `vision`.
- [ ] **1.3** Run the direct-call path of the test harness (Test T1).

**Exit test T1 — MCP tool returns a real description:**
> **Given** `tests/test_vision.py <path-to-screenshot.png>` run with `VISION_API_BASE`
> pointing at `:8081`,
> **when** it calls the `analyze` coroutine directly with the image path,
> **then** it prints a non-empty string that is NOT prefixed `VISION ERROR:` and that
> plausibly describes the image (manual eyeball for the first run).

Negative cases (the harness asserts these too):
- `analyze("C:/does/not/exist.png")` → returns a string starting `VISION ERROR:` (no exception).
- `analyze("/etc/hostname")` (wrong type) → `VISION ERROR:` (no crash).

---

## Phase 2 — Vendor + build the plugin from the fork (`plugin/`)

See `plugin/README.md` for exact commands. Summary:

- [ ] **2.1** `git clone https://github.com/bankenichi/opencode-vision` into `plugin/src-fork`
      (or add as a submodule — see plugin/README for the trade-off).
- [ ] **2.2** `cd plugin/src-fork && npm install && npm run build` → produces `dist/index.js`.
- [ ] **2.3** Symlink the build into OpenCode's plugin dir:
      - Windows (admin or developer mode):
        `New-Item -ItemType SymbolicLink -Path "$env:USERPROFILE\.config\opencode\plugin\opencode-vision.js" -Target "<repo>\opencode-vision\plugin\src-fork\dist\index.js"`
      - If symlinks are blocked, copy the file instead (note: copy must be re-done after each rebuild).

**Exit test T2 — plugin loads:**
> **Given** the symlink/copy in place,
> **when** OpenCode starts,
> **then** the OpenCode log contains `[opencode-vision] Plugin initialized` and a line
> reporting the loaded model patterns.

---

## Phase 3 — Register the MCP server in OpenCode

- [ ] **3.1** Merge `config/opencode.snippet.json` into
      `opencode and skills/opencode/opencode.json` (add the `vision` mcp entry; add/extend the
      top-level `plugin` array only if loading via npm rather than symlink — see snippet notes).
- [ ] **3.2** Keep `VISION_MODEL` in the snippet aligned with the `id` observed in Phase 0.1.
- [ ] **3.3** Restart OpenCode so it spawns the MCP subprocess.

**Exit test T3 — tool is registered:**
> **Given** OpenCode restarted,
> **when** you inspect available tools (start a session and ask the model to "list your tools",
> or check the MCP startup log),
> **then** a tool named `vision_analyze` is present.
>
> ⚠️ **If the name differs** (e.g. `mcp_vision_analyze` or `vision_analyze` is absent but a
> similar one exists), record the actual name and set `imageAnalysisTool` in
> `config/opencode-vision.json` to that exact string. This is the #1 integration gotcha.

---

## Phase 4 — Install plugin config + wire the model pattern

- [ ] **4.1** Copy `config/opencode-vision.json` to `~/.config/opencode/opencode-vision.json`
      (user-level) OR to `<project>/.opencode/opencode-vision.json` (project-level, higher
      precedence). For the homelab default, user-level is right.
- [ ] **4.2** Confirm `models` includes `llama.cpp/*` (or the exact provider/model in use).
- [ ] **4.3** Confirm `imageAnalysisTool` equals the name verified in T3.
- [ ] **4.4** Restart OpenCode; confirm the log line `Loaded models from user config: llama.cpp/*`.

---

## Phase 5 — End-to-end test in OpenCode

**Exit test T5 — the full paste-and-ask loop (the acceptance test):**
> **Given** OpenCode with the local model selected, the plugin loaded, and the `vision`
> MCP registered,
> **when** the user pastes a screenshot containing readable text/UI and asks
> "What does this screenshot show?",
> **then** in order:
> 1. log: `Model matched, checking for images...`
> 2. log: `Found images in message, processing...`
> 3. log: `Saved 1 image(s), transforming message...`
> 4. the model issues a `vision_analyze` tool call with the temp file path,
> 5. the tool returns a description, and
> 6. the model's final answer references the actual content of the screenshot.

Supplementary checks:
- [ ] **5.1 Multi-image:** paste 2 images in one message → injected prompt lists both;
      model calls the tool for each; both are described.
- [ ] **5.2 Temp cleanup:** note the temp file path from the log; after the session goes idle,
      confirm the file under `%TEMP%/opencode-vision/` is deleted.
- [ ] **5.3 Non-matching model (negative):** temporarily switch to a different (non-matched)
      model, paste an image → plugin does NOT transform (no "Model matched" log). Confirms the
      pattern gate works and the plugin is inert for models that don't need it.
- [ ] **5.4 Unsupported format (negative):** paste/attach a GIF or BMP → plugin ignores it
      (only PNG/JPEG/WebP are processed); no crash.

---

## Phase 6 — Promote to homelab (deferred until T5 passes)

- [ ] **6.1** Add fork clone + `npm install && npm run build` + symlink to `Deploy-Homelab.ps1`
      (near §11, after llamacpp + models). Use `$env:HOMELAB_ROOT` style paths.
- [ ] **6.2** Add the `vision` mcp entry + plugin load to the committed
      `opencode and skills/opencode/opencode.json` and its `.jsonc` twin.
- [ ] **6.3** Ship `opencode-vision.json` into the symlinked `~/.config/opencode/` via the
      deploy script's config-symlink step.
- [ ] **6.4** Document the new `VISION_*` env knobs in the top-level homelab `README.md` and
      `llama/README.md` ("Homelab integration gaps" → now closed).
- [ ] **6.5** Re-run T0–T5 on a clean deploy to confirm reproducibility.

---

## Testing criteria summary (input → expected output)

| ID | Input | Expected output |
| --- | --- | --- |
| T0 | base64 red-circle PNG → `:8081` `image_url` request, "what shape/color?" | text contains "circle" and "red" |
| T1 | `analyze("<real screenshot>.png")` | non-empty, non-`VISION ERROR:` description |
| T1-neg | `analyze("C:/does/not/exist.png")` | string starting `VISION ERROR:`, no exception |
| T2 | OpenCode start with plugin symlinked | log: `[opencode-vision] Plugin initialized` |
| T3 | OpenCode MCP startup | tool `vision_analyze` registered (verify exact name) |
| T5 | paste screenshot + "what does this show?" | logs 1–6 fire; answer references real content |
| 5.2 | session goes idle | temp image file deleted |
| 5.3 | non-matched model + image | no transform; plugin inert |

## Open risks / watch-items

- **Tool-name prefix drift** (T3) — the single most likely thing to break wiring.
- **Queue contention** — a big image analysis briefly stalls coding generation on the shared
  `:8081` server. Mitigation path documented in ARCHITECTURE §7 (point `VISION_API_BASE` at a
  second instance, no code change).
- **mmproj/GGUF version skew** — if the mmproj projector and main GGUF are from mismatched
  builds, T0 fails with garbled output. Keep both pinned together in `Deploy-Homelab.ps1`.
- **Symlink permissions on Windows** — may need Developer Mode; fallback is copy-after-build.
