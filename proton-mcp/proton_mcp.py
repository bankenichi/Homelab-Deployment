#!/usr/bin/env python3
# proton_mcp.py
"""
Standalone Python MCP server for the Proton privacy suite.

Exposes the same 31 tools as the Node version (index.js) — Mail (15),
Pass (9), Drive (6), VPN (1) — but as a FastMCP stdio server so it can be
plugged into OpenCode or any other Python-friendly MCP host without
needing Node.

Configuration (same env vars as the Node MCP):

  PROTON_BRIDGE_HOST          default 127.0.0.1
  PROTON_BRIDGE_IMAP_PORT     default 1143
  PROTON_BRIDGE_SMTP_PORT     default 1025
  PROTON_BRIDGE_USERNAME      required (your Proton account email)
  PROTON_BRIDGE_PASSWORD      required (Bridge-generated app password)
  PROTON_BRIDGE_FROM          optional (defaults to USERNAME)
  PROTON_SIGNATURE            optional (HTML appended to outgoing mail)
  PROTON_PASS_BIN             default 'pass-cli'
  PROTON_PASS_VAULT           default 'Personal'
  RCLONE_BIN                  default 'rclone'
  PROTON_DRIVE_REMOTE         default 'protondrive:'

Run:
    python3 proton_mcp.py

Or wire it into your MCP host's config as a stdio server with
`command: python3` and `args: [path/to/proton_mcp.py]`.
"""
from __future__ import annotations

import asyncio
import base64
import email
import email.utils
import imaplib
import json
import os
import re
import smtplib
import socket
import ssl
import subprocess
import sys
import urllib.request
from email.message import EmailMessage
from email.parser import BytesParser
from email.policy import default as default_policy
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

server = FastMCP("proton-mcp")


# ============================================================================
# Config
# ============================================================================

# The Python MCP host (OpenCode, etc.) typically just launches this script
# and connects over stdio — it does NOT inject Proton Bridge credentials via
# env vars. So the script self-discovers its config from these sources, in
# priority order:
#
#   1. Process environment (os.environ)            — set if the host does pass them
#   2. ~/.proton-mcp/bridge.json                   — same path the Node MCP reads
#   3. .env file next to this script               — `KEY=value` per line
#   4. .env file in the current working directory
#
# Values found earlier in the chain win. Missing creds raise a clear error
# only when a tool that actually needs them is called.

_SCRIPT_DIR = Path(__file__).resolve().parent
_CONFIG_CACHE: dict | None = None


def _parse_dotenv(path: Path) -> dict[str, str]:
    """Minimal .env parser — `KEY=VALUE` lines, ignores blanks / # comments,
    strips matched surrounding quotes. No shell expansion."""
    out: dict[str, str] = {}
    try:
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[len("export "):].lstrip()
            if "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip()
            if (len(val) >= 2 and val[0] == val[-1] and val[0] in ("'", '"')):
                val = val[1:-1]
            if key:
                out[key] = val
    except OSError:
        pass
    return out


