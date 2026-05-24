#!/usr/bin/env python3
"""
Proton MCP prerequisite checker / installer.

Checks for the desktop tools the Proton MCP server depends on:
  - Node.js >= 22         (runs the server)
  - Proton Bridge running (IMAP/SMTP on localhost)
  - pass-cli              (Proton Pass CLI)
  - rclone                (with a 'protondrive' remote)

Where possible, offers to install missing items via the OS package manager
(winget on Windows, brew on macOS, apt/dnf on Linux). For tools that can't
be auto-installed (Bridge GUI app, pass-cli, rclone remote config), it
prints download URLs and the exact next step.

Runs cross-platform. Re-run any time — idempotent.
"""
from __future__ import annotations

import os
import platform
import re
import shutil
import socket
import subprocess
import sys
from dataclasses import dataclass
from typing import Callable, Optional


# ---------- terminal helpers ----------

USE_COLOR = sys.stdout.isatty() and os.environ.get("NO_COLOR") is None

def _c(code: str, s: str) -> str:
    return f"\033[{code}m{s}\033[0m" if USE_COLOR else s

def ok(s: str)    -> str: return _c("32", s)
def warn(s: str)  -> str: return _c("33", s)
def err(s: str)   -> str: return _c("31", s)
def bold(s: str)  -> str: return _c("1", s)
def dim(s: str)   -> str: return _c("2", s)


def header(title: str) -> None:
    bar = "=" * len(title)
    print(f"\n{bold(title)}\n{bar}")


def status(label: str, state: str, detail: str = "") -> None:
    icon = {"ok": ok("[ OK ]"), "warn": warn("[WARN]"), "err": err("[FAIL]")}[state]
    msg = f"{icon} {label}"
    if detail:
        msg += f"  {dim(detail)}"
    print(msg)


def ask(prompt: str, default: bool = False) -> bool:
    suffix = " [Y/n] " if default else " [y/N] "
    while True:
        try:
            reply = input(prompt + suffix).strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            return False
        if not reply:
            return default
        if reply in ("y", "yes"):
            return True
        if reply in ("n", "no"):
            return False


# ---------- OS detection ----------

IS_WIN  = platform.system() == "Windows"
IS_MAC  = platform.system() == "Darwin"
IS_LIN  = platform.system() == "Linux"


def have(cmd: str) -> Optional[str]:
    """Return absolute path of cmd if on PATH, else None."""
    return shutil.which(cmd)


def run_capture(args: list[str], timeout: int = 15) -> tuple[int, str, str]:
    """Run a command, return (returncode, stdout, stderr). Never raises."""
    try:
        p = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return p.returncode, p.stdout or "", p.stderr or ""
    except FileNotFoundError:
        return 127, "", "command not found"
    except subprocess.TimeoutExpired:
        return 124, "", "timeout"
    except Exception as e:
        return 1, "", str(e)


# ---------- checks ----------

@dataclass
class CheckResult:
    state: str             # ok | warn | err
    detail: str = ""
    fix: Optional[Callable[[], bool]] = None  # returns True if a follow-up retry is worth doing
    fix_label: str = ""


def check_node() -> CheckResult:
    path = have("node")
    if not path:
        return CheckResult("err", "node not on PATH", fix=install_node, fix_label="Install Node.js LTS via package manager")
    rc, out, _ = run_capture(["node", "--version"])
    if rc != 0:
        return CheckResult("err", "node failed to report version")
    m = re.match(r"v(\d+)\.(\d+)\.(\d+)", out.strip())
    if not m:
        return CheckResult("warn", f"unexpected version string: {out.strip()}")
    major = int(m.group(1))
    if major < 22:
        return CheckResult(
            "err",
            f"need >= 22, found {out.strip()}",
            fix=install_node,
            fix_label="Upgrade Node.js via package manager",
        )
    return CheckResult("ok", f"{out.strip()} at {path}")


