# AGENTS.md — `opencode and skills/`

Parent folder for the local-AI configuration in the homelab stack. Right now it contains one thing: the `.agents/` skill registry.

## Layout

```
opencode and skills/
├── AGENTS.md          — this file
└── .agents/
    ├── AGENTS.md      — guidance for AI agents editing skills
    ├── README.md      — skill catalog + author guide + runner integration table
    ├── NOTICES.md     — per-skill license and provenance
    ├── .skill-lock.json  — managed by `npx skills`, records each skill's upstream source
    └── skills/
        └── <skill-name>/
            └── SKILL.md
```

The `.agents/` name follows the cross-runner convention — OpenCode, Cursor, Codex, Gemini CLI, GitHub Copilot, Amp, Replit, and others all look there. The vercel-labs `skills` CLI installs to `.agents/skills/` by default for any of these.

## When you (the agent) should touch this folder

- The user wants to add, remove, or modify a skill.
- The user wants to add OpenCode-specific configuration that doesn't fit inside a skill.
- The user wants to consolidate multiple runners' configs.

For all of these, the actual work happens inside `.agents/`. See `.agents/AGENTS.md` for the deeper rules.

## Why "opencode and skills" as the parent name

Historical — when this folder was created it held both OpenCode's working files and the skill registry. The OpenCode-specific files have since moved elsewhere; the folder name stuck. Don't rename it without coordinating with whatever OpenCode session expects this path.

## Related

- [`./.agents/`](./.agents) — the actual registry. Start here for any skill work.
- [`../AGENTS.md`](../AGENTS.md) — top-level homelab orientation.
- [`../proton-mcp/`](../proton-mcp) — the MCP server that the `proton-mail` skill drives.
