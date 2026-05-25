# AGENTS.md — proton-mcp

Guidance for AI agents (Claude Code, OpenCode, Cursor, etc.) working on this repo.

## What this project is

A self-contained MCP server giving an AI model access to the user's Proton suite via the locally-running Proton Bridge desktop app, the `pass-cli` Proton Pass CLI, and `rclone` (configured with a `protondrive` remote). Two implementations live side-by-side:

- **`index.js`** — Node 22+ server using `@modelcontextprotocol/sdk`, `imap`, `mailparser`, and `nodemailer`. This is what the `.mcpb` bundle ships for Claude Desktop.
- **`proton_mcp.py`** — Pure-Python FastMCP server using stdlib (`imaplib`, `smtplib`, `email`) — drop-in for OpenCode and other Python-friendly MCP hosts. Self-configures from env vars / `~/.proton-mcp/bridge.json` / a `.env` next to the script.

Both expose the **same 31 tools** with **the same names** (`mail__*`, `pass__*`, `drive__*`, `vpn__*`). Treat them as parity implementations — any change to one needs the equivalent change in the other.

## Repo layout

```
proton-mcp/
├── index.js                   # Node MCP server, registers all 31 tools
├── proton_mcp.py              # Python FastMCP server, mirrors index.js
├── manifest.json              # .mcpb bundle metadata (Claude Desktop install config)
├── package.json / package-lock.json
├── requirements.txt           # Python deps (just `mcp>=1.0`)
├── .env.example               # template for env-var config
├── bridge.json.example        # template for ~/.proton-mcp/bridge.json
├── setup.py / setup.bat       # cross-platform prereq checker / auto-installer
│
├── mail/
│   ├── imap-client.js         # all mail__* tools (IMAP read + flag ops)
│   └── smtp-client.js         # send / reply / forward via nodemailer
├── pass/
│   └── pass-client.js         # wraps pass-cli (Proton Pass)
├── drive/
│   └── drive-client.js        # wraps rclone against the protondrive remote
├── vpn/
│   └── vpn-client.js          # ipinfo.io-based VPN detection
│
├── skill/proton-mail/         # AI-agent skill that drives this MCP
│   ├── SKILL.md
│   ├── tools-reference.md
│   └── workflows.md
│
├── README.md                  # user-facing docs (prereqs, install, tool list)
└── ARCHITECTURE.md            # design / extension guide for agents and devs
```

## Critical patterns and gotchas

These are non-obvious and have already burned past iterations. Read before editing.

### 1. IMAP sequence numbers are folder-scoped

Every mail tool that takes `message_id` MUST also accept a `folder` parameter (defaults to `INBOX`). The same sequence number `237` points to different messages in INBOX vs. Sent vs. Archive. Tools that ignore this return "message not found" or — worse — silently act on the wrong email.

### 2. `mailparser` returns `references` as a STRING for single references, ARRAY for multiple

This caused a 30-minute `get_thread` timeout in the Node implementation. Spreading a string into an array explodes it into individual characters and triggers one HEADER search per character. **Always normalize before iterating:**

```js
const refsArr = Array.isArray(rawRefs) ? rawRefs : (rawRefs ? [rawRefs] : []);
```

Python's `email.message.Message` doesn't have this footgun — `_refs_list()` in `proton_mcp.py` parses the References header with a regex.

### 3. Proton Bridge synthesizes `@protonmail.internalid` threading IDs

These are not real Message-IDs and don't match anywhere else. `get_thread` filters them out via `isSyntheticRef()` / `_is_synthetic_ref()` before doing any cross-folder HEADER search. If a message has only synthetic refs and no real `in_reply_to`, `get_thread` short-circuits and returns just the seed — DO NOT remove that short-circuit, it's what makes Glassdoor-style notification mail responsive.

### 4. The Node `imap` library has `addFlags`/`delFlags`, not `store`