def check_bridge() -> CheckResult:
    """We can't tell if Bridge is installed without poking around, but we can
    tell whether it's *running* by checking the IMAP port."""
    host = os.environ.get("PROTON_BRIDGE_HOST", "127.0.0.1")
    port = int(os.environ.get("PROTON_BRIDGE_IMAP_PORT", "1143"))
    try:
        with socket.create_connection((host, port), timeout=2):
            pass
        return CheckResult("ok", f"IMAP reachable at {host}:{port}")
    except OSError as e:
        return CheckResult(
            "err",
            f"can't reach {host}:{port} ({e.__class__.__name__})",
            fix=hint_bridge,
            fix_label="Show Bridge install / launch instructions",
        )


def check_pass_cli() -> CheckResult:
    bin_name = os.environ.get("PROTON_PASS_BIN", "pass-cli")
    path = have(bin_name)
    if not path:
        return CheckResult(
            "err",
            f"{bin_name} not on PATH",
            fix=hint_pass_cli,
            fix_label="Show pass-cli download instructions",
        )
    # Probe it — `vault list` is a read-only call that works once logged in.
    rc, out, errout = run_capture([bin_name, "vault", "list", "--output", "json"])
    if rc != 0:
        snippet = (errout or out).strip().splitlines()[0:1]
        detail = snippet[0] if snippet else "vault list failed"
        return CheckResult("warn", f"installed at {path}, but: {detail}",
                           fix=hint_pass_login, fix_label="Show pass-cli login instructions")
    return CheckResult("ok", f"installed at {path}, logged in")


def check_rclone() -> CheckResult:
    path = have("rclone")
    if not path:
        return CheckResult(
            "err",
            "rclone not on PATH",
            fix=install_rclone,
            fix_label="Install rclone via package manager",
        )
    # Check protondrive remote exists.
    rc, out, _ = run_capture(["rclone", "listremotes"])
    if rc != 0:
        return CheckResult("warn", "installed but listremotes failed")
    remotes = [line.strip().rstrip(":") for line in out.splitlines() if line.strip()]
    remote_name = os.environ.get("PROTON_DRIVE_REMOTE", "protondrive:").rstrip(":")
    if remote_name not in remotes:
        return CheckResult(
            "warn",
            f"installed at {path}, but '{remote_name}' remote not configured",
            fix=hint_rclone_remote,
            fix_label=f"Show how to add '{remote_name}' remote",
        )
    return CheckResult("ok", f"installed, '{remote_name}' remote present")


# ---------- fixers / hints ----------

def install_node() -> bool:
    print()
    if IS_WIN:
        if have("winget"):
            if ask("Install Node.js LTS via winget?", default=True):
                rc = subprocess.run(["winget", "install", "-e", "--id", "OpenJS.NodeJS.LTS"]).returncode
                return rc == 0
        else:
            print("  No winget found. Install Node.js from https://nodejs.org/")
    elif IS_MAC:
        if have("brew"):
            if ask("Install Node via Homebrew?", default=True):
                rc = subprocess.run(["brew", "install", "node"]).returncode
                return rc == 0
        else:
            print("  No Homebrew found. Install Node.js from https://nodejs.org/")
    elif IS_LIN:
        # Try apt, then dnf — only if we can sudo.
        if have("apt-get") and ask("Install Node.js via apt? (requires sudo)", default=False):
            subprocess.run(["sudo", "apt-get", "update"])
            return subprocess.run(["sudo", "apt-get", "install", "-y", "nodejs", "npm"]).returncode == 0
        if have("dnf") and ask("Install Node.js via dnf? (requires sudo)", default=False):
            return subprocess.run(["sudo", "dnf", "install", "-y", "nodejs"]).returncode == 0
        print("  Install Node.js >= 22 from https://nodejs.org/ or via your distro.")
    return False


