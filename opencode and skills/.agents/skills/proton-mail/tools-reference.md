# Proton MCP — full tool reference

Loaded on demand by the `proton-mail` skill when SKILL.md doesn't cover enough detail.

All tools return JSON. Errors come back as `{"error": "..."}` instead of throwing.

---

## Mail (15)

### `mail__get_unread`

Lists unseen INBOX messages.

- Params: *(none)*
- Returns: `{ count: number, messages: [{id, subject, from, date}, ...] }` — up to the 20 most recent unseen messages.

### `mail__list_messages`

Recent INBOX messages (newest first).

- Params: `limit` (1–50, default 10)
- Returns: array of message summaries with `seen` flag, no body.

### `mail__list_folder_messages`

Recent messages in a specific folder.

- Params: `folder` (required, e.g. `"Sent"`, `"Archive"`, `"Trash"`, `"Drafts"`), `limit` (1–50, default 10)
- Returns: same shape as `list_messages`, but each item has `folder` set so you can chain to `get_message`.

### `mail__list_folders`

All Proton folders and labels.

- Returns: `[{name, delimiter}, ...]` — Proton uses `/` as a label delimiter (`Labels/Personal`).

### `mail__get_message`

Full message body + headers.

- Params: `message_id`, `folder` (default `"INBOX"`)
- Returns: `{id, folder, subject, from, to, cc, date, body, message_id_header, in_reply_to, references, seen}`
- The `body` is plain text if available, else HTML.

### `mail__get_thread`

Full conversation chain across INBOX + Sent + Archive.

- Params: `message_id`, `folder` (default `"INBOX"`)
- Returns: array of messages sorted chronologically. **For standalone messages (no real `in_reply_to`, only synthetic `@protonmail.internalid` references), returns just the seed message** — that's by design, not a bug. Saves a huge cross-folder HEADER scan.

### `mail__search_messages`

Keyword search across INBOX + Sent + Drafts + Archive.

- Params: `query` (TEXT search)
- Returns: up to 20 newest-first matches, each tagged with `folder`.

### `mail__get_attachments`

Download attachments as base64.

- Params: `message_id`, `folder` (default `"INBOX"`)
- Returns: `[{filename, contentType, size, content}, ...]` where `content` is base64.

### `mail__send_message`

New outgoing message.

- Params: `to` (required, comma-separated), `subject`, `body`, plus optional `cc`, `bcc`, `html`, `from_`, `attachments[]`
- `attachments` items: `{filename, contentType?, path? OR content?}` — `content` is base64.
- Signature is appended automatically if configured.

### `mail__reply_message`

Reply preserving In-Reply-To and References headers.

- Params: `message_id`, `body`, optional `reply_all` (default false), `cc`, `bcc`, `folder` (default `"INBOX"`), `from_`, `attachments[]`
- `reply_all=true` includes original To and Cc recipients (minus your own address).

### `mail__forward_message`

Forward a message.

- Params: `message_id`, `to`, optional `body` (prepended), `cc`, `bcc`, `folder` (default `"INBOX"`), `from_`
- Forwarded body includes a standard `---------- Forwarded message ----------` block.

### `mail__mark_message`

Toggle read/unread.

- Params: `message_id`, `read` (bool), `folder` (default `"INBOX"`)

### `mail__star_message`

Toggle star.

- Params: `message_id`, `star` (bool), `folder` (default `"INBOX"`)

### `mail__move_message`

Move to a different folder. **Sequence numbers change after a move** — re-look-up the message if you need to act on it again.

- Params: `message_id`, `destination` (folder name to move TO), `folder` (default `"INBOX"`, the source folder)

### `mail__delete_message`

Move to Trash (Proton's interpretation of expunge).

- Params: `message_id`, `folder` (default `"INBOX"`)

---

## Pass (9)

### `pass__list_vaults`

All vaults on the account.

- Returns: `{vaults: [{name, vault_id, share_id}, ...]}`

### `pass__list_items`

Items in a vault — **no passwords or TOTP seeds**.

- Params: `vault` (default `"Personal"`)
- Returns: `[{title, username, urls, state}, ...]`

### `pass__search_items`

Keyword filter over `list_items` — same safe shape.

- Params: `query`, `vault` (default `"Personal"`)

### `pass__get_item`

**Returns the actual password.** Use deliberately.

- Params: `name` (item title, exact match), `vault` (default `"Personal"`)
- Returns: `{title, username, password, urls, note, has_totp}`

### `pass__create_item`

New login credential.

- Params: `title`, `username?`, `password?`, `url?`, `vault?`

### `pass__update_item`

Modify fields on an existing item.

- Params: `name` (existing title), `username?`, `password?`, `vault?`

### `pass__trash_item`

Move to Pass trash.

- Params: `name`, `vault?`

### `pass__get_totp`

**Generates the current 2FA code.** Use deliberately.

- Params: `name`, `vault?`
- Returns: `{totp: "123456", ...timing fields}`
- If the item has no TOTP seed: `{"error": "No TOTP fields found in this item"}` — that's a legitimate "this credential doesn't have 2FA configured" response, not a tool failure.

### `pass__generate_password`

Random password.

- Params: `length` (optional)
- Returns: `{password: "..."}`

---

## Drive (6)

### `drive__list`

List files and folders.

- Params: `path` (default `""` = root)
- Returns: `[{name, type, size, modified}, ...]` — `type` is `"folder"` or `"file"`, `size` is `-1` for folders.

### `drive__mkdir`

Create a directory (and parents if needed).

- Params: `remote_path`

### `drive__upload`

Upload **one file**. `remote_path` is the exact destination filename, not a parent.

- Params: `local_path`, `remote_path`

### `drive__upload_folder`

Upload a directory (recursive). `remote_path` is the destination directory.

- Params: `local_path`, `remote_path`

### `drive__download`

Download **one file**. `local_path` is the exact destination filename, not a parent.

- Params: `remote_path`, `local_path`

### `drive__delete`

Delete a file or folder (auto-detects).

- Params: `remote_path`

---

## VPN (1)

### `vpn__status`

Public IP + region + Proton VPN detection.

- Returns: `{connected, ip, city, region, country, org, timezone}` — `connected: true` if `org` matches a known Proton VPN provider (Datacamp, M247, DataPacket, ProtonVPN).