def _load_bridge_json() -> dict[str, str]:
    """Load ~/.proton-mcp/bridge.json if present. Same path & schema as the
    Node MCP, so a single config file serves both implementations."""
    home = Path(os.environ.get("HOME") or os.path.expanduser("~"))
    candidates = [
        home / ".proton-mcp" / "bridge.json",
        Path("/home/node/.proton-mcp/bridge.json"),  # docker/container fallback
    ]
    for p in candidates:
        try:
            if not p.exists():
                continue
            data = json.loads(p.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                continue
            # Normalize bridge.json's schema (lowercase, no PROTON_ prefix)
            # back into the env-var schema this module uses everywhere else.
            mapping = {
                "username":  "PROTON_BRIDGE_USERNAME",
                "password":  "PROTON_BRIDGE_PASSWORD",
                "imap_host": "PROTON_BRIDGE_HOST",
                "imap_port": "PROTON_BRIDGE_IMAP_PORT",
                "smtp_port": "PROTON_BRIDGE_SMTP_PORT",
                "from":      "PROTON_BRIDGE_FROM",
                "signature": "PROTON_SIGNATURE",
            }
            out: dict[str, str] = {}
            for k, env_key in mapping.items():
                if k in data and data[k] not in (None, ""):
                    out[env_key] = str(data[k])
            return out
        except (OSError, ValueError):
            continue
    return {}


def _resolve_env() -> dict[str, str]:
    """Merge config sources in priority order. Cached for the process
    lifetime — restart the server to pick up changed config."""
    global _CONFIG_CACHE
    if _CONFIG_CACHE is not None:
        return _CONFIG_CACHE

    merged: dict[str, str] = {}
    # Lowest priority first so higher-priority sources can overwrite.
    merged.update(_parse_dotenv(Path.cwd() / ".env"))
    merged.update(_parse_dotenv(_SCRIPT_DIR / ".env"))
    merged.update(_load_bridge_json())
    # os.environ wins — it's what an MCP host would inject, if anything.
    for k, v in os.environ.items():
        if v != "":
            merged[k] = v

    _CONFIG_CACHE = merged
    return merged


def _env(name: str, default: str = "") -> str:
    v = _resolve_env().get(name)
    return v if v is not None and v != "" else default

def _load_signature() -> str:
    """Load the HTML signature from a file if PROTON_SIGNATURE is not set.
    
    Looks for `html_signature.txt` (or `signature.html`) in:
      1. The directory next to this script
      2. ~/.proton-mcp/
    Returns empty string if none found.
    """
    home = Path(os.environ.get("HOME") or os.path.expanduser("~"))
    candidates = [
    _SCRIPT_DIR / "html_signature.txt",
    _SCRIPT_DIR / "html signature.txt",
    _SCRIPT_DIR / "signature.html",
    home / ".proton-mcp" / "html_signature.txt",
    home / ".proton-mcp" / "html signature.txt",
    home / ".proton-mcp" / "signature.html",
]
    for p in candidates:
        try:
            if p.exists():
                return p.read_text(encoding="utf-8").strip()
        except OSError:
            pass
    return ""

def _config() -> dict:
    """Resolved Proton Bridge / pass-cli / rclone configuration."""
    user = _env("PROTON_BRIDGE_USERNAME") or _env("PROTON_BRIDGE_USER")
    pw   = _env("PROTON_BRIDGE_PASSWORD") or _env("PROTON_BRIDGE_PASS")
    return {
        "imap_host": _env("PROTON_BRIDGE_HOST", "127.0.0.1"),
        "imap_port": int(_env("PROTON_BRIDGE_IMAP_PORT", "1143")),
        "smtp_host": _env("PROTON_BRIDGE_HOST", "127.0.0.1"),
        "smtp_port": int(_env("PROTON_BRIDGE_SMTP_PORT", "1025")),
        "username":  user,
        "password":  pw,
        "from":      _env("PROTON_BRIDGE_FROM", user),
        "signature": _env("PROTON_SIGNATURE") or _load_signature(),
        "pass_bin":  _env("PROTON_PASS_BIN", "pass-cli"),
        "pass_vault": _env("PROTON_PASS_VAULT", "Personal"),
        "rclone_bin": _env("RCLONE_BIN", "rclone"),
        "drive_remote": _env("PROTON_DRIVE_REMOTE", "protondrive:"),
    }


def _require_bridge(cfg: dict) -> None:
    if not cfg["username"] or not cfg["password"]:
        raise RuntimeError(
            "Proton Bridge credentials missing. Provide them via any of:\n"
            "  - environment variables PROTON_BRIDGE_USERNAME and PROTON_BRIDGE_PASSWORD\n"
            "  - a .env file next to proton_mcp.py\n"
            "  - ~/.proton-mcp/bridge.json with `username` and `password` keys"
        )


# ============================================================================
# IMAP / SMTP helpers
# ============================================================================

class ImapSession:
    """Short-lived IMAP connection, used in a `with` block."""

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.imap: imaplib.IMAP4 | None = None

    def __enter__(self) -> imaplib.IMAP4:
        _require_bridge(self.cfg)
        # Proton Bridge serves IMAP unencrypted on localhost by default —
        # mirrors the Node MCP's `tls: false`.
        self.imap = imaplib.IMAP4(self.cfg["imap_host"], self.cfg["imap_port"])
        self.imap.login(self.cfg["username"], self.cfg["password"])
        return self.imap

    def __exit__(self, exc_type, exc, tb) -> None:
        if self.imap is None:
            return
        try:
            if self.imap.state == "SELECTED":
                try:
                    self.imap.close()
                except Exception:
                    pass
            self.imap.logout()
        except Exception:
            pass


def _select(imap: imaplib.IMAP4, folder: str, readonly: bool = True) -> int:
    """Open a folder and return total message count."""
    status, data = imap.select(_imap_folder(folder), readonly=readonly)
    if status != "OK":
        raise RuntimeError(f"Cannot open folder {folder!r}: {data!r}")
    try:
        return int(data[0])
    except (ValueError, IndexError):
        return 0


def _imap_folder(name: str) -> str:
    """Quote folder names with spaces or special chars."""
    if any(c in name for c in (' ', '"', '/')):
        return f'"{name}"'
    return name


def _parse_message(raw: bytes) -> dict:
    """Parse a raw RFC822 message into the shape the Node MCP returns."""
    msg = BytesParser(policy=default_policy).parsebytes(raw)
    return {
        "subject": msg.get("Subject", "(no subject)"),
        "from": str(msg.get("From", "")),
        "to": str(msg.get("To", "")),
        "cc": str(msg.get("Cc", "") or ""),
        "date": _iso_date(msg.get("Date", "")),
        "body": _extract_body(msg),
        "message_id_header": msg.get("Message-ID"),
        "in_reply_to": msg.get("In-Reply-To"),
        "references": _refs_list(msg.get("References")),
    }


def _iso_date(raw: str) -> str:
    if not raw:
        return ""
    try:
        dt = email.utils.parsedate_to_datetime(raw)
        return dt.isoformat() if dt else ""
    except Exception:
        return ""


def _refs_list(raw: str | None) -> list[str]:
    """References header is space-separated <id> tokens. Always return a list."""
    if not raw:
        return []
    return re.findall(r"<[^>]+>", raw)


def _extract_body(msg: email.message.Message) -> str:
    """Prefer text/plain, fall back to text/html, strip to text."""
    if msg.is_multipart():
        plain = None
        html = None
        for part in msg.walk():
            ctype = part.get_content_type()
            if part.get_content_disposition() == "attachment":
                continue
            if ctype == "text/plain" and plain is None:
                try:
                    plain = part.get_content()
                except Exception:
                    plain = part.get_payload(decode=True, errors="replace") or ""
                    if isinstance(plain, bytes):
                        plain = plain.decode("utf-8", errors="replace")
            elif ctype == "text/html" and html is None:
                try:
                    html = part.get_content()
                except Exception:
                    html = part.get_payload(decode=True, errors="replace") or ""
                    if isinstance(html, bytes):
                        html = html.decode("utf-8", errors="replace")
        return plain or html or ""
    try:
        return msg.get_content()
    except Exception:
        payload = msg.get_payload(decode=True) or b""
        if isinstance(payload, bytes):
            return payload.decode("utf-8", errors="replace")
        return str(payload)


def _fetch_seqnos(imap: imaplib.IMAP4, seqnos: list[int],
                  bodies: str = "RFC822") -> list[dict]:
    """Fetch by sequence numbers, return parsed messages with id field."""
    if not seqnos:
        return []
    seqset = ",".join(str(n) for n in seqnos)
    status, data = imap.fetch(seqset, f"({bodies})")
    if status != "OK":
        return []
    out: list[dict] = []
    # data is a list of tuples + flat bytes; pair them up by message
    current_seq: int | None = None
    for item in data:
        if isinstance(item, tuple):
            envelope = item[0].decode("utf-8", errors="replace")
            raw = item[1]
            m = re.match(r"^(\d+)\s", envelope)
            current_seq = int(m.group(1)) if m else None
            parsed = _parse_message(raw)
            parsed["id"] = current_seq
            out.append(parsed)
    return out


def _fetch_one(imap: imaplib.IMAP4, seqno: int, bodies: str = "RFC822") -> dict | None:
    msgs = _fetch_seqnos(imap, [seqno], bodies)
    return msgs[0] if msgs else None


def _check_seen(imap: imaplib.IMAP4) -> set[int]:
    """Return the set of UNSEEN sequence numbers in the current folder."""
    status, data = imap.search(None, "UNSEEN")
    if status != "OK" or not data or not data[0]:
        return set()
    return {int(x) for x in data[0].split()}


# ============================================================================
# Mail — read tools
# ============================================================================

def _result(payload: Any) -> str:
    """Serialize a tool result to JSON for the MCP wire."""
    return json.dumps(payload, default=str, ensure_ascii=False)


def _error(msg: str) -> str:
    return _result({"error": msg})


@server.tool(name="mail__get_unread")
async def mail_get_unread() -> str:
    """List unseen messages in the Proton inbox.

    Returns a JSON object with the unseen count plus summaries (id, subject,
    from, date) of up to the 20 most recent. Tags are scoped to INBOX.
    """
    try:
        cfg = _config()
        with ImapSession(cfg) as imap:
            _select(imap, "INBOX", readonly=True)
            status, data = imap.search(None, "UNSEEN")
            if status != "OK":
                return _error("search failed")
            uids = data[0].split() if data and data[0] else []
            if not uids:
                return _result({"count": 0, "messages": []})
            recent = [int(u) for u in uids[-20:]]
            msgs = _fetch_seqnos(imap, recent, "RFC822.HEADER")
            return _result({
                "count": len(uids),
                "messages": [
                    {"id": m["id"], "subject": m["subject"],
                     "from": m["from"], "date": m["date"]}
                    for m in msgs
                ],
            })
    except Exception as e:
        return _error(str(e))


@server.tool(name="mail__list_messages")
async def mail_list_messages(limit: int = 10) -> str:
    """List recent Proton inbox messages (default 10, max 50)."""
    limit = max(1, min(50, int(limit)))
    try:
        cfg = _config()
        with ImapSession(cfg) as imap:
            total = _select(imap, "INBOX", readonly=True)
            if total == 0:
                return _result([])
            start = max(1, total - limit + 1)
            seqnos = list(range(start, total + 1))
            msgs = _fetch_seqnos(imap, seqnos, "RFC822.HEADER")
            unseen = _check_seen(imap)
            out = []
            for m in msgs:
                m = {k: v for k, v in m.items() if k != "body"}
                m["seen"] = m["id"] not in unseen
                out.append(m)
            out.reverse()
            return _result(out)
    except Exception as e:
        return _error(str(e))


@server.tool(name="mail__list_folder_messages")
async def mail_list_folder_messages(folder: str, limit: int = 10) -> str:
    """List recent messages in a specific Proton folder (Sent, Archive, etc.)."""
    limit = max(1, min(50, int(limit)))
    try:
        cfg = _config()
        with ImapSession(cfg) as imap:
            total = _select(imap, folder, readonly=True)
            if total == 0:
                return _result([])
            start = max(1, total - limit + 1)
            seqnos = list(range(start, total + 1))
            msgs = _fetch_seqnos(imap, seqnos, "RFC822.HEADER")
            out = []
            for m in msgs:
                m = {k: v for k, v in m.items() if k != "body"}
                m["folder"] = folder
                out.append(m)
            out.reverse()
            return _result(out)
    except Exception as e:
        return _error(str(e))


@server.tool(name="mail__list_folders")
async def mail_list_folders() -> str:
    """List all Proton mail folders and labels."""
    try:
        cfg = _config()
        with ImapSession(cfg) as imap:
            status, data = imap.list()
            if status != "OK":
                return _error("LIST failed")
            folders = []
            for line in data or []:
                if isinstance(line, bytes):
                    line = line.decode("utf-8", errors="replace")
                # Typical:  (\HasNoChildren) "/" "INBOX"
                m = re.match(r'^\([^)]*\)\s+"([^"]*)"\s+"?([^"]*)"?$', line)
                if m:
                    folders.append({"name": m.group(2), "delimiter": m.group(1)})
            return _result(folders)
    except Exception as e:
        return _error(str(e))


@server.tool(name="mail__get_message")
async def mail_get_message(message_id: int, folder: str = "INBOX") -> str:
    """Fetch a single message in full (subject, body, headers, threading info).

    `message_id` is the IMAP sequence number returned by list_messages /
    list_folder_messages / search_messages. Sequence numbers are scoped to a
    folder, so pass the same `folder` you got the id from.
    """
    try:
        cfg = _config()
        with ImapSession(cfg) as imap:
            _select(imap, folder, readonly=True)
            msg = _fetch_one(imap, int(message_id))
            if msg is None:
                return _error(f"Message {message_id} not found in {folder}")
            msg["folder"] = folder
            msg["seen"] = True
            return _result(msg)
    except Exception as e:
        return _error(str(e))


def _is_synthetic_ref(rid: str | None) -> bool:
    """Proton Bridge synthesizes per-message threading IDs ending in
    @protonmail.internalid. These don't match real Message-IDs anywhere
    else, so feeding them into IMAP HEADER searches is wasted work."""
    return bool(rid) and "@protonmail.internalid" in rid


def _build_or_search(refs: list[str]) -> list[str] | None:
    """Build a nested IMAP OR criteria for many HEADER Message-ID lookups.
    Returns None when refs is empty."""
    if not refs:
        return None
    if len(refs) == 1:
        return ["HEADER", "Message-ID", refs[0]]
    return ["OR", ["HEADER", "Message-ID", refs[0]], _build_or_search(refs[1:])]


def _flatten_search(criteria: list) -> list[str]:
    """Flatten nested list criteria into args imaplib.search accepts (each arg
    is a separate string token; literals containing spaces must be quoted)."""
    out: list[str] = []
    for item in criteria:
        if isinstance(item, list):
            out.extend(_flatten_search(item))
        else:
            out.append(str(item))
    return out


@server.tool(name="mail__get_thread")
async def mail_get_thread(message_id: int, folder: str = "INBOX") -> str:
    """Reconstruct an email thread starting from a given message.

    Looks across INBOX, Sent, and Archive. Standalone messages (no real
    In-Reply-To / References) return just the seed without paying for the
    cross-folder scan, which keeps Glassdoor-style notification mails fast.
    """
    try:
        cfg = _config()
        with ImapSession(cfg) as imap:
            _select(imap, folder, readonly=True)
            seed = _fetch_one(imap, int(message_id), "RFC822.HEADER")
            if seed is None:
                return _error(f"Message {message_id} not found in {folder}")

            real_refs = [r for r in (seed.get("references") or [])
                         if not _is_synthetic_ref(r)]
            in_reply_to = seed.get("in_reply_to")
            if _is_synthetic_ref(in_reply_to):
                in_reply_to = None

            # Pull the seed's full body so we can return at least that.
            seed_full = _fetch_one(imap, int(message_id))
            collected: list[dict] = []
            if seed_full is not None:
                seed_full["folder"] = folder
                collected.append(seed_full)

            # Standalone message — short-circuit, skip the expensive scan.
            if not real_refs and not in_reply_to:
                return _result(collected)

            ref_ids: list[str] = []
            if seed.get("message_id_header"):
                ref_ids.append(seed["message_id_header"])
            ref_ids.extend(real_refs)
            if in_reply_to:
                ref_ids.append(in_reply_to)
            ref_ids = [r for r in ref_ids if r and not _is_synthetic_ref(r)]

            or_criteria = _build_or_search(ref_ids)
            if or_criteria is None:
                return _result(collected)
            search_args = _flatten_search([or_criteria])

            for f in ("INBOX", "Sent", "Archive"):
                try:
                    _select(imap, f, readonly=True)
                    status, data = imap.search(None, *search_args)
                    if status != "OK" or not data or not data[0]:
                        continue
                    seqnos = [int(x) for x in data[0].split()]
                    if f == folder:
                        seqnos = [n for n in seqnos if n != int(message_id)]
                    if not seqnos:
                        continue
                    msgs = _fetch_seqnos(imap, seqnos)
                    for m in msgs:
                        m["folder"] = f
                        collected.append(m)
                except Exception:
                    continue

            # Dedupe by Message-ID, sort by date ascending.
            seen_ids: set[str] = set()
            unique: list[dict] = []
            for m in collected:
                key = m.get("message_id_header") or f"{m.get('folder')}:{m.get('id')}"
                if key in seen_ids:
                    continue
                seen_ids.add(key)
                unique.append(m)
            unique.sort(key=lambda m: m.get("date") or "")
            return _result(unique)
    except Exception as e:
        return _error(str(e))


@server.tool(name="mail__search_messages")
async def mail_search_messages(query: str) -> str:
    """Search for a keyword across INBOX, Sent, Drafts, and Archive.

    Returns up to 20 most-recent matches with folder labels.
    """
    try:
        cfg = _config()
        all_results: list[dict] = []
        with ImapSession(cfg) as imap:
            for f in ("INBOX", "Sent", "Drafts", "Archive"):
                try:
                    _select(imap, f, readonly=True)
                    status, data = imap.search(None, "TEXT", f'"{query}"')
                    if status != "OK" or not data or not data[0]:
                        continue
                    seqnos = [int(x) for x in data[0].split()][-10:]
                    msgs = _fetch_seqnos(imap, seqnos, "RFC822.HEADER")
                    for m in msgs:
                        m = {k: v for k, v in m.items() if k != "body"}
                        m["folder"] = f
                        all_results.append(m)
                except Exception:
                    continue
        all_results.sort(key=lambda m: m.get("date") or "", reverse=True)
        return _result(all_results[:20])
    except Exception as e:
        return _error(str(e))


@server.tool(name="mail__get_attachments")
async def mail_get_attachments(message_id: int, folder: str = "INBOX") -> str:
    """Download attachments from a message. Each attachment is returned as
    base64-encoded content along with filename, content type, and size."""
    try:
        cfg = _config()
        with ImapSession(cfg) as imap:
            _select(imap, folder, readonly=True)
            status, data = imap.fetch(str(int(message_id)), "(RFC822)")
            if status != "OK":
                return _error(f"Fetch failed for {message_id}")
            raw = b""
            for item in data or []:
                if isinstance(item, tuple):
                    raw = item[1]
                    break
            if not raw:
                return _error(f"Message {message_id} not found in {folder}")
            msg = BytesParser(policy=default_policy).parsebytes(raw)
            attachments = []
            for part in msg.walk():
                if part.get_content_disposition() != "attachment":
                    continue
                payload = part.get_payload(decode=True) or b""
                attachments.append({
                    "filename": part.get_filename() or "",
                    "contentType": part.get_content_type(),
                    "size": len(payload),
                    "content": base64.b64encode(payload).decode("ascii"),
                })
            return _result(attachments)
    except Exception as e:
        return _error(str(e))


# ============================================================================
# Mail — write tools (send / reply / forward / flag / move / delete)
# ============================================================================

def _is_html(s: str) -> bool:
    return bool(s) and bool(re.search(r"<[a-zA-Z][^>]*>", s))


def _html_to_plain(s: str) -> str:
    s = re.sub(r"<br\s*/?>", "\n", s, flags=re.IGNORECASE)
    s = re.sub(r"</p>", "\n", s, flags=re.IGNORECASE)
    s = re.sub(r"<[^>]+>", "", s)
    s = (s.replace("&amp;", "&").replace("&lt;", "<")
           .replace("&gt;", ">").replace("&nbsp;", " "))
    return s.strip()


def _build_outgoing(
    cfg: dict,
    *,
    sender: str,
    to: str,
    cc: str = "",
    bcc: str = "",
    subject: str,
    body: str,
    html: str = "",
    in_reply_to: str | None = None,
    references: str | None = None,
    attachments: list[dict] | None = None,
) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = to
    if cc:
        msg["Cc"] = cc
    if bcc:
        # smtplib still uses the envelope BCC; the header isn't strictly needed
        msg["Bcc"] = bcc
    msg["Subject"] = subject
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
    if references:
        msg["References"] = references

    sig = cfg.get("signature", "") or ""
    sig_is_html = _is_html(sig)

    if html:
        full_html = html + (f"<br><br>{sig}" if sig_is_html else
                            (f"<br><br>{sig.replace(chr(10), '<br>')}" if sig else ""))
        full_text = body + ("\n\n" + (_html_to_plain(sig) if sig_is_html else sig)
                            if sig else "")
        msg.set_content(full_text)
        msg.add_alternative(full_html, subtype="html")
    elif sig_is_html:
        escaped = (body.replace("&", "&amp;").replace("<", "&lt;")
                       .replace(">", "&gt;").replace("\n", "<br>"))
        full_html = f"{escaped}<br><br>{sig}"
        full_text = body + "\n\n" + _html_to_plain(sig)
        msg.set_content(full_text)
        msg.add_alternative(full_html, subtype="html")
    else:
        text = body + (f"\n\n{sig}" if sig else "")
        msg.set_content(text)

    for att in (attachments or []):
        filename = att.get("filename") or "attachment"
        ctype = att.get("contentType") or "application/octet-stream"
        maintype, _, subtype = ctype.partition("/")
        if att.get("path"):
            data = Path(att["path"]).read_bytes()
        elif att.get("content"):
            data = base64.b64decode(att["content"])
        else:
            continue
        msg.add_attachment(data, maintype=maintype or "application",
                           subtype=subtype or "octet-stream",
                           filename=filename)
    return msg


def _send_smtp(cfg: dict, msg: EmailMessage) -> str:
    _require_bridge(cfg)
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    with smtplib.SMTP(cfg["smtp_host"], cfg["smtp_port"], timeout=30) as smtp:
        smtp.ehlo()
        if smtp.has_extn("STARTTLS"):
            smtp.starttls(context=ctx)
            smtp.ehlo()
        smtp.login(cfg["username"], cfg["password"])
        smtp.send_message(msg)
    return msg.get("Message-ID") or ""


@server.tool(name="mail__send_message")
async def mail_send_message(
    to: str,
    subject: str,
    body: str,
    from_: str = "",
    html: str = "",
    cc: str = "",
    bcc: str = "",
) -> str:
    """Send a new email via Proton Mail.

    Pass `from_` to override the sender address; otherwise the configured
    PROTON_BRIDGE_FROM (or PROTON_BRIDGE_USERNAME) is used.
    """
    try:
        cfg = _config()
        sender = from_ or cfg["from"] or cfg["username"]
        msg = _build_outgoing(
            cfg, sender=sender, to=to, cc=cc, bcc=bcc,
            subject=subject, body=body, html=html,
        )
        mid = _send_smtp(cfg, msg)
        return _result({"success": True, "message_id": mid})
    except Exception as e:
        return _error(str(e))


def _get_headers(cfg: dict, message_id: int, folder: str) -> dict:
    with ImapSession(cfg) as imap:
        _select(imap, folder, readonly=True)
        m = _fetch_one(imap, int(message_id), "RFC822.HEADER")
        if m is None:
            raise RuntimeError(f"Message {message_id} not found in {folder}")
        return m


@server.tool(name="mail__reply_message")
async def mail_reply_message(
    message_id: int,
    body: str,
    reply_all: bool = False,
    cc: str = "",
    bcc: str = "",
    folder: str = "INBOX",
    from_: str = "",
) -> str:
    """Reply to a message, preserving the thread headers.

    `reply_all=True` includes the original To/Cc recipients (excluding our own
    address). `folder` is where the message lives — defaults to INBOX.
    """
    try:
        cfg = _config()
        h = _get_headers(cfg, message_id, folder)

        subject = h.get("subject") or ""
        if not subject.startswith("Re:"):
            subject = f"Re: {subject}"

        # References = previous References + previous Message-ID
        refs_arr = h.get("references") or []
        if h.get("message_id_header"):
            refs_arr = refs_arr + [h["message_id_header"]]
        refs_str = " ".join(r for r in refs_arr if r)

        to = h.get("from") or ""
        if reply_all:
            own = (cfg["username"] or "").lower()
            all_addrs = [a for a in [h.get("from"), h.get("to"), h.get("cc")] if a]
            joined = ", ".join(all_addrs)
            filtered = [
                a.strip() for a in re.split(r",\s*", joined)
                if a.strip() and own not in a.lower()
            ]
            to = ", ".join(filtered) or h.get("from") or ""

        sender = from_ or cfg["from"] or cfg["username"]
        msg = _build_outgoing(
            cfg, sender=sender, to=to, cc=cc, bcc=bcc,
            subject=subject, body=body,
            in_reply_to=h.get("message_id_header"),
            references=refs_str or None,
        )
        mid = _send_smtp(cfg, msg)
        return _result({"success": True, "message_id": mid})
    except Exception as e:
        return _error(str(e))


@server.tool(name="mail__forward_message")
async def mail_forward_message(
    message_id: int,
    to: str,
    body: str = "",
    cc: str = "",
    bcc: str = "",
    folder: str = "INBOX",
    from_: str = "",
) -> str:
    """Forward a message to another recipient. Prepends `body` (if provided)
    above the forwarded content."""
    try:
        cfg = _config()
        with ImapSession(cfg) as imap:
            _select(imap, folder, readonly=True)
            original = _fetch_one(imap, int(message_id))
        if original is None:
            return _error(f"Message {message_id} not found in {folder}")

        subject = original.get("subject") or ""
        if not subject.startswith("Fwd:"):
            subject = f"Fwd: {subject}"

        fwd_body = (
            f"{body}\n\n---------- Forwarded message ----------\n"
            f"From: {original.get('from','')}\n"
            f"Date: {original.get('date','')}\n"
            f"Subject: {original.get('subject','')}\n"
            f"To: {original.get('to','')}\n\n"
            f"{original.get('body','')}"
        )

        sender = from_ or cfg["from"] or cfg["username"]
        msg = _build_outgoing(
            cfg, sender=sender, to=to, cc=cc, bcc=bcc,
            subject=subject, body=fwd_body,
        )
        mid = _send_smtp(cfg, msg)
        return _result({"success": True, "message_id": mid})
    except Exception as e:
        return _error(str(e))


@server.tool(name="mail__mark_message")
async def mail_mark_message(message_id: int, read: bool,
                             folder: str = "INBOX") -> str:
    """Mark a message as read (`read=true`) or unread (`read=false`)."""
    try:
        cfg = _config()
        with ImapSession(cfg) as imap:
            _select(imap, folder, readonly=False)
            cmd = "+FLAGS" if read else "-FLAGS"
            status, _ = imap.store(str(int(message_id)), cmd, r"\Seen")
            if status != "OK":
                return _error(f"STORE failed for {message_id}")
        return _result({"success": True, "message_id": message_id,
                        "folder": folder, "read": read})
    except Exception as e:
        return _error(str(e))


@server.tool(name="mail__star_message")
async def mail_star_message(message_id: int, star: bool,
                             folder: str = "INBOX") -> str:
    """Star (`star=true`) or unstar (`star=false`) a message."""
    try:
        cfg = _config()
        with ImapSession(cfg) as imap:
            _select(imap, folder, readonly=False)
            cmd = "+FLAGS" if star else "-FLAGS"
            status, _ = imap.store(str(int(message_id)), cmd, r"\Flagged")
            if status != "OK":
                return _error(f"STORE failed for {message_id}")
        return _result({"success": True, "message_id": message_id,
                        "folder": folder, "starred": star})
    except Exception as e:
        return _error(str(e))


@server.tool(name="mail__delete_message")
async def mail_delete_message(message_id: int, folder: str = "INBOX") -> str:
    """Permanently delete a message from the given folder (defaults to INBOX).
    On Proton, this typically moves it to Trash."""
    try:
        cfg = _config()
        with ImapSession(cfg) as imap:
            _select(imap, folder, readonly=False)
            status, _ = imap.store(str(int(message_id)), "+FLAGS", r"\Deleted")
            if status != "OK":
                return _error(f"STORE failed for {message_id}")
            imap.expunge()
        return _result({"success": True, "message_id": message_id,
                        "folder": folder, "deleted": True})
    except Exception as e:
        return _error(str(e))


@server.tool(name="mail__move_message")
async def mail_move_message(message_id: int, destination: str,
                             folder: str = "INBOX") -> str:
    """Move a message from `folder` (default INBOX) to `destination`. Uses
    COPY + STORE \\Deleted + EXPUNGE for maximum IMAP compatibility."""
    try:
        cfg = _config()
        with ImapSession(cfg) as imap:
            _select(imap, folder, readonly=False)
            seqno = str(int(message_id))
            status, _ = imap.copy(seqno, _imap_folder(destination))
            if status != "OK":
                return _error(f"COPY to {destination} failed for {message_id}")
            imap.store(seqno, "+FLAGS", r"\Deleted")
            imap.expunge()
        return _result({"success": True, "message_id": message_id,
                        "moved_from": folder, "moved_to": destination})
    except Exception as e:
        return _error(str(e))


# ============================================================================
# Pass — wraps the `pass-cli` binary
# ============================================================================

def _run_pass(args: list[str], cfg: dict, timeout: int = 15,
              parse_json: bool = False) -> Any:
    """Run pass-cli with the configured binary. Returns parsed JSON if
    `parse_json` is True, otherwise stripped stdout. Raises RuntimeError with
    stderr on non-zero exit."""
    cmd = [cfg["pass_bin"]] + args
    if parse_json:
        cmd += ["--output", "json"]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True,
                           timeout=timeout, check=False)
    except FileNotFoundError as e:
        raise RuntimeError(
            f"{cfg['pass_bin']!r} not on PATH. Install Proton Pass CLI from "
            "https://proton.me/pass/download and run `pass-cli login`."
        ) from e
    if p.returncode != 0:
        raise RuntimeError((p.stderr or p.stdout).strip())
    out = (p.stdout or "").strip()
    if parse_json:
        return json.loads(out) if out else {}
    return out


