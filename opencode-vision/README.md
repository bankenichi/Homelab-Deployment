# opencode-vision (homelab integration)

Give the local OpenCode coding model **vision** by routing pasted images through a
**dedicated** vision `llama-server` on `:8083`, which the vision MCP server spawns on
demand and shuts down when idle.

This folder is **self-contained** and integrated into `Deploy-Homelab.ps1` (model
download in §11, Python deps in §8b, OpenCode wiring via the symlinked config in §9).

## The one-paragraph version

OpenCode's `@ai-sdk/openai-compatible` provider has **no signal** that our local coding
model can accept images, so it never sends attachments. We close that gap with two pieces:

1. **`opencode-vision` plugin** (`plugins/opencode-vision.ts`, vendored from the
   `bankenichi/opencode-vision` fork) — intercepts a pasted image, saves it to a temp file,
   strips the raw image part the text model can't consume, and rewrites the message to tell
   the model: *"the image is at `<path>`, call the `vision_analyze` tool."* OpenCode runs on
   Bun, which executes the TypeScript directly, so there is **no build step**.
2. **`vision` MCP server** (`mcp/vision_mcp.py`, ours) — exposes the `vision_analyze` tool.
   On the first call it **lazily spawns** a dedicated `llama-server` on `:8083` running a
   small VLM (Qwen2.5-VL-3B), base64-encodes the image, POSTs it as an `image_url`, and
   returns the model's textual description. The server idles out after
   `VISION_LLAMA_IDLE_TIMEOUT` seconds and is reaped by a Windows Job Object on OpenCode exit.

The coding model's main context never sees raw image bytes — only the returned text.

### Why a separate `:8083` server (not the `:8081` coding server)
The `:8081` coding server runs `--spec-type mtp` (Multi-Token-Prediction). **MTP is
incompatible with multimodal input** in llama.cpp — feeding an image crashes it with
`find_slot: non-consecutive token position`. So vision runs on an isolated `:8083`
instance with a small VLM and **no `--spec-type` / no `--context-shift`**. (The earlier
"reuse :8081" design is obsolete; see ARCHITECTURE §7 decision log.)

## Folder map

```
opencode-vision/
├── README.md                  ← you are here
├── CyberNeurova-Qwen2.5-VL-3B-Instruct-abliterated.Q4_K_M.gguf      ← VLM weights (gitignored)
├── CyberNeurova-Qwen2.5-VL-3B-Instruct-abliterated.mmproj-Q8_0.gguf ← mmproj   (gitignored)
├── docs/
│   ├── ARCHITECTURE.md        ← components, data flow, schemas, design rationale
│   ├── PLAN.md                ← phased implementation checklist + testing criteria
│   └── AGENTS.md              ← rules + gotchas for downstream agentic coders
├── plugin/
│   └── README.md              ← how the .ts plugin is vendored (no build needed)
├── mcp/
│   ├── vision_mcp.py          ← the vision MCP server: spawns :8083, forwards images
│   └── requirements.txt       ← mcp>=1.0, requests>=2.34
├── config/
│   ├── opencode-vision.json   ← plugin config (models + imageAnalysisTool)
│   └── opencode.snippet.json  ← the `vision` mcp entry merged into opencode.json
├── vision-server/
│   ├── run-vision.ps1         ← manual/debug launcher (the MCP reuses it if already up)
│   └── vision-args.txt        ← :8083 llama-server flags (CPU-only; no MTP/context-shift)
└── tests/
    └── test_vision.py         ← validates the :8083 endpoint and the MCP tool
```

## Runtime layout

```
OpenCode (Bun)
 ├─ loads plugins/opencode-vision.ts        (rewrites image messages)
 └─ spawns mcp/vision_mcp.py  (MCP "vision") ──first vision_analyze call──┐
                                                                          ▼
                                          lazily spawns llama-server :8083 (Qwen2.5-VL-3B,
                                          CPU-only via vision-args.txt) → describes image →
                                          idles out after 300s → Job Object reaps on exit
```

## Status

**Live.** Plugin, MCP server, and config are placed in `opencode and skills/opencode/`
and wired in `opencode.json`. The deploy script downloads the VLM and installs deps. The
upstream plugin is AGPL-3.0 — see `docs/AGENTS.md` for attribution obligations.

## Verifying

- `curl http://127.0.0.1:8083/v1/models` returns JSON while a vision session is active.
- Spawn banner + launch command are logged to `%TEMP%/opencode-vision/llama-server.log`.
- `python tests/test_vision.py <image.png>` exercises the backend end-to-end (triggers the
  MCP's lazy spawn if nothing is up). See `docs/PLAN.md` for the full T0–T5 test matrix.