An earlier version of `mail__delete_message` called `imap.store()` (which doesn't exist) and silently failed. Use `imap.addFlags(msg, ['\\Deleted'], cb)` + `imap.expunge(cb)`.

### 5. `pass-cli password generate` requires a subcommand

It's `pass-cli password generate random [--length N]`, not `pass-cli password generate <N>`. Earlier code missed the `random` and failed with an unhelpful "command failed" error.

### 6. `pass-cli` key provider must match what the user logged in with

DO NOT set `PROTON_PASS_KEY_PROVIDER=fs` in the spawn env. pass-cli picks its own provider based on the OS (wincred on Windows, etc.). Forcing `fs` makes it look for a filesystem-backed key that doesn't exist and reports the misleading "database corrupted / hmac check failed" error.

### 7. `rclone copy` treats the destination as a parent directory

For single-file ops, this puts the file *inside* what the user thought was the destination filename. Use `rclone copyto` instead — it treats the destination as the exact path. The `drive__upload` and `drive__download` tools have this fix; `drive__upload_folder` still uses `copy` because folder semantics are different.

### 8. From-address resolution

Reply / forward / send use this priority: `from_param || config.from || config.username`. An earlier version hardcoded `config.username`, which made replies always go out from the Proton account email instead of the user-configured `PROTON_BRIDGE_FROM` alias.

### 9. Bundle build excludes

When rebuilding the `.mcpb` (just `zip -r proton-mcp.mcpb . -x …`), exclude:

- `.env` (real credentials)
- `html signature.txt` (personal HTML signature)
- `.git/` (dev history)
- `proton-mcp.mcpb` and `proton-mcp-*.mcpb` (old bundles, chicken-and-egg)
- `*.DS_Store`, `Thumbs.db`
- `__pycache__/`, `*/__pycache__/`
- `drive-*test*.txt` and `drive-*-downloaded.txt/` (session test artifacts)
- `*Name clash*` (Cowork file-system artifacts from prior Edit-tool truncations)

After building, scan the bundle for personal-info strings before shipping (see "Verifying a build" in ARCHITECTURE.md).

### 10. Sandbox file ops vs. real filesystem

If you're running inside Cowork: the sandbox's bash can WRITE NEW files in mounted folders but CANNOT OVERWRITE OR DELETE existing ones. Use new filenames (`proton-mcp-v1.0.13.mcpb`) for replacements and let the user remove the old ones manually. The Edit tool has been observed to occasionally truncate files mid-write; after any Edit, verify with `wc -l` and `tail -3`, and recover via `sed -i '$ d'` + `cat >> file << 'EOF'` if needed.

## Adding a new tool

1. Decide which subsystem it belongs to (`mail`, `pass`, `drive`, `vpn`) and pick a name following the `<subsystem>__<snake_case>` convention.
2. Implement the underlying logic in `<subsystem>/<subsystem>-client.js` (Node) AND in the matching section of `proton_mcp.py` (Python). Keep names parallel.
3. Register the tool in `index.js` with `server.tool('name', 'description', { zodSchema }, asyncHandler)` AND in `proton_mcp.py` with `@server.tool(name='name')` on an `async def`.
4. If the tool takes a `message_id`, also take an optional `folder` (default `"INBOX"`).
5. Add the tool name + description to the table in `README.md` under "Tools (31)".
6. Add a row to `skill/proton-mail/tools-reference.md` with the parameters and return shape.
7. Bump `manifest.json` `version` and rebuild the `.mcpb`.

## Testing locally

There's no test suite. The validation we have is "drive each tool through Claude Desktop or OpenCode and watch the connector output". Order of operations when debugging:

1. `setup.py` / `setup.bat` — verifies prereqs (Node, Bridge, pass-cli, rclone, protondrive remote).
2. Manual MCP call from the host — Claude Desktop's developer logs and OpenCode's stderr both surface MCP errors verbatim.
3. For mail tools specifically: pick a stable INBOX message id (e.g. via `mail__list_messages`) and exercise read/flag/move/delete on it.

## Conventions

- Two-space indent in JS, four-space in Python (PEP 8). Match the existing file.
- ES modules in Node (`import`/`export`, `"type": "module"` in package.json). No CommonJS.
- Python: prefer stdlib over deps. The only declared dep is `mcp>=1.0`.
- Tool function names in Python use `<subsystem>_<name>` (single underscore — `mail_get_unread`), with the `@server.tool(name="<subsystem>__<name>")` override providing the double-underscore name that matches the Node MCP.
- All tool handlers return JSON-serialized strings. Errors come back as `{"error": "..."}` instead of throwing.
- The `from_` parameter name (with trailing underscore) is intentional in Python — `from` is a reserved keyword.

## Related docs

- `README.md` — user-facing install / config / tool list.
- `ARCHITECTURE.md` — design rationale, extension guide, build process.
- `skill/proton-mail/SKILL.md` — how an AI runner invokes these tools.
- `../opencode and skills/.agents/AGENTS.md` — sibling skill folder.
