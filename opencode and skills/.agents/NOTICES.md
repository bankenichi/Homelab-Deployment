# Third-Party Notices

This `.agents/` folder bundles skills from several upstream open-source projects. This file consolidates their copyright notices and license terms so that the folder can be redistributed as a unit.

Every skill's installation source is also recorded in `.skill-lock.json` (the file the `npx skills` CLI maintains automatically). That file is the authoritative provenance manifest — the table below is the human-readable summary.

---

## Skills and their upstream sources

| Skill (under `skills/`) | Upstream | License | Copyright holder |
| --- | --- | --- | --- |
| `brainstorming` | [obra/superpowers](https://github.com/obra/superpowers) | MIT | © 2025 Jesse Vincent |
| `using-superpowers` | [obra/superpowers](https://github.com/obra/superpowers) | MIT | © 2025 Jesse Vincent |
| `requesting-code-review` | [obra/superpowers](https://github.com/obra/superpowers) | MIT | © 2025 Jesse Vincent |
| `systematic-debugging` | [obra/superpowers](https://github.com/obra/superpowers) | MIT | © 2025 Jesse Vincent |
| `find-skills` | [vercel-labs/skills](https://github.com/vercel-labs/skills) | MIT | © Vercel |
| `frontend-design` | [anthropics/skills](https://github.com/anthropics/skills) | Apache License 2.0 | © Anthropic |
| `ui-ux-pro-max` | [nextlevelbuilder/ui-ux-pro-max-skill](https://github.com/nextlevelbuilder/ui-ux-pro-max-skill) | MIT | © 2024 Next Level Builder |
| `proton-mail` | This homelab repo (Proton MCP fork) | MIT | © the proton-mcp fork maintainer |

---

## License texts

### MIT License

Applies to: `brainstorming`, `using-superpowers`, `requesting-code-review`, `systematic-debugging` (Copyright © 2025 Jesse Vincent); `find-skills` (Copyright © Vercel); `ui-ux-pro-max` (Copyright © 2024 Next Level Builder); `proton-mail` (Copyright © the proton-mcp fork maintainer).

```
Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

### Apache License 2.0

Applies to: `frontend-design` (Copyright © Anthropic, PBC).

The full text of the Apache License 2.0 ships alongside the skill at:
`skills/frontend-design/LICENSE.txt`

Summary of obligations when redistributing `frontend-design` (or derivative works of it):

- Retain `LICENSE.txt` (or an equivalent copy of Apache 2.0) with the distribution.
- Preserve all copyright, patent, trademark, and attribution notices that ship with the skill.
- If you modify any file, add a prominent notice that you changed it.
- If the upstream included a `NOTICE` file (it currently does not for `frontend-design` specifically — Anthropic's repo-level `THIRD_PARTY_NOTICES.md` covers vendored deps only), reproduce it in your distribution.
- You may not use Anthropic's name, trademarks, or service marks in ways that imply endorsement of your derivative work.

---

## Anthropic's disclaimer (re: `frontend-design`)

The upstream `anthropics/skills` repository carries this caveat, which applies to `frontend-design` as well:

> These skills are provided for demonstration and educational purposes only. While some of these capabilities may be available in Claude, the implementations and behaviors you receive from Claude may differ from what is shown in these skills. These skills are meant to illustrate patterns and possibilities. Always test skills thoroughly in your own environment before relying on them for critical tasks.

Do not represent the `frontend-design` skill (or any derivative work of it) as officially supported by Anthropic.

---

## What this folder does NOT bundle

For clarity: `anthropics/skills` also publishes document-handling skills (`docx`, `pdf`, `pptx`, `xlsx`) that are **source-available, not open source**, under terms different from Apache 2.0. **None of those skills are installed here.** If you add them later, audit their licensing separately.

---

## Re-verifying

If you want to confirm any line in the table above, the upstream license file is one fetch away:

```bash
curl -sL https://raw.githubusercontent.com/obra/superpowers/main/LICENSE
curl -sL https://raw.githubusercontent.com/vercel-labs/skills/main/LICENSE
curl -sL https://raw.githubusercontent.com/nextlevelbuilder/ui-ux-pro-max-skill/main/LICENSE
curl -sL https://raw.githubusercontent.com/anthropics/skills/main/LICENSE       # or LICENSE.md, varies
```

The exact commit each skill was installed from is in `.skill-lock.json` under `skillFolderHash` — pin those if you need a stable reference.
