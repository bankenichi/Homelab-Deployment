# plugin/ — the opencode-vision plugin

Source of truth / fork: **https://github.com/bankenichi/opencode-vision** (AGPL-3.0),
itself a fork of DavidEasden/opencode-vision → devadathanmb/opencode-minimax-easy-vision.

## TL;DR — no build is needed

Per the official OpenCode plugin docs, local plugins are loaded **directly** from the plugin
directory as `.js` *or* `.ts` files. OpenCode runs on Bun, which executes TypeScript natively.
This plugin imports only `import type {…}` (erased at runtime) plus Node built-ins
(`node:os/path/fs/crypto`) — it has **zero runtime npm dependencies**. So the raw `index.ts`
runs as-is; `npm install` / `tsc` are optional (only useful for type-checking).

## Where the live plugin lives (already placed)

```
opencode and skills/opencode/plugins/opencode-vision.ts
   ⇒ symlinked by Deploy-Homelab.ps1 to ~/.config/opencode/plugins/opencode-vision.ts
   ⇒ auto-loaded by OpenCode at startup (directory is "plugins", plural)
```

⚠️ Directory is **`plugins/`** (plural). The upstream README's `~/.config/opencode/plugin/`
(singular) + symlink-the-dist instructions are outdated vs. the current docs. We load the
`.ts` directly from `plugins/`.

## Configuration (model matching) — already placed

The plugin reads `opencode-vision.json` from (project beats user):
`./.opencode/opencode-vision.json` → `~/.config/opencode/opencode-vision.json` → built-in defaults.

We placed the **user-level** config at `opencode and skills/opencode/opencode-vision.json`
(⇒ `~/.config/opencode/opencode-vision.json`):

```json
{
  "models": ["*"],
  "imageAnalysisTool": "vision_analyze"
}
```

`"models": ["*"]` makes the plugin **model-agnostic** — it triggers for every model, not just
the upstream MiniMax/`qwen3-coder-next-mlx` default. `imageAnalysisTool` is set to the tool the
`vision` MCP server registers (`<mcp-key>_<tool>` = `vision_analyze`). The plugin's own
hard-coded defaults (`*/qwen3-coder-next-mlx`, `local_vision`) are thus overridden by config —
the vendored `.ts` stays faithful to the fork; behavior is driven entirely by this JSON.

> Note: with `["*"]`, if you ever load a model with **native** image support, the plugin will
> still intercept its images. To exempt such a model, narrow the list, e.g.
> `["llama.cpp/*"]`, or list only the models that lack native vision.

## Optional: build / type-check from the fork

Only needed if you want to edit the plugin and type-check it, or produce a bundled `dist/`.

```bash
cd opencode-vision/plugin
git clone https://github.com/bankenichi/opencode-vision src-fork
cd src-fork && npm install
npm run build          # tsc → dist/index.js (+ .d.ts)
npx tsc --noEmit       # type-check only
```

To deploy a build instead of the raw `.ts`, copy `dist/index.js` to
`~/.config/opencode/plugins/opencode-vision.js`. Editing workflow: change in the fork →
commit/push → re-copy `src/index.ts` to the live `plugins/opencode-vision.ts`.

> ⚠️ **Never put `tsconfig.json` or a `@types/*` devDependency into the runtime config
> dir (`opencode and skills/opencode/`, symlinked to `~/.config/opencode/`).** OpenCode
> runs on Bun and reads any `tsconfig.json` in that tree when loading the `.ts` plugin;
> editor-only tooling there can break OpenCode startup (config/provider/agent load
> failures). Type-check **here in the fork** (`src-fork`, which has its own tsconfig +
> `@types`) instead. Squiggles on the *vendored* `plugins/opencode-vision.ts` are cosmetic
> — that copy is generated/vendored, not hand-edited.

## Activation checklist (for the live test)

1. `opencode and skills/opencode/plugins/opencode-vision.ts` exists ✅ (placed)
2. `opencode and skills/opencode/opencode-vision.json` exists with `["*"]` ✅ (placed)
3. `opencode and skills/opencode/opencode.json` has the `vision` MCP server ✅ (placed)
4. Restart OpenCode. Expect log: `[opencode-vision] Plugin initialized` and
   `Loaded models from user config: *`.
5. Select your model, paste an image, ask a question. See `docs/PLAN.md` Phase 5 for the
   full expected log sequence and the tool-name verification step.

## License (AGPL-3.0)

Keep attribution and the upstream `LICENSE` with any redistribution. The Python MCP server
(`../mcp/vision_mcp.py`) and JSON configs are separate works that merely call the plugin's
tool over MCP.
