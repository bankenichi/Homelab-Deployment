# AGENTS.md — `opencode and skills/`

Parent folder for the local-AI configuration in the homelab stack. Holds OpenCode's provider/MCP wiring and the `.agents/` skill registry side-by-side; both directories get symlinked into the user's home by `Deploy-Homelab.ps1`.

## Layout

```
opencode and skills/
├── AGENTS.md            — this file
├── opencode/            — OpenCode config (symlinked to ~/.config/opencode)
│   ├── AGENTS.md        — coding-assistant guidelines surfaced to OpenCode
│   ├── opencode.json    — provider, model, MCP wiring (references {env:HOMELAB_ROOT})
│   └── opencode.jsonc   — schema-pinned variant
└── .agents/             — skill registry (symlinked to ~/.agents)
    ├── AGENTS.md        — guidance for AI agents editing skills
    ├── README.md        — skill catalog + author guide + runner integration table
    ├── NOTICES.md       — per-skill license and provenance
    ├── .skill-lock.json — managed by `npx skills`, records each skill's upstream source
    └── skills/
        └── <skill-name>/
            └── SKILL.md
```

The `.agents/` name follows the cross-runner convention — OpenCode, Cursor, Codex, Gemini CLI, GitHub Copilot, Amp, Replit, and others all look there. The vercel-labs `skills` CLI installs to `.agents/skills/` by default for any of these.

## When you (the agent) should touch this folder

- The user wants to add, remove, or modify a skill → work inside `.agents/`. See `.agents/AGENTS.md`.
- The user wants to change OpenCode provider, model, or MCP wiring → edit `opencode/opencode.json`.
- The user wants to consolidate multiple runners' configs → coordinate across both subtrees.

## `HOMELAB_ROOT` in `opencode.json`

The MCP command paths in `opencode/opencode.json` use the `{env:HOMELAB_ROOT}` substitution rather than absolute paths:

```json
"command": ["python", "{env:HOMELAB_ROOT}/mcp-server/mcp_server.py"]
```

`HOMELAB_ROOT` is set as a machine-scope environment variable by `Deploy-Homelab.ps1` to the forward-slashed path of the deployed repo. This keeps the config portable: the same `opencode.json` works on any machine the stack is deployed to, regardless of username or drive letter, and the file commits cleanly without leaking host-specific paths.

When editing `opencode.json`:

- Use `{env:HOMELAB_ROOT}` for any path inside this repo. Forward slashes only — OpenCode passes the string through to the OS shell, and Windows accepts forward slashes in arg paths but JSON-escaping backslashes is fragile.
- Do not hard-code `C:\Users\<name>\...` or any other absolute prefix to repo contents.
- External paths outside the repo (e.g. `C:/Program Files/llamacpp/...`) can be absolute — they're fixed by the deploy script regardless of where the repo lives.
- The variable is machine-scope, so a newly spawned MCP subprocess inherits it. Already-running OpenCode sessions need to be restarted after the first deploy to see the new value.

## Why "opencode and skills" as the parent name

Historical — the folder grew to hold both OpenCode's working files and the skill registry, and the name stuck. Don't rename it without coordinating with the symlink logic in `Deploy-Homelab.ps1` (section 9), which targets this exact path.

## Related

- [`./.agents/`](./.agents) — the actual registry. Start here for any skill work.
- [`../AGENTS.md`](../AGENTS.md) — top-level homelab orientation.
- [`../proton-mcp/`](../proton-mcp) — the MCP server that the `proton-mail` skill drives.