def _safe_item(item: dict) -> dict:
    """Strip the password and TOTP seed from a pass-cli item dict."""
    content = item.get("content") or {}
    inner = content.get("content") or {}
    login = inner.get("Login") or {}
    return {
        "title": content.get("title"),
        "username": login.get("username") or login.get("email"),
        "urls": login.get("urls") or [],
        "state": item.get("state"),
    }


@server.tool(name="pass__list_vaults")
async def pass_list_vaults() -> str:
    """List available Proton Pass vaults (name + IDs)."""
    try:
        cfg = _config()
        return _result(_run_pass(["vault", "list"], cfg, parse_json=True))
    except Exception as e:
        return _error(str(e))


@server.tool(name="pass__list_items")
async def pass_list_items(vault: str = "") -> str:
    """List credentials in a vault (titles, usernames, URLs — no passwords).

    Defaults to PROTON_PASS_VAULT (or 'Personal' if unset).
    """
    try:
        cfg = _config()
        v = vault or cfg["pass_vault"]
        data = _run_pass(["item", "list", v], cfg, parse_json=True)
        items = [_safe_item(i) for i in (data.get("items") or [])]
        return _result(items)
    except Exception as e:
        return _error(str(e))


@server.tool(name="pass__search_items")
async def pass_search_items(query: str, vault: str = "") -> str:
    """Search vault items by keyword (titles, usernames, URLs — no passwords)."""
    try:
        cfg = _config()
        v = vault or cfg["pass_vault"]
        data = _run_pass(["item", "list", v], cfg, parse_json=True)
        q = query.lower()
        out = []
        for it in (data.get("items") or []):
            content = it.get("content") or {}
            inner = content.get("content") or {}
            login = inner.get("Login") or {}
            title = (content.get("title") or "").lower()
            user  = ((login.get("username") or login.get("email") or "")).lower()
            urls  = " ".join(login.get("urls") or []).lower()
            if q in title or q in user or q in urls:
                out.append(_safe_item(it))
        return _result(out)
    except Exception as e:
        return _error(str(e))


