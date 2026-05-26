# AGENTS.md — Homelab

Top-level guidance for AI agents working anywhere in this homelab stack. Every component below has its own `AGENTS.md` with deeper detail — start here, then descend into the one for the component you're touching.

## What the homelab is

A privacy-first, locally-hosted AI stack. The pieces are designed to compose: a general-purpose coding-assistant MCP server, a Proton suite MCP server, and a curated skill registry that local AI runners (OpenCode, Claude Code, Gemini CLI, Cursor, Copilot CLI) auto-discover. All components run on the same machine the runner runs on — nothing leaves the box unless the user explicitly asks for it.

## Repo layout

```
Homelab/                              — this repo (version-controlled)
├── AGENTS.md                         — this file: top-level orientation
├── Deploy-Homelab.ps1                — Windows bootstrap (deps, clone, Docker, llama)
├── llama/README.md                   — manifest for external llama repos (not submodules)
├── mcp-server/                       — coding-assistant MCP (in-tree here)
├── proton-mcp/                       — Proton Mail / Pass / Drive / VPN MCP server
├── proxy/                            — Caddy reverse proxy (find, draw)
├── searxng/                          — private search (http://find → :8080)
├── excalidraw/                       — whiteboard (http://draw → :5000)
└── opencode and skills/
    ├── AGENTS.md
    ├── opencode/                     — OpenCode provider + MCP wiring (symlinked to ~/.config/opencode)
    └── .agents/                      — skill registry (symlinked to ~/.agents)
```

**External repos** (cloned by `Deploy-Homelab.ps1`, not stored in Homelab):

| Location | Repository |
| --- | --- |
| `$env:LLAMACPP_ROOT` (default `C:\Program Files\llamacpp`) | `bankenichi/llamacpp-turboquant-mtp-executables-for-cuda-12.8` |
| `$env:LLAMACPP_ROOT\llama-config-ui` (submodule) | `bankenichi/llama-config-ui` |

See `llama/README.md` for ports, commands, and how OpenCode + SearXNG connect.

## Components in one line each

