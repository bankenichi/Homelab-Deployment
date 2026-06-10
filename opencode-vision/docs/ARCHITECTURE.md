# ARCHITECTURE — opencode-vision homelab integration

> **CURRENT IMPLEMENTATION (2026-05): dedicated `:8083` vision server, lazily spawned by
> `vision_mcp.py`.** The §2 problem statement and the original diagram below describe the
> first design (reuse the `:8081` coding server). That approach was abandoned because the
> `:8081` server runs `--spec-type mtp`, which is incompatible with multimodal input. The
> authoritative current design is the **§7 decision log** ("Dedicated vision server on
> `:8083`" + "MCP-managed lazy lifecycle"). Where this doc says `:8081` as the vision
> backend, read `:8083`. The plugin is loaded as raw `.ts` (Bun) — **no build step**.

## 1. Problem statement

`llama-server.exe` is launched by `Deploy-Homelab.ps1` with both the main GGUF **and**
`--mmproj mmproj.gguf`. That makes the server multimodal at the HTTP layer: its
OpenAI-compatible `/v1/chat/completions` endpoint accepts `image_url` content parts and
runs them through the mmproj projector.

OpenCode, however, talks to that server through the `@ai-sdk/openai-compatible` provider,
and **that provider has no signal that the model supports image input**. As a result
OpenCode discards/ignores pasted images and never emits an `image_url` part. The capability
exists on the server but there is no path from the editor to it. (This is exactly the
"OpenCode isn't using the vision model at all" symptom — the model *can* see; nothing is
*feeding* it images.)

## 2. Chosen design

**Plugin + standalone `local_vision` MCP server, both pointed at the existing `:8081`
server.** (Decision log in §7.)

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ OpenCode (TUI)                                                                │
│                                                                               │
│   user pastes image + types question                                          │
│            │                                                                  │
│            ▼                                                                  │
│   message.parts = [ FilePart(image/png), TextPart("why does this fail?") ]    │
│            │                                                                  │
│   ┌────────┴─────────────────────────────────────────────┐                   │
│   │ opencode-vision plugin                                │                   │
│   │  hook: experimental.chat.messages.transform          │                   │
│   │  1. model matches configured pattern?  (llama.cpp/*)  │                   │
│   │  2. save FilePart bytes → %TEMP%/opencode-vision/x.png│                   │
│   │  3. remove the FilePart (text model can't read it)    │                   │
│   │  4. rewrite TextPart →                                │                   │
│   │     "image saved at <path>. Use `vision_analyze`.     │                   │
│   │      User's request: why does this fail?"             │                   │
│   └────────┬──────────────────────────────────────────────┘                  │
│            ▼                                                                  │
│   model (Qwen3.6) reads instruction, decides to call tool                     │
│            │  tool call: vision_analyze(image_path="<path>", question=...)    │
└────────────┼──────────────────────────────────────────────────────────────────┘
             ▼
   ┌───────────────────────────────────────────────────────────┐
   │ local_vision MCP server  (mcp/vision_mcp.py, FastMCP)       │
   │  - read file at image_path                                  │
   │  - base64-encode → data: URI (or pass http(s) URL through)  │
   │  - POST /v1/chat/completions with image_url part            │
   └───────────────┬─────────────────────────────────────────────┘
                   ▼
        ┌──────────────────────────────────────────┐
        │ llama-server :8081  (main GGUF + mmproj)   │
        │  runs image through mmproj projector       │
        │  returns textual description               │
        └───────────────┬────────────────────────────┘
                        ▼
   MCP tool returns the description string → model continues the
   conversation with text only → answers the user.

On `session.idle`: plugin deletes the temp images it created this session.
```

The crucial property: **two independent calls to the same server**. The coding model's
agent loop is one chat-completions stream; `vision_analyze` makes a *separate, stateless*
chat-completions call carrying the image and returns only text. The agent loop never holds
raw image bytes, so image tokens don't pollute the long coding context.

## 3. Components

| Component | Path | Language | Ownership | Role |
| --- | --- | --- | --- | --- |
| opencode-vision plugin | `plugin/` (vendored) | TypeScript | fork: `bankenichi/opencode-vision` | Intercept image, save, rewrite message to invoke the vision tool |
| local_vision MCP server | `mcp/vision_mcp.py` | Python (FastMCP) | ours (new) | Expose `vision_analyze`; forward image to `:8081`; return text |
| Plugin config | `config/opencode-vision.json` | JSON | ours | Which models trigger the plugin; which tool name to call |
| OpenCode config additions | `config/opencode.snippet.json` | JSON | ours | Register the MCP server + load the plugin |
| Test harness | `tests/test_vision.py` | Python | ours | Validate the `:8081` endpoint and the MCP tool against a known image |
| Vision backend | `llama-server :8081` | — | existing | The mmproj-equipped model that actually does the seeing |

## 4. Data flow detail & schemas

### 4.1 Plugin trigger (model match)

The plugin's `experimental.chat.messages.transform` hook reads
`message.info.model = { providerID, modelID }`. For this stack:

- `providerID = "llama.cpp"` (the provider key in `opencode.json`)
- `modelID   = "Qwen3.6-35B-A3B-Claude-4.7-Opus-Reasoning-Distilled-APEX-MTP-I-Compact"`

Pattern `"llama.cpp/*"` matches (`providerPattern="llama.cpp"` == providerID, `modelPattern="*"`
matches any modelID). See §6 for the full pattern grammar.

### 4.2 Plugin config schema — `opencode-vision.json`

Resolution order (project beats user beats built-in default):
`./.opencode/opencode-vision.json` → `~/.config/opencode/opencode-vision.json` → defaults.

```jsonc
{
  // Glob patterns. A model triggers the plugin if it matches ANY pattern.
  "models": ["llama.cpp/*"],            // string[]  (default: ["*/qwen3-coder-next-mlx"])

  // The EXACT tool name OpenCode registers for our MCP server's tool.
  // OpenCode registers MCP tools as "<mcp-config-key>_<tool-fn-name>".
  // We name the mcp key "vision" and the tool fn "analyze" → "vision_analyze".
  "imageAnalysisTool": "vision_analyze", // string   (default: "local_vision")

  // Optional. Overrides the injected instruction prompt. Must contain at least
  // one of: {imageList} {imageCount} {toolName} {userText}. Omit to use default.
  "promptTemplate": "..."                // string?  (default: built-in template)
}
```

### 4.3 MCP tool I/O schema — `vision_analyze`

```jsonc
// INPUT (arguments the model supplies)
{
  "image_path": "C:\\Users\\...\\Temp\\opencode-vision\\<uuid>.png",  // required; local path OR http(s) URL
  "question":   "Why does this build fail?"                            // optional; default: "Describe this image in detail."
}

// OUTPUT
"string"   // the vision model's textual answer, or a clearly-prefixed error string
           // (e.g. "VISION ERROR: file not found: <path>")
```

Behavior rules:
- `image_path` starting with `http://`/`https://` is passed straight through as the
  `image_url.url` (the server fetches it). Otherwise the file is read and inlined as a
  base64 `data:` URI.
- Allowed extensions/MIME: `.png`→image/png, `.jpg`/`.jpeg`→image/jpeg, `.webp`→image/webp.
  Unknown extension → MIME guessed via `mimetypes`, fallback `image/png`.
- Errors never raise; they return a string starting `VISION ERROR:` so the calling model
  can react gracefully.

### 4.4 Upstream request to `:8081` — chat-completions body

```jsonc
{
  "model": "<VISION_MODEL env>",          // must match what /v1/models advertises
  "messages": [
    { "role": "user", "content": [
      { "type": "text",      "text": "<question>" },
      { "type": "image_url", "image_url": { "url": "data:image/png;base64,<...>" } }
    ]}
  ],
  "max_tokens": 1024,                      // VISION_MAX_TOKENS
  "temperature": 0.2,
  "stream": false
}
```

Response (standard OpenAI shape) — we read `choices[0].message.content`.

### 4.5 MCP server configuration (env vars)

Authoritative values live in the `vision` mcp `environment` block of
`opencode and skills/opencode/opencode.json`. Current set:

| Env var | Value (opencode.json) | Purpose |
| --- | --- | --- |
| `VISION_API_BASE` | `http://127.0.0.1:8083/v1` | Base URL of the **dedicated** vision server |
| `VISION_MODEL` | `vision-vlm` | Model id sent in the request (matches `--alias` in vision-args.txt) |
| `VISION_MAX_TOKENS` | `1024` | Cap on the description length |
| `VISION_TIMEOUT` | `180` | Per-request timeout (seconds) |
| `VISION_SPAWN_SERVER` | `1` | `0` = don't spawn; use an externally-run `:8083` |
| `VISION_LLAMA_MODEL` | `{env:HOMELAB_ROOT}/opencode-vision/CyberNeurova-…Q4_K_M.gguf` | VLM GGUF to spawn |
| `VISION_LLAMA_MMPROJ` | `…CyberNeurova-…mmproj-Q8_0.gguf` | mmproj GGUF to spawn |
| `VISION_LLAMA_ARGS_FILE` | `…/opencode-vision/vision-server/vision-args.txt` | Extra llama-server flags (no `--spec-type`/`--context-shift`) |
| `VISION_LLAMA_IDLE_TIMEOUT` | `300` | Idle seconds before auto-shutdown (`0` = stay alive) |
| `VISION_LLAMA_STARTUP_TIMEOUT` | `60` | Seconds to wait for `/v1/models` after spawn |
| `VISION_LLAMA_VISIBLE_CONSOLE` | `1` | `0` = spawn hidden, log to `%TEMP%/opencode-vision/llama-server.log` |

`VISION_API_KEY` (default `sk-no-key-required`) is still accepted and forwarded as a bearer
token; llama-server ignores it.

## 5. Tool-name registration (the load-bearing detail)

Per OpenCode MCP docs: *"MCP server tools are registered with server name as prefix"* —
i.e. `"<mcp-key>_<tool>"`. We therefore get a clean, predictable name:

- `opencode.json` mcp key: **`vision`**
- FastMCP tool function name: **`analyze`**
- ⇒ registered tool name: **`vision_analyze`**
- ⇒ `opencode-vision.json` → `"imageAnalysisTool": "vision_analyze"`

⚠️ Prefixing has differed across OpenCode versions (some builds prepend `mcp_`, the upstream
plugin's default is the bare `local_vision`). **Always confirm empirically** — see PLAN.md
Phase 4, step "verify registered tool name". If OpenCode lists it as something else, update
only `imageAnalysisTool` to match; nothing else changes.

## 6. Plugin model-pattern grammar (reference)

`pattern` may contain a `/` to separate provider from model, or not (then it matches against
either). Each side supports wildcards:

| Pattern | Matches |
| --- | --- |
| `*` | every model |
| `llama.cpp/*` | every model from provider `llama.cpp` |
| `*/Qwen3.6-...-Compact` | that exact model from any provider |
| `*compact*` | any provider/model containing `compact` (case-insensitive) |

Wildcard rules: `*` = all, `prefix*` = starts-with, `*suffix` = ends-with, `*mid*` = contains.

## 7. Decision log

- **Plugin + MCP over native passthrough.** Native (marking the model vision-capable so
  OpenCode sends `image_url` directly) is simpler but (a) depends on `@ai-sdk/openai-compatible`
  honoring an attachment capability flag for a custom local model, and (b) injects image
  tokens into the main coding context. The plugin path keeps vision as a discrete tool call
  returning text, which suits a long-running reasoning/coding agent. Native remains a
  documented fallback.
- **Dedicated vision server on `:8083` (revised 2026-05-27).** Originally we reused `:8081`
  since mmproj was already loaded there. **This does not work:** the `:8081` coding server runs
  `--spec-type mtp` (Multi-Token-Prediction speculative decoding), and MTP is **incompatible
  with multimodal image input** in llama.cpp — inserting an image batch breaks slot/position
  tracking and crashes the server (`find_slot: non-consecutive token position … 512 new
  tokens`). The image *does* reach the server and the mmproj projector *does* encode it, so the
  plugin+MCP pipeline is correct; the failure is purely the MTP/`mtmd` collision. Resolution: a
  **separate llama-server instance on `:8083`** running a small dedicated VLM (e.g.
  Qwen2.5-VL-7B-Instruct + mmproj) with **no `--spec-type` and no `--context-shift`**. The 35B
  coding server keeps MTP for speed; vision is isolated and robust. `VISION_API_BASE` →
  `http://127.0.0.1:8083/v1`. See `vision-server/` for setup. (Port 8083 because 8082 is used
  elsewhere in the stack.) Trade-off: a small amount of extra VRAM for the VLM — acceptable, and
  far cheaper than a second 35B instance.
- **Standalone MCP over extending `mcp-server/mcp_server.py`.** Keeps vision isolated, lets it
  restart/scale independently, and avoids adding `requests`-based image handling into the
  coding-assistant tool surface. Costs one extra `opencode.json` mcp entry.
- **Vendor + build from the `bankenichi/opencode-vision` fork.** Reproducible/offline homelab
  deploys, and we can commit changes (e.g. WebP edge cases, prompt tweaks) without waiting on
  upstream. AGPL-3.0 obligations carry — see AGENTS.md.
- **MCP-managed `:8083` lifecycle, lazy + idle-driven (revised 2026-05-27).** Earlier plan had
  the user launching `run-vision.ps1` manually (mirroring `run-llama`). Replaced with: the
  Python MCP server spawns `llama-server` on first vision call, attaches it to a Windows Job
  Object with `KILL_ON_JOB_CLOSE`, and auto-terminates it after `VISION_LLAMA_IDLE_TIMEOUT`
  seconds of inactivity. Driving requirements: (a) zero resources held at OpenCode startup,
  (b) no orphan processes on hard crash (the Job Object kills the child at OS level the moment
  the MCP handle closes — works where `atexit` doesn't), (c) a visible console window for the
  user to inspect or Ctrl+C, (d) graceful "reuse if already running" so `run-vision.ps1`
  remains a useful manual/debug launcher (the MCP probes `:8083` and reuses any existing
  responder). Cold-start cost: ~5–15 s for a 3B Q4 the first call after each idle stretch.
  Settable via `VISION_LLAMA_IDLE_TIMEOUT=0` (always-on) or `VISION_SPAWN_SERVER=0` (pure
  external-server mode).
- **Path portability throughout the module.** All repo-internal paths use `{env:HOMELAB_ROOT}`
  (in `opencode.json`) or `$env:HOMELAB_ROOT` (in PowerShell), with a script-location fallback
  where reasonable. Matches the existing convention in `opencode and skills/opencode/AGENTS.md`.
  No `C:\Users\<name>\...` anywhere in the vision module.

## 8. Related homelab files

- `Deploy-Homelab.ps1` §11 — downloads `mmproj.gguf` and writes the `--mmproj` flag into
  `llama-args.txt`. Promotion target for this project (clone fork, build plugin, symlink, add
  mcp entry).
- `opencode and skills/opencode/opencode.json` — where the `vision` mcp entry + plugin load go.
- `llama/README.md` — documents the `llama-server` / mmproj runtime layout.