@server.tool(name="pass__get_item")
async def pass_get_item(name: str, vault: str = "") -> str:
    """Return the full credential (username, password, URLs, TOTP flag) for
    a Proton Pass item. Use deliberately — this returns the password."""
    try:
        cfg = _config()
        v = vault or cfg["pass_vault"]
        data = _run_pass(
            ["item", "view", "--item-title", name, "--vault-name", v],
            cfg, parse_json=True,
        )
        item = data.get("item") or {}
        content = item.get("content") or {}
        inner = content.get("content") or {}
        login = inner.get("Login") or {}
        return _result({
            "title": content.get("title"),
            "username": login.get("username") or login.get("email"),
            "password": login.get("password"),
            "urls": login.get("urls") or [],
            "note": content.get("note") or "",
            "has_totp": bool(login.get("totp_uri")),
        })
    except Exception as e:
        return _error(str(e))


@server.tool(name="pass__create_item")
async def pass_create_item(
    title: str,
    username: str = "",
    password: str = "",
    url: str = "",
    vault: str = "",
) -> str:
    """Store a new login credential in Proton Pass."""
    try:
        cfg = _config()
        v = vault or cfg["pass_vault"]
        args = ["item", "create", "login", "--vault-name", v, "--title", title]
        if username:
            args += ["--username", username]
        if password:
            args += ["--password", password]
        if url:
            args += ["--url", url]
        result = _run_pass(args, cfg)
        return _result({"success": True, "result": result})
    except Exception as e:
        return _error(str(e))