| Component | Role | Entry point |
| --- | --- | --- |
| **Deploy-Homelab.ps1** | Bare-metal Windows installer: WSL2, Docker, Git, Node/OpenCode, Python/HF CLI, Homelab pull, llama clone, containers, hosts DNS. Publishes two machine-scope env vars: `HOMELAB_ROOT` (deployed repo path, forward-slashed; referenced by `opencode.json`) and `LLAMACPP_ROOT` (llama.cpp install dir; overridable by pre-setting it before deploy). | run as Administrator |
| **llamacpp (external)** | Local inference: prebuilt `llama-server`, GGUF models, `run-llama`, optional MTP flags. | `run-llama` → `:8081/v1` |
| **llama-config-ui (external)** | Browser UI to edit/save `llama-args.txt` profiles for `run-llama`. | submodule under `C:\Program Files\llamacpp` |
| **proton-mcp** | 31-tool MCP server giving an AI access to the user's Proton Mail, Pass, Drive, and VPN. | `proton-mcp/index.js` (Node) or `proton-mcp/proton_mcp.py` (Python) |
| **.agents/skills/** | Registry of small instruction packets ("skills") that teach AI runners when and how to use specific capabilities. Auto-discovered by OpenCode, Claude Code, etc. | individual `SKILL.md` files |
| **mcp-server** | General-purpose coding-assistant MCP. ~30 tools for web search (SearXNG), file ops, shell, git, formatters, etc. | `mcp-server/mcp_server.py` |
| **searxng + proxy** | Private search and pretty local hostnames (`find`, `draw`). | `proxy/Caddyfile`, `searxng/docker-compose.yml` |
| **OpenCode** | Agentic CLI; default model provider points at local llama-server. | `opencode and skills/opencode/opencode.json` |

## How the pieces fit together

```
┌──────────────────────────────────────────────────────────────────────────┐
│  Local AI runner (OpenCode / Claude Code / Gemini CLI / Cursor / …)     │
│  · ~/.agents/skills  (symlink → repo/.agents)                            │
│  · ~/.config/opencode (symlink → repo/opencode)                            │
│  · MCP over stdio                                                        │
└───────┬──────────────────────┬──────────────────────┬────────────────────┘
        │                      │                      │
        ▼                      ▼                      ▼
┌───────────────┐    ┌──────────────────┐    ┌────────────────────────────┐
│ llama-server  │    │ mcp-server       │    │ proton-mcp                 │
│ :8081 /v1     │    │ web_search →     │    │ mail / pass / drive / vpn  │
│ (run-llama)   │    │ SearXNG :8080    │    │                            │
│ + built-in UI │    │ + shell/git/…    │    │                            │
└───────────────┘    └────────┬─────────┘    └────────────────────────────┘
        ▲                       │
        │              ┌────────▼─────────┐     Caddy :80
        │              │ SearXNG (Docker) │◄──── http://find
 llama-config-ui       └──────────────────┘     http://draw → Excalidraw
 (edits llama-args.txt)
 External: $env:LLAMACPP_ROOT (default C:\Program Files\llamacpp) + submodule
```

The user's local AI talks MCP to both servers in the same session. Skills in `.agents/skills/` tell the AI *when* to reach for which tool — for example, `proton-mail/SKILL.md` says "if the user mentions email or passwords, use the `mail__*` and `pass__*` tools."

## Conventions across the stack

Things that hold true in every component. Match these or call out a deliberate exception.

1. **Tool names use `<subsystem>__<snake_case>`.** (`mail__get_unread`, `pass__list_items`, `drive__upload`, etc.) Two underscores between subsystem and action.
2. **Errors return `{"error": "..."}` strings, not raised exceptions.** The MCP wire protocol surfaces raw exceptions awkwardly.
3. **Large outputs truncate at 50,000 chars** with a `...[SYSTEM WARNING: CONTENT TRUNCATED FOR LENGTH]...` marker.
4. **Config self-discovers when possible.** Servers walk a chain (env vars → `~/.<component>/config.{json,yaml}` → `.env` next to script → cwd `.env`) so the user can stand up a server without host-side config injection. Important for runners like OpenCode that just attach to running servers. Two cross-cutting handles are published by `Deploy-Homelab.ps1` at machine scope: `HOMELAB_ROOT` ("where does the Homelab repo live on this machine", forward-slashed) and `LLAMACPP_ROOT` ("where is llama.cpp installed", overridable by pre-setting before deploy; defaults to `C:\Program Files\llamacpp`). Reference them as `{env:HOMELAB_ROOT}` in JSON configs, `$env:HOMELAB_ROOT` in PowerShell, `os.environ["HOMELAB_ROOT"]` in Python — never hard-code absolute paths to repo contents or to the llama install dir.
5. **No secrets in repo.** `.gitignore` excludes `.env`. Anything matching the original maintainer's identifiers (`kenic`, `bankenichi`, `ifritcr`, `gabriel.hernandez`, the bridge app password `0muVpgV1xfbXRn0zAwJHhw`) is contamination and should be stripped before any redistribution.
6. **Skills are open-source artifacts** — see `.agents/NOTICES.md` for per-skill licenses. Don't bundle a skill into a redistributable without including its license.
7. **`AGENTS.md` is the universal agent-instruction file.** Every component has one. Recognized by Claude Code, Cursor, Codex, etc.

## When you're an AI agent working on this stack

1. **Read this file, then the component's `AGENTS.md`, before making changes.** They flag the non-obvious gotchas.
2. **Match the existing patterns.** All three components were written by hand at different times — they look related on purpose. New code in any component should feel like it belongs.
3. **Bump version + rebuild artifacts when shipping.** For proton-mcp specifically: `manifest.json` version bump + `.mcpb` rebuild. See `proton-mcp/ARCHITECTURE.md` "Verifying a build" for the post-build checks.
4. **Don't proactively modify upstream skills.** Anything in `.skill-lock.json` with a `source` field is pulled from a third-party repo and gets overwritten by `npx skills update`. Forward changes upstream instead.
5. **Sandbox quirks (Cowork specifically):** writes work for NEW files; OVERWRITE and DELETE fail silently on mounted folders. Edit-tool writes have been observed to truncate files mid-write — verify with `wc -l` after any non-trivial edit, recover via `sed -i '$ d'` + `cat >> file << 'EOF'`. Detail in `proton-mcp/AGENTS.md`.

## Documentation layout

| File | Audience | Scope |
| --- | --- | --- |
| `Homelab/AGENTS.md` (this) | AI agents touching any part of the stack | top-level orientation, conventions, file map |
| `proton-mcp/AGENTS.md` | AI agents touching proton-mcp | component-specific gotchas, extension guide |
| `proton-mcp/ARCHITECTURE.md` | Anyone designing or extending proton-mcp | design rationale, build process, future work |
| `proton-mcp/README.md` | end users installing proton-mcp | prereqs, install, tool list |
| `mcp-server/AGENTS.md` | AI agents touching mcp-server | docstring conventions, gotchas |
| `opencode and skills/AGENTS.md` | AI agents touching that parent folder | orientation; points into `.agents/` |
| `.agents/AGENTS.md` | AI agents authoring / editing skills | design rules for skill content |
| `.agents/README.md` | end users browsing the skill registry | skill catalog + runner integration |
| `.agents/NOTICES.md` | anyone redistributing the skill folder | licensing + provenance |

Read top-down for orientation, bottom-up for detail.
