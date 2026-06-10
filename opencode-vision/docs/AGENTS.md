# AGENTS.md — guidance for downstream agentic coders

You are implementing the plan in `docs/PLAN.md`. Read `docs/ARCHITECTURE.md` first for the
why. This file is the rulebook: conventions, gotchas, and do/don't.

## What this project is

A two-part bridge that gives the local OpenCode coding model vision: a vendored TypeScript
**plugin** (`bankenichi/opencode-vision` fork) that rewrites image-bearing messages, plus a
**Python MCP server** (`mcp/vision_mcp.py`) that forwards images to the existing
mmproj-equipped `llama-server` on `:8081`. The plugin is upstream code we vendor; the MCP
server and configs are ours.

## Golden rules

1. **Phase 0 is mandatory.** Never build on the assumption that vision works. Prove T0
   (server describes a known image over HTTP) before touching anything else. The original
   symptom that started this project was "OpenCode isn't using the vision model" — confirm
   whether that's a wiring gap (this project fixes it) or a server-side mmproj load failure
   (this project can't fix that; fix `llama-args.txt`).
2. **The tool name is load-bearing and version-sensitive.** OpenCode registers MCP tools as
   `<mcp-key>_<tool-fn>` → `vision_analyze` here, but some builds prepend `mcp_`. Verify the
   real name (PLAN T3) and set `imageAnalysisTool` in `config/opencode-vision.json` to match.
   If vision "silently does nothing", check this first.
3. **Don't hard-code host paths.** In `opencode.json`, repo-internal paths use
   `{env:HOMELAB_ROOT}/opencode-vision/...` with forward slashes. External paths
   (`C:/Program Files/llamacpp/...`) may be absolute. This matches the existing convention in
   `opencode and skills/opencode/AGENTS.md`.
4. **The MCP tool must never raise.** `vision_analyze` returns a `str` always — success text
   or a string starting `VISION ERROR:`. A raised exception surfaces as an opaque tool
   failure to the model; a prefixed string lets the model recover or report cleanly.
5. **Keep `VISION_MODEL` aligned with `/v1/models`.** If the GGUF filename / advertised model
   id changes in `Deploy-Homelab.ps1`, update the `VISION_MODEL` env in the mcp entry too.
   Same discipline as the existing note in `llama/README.md`.
6. **Local PC first.** Do not edit `Deploy-Homelab.ps1` until PLAN Phase 5 (E2E) passes.
   Phase 6 is the only phase that touches the deploy script.

## File ownership & edit boundaries

| File | Edit freely? | Notes |
| --- | --- | --- |
| `mcp/vision_mcp.py` | ✅ ours | The novel logic. Keep tool fn named `analyze` unless you also update the mcp key / `imageAnalysisTool`. |
| `config/*.json` | ✅ ours | Keep JSON valid; `opencode.snippet.json` is a *merge source*, not a drop-in replacement. |
| `tests/test_vision.py` | ✅ ours | Extend with more cases as needed. |
| `plugin/src-fork/**` | ⚠️ via the fork | This is the AGPL upstream. Make changes as commits to `bankenichi/opencode-vision`, then re-vendor. Don't fork-in-place without committing upstream — you'll lose changes on re-clone. |
| `docs/**` | ✅ keep in sync | If you change behavior, update ARCHITECTURE + PLAN. |

## License obligation (AGPL-3.0)

The plugin is AGPL-3.0. The fork inherits it. If you distribute the plugin (including over a
network in some interpretations), you must offer the corresponding source. Practically for a
private homelab: keep the fork public (it already is), retain the `LICENSE` file, and don't
strip attribution. Our Python MCP server and configs are separate works and are not forced
under AGPL by merely calling the plugin's tool over MCP — but keep them in clearly separate
files (they already are) to avoid ambiguity.

## Conventions to match (from the existing repo)

- Python MCP servers use `from mcp.server.fastmcp import FastMCP`, `server = FastMCP("name")`,
  `@server.tool()` async functions returning `str`, and run via
  `python {env:HOMELAB_ROOT}/.../x.py` from an `opencode.json` mcp entry. `vision_mcp.py`
  follows this exactly (see `mcp-server/mcp_server.py` for the reference style).
- MCP entries in `opencode.json` use `"type": "local"`, a `command` array, and `"enabled": true`.
- Config is portable via `{env:HOMELAB_ROOT}`; the deploy script sets it machine-scope.

## Common failure modes → first thing to check

| Symptom | Check |
| --- | --- |
| Plugin never fires (no "Model matched" log) | `models` pattern vs actual `providerID/modelID`; provider key is `llama.cpp` |
| Model gets the instruction but calls nothing / wrong tool | `imageAnalysisTool` ≠ registered tool name (T3) |
| Tool call returns `VISION ERROR: VISION_LLAMA_MODEL not set or not found` | The MCP couldn't find the GGUF the spawn needs. Verify `{env:HOMELAB_ROOT}` substitution worked in the env block, and the file exists at that path. |
| Tool returns `VISION ERROR: llama-server did not become ready within …` | First-spawn cold start exceeded `VISION_LLAMA_STARTUP_TIMEOUT`. Check `%TEMP%/opencode-vision/llama-server.log` — usually a model load error. |
| Tool returns `VISION ERROR: connection refused` *after working before* | Spawned server crashed; MCP will respawn on next call. Inspect llama-server.log for the cause. |
| Tool call returns `VISION ERROR: file not found` (image path) | Image temp dir / path escaping; the plugin saves to `%TEMP%/opencode-vision/`. Distinct from the *model* not-found error above. |
| Coding feels stalled during image analysis | VRAM contention between the 35B and the spawned VLM. Either drop the VLM to CPU (`--n-gpu-layers 0 --no-mmproj-offload` + `CUDA_VISIBLE_DEVICES=-1`) or use a smaller VLM. |
| `:8083` keeps an orphan llama-server running after OpenCode hard-crashes | Job Object attach failed at spawn time (look for `WARN: could not attach pid … to job` in MCP stderr / OpenCode log). Kill via Task Manager and report — should not happen in normal flow. |
| Description is garbled/irrelevant | mmproj/GGUF version skew, or wrong `VISION_MODEL` id |

## Definition of done

All of T0–T5 in PLAN.md pass on the local PC, including the negative cases (T1-neg, 5.3, 5.4).
Only then proceed to Phase 6 (homelab promotion) and re-run the suite on a clean deploy.