@server.tool(name="pass__update_item")
async def pass_update_item(
    name: str,
    username: str = "",
    password: str = "",
    vault: str = "",
) -> str:
    """Update an existing credential. Provide whichever fields you want to
    change (`username`, `password`)."""
    try:
        cfg = _config()
        v = vault or cfg["pass_vault"]
        args = ["item", "update", "--item-title", name, "--vault-name", v]
        if username:
            args += ["--field", f"username={username}"]
        if password:
            args += ["--field", f"password={password}"]
        result = _run_pass(args, cfg)
        return _result({"success": True, "result": result})
    except Exception as e:
        return _error(str(e))


@server.tool(name="pass__trash_item")
async def pass_trash_item(name: str, vault: str = "") -> str:
    """Move a credential to the Proton Pass trash."""
    try:
        cfg = _config()
        v = vault or cfg["pass_vault"]
        result = _run_pass(
            ["item", "trash", "--item-title", name, "--vault-name", v], cfg
        )
        return _result({"success": True, "result": result})
    except Exception as e:
        return _error(str(e))


@server.tool(name="pass__get_totp")
async def pass_get_totp(name: str, vault: str = "") -> str:
    """Generate the current TOTP code for a credential that has a TOTP seed
    stored. Use for autonomous 2FA flows."""
    try:
        cfg = _config()
        v = vault or cfg["pass_vault"]
        return _result(_run_pass(
            ["item", "totp", "--item-title", name, "--vault-name", v],
            cfg, parse_json=True,
        ))
    except Exception as e:
        return _error(str(e))


