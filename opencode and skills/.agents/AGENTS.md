# AGENTS.md — `.agents/skills/`

Guidance for AI agents working ON these skill files (authoring, editing, adding new ones). For runner integration (how skills are loaded by OpenCode / Claude Code / Gemini CLI / etc.) see [`README.md`](./README.md). For licensing and provenance see [`NOTICES.md`](./NOTICES.md).

## What this folder is

The skills registry for local AI runners. Each subfolder under `skills/` is one skill, defined by a `SKILL.md` with YAML frontmatter. Runners discover skills by scanning frontmatter at session start and activate the body when the user's request matches.

## When you (the agent) should touch this folder

- The user explicitly asks to add, modify, or remove a skill.
- The user mentions a workflow that doesn't have a skill yet and asks for one.
- A skill has incorrect behavior or outdated info.

DO NOT proactively modify skills from upstream sources (anything in `.skill-lock.json` with a `source` field) without the user's say-so. Those skills get pulled fresh from their repos by `npx skills update` — local edits will be overwritten.

The user's own / forked skills (currently just `proton-mail`) are fair game.

## Anatomy of a skill

```
skills/<skill-name>/
├── SKILL.md             — required. YAML frontmatter (name, description) + body.
├── <helper>.md          — optional. Referenced by SKILL.md, loaded only when needed.
└── <helper>.sh / .py    — optional. Executable helpers the skill may run.
```

Minimum `SKILL.md`:

```markdown
---
name: <skill-name>
description: One or two sentences. Be specific about WHEN to invoke. The runner uses this string alone to decide whether to activate, so concrete trigger phrases help.
---

# Skill heading

Body. Keep it lean — every byte here is in the always-on prompt until the runner activates the skill.
```

## Design rules (distilled from the existing skills)

1. **Lead with an iron law** if the skill enforces one. Examples:
   - `NO FIXES WITHOUT ROOT CAUSE INVESTIGATION FIRST` (systematic-debugging)
   - `Do NOT invoke any implementation skill until design is approved` (brainstorming)
2. **Use HTML-sentinel blocks** for things the runner is likely to skim past:
   - `<HARD-GATE>...</HARD-GATE>`
   - `<EXTREMELY-IMPORTANT>...</EXTREMELY-IMPORTANT>`
   - `<CREDENTIAL-HANDLING>...</CREDENTIAL-HANDLING>` (used in proton-mail)
   These stand out and survive context compression.
3. **Imperative verbs.** `Use this when…`, `Do not…`, `Always…` — not "you might want to" or "it can be useful to".
4. **Move long reference material out of SKILL.md.** Anything the runner only needs sometimes (parameter tables, multi-step recipes, code templates) goes in a separate `<helper>.md` in the same folder. SKILL.md should reference it by relative filename.
5. **Trigger phrases in the `description`.** The runner decides whether to activate from the frontmatter alone. Include concrete phrases the user might say.
6. **No novelistic prose.** Bullets and short paragraphs. The runner doesn't need to be charmed.

## Adding a new skill

1. Create `skills/<name>/SKILL.md` following the template above.
2. If the skill has any helpers, drop them in the same folder.
3. Add a row to the **Installed skills** table in [`README.md`](./README.md). Keep the "When the runner uses it" cell crisp.
4. If the skill came from an upstream source (vs. authored locally), `npx skills add <source>` is the canonical way to install it — that updates `.skill-lock.json` with provenance. Don't manually copy files in.
5. If the skill is original, add it to [`NOTICES.md`](./NOTICES.md) under the appropriate license section.

## Editing an existing skill

- For upstream skills (anything with a `source` field in `.skill-lock.json`): prefer upstreaming the change via PR. Local edits will be silently overwritten by `npx skills update`.
- For local skills (`proton-mail`): edit in place. Bump no version; skills don't have a version concept.

## File system gotchas (Cowork sandbox)

If you're running inside Cowork's sandbox: writes work for NEW files in the `.agents/` mount, but you CANNOT overwrite or delete existing ones. If you need to replace a file, write under a new name and ask the user to swap them. The Edit tool has occasionally truncated files mid-write — after any edit, verify with `wc -l` and `tail -3`; recover via `sed -i '$ d'` + `cat >> file << 'EOF'` if needed.

## What's installed right now

See [`README.md`](./README.md) for the live catalog. Quick recap: 7 third-party skills (brainstorming, find-skills, frontend-design, requesting-code-review, systematic-debugging, ui-ux-pro-max, using-superpowers) plus 1 local skill (proton-mail). Licensing summary is in [`NOTICES.md`](./NOTICES.md).
