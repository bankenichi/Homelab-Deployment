# Architecture — proton-mcp

Design rationale and extension guide. Companion to `AGENTS.md` (which covers gotchas) and `README.md` (which covers install/use).

## Why two implementations?

The project ships a Node version (`index.js`) AND a Python version (`proton_mcp.py`) with byte-for-byte parity on tool names and behavior.

| | Node (`index.js`) | Python (`proton_mcp.py`) |
| --- | --- | --- |
| Runtime | Node 22+ | Python 3.10+ |
| Distribution | `.mcpb` bundle, self-contained `node_modules/` | single `.py` file + `pip install mcp` |
| MCP SDK | `@modelcontextprotocol/sdk` | `mcp` (FastMCP) |
| Primary host | Claude Desktop | OpenCode |
| Bridge auth | env vars injected by Claude Desktop from `manifest.json`'s `user_config` | self-discovers: env → `~/.proton-mcp/bridge.json` → `.env` next to script → `.env` in cwd |

Two implementations because Claude Desktop's `.mcpb` packaging assumes Node, and OpenCode auto-discovers running MCP servers without injecting config — different deployment models. Sharing one implementation across both would mean compromises on both sides.

## File layout — what lives where

```
.
├── index.js               — Node entry. Registers all 31 tools, wires them to mail/pass/drive/vpn clients.
├── proton_mcp.py          — Python entry. Same 31 tools, FastMCP-style. ~1200 lines, single file.
├── manifest.json          — .mcpb manifest. Declares user_config (the form Claude Desktop shows on install).
├── package.json           — Node deps. Five total: imap, mailparser, nodemailer, @modelcontextprotocol/sdk, zod.
├── requirements.txt       — Python deps. Just `mcp>=1.0`. Everything else is stdlib.
│
├── mail/imap-client.js    — IMAP read + flag ops. The big file. 15-ish exports, all mail__ tools route here.
├── mail/smtp-client.js    — SMTP send/reply/forward via nodemailer. Builds RFC 2822 messages with proper threading headers.
│
├── pass/pass-client.js    — Subprocess wrapper around `pass-cli`. JSON-parsing helper + per-command builders.
├── drive/drive-client.js  — Subprocess wrapper around `rclone`. Each tool maps to one or two rclone subcommands.
├── vpn/vpn-client.js      — Fetches ipinfo.io, regexes the org name for known Proton VPN providers.
│
├── skill/proton-mail/     — Drop-in skill for AI runners (OpenCode, Claude Code, etc.).
├── setup.py / setup.bat   — Cross-platform prereq checker. Installs Node/rclone via OS package manager.
└── .env.example, bridge.json.example, README.md, AGENTS.md, ARCHITECTURE.md, LICENSE
```

The Node tree has 15 mail tools, 9 pass, 6 drive, 1 vpn = 31. Python mirrors exactly.

## How tool calls flow

### Node (`index.js`)

```
Claude Desktop / Cowork
        │ (stdio JSON-RPC)
        ▼
@modelcontextprotocol/sdk McpServer ──► tool handler (async function in index.js)
                                             │
                                             ▼
                                    mail/pass/drive/vpn client
                                             │
                                             ▼
                          Proton Bridge (IMAP/SMTP) | pass-cli | rclone | ipinfo.io
```

Each handler in `index.js` is a thin wrapper that:
1. Reads args from the schema (Zod-validated).
2. Calls the underlying client function.
3. Wraps the result in `{ content: [{ type: 'text', text: JSON.stringify(result) }] }` or `errorResult(msg)`.

### Python (`proton_mcp.py`)

Same flow, but in one file: `@server.tool(name="...")` decorated `async def`s. Each tool resolves config via `_config()` (which lazily walks the env → bridge.json → .env chain once and caches the result for the process lifetime), opens a short-lived IMAP/SMTP/subprocess connection inside a `with` block, returns JSON via `_result()` or `_error()`.

## Config resolution

### Node — env vars injected by host

`manifest.json` declares the `user_config` schema. When a user installs the `.mcpb`, Claude Desktop renders the form, collects values, and injects them as env vars when launching `node index.js`. `loadConfig()` in `index.js` reads them straight from `process.env`. No file I/O on the hot path.

There's a fallback to `~/.proton-mcp/bridge.json` for direct-from-source dev runs, but the `.mcpb` flow doesn't use it.

