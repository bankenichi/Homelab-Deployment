---
name: proton-mail
description: Use whenever the user wants to interact with their Proton account — read or send mail, search inbox, retrieve passwords or TOTP codes, generate a password, list/upload/download Proton Drive files, or check VPN status. Covers all 31 Proton MCP tools (mail__*, pass__*, drive__*, vpn__*). Trigger phrases include "check my email", "send an email", "any unread mail", "what's in my inbox", "reply to that", "get my <site> password", "two-factor code for <site>", "upload to Proton Drive", "am I on Proton VPN".
---

# Proton Mail / Pass / Drive / VPN

This skill drives the **proton MCP server** — a local Node (or Python) server that proxies into the user's Proton suite via Proton Bridge, pass-cli, and rclone. All 31 tools share the `mail__`, `pass__`, `drive__`, and `vpn__` prefixes.

Use this skill any time the user mentions email, passwords, 2FA codes, Proton Drive files, or their VPN. If a request smells Proton-shaped (`"check my inbox"`, `"send a message to..."`, `"get me the totp for github"`, `"what's on Drive"`, `"am I connected to the VPN"`), this is the right tool surface.

## The whole tool surface at a glance

```
mail__   get_unread, list_messages, list_folder_messages, list_folders,
         get_message, get_thread, search_messages, get_attachments,
         send_message, reply_message, forward_message,
         mark_message, star_message, move_message, delete_message
pass__   list_vaults, list_items, search_items, get_item, get_totp,
         create_item, update_item, trash_item, generate_password
drive__  list, mkdir, upload, upload_folder, download, delete
vpn__    status
```

Full parameter reference: `tools-reference.md` (load on demand — it's long).
Recipes for common multi-tool workflows: `workflows.md` (load when the user describes a multi-step task).

## Core mental model — sequence numbers are folder-scoped

The single most important gotcha. Mail tools identify messages by an **IMAP sequence number** that only makes sense inside a specific folder. The same number `237` points to a different message in INBOX vs. Sent vs. Archive.

- Every tool that takes `message_id` also takes an optional `folder` (default `INBOX`).
- `mail__search_messages` and `mail__list_folder_messages` return results tagged with their `folder` — pass that folder along in any follow-up call.
- If a follow-up call returns "Message not found", you probably have the wrong folder.
- Moving a message changes its sequence number. After `mail__move_message`, re-look-up the message before using it again.

## Security rules — non-negotiable

<CREDENTIAL-HANDLING>
1. **`pass__get_item` returns the actual password.** Use it only when the user asked you to act on a credential. Never paste the password back into the chat verbatim unless the user explicitly asked to see it. Prefer summaries ("retrieved the password for github.com") and pass the credential into the next tool call directly.
2. **`pass__get_totp` returns a one-time 2FA code.** Same rule — feed it into the next step (login form, etc.) rather than echoing it for no reason.
3. `pass__list_items` and `pass__search_items` deliberately strip passwords and TOTP seeds — they're safe to display freely.
4. `pass__generate_password` produces a fresh random password — fine to show the user (they need to see it to use it).
5. The Bridge app password (in env config) is NOT the user's Proton account password. Never suggest typing the Proton account password into Bridge — it won't work, and asking confuses the user.
</CREDENTIAL-HANDLING>

## Mail patterns to use, not reinvent

- **Want to find a specific email?** `mail__search_messages` first (it searches INBOX + Sent + Drafts + Archive in one call). Only fall back to `mail__list_messages` if the user wants a chronological browse.
- **Want the full body of a search hit?** Pass the `id` AND `folder` from the search result into `mail__get_message`.
- **Reading a conversation?** `mail__get_thread` reconstructs the chain across INBOX / Sent / Archive. For standalone notification mail (e.g. Glassdoor blasts) it short-circuits to just the seed message — that's correct behavior, not a bug.
- **Replying or forwarding?** `mail__reply_message` and `mail__forward_message` preserve threading headers automatically. Both accept an optional `from_` to override the sender alias.
- **Cleaning up?** `mail__delete_message` actually moves to Trash on Proton — it's safe to use even when the user says "delete". For "really delete forever" you'd have to move to Trash, then delete from Trash.

## Drive patterns

- `drive__list` defaults to root. Pass a relative path (`reports/q4`) to navigate deeper.
- `drive__upload` and `drive__download` use rclone `copyto` semantics: the destination path is the **exact filename**, not a parent directory. So `download(remote_path="reports/q4.pdf", local_path="C:/Users/me/q4.pdf")` does what you expect.
- `drive__upload_folder` is the recursive cousin — destination is a directory.
- `drive__delete` works on both files and folders (auto-detects).

## VPN

- `vpn__status` returns current public IP, city, country, and `connected: true` if traffic is exiting through a known Proton VPN provider (Datacamp, M247, DataPacket, etc.). Useful when the user asks "am I on VPN" or before any operation where they want to confirm privacy.

## When NOT to use this skill

- The user asked about something Proton-shaped but **doesn't have the Proton MCP installed**. Tool calls will error out with `command failed` / `IMAP connection refused`. Tell them to install proton-mcp first (the bundle plus Proton Bridge running) and stop. Don't keep retrying.
- The user wants to set up a NEW Proton account, recover a lost password, or change account settings. Those go through the Proton web app, not this MCP.

## Prerequisites the tools depend on

These have to be live on the user's machine — the skill won't make them so:
- **Proton Mail Bridge** running (mail__* tools)
- **pass-cli** installed and logged in (pass__* tools)
- **rclone** with a remote literally named `protondrive` (drive__* tools)

If a tool call fails with "Is Proton Bridge running?", "pass-cli not found", or "rclone: command not found", surface that to the user verbatim — those error messages are deliberately diagnostic.

## Reference files in this skill

- `tools-reference.md` — full per-tool parameter list, return shape, examples
- `workflows.md` — multi-step recipes (find-and-reply, secure-credential-update, drive-backup, etc.)

Load them only when you need them. Keeping this main file lean keeps the prompt cheap.