def install_rclone() -> bool:
    print()
    if IS_WIN:
        if have("winget"):
            if ask("Install rclone via winget?", default=True):
                rc = subprocess.run(["winget", "install", "-e", "--id", "Rclone.Rclone"]).returncode
                return rc == 0
        else:
            print("  Install rclone from https://rclone.org/install/")
    elif IS_MAC:
        if have("brew"):
            if ask("Install rclone via Homebrew?", default=True):
                return subprocess.run(["brew", "install", "rclone"]).returncode == 0
        else:
            print("  Install rclone from https://rclone.org/install/")
    elif IS_LIN:
        if have("apt-get") and ask("Install rclone via apt? (requires sudo)", default=False):
            subprocess.run(["sudo", "apt-get", "update"])
            return subprocess.run(["sudo", "apt-get", "install", "-y", "rclone"]).returncode == 0
        if have("dnf") and ask("Install rclone via dnf? (requires sudo)", default=False):
            return subprocess.run(["sudo", "dnf", "install", "-y", "rclone"]).returncode == 0
        print("  Install rclone from https://rclone.org/install/")
    return False


def hint_bridge() -> bool:
    print()
    print(bold("  Proton Bridge"))
    print("  1. Download:  https://proton.me/mail/bridge")
    print("  2. Install and sign in to your Proton account")
    print("  3. In Bridge → your account → IMAP/SMTP, copy the Bridge password")
    print("     (this is NOT your Proton account password — it's auto-generated)")
    print("  4. Leave Bridge running. You'll paste the Bridge password into")
    print("     Claude Desktop when it prompts on first run of the MCP.")
    return False


def hint_pass_cli() -> bool:
    print()
    print(bold("  pass-cli (Proton Pass CLI)"))
    print("  1. Download:  https://proton.me/pass/download")
    print("  2. Install, then run:  pass-cli login")
    print("  3. Authenticate with your Proton credentials")
    return False


def hint_pass_login() -> bool:
    print()
    print(bold("  pass-cli is installed but not logged in (or DB is locked)"))
    print("  Run:")
    print("    pass-cli logout --force")
    print("    pass-cli login")
    return False


def hint_rclone_remote() -> bool:
    print()
    name = os.environ.get("PROTON_DRIVE_REMOTE", "protondrive:").rstrip(":")
    print(bold(f"  Add an rclone remote named '{name}' using the Proton Drive backend"))
    print("  Run:")
    print("    rclone config")
    print("  Then choose:")
    print("    n  — New remote")
    print(f"    name>  {name}")
    print("    Storage>  protondrive   (Proton Drive)")
    print("  Follow the prompts to sign in with your Proton account.")
    return False


# ---------- main ----------

CHECKS = [
    ("Node.js >= 22",         check_node),
    ("Proton Bridge running", check_bridge),
    ("pass-cli (logged in)",  check_pass_cli),
    ("rclone + protondrive",  check_rclone),
]


def main() -> int:
    header("Proton MCP — prerequisite check")
    print(dim(f"  platform: {platform.system()} {platform.release()}"))
    print(dim(f"  python:   {sys.version.split()[0]}"))

    results: list[tuple[str, CheckResult]] = []
    for label, fn in CHECKS:
        try:
            results.append((label, fn()))
        except Exception as e:
            results.append((label, CheckResult("err", f"check crashed: {e}")))

    print()
    for label, r in results:
        status(label, r.state, r.detail)

    # Offer fixes for anything not OK.
    failing = [(label, r) for label, r in results if r.state != "ok" and r.fix is not None]
    if not failing:
        print()
        print(ok(bold("All prerequisites look good.")))
        return 0

    print()
    print(bold("Some items need attention:"))
    for label, r in failing:
        print(f"  - {label}: {r.fix_label}")

    print()
    if not ask("Walk through them now?", default=True):
        print("Re-run this script any time you want to retry.")
        return 1

    any_changed = False
    for label, r in failing:
        print()
        print(bold(f"-> {label}"))
        if r.fix:
            changed = r.fix()
            any_changed = any_changed or bool(changed)

    print()
    if any_changed:
        print(dim("Some checks may need a re-run after their installer finishes."))
    print("Re-run:  python3 setup.py")
    return 1 if failing else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print()
        sys.exit(130)