@server.tool(name="pass__generate_password")
async def pass_generate_password(length: int = 0) -> str:
    """Generate a random password using Proton Pass.

    `length` is optional — pass-cli's default is used when omitted.
    """
    try:
        cfg = _config()
        # pass-cli requires a subcommand under `password generate` — `random`
        # produces a character-based password; `passphrase` would produce
        # words. We default to `random` here.
        args = ["password", "generate", "random"]
        if length and int(length) > 0:
            args += ["--length", str(int(length))]
        out = _run_pass(args, cfg)
        return _result({"password": out})
    except Exception as e:
        return _error(str(e))


# ============================================================================
# Drive — wraps `rclone` against the configured Proton Drive remote
# ============================================================================

def _run_rclone(args: list[str], cfg: dict, timeout: int = 300) -> str:
    cmd = [cfg["rclone_bin"]] + args
    try:
        p = subprocess.run(cmd, capture_output=True, text=True,
                           timeout=timeout, check=False)
    except FileNotFoundError as e:
        raise RuntimeError(
            f"{cfg['rclone_bin']!r} not on PATH. Install rclone from "
            "https://rclone.org/install/ and configure a 'protondrive' remote."
        ) from e
    if p.returncode != 0:
        raise RuntimeError((p.stderr or p.stdout).strip())
    return (p.stdout or "").strip()


