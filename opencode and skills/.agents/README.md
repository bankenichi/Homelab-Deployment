# `.agents/` — Skills for local AIs in the Homelab stack

This folder is where local AI runners (OpenCode, Claude Code in self-hosted mode, Gemini CLI, Copilot CLI, etc.) discover and load **skills** — small, focused instruction packets that tell the model when and how to use a particular capability.

Skills live in `~/.agents/skills/<skill-name>/SKILL.md`. The runner reads the YAML frontmatter of every `SKILL.md` it can find and invokes the matching skill when the user's request fits the skill's `description`. The body of `SKILL.md` is what the model sees once the skill is activated; helper files in the same folder are loaded on demand so the always-on prompt stays cheap.

This folder is part of the [homelab](../..) deployment — it ships pre-populated with a curated set of skills, but you can drop any skill that follows the format into `~/.agents/skills/` and it'll be picked up.

---

## Installed skills

| Skill | When the runner uses it | Notes |
| --- | --- | --- |
| **brainstorming** | Before any creative or implementation work — turns a vague idea into a written design spec via collaborative dialogue. | Hard-gated: blocks code-writing skills until a design has been written and approved. Helper files: `visual-companion.md`, `spec-document-reviewer-prompt.md`. |
| **find-skills** | When the user asks "how do I do X" or "is there a skill for...". | Walks the user through searching the skills.sh registry, vets candidates (install count, security audit, GitHub stars), and installs via `npx skills add`. |
| **frontend-design** | When building web components, pages, posters, dashboards, or anything visual with HTML/CSS/JS/React. | Pushes for distinctive, BOLD aesthetic choices — explicitly steers away from generic "AI slop" defaults (Inter font, purple-on-white gradients, etc.). Helper: `LICENSE.txt`. |
| **proton-mail** | Any interaction with the user's Proton account — read/send mail, fetch passwords, get TOTP codes, list/upload/download Proton Drive, check VPN status. | Drives the [proton-mcp](../proton-mcp) server. Covers all 31 `mail__*` / `pass__*` / `drive__*` / `vpn__*` tools. Helpers: `tools-reference.md` (full parameter list), `workflows.md` (common multi-step recipes). |
| **requesting-code-review** | When the runner finishes a task, completes a major feature, or is about to merge — dispatches a code-reviewer subagent with crafted context. | Helper: `code-reviewer.md` (the subagent prompt template). |
| **systematic-debugging** | When encountering any bug, test failure, or unexpected behavior — enforces root-cause investigation before any fix. | Helpers: `root-cause-tracing.md`, `defense-in-depth.md`, `condition-based-waiting.md` (+ example), `find-polluter.sh`. |
| **ui-ux-pro-max** | Designing UI components, dashboards, mobile apps — picks from a curated library of 50+ styles, 161 color palettes, 57 font pairings, etc. across 10 frameworks. | Complementary to `frontend-design` — that one handles the aesthetic decisions, this one provides the deep reference data. |
| **using-superpowers** | At the start of every conversation — establishes the skill-discovery protocol the runner uses for the rest of the session. | The bootstrap skill. Tells the runner that skills override default behavior, but user instructions (CLAUDE.md / GEMINI.md / AGENTS.md) override skills. |

---

## How to add a new skill

```
~/.agents/skills/
└── <skill-name>/
    ├── SKILL.md           # required — YAML frontmatter + body
    ├── <helper>.md        # optional — referenced by SKILL.md, loaded on demand
    └── <helper>.sh / .py  # optional — executable helpers the skill may run
```

Minimum `SKILL.md`:

```markdown
---
name: <skill-name>
description: One or two sentences. Be specific about WHEN to invoke this skill — the runner uses the description to decide.
---

# Skill heading

Body. Keep it lean. Move long reference material into helper files in the same folder so the always-on prompt stays small.
```

### Loaded-on-demand helpers

The runner only loads `SKILL.md` until it actively invokes the skill. If your skill has long reference material (parameter tables, multi-step recipes, large code templates), put those in **separate files in the skill folder** and reference them by relative path from `SKILL.md`. The runner will read them only when needed.

Example layout: `proton-mail/` has `SKILL.md` (85 lines, the always-loaded part) plus `tools-reference.md` (225 lines, loaded only when the model needs full parameter detail) and `workflows.md` (71 lines, loaded only when the user describes a multi-step task).

### Style guidance (from the existing skills)

- Lead with a clear **principle** or **iron law** if the skill enforces one (`NO FIXES WITHOUT ROOT CAUSE INVESTIGATION FIRST`, `Do NOT invoke any implementation skill until design is approved`).
- Use `<HARD-GATE>` / `<CREDENTIAL-HANDLING>` / `<EXTREMELY-IMPORTANT>` HTML-sentinel blocks for things the runner is likely to skim past — those stand out.
- Write the `description` so the runner can decide from it alone whether to invoke. Mention concrete trigger phrases the user might say.
- Keep verbs imperative (`Use this when…`, `Do not…`, `Always…`).
- The runner doesn't need novelistic prose. Bullets and short paragraphs ship faster.

---

## Runner integration

| Runner | How skills are loaded |
| --- | --- |
| **Claude Code / Claude Desktop (Cowork)** | Reads frontmatter from every `SKILL.md` in `~/.claude/skills/` and installed plugin skill folders. Invoke with the `Skill` tool. |
| **OpenCode** | Auto-discovers skills from `~/.agents/skills/` at session start. Loads the body of a `SKILL.md` only when its `description` matches the active request. |
| **Copilot CLI** | Same `skill` tool as Claude Code; reads from installed plugin folders. |
| **Gemini CLI** | Loads frontmatter at session start, activates full content via the `activate_skill` tool. |

The `using-superpowers` skill (installed here) bootstraps the per-runner specifics — it tells the runner where to look and which tool name to use.

---

## Licensing

All bundled skills are open-source:

- **MIT** — `brainstorming`, `using-superpowers`, `requesting-code-review`, `systematic-debugging` (© Jesse Vincent), `find-skills` (© Vercel), `ui-ux-pro-max` (© Next Level Builder), `proton-mail` (this repo)
- **Apache 2.0** — `frontend-design` (© Anthropic; `LICENSE.txt` ships inside the skill folder)

See [`NOTICES.md`](./NOTICES.md) for the full per-skill provenance table, the consolidated license texts, and the Apache 2.0 redistribution checklist. `.skill-lock.json` (managed by the `npx skills` CLI) records the exact upstream commit each skill was installed from.

---

## Related folders in this homelab

- [`../proton-mcp/`](../proton-mcp) — the Proton MCP server (Node + Python implementations) that the `proton-mail` skill drives.
- [`../mcp-server/`](../mcp-server) — the general-purpose coding-assistant MCP server (web search, file ops, shell, git, etc.). Most skills target tools surfaced by this server.

---

## Updating this README

When you add a new skill: append a row to the **Installed skills** table above. Keep the "When the runner uses it" cell crisp — that's the only part most readers will scan.