### Python — self-discovery

`_resolve_env()` in `proton_mcp.py` merges four sources in priority order:

1. Process environment (highest — wins if anything is set there)
2. `~/.proton-mcp/bridge.json` (lowercase keys, mapped to the env-var schema)
3. `.env` next to the script (`Path(__file__).resolve().parent / ".env"`)
4. `.env` in the cwd (lowest)

Cached after first resolution. Restart the script to pick up changed config.

This makes the Python server **fully portable** — drop `proton_mcp.py` anywhere, point `~/.proton-mcp/bridge.json` at your Bridge, and it works. No host-side config required.

## Threading and concurrency

Each tool opens a fresh IMAP / SMTP / subprocess connection per call and tears it down at the end (`ImapSession` context manager in Python, `withImap()` helper in Node). No connection pooling.

This is deliberate. Proton Bridge's IMAP server is single-threaded per connection and has odd state issues when commands interleave. Short-lived connections are slower per-call but bulletproof. Bridge can handle multiple concurrent connections fine.

There's no concurrency control on the tool dispatch side — if Claude calls two mail tools in parallel, both will spawn their own connections.

## The `get_thread` saga (in case you have to revisit it)

`get_thread` is the only tool with non-trivial logic. The original implementation timed out on Glassdoor-style notification mail. Three bugs stacked:

1. **Spread-string on `references`** — mailparser returns a string for single-ref messages and an array for multi-ref. The original `[...(refs || []), id]` exploded a 115-character Message-ID into 115 single-character "refs", each driving its own HEADER search.
2. **Synthetic `@protonmail.internalid` refs** — Proton Bridge synthesizes these for every message. They never match anywhere else, so searching for them is wasted work AND slow (Bridge has to decrypt the mailbox).
3. **No standalone-message fast path** — even mail with no real `in_reply_to` and only a synthetic reference got a full cross-folder scan.

Current implementation:

1. Fetch only HEADERS of the seed message (not the full body).
2. Normalize `references` to an array.
3. Filter out synthetic refs.
4. If no real ancestor remains, short-circuit and return just the seed.
5. Otherwise, build one nested-OR `HEADER Message-ID` search across the real refs and run it in INBOX + Sent + Archive.
6. Dedupe by Message-ID, sort by date.

If you change this, exercise it on:
- A standalone Glassdoor email (should return ~immediately).
- A reply chain that spans INBOX + Sent (should return the full chain).

## Verifying a build

After `zip -r proton-mcp-vX.Y.Z.mcpb . -x …`, run these checks:

```bash
# manifest shape
unzip -p bundle.mcpb manifest.json | python3 -c "
import json, sys; m = json.load(sys.stdin)
print('version:', m['version'])
print('args:', m['server']['mcp_config']['args'])  # should be ['${__dirname}/index.js']
"

# tool count
unzip -p bundle.mcpb index.js | grep -cE "^server\.tool\("       # → 31
unzip -p bundle.mcpb proton_mcp.py | grep -cF "@server.tool("    # → 31

# no calendar / personal info / stray files
unzip -l bundle.mcpb | grep -iE "calendar|Name clash"            # → empty
unzip -p bundle.mcpb manifest.json index.js proton_mcp.py \
  mail/imap-client.js mail/smtp-client.js pass/pass-client.js \
  drive/drive-client.js vpn/vpn-client.js README.md setup.py \
  setup.bat requirements.txt bridge.json.example .env.example \
  skill/proton-mail/SKILL.md skill/proton-mail/tools-reference.md \
  skill/proton-mail/workflows.md \
  | grep -iE "personal-info-pattern"
                                                                   # → empty
```

If all four checks pass, the bundle is shippable.

## Future work / known gaps

- No automated tests. A small Node test suite that mocks IMAP would catch the threading bugs we hit.
- Drive's `delete` does `deletefile` then falls back to `purge` on error. That's a guess-and-retry pattern; rclone has a clean "is this a directory" probe we should use instead.
- The Python server's SMTP STARTTLS path hasn't been exercised end-to-end against a real Bridge — it should work based on the spec, but the Node implementation (via nodemailer) is the better-tested one.
- `pass-cli` returns secrets as raw strings in stdout. We treat them carefully in the tool layer but they may transit through subprocess pipe buffers in memory; not a hardening issue most users care about, worth noting.