def _drive_path(cfg: dict, remote_path: str) -> str:
    remote = cfg["drive_remote"]
    if not remote.endswith(":") and not remote.endswith("/"):
        remote = remote + ":"
    return f"{remote}{remote_path}"


@server.tool(name="drive__list")
async def drive_list(path: str = "") -> str:
    """List files and folders at a path on Proton Drive (default: root)."""
    try:
        cfg = _config()
        out = _run_rclone(["lsjson", _drive_path(cfg, path)], cfg, timeout=60)
        if not out:
            return _result([])
        items = json.loads(out)
        safe = [{
            "name": i.get("Name"),
            "type": "folder" if i.get("IsDir") else "file",
            "size": i.get("Size"),
            "modified": i.get("ModTime"),
        } for i in items]
        return _result(safe)
    except Exception as e:
        return _error(str(e))


@server.tool(name="drive__mkdir")
async def drive_mkdir(remote_path: str) -> str:
    """Create a folder on Proton Drive."""
    try:
        cfg = _config()
        _run_rclone(["mkdir", _drive_path(cfg, remote_path)], cfg, timeout=60)
        return _result({"success": True, "path": remote_path})
    except Exception as e:
        return _error(str(e))


@server.tool(name="drive__upload")
async def drive_upload(local_path: str, remote_path: str) -> str:
    """Upload a single file to Proton Drive. `remote_path` is the exact
    destination filename — uses rclone `copyto` so the file isn't silently
    placed inside a directory named `remote_path`."""
    try:
        cfg = _config()
        _run_rclone(["copyto", local_path, _drive_path(cfg, remote_path)],
                    cfg, timeout=600)
        return _result({"success": True, "remote_path": remote_path})
    except Exception as e:
        return _error(str(e))


@server.tool(name="drive__upload_folder")
async def drive_upload_folder(local_path: str, remote_path: str) -> str:
    """Upload an entire folder to Proton Drive (recursive copy)."""
    try:
        cfg = _config()
        _run_rclone(["copy", local_path, _drive_path(cfg, remote_path)],
                    cfg, timeout=1800)
        return _result({"success": True, "remote_path": remote_path})
    except Exception as e:
        return _error(str(e))


@server.tool(name="drive__download")
async def drive_download(remote_path: str, local_path: str) -> str:
    """Download a file from Proton Drive to an exact local path. Uses rclone
    `copyto` so `local_path` is the destination filename, not a parent dir."""
    try:
        cfg = _config()
        _run_rclone(["copyto", _drive_path(cfg, remote_path), local_path],
                    cfg, timeout=600)
        return _result({"success": True, "local_path": local_path})
    except Exception as e:
        return _error(str(e))


@server.tool(name="drive__delete")
async def drive_delete(remote_path: str) -> str:
    """Delete a file or folder from Proton Drive. Tries `deletefile` first,
    falls back to `purge` for directories."""
    try:
        cfg = _config()
        target = _drive_path(cfg, remote_path)
        try:
            _run_rclone(["deletefile", target], cfg, timeout=120)
        except RuntimeError:
            _run_rclone(["purge", target], cfg, timeout=300)
        return _result({"success": True, "deleted": remote_path})
    except Exception as e:
        return _error(str(e))


# ============================================================================
# VPN — current IP / region / Proton routing detection
# ============================================================================

@server.tool(name="vpn__status")
async def vpn_status() -> str:
    """Check current IP, region, and whether traffic is exiting through a
    Proton VPN node. Looks at ipinfo.io and matches the org against known
    Proton VPN providers (Datacamp, M247, DataPacket, etc.)."""
    try:
        req = urllib.request.Request(
            "https://ipinfo.io/json",
            headers={"User-Agent": "proton-mcp/1.0"},
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            info = json.loads(resp.read().decode("utf-8", errors="replace"))
        org = (info.get("org") or "").lower()
        is_vpn = any(token in org for token in
                     ("datacamp", "protonvpn", "m247", "datapacket"))
        return _result({
            "connected": is_vpn,
            "ip": info.get("ip"),
            "city": info.get("city"),
            "region": info.get("region"),
            "country": info.get("country"),
            "org": info.get("org"),
            "timezone": info.get("timezone"),
        })
    except Exception as e:
        return _error(str(e))


# ============================================================================
# Entrypoint
# ============================================================================

if __name__ == "__main__":
    server.run()
