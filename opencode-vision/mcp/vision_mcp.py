#!/usr/bin/env python3
# vision_mcp.py
"""Standalone MCP server exposing a local vision tool.

Bridges the OpenCode `opencode-vision` plugin to a dedicated `llama-server` running a
small VLM on port 8083. The plugin saves a pasted image to a temp file and tells the model
to call this server's tool with the path; this server reads the file, base64-inlines it as
an `image_url`, POSTs it to the VLM's OpenAI-compatible `/v1/chat/completions`, and returns
the model's textual description.

Registered tool name in OpenCode = "<mcp-config-key>_<tool-fn>". With the recommended
opencode.json key `vision` and the tool fn `analyze`, the model calls `vision_analyze`.

LIFECYCLE (controlled by VISION_SPAWN_SERVER, default on):
  - On import: NOTHING is spawned. Zero resources at OpenCode startup.
  - On first `analyze` call: lazily spawn `llama-server` on :8083 in a separate visible
    console window so the user can see boot output and Ctrl+C to terminate manually.
    The MCP first probes :8083 — if something is already responding (e.g. you ran
    run-vision.ps1 by hand), it reuses that and does NOT spawn a duplicate.
  - The spawned child is attached to a Windows Job Object with KILL_ON_JOB_CLOSE, so it
    is reaped by the OS the instant the MCP process exits (graceful or hard crash).
  - Idle watchdog: after VISION_LLAMA_IDLE_TIMEOUT seconds with no active calls, the
    spawned server is shut down to release RAM/VRAM. Subsequent calls re-spawn (cold).
    Set VISION_LLAMA_IDLE_TIMEOUT=0 to disable the auto-shutdown.

PORTABILITY: paths default to `{HOMELAB_ROOT}/opencode-vision/...` so the module works
regardless of where the repo lives, matching the rest of the homelab project. Override
via VISION_LLAMA_MODEL / VISION_LLAMA_MMPROJ / VISION_LLAMA_ARGS_FILE if needed.
"""

import asyncio
import atexit
import base64
import mimetypes
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import requests
from mcp.server.fastmcp import FastMCP

if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes  # noqa: F401  (used inside _create_job_kill_on_close)

# ==================== CONFIG ====================

HOMELAB_ROOT = (os.environ.get("HOMELAB_ROOT") or "").replace("\\", "/").rstrip("/")


def _truthy(env_value: str) -> bool:
    return env_value.strip().lower() not in ("0", "false", "no", "off", "")


def _default_under_root(rel: str) -> str:
    """Return an absolute path under HOMELAB_ROOT, or '' if root is unset."""
    if not HOMELAB_ROOT:
        return ""
    return os.path.normpath(os.path.join(HOMELAB_ROOT, rel))


# HTTP / model
VISION_API_BASE = os.environ.get("VISION_API_BASE", "http://127.0.0.1:8083/v1")
VISION_MODEL = os.environ.get("VISION_MODEL", "vision-vlm")
VISION_API_KEY = os.environ.get("VISION_API_KEY", "sk-no-key-required")
VISION_TIMEOUT = int(os.environ.get("VISION_TIMEOUT", "180"))
VISION_MAX_TOKENS = int(os.environ.get("VISION_MAX_TOKENS", "1024"))

# Spawn / lifecycle
VISION_SPAWN_SERVER = _truthy(os.environ.get("VISION_SPAWN_SERVER", "1"))
VISION_LLAMA_EXE = os.environ.get("VISION_LLAMA_EXE", "")
VISION_LLAMA_MODEL = os.environ.get("VISION_LLAMA_MODEL", "")
VISION_LLAMA_MMPROJ = os.environ.get("VISION_LLAMA_MMPROJ", "")
VISION_LLAMA_ARGS_FILE = os.environ.get(
    "VISION_LLAMA_ARGS_FILE",
    _default_under_root("opencode-vision/vision-server/vision-args.txt"),
)
VISION_LLAMA_STARTUP_TIMEOUT = int(os.environ.get("VISION_LLAMA_STARTUP_TIMEOUT", "60"))
VISION_LLAMA_IDLE_TIMEOUT = int(os.environ.get("VISION_LLAMA_IDLE_TIMEOUT", "300"))
VISION_LLAMA_VISIBLE_CONSOLE = _truthy(os.environ.get("VISION_LLAMA_VISIBLE_CONSOLE", "1"))

DEFAULT_QUESTION = "Describe this image in detail."

EXT_TO_MIME = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}

# Flags that break llama.cpp multimodal inference — refuse to spawn if found.
BANNED_ARGS = {"--spec-type", "--context-shift"}

server = FastMCP("vision")


# ==================== HELPERS ====================

def _err(msg: str) -> str:
    return f"VISION ERROR: {msg}"


def _mcp_log(msg: str) -> None:
    sys.stderr.write(f"[vision_mcp] {msg}\n")
    sys.stderr.flush()


def _log_dir() -> str:
    base = os.environ.get("TEMP") or os.environ.get("TMPDIR") or "/tmp"
    p = os.path.join(base, "opencode-vision")
    os.makedirs(p, exist_ok=True)
    return p


def _guess_mime(path: Path) -> str:
    mime = EXT_TO_MIME.get(path.suffix.lower())
    if mime:
        return mime
    guessed, _ = mimetypes.guess_type(str(path))
    return guessed or "image/png"


def _build_data_uri(path: Path) -> str:
    mime = _guess_mime(path)
    raw = path.read_bytes()
    b64 = base64.b64encode(raw).decode("ascii")
    return f"data:{mime};base64,{b64}"


def _resolve_image_url(image_path: str) -> str:
    if image_path.startswith(("http://", "https://", "data:")):
        return image_path
    path = Path(image_path)
    if not path.exists():
        raise FileNotFoundError(f"file not found: {image_path}")
    if not path.is_file():
        raise IsADirectoryError(f"not a file: {image_path}")
    return _build_data_uri(path)


# ==================== SPAWN / LIFECYCLE ====================

_spawn_lock = threading.Lock()
_child_proc: "subprocess.Popen | None" = None
_server_ready_event = threading.Event()
_active_calls = 0
_active_lock = threading.Lock()
_last_call_time = time.time()  # initialised so the watchdog never starts "infinitely idle"
_watchdog_started = False
_watchdog_lock = threading.Lock()


def _server_ready(probe_timeout: float = 1.5) -> bool:
    try:
        r = requests.get(VISION_API_BASE.rstrip("/") + "/models", timeout=probe_timeout)
        return r.status_code == 200
    except Exception:
        return False


def _find_llama_exe() -> str:
    if VISION_LLAMA_EXE and os.path.isfile(VISION_LLAMA_EXE):
        return VISION_LLAMA_EXE
    found = shutil.which("llama-server.exe") or shutil.which("llama-server")
    if found:
        return found
    root = os.environ.get("LLAMACPP_ROOT") or (
        r"C:\Program Files\llamacpp" if sys.platform == "win32" else "/usr/local/bin"
    )
    candidate = os.path.join(root, "llama-server.exe" if sys.platform == "win32" else "llama-server")
    return candidate if os.path.isfile(candidate) else ""


def _load_args_file(path: str) -> list:
    args = []
    if not path or not os.path.isfile(path):
        return args
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            t = line.strip()
            if not t or t.startswith("#"):
                continue
            args.extend(t.split())
    banned = [a for a in args if a in BANNED_ARGS]
    if banned:
        raise ValueError(
            f"vision args file {path} contains banned flags (break multimodal): {banned}"
        )
    return args


# ---- Windows Job Object: KILL_ON_JOB_CLOSE → no orphan llama-server on hard exit ----

_job_handle = None


def _create_job_kill_on_close():
    if sys.platform != "win32":
        return None
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
    JobObjectExtendedLimitInformation = 9

    class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_int64),
            ("PerJobUserTimeLimit", ctypes.c_int64),
            ("LimitFlags", ctypes.c_ulong),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", ctypes.c_ulong),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", ctypes.c_ulong),
            ("SchedulingClass", ctypes.c_ulong),
        ]

    class IO_COUNTERS(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_uint64),
            ("WriteOperationCount", ctypes.c_uint64),
            ("OtherOperationCount", ctypes.c_uint64),
            ("ReadTransferCount", ctypes.c_uint64),
            ("WriteTransferCount", ctypes.c_uint64),
            ("OtherTransferCount", ctypes.c_uint64),
        ]

    class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
            ("IoInfo", IO_COUNTERS),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    h = kernel32.CreateJobObjectW(None, None)
    if not h:
        return None
    info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
    info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    ok = kernel32.SetInformationJobObject(
        h, JobObjectExtendedLimitInformation, ctypes.byref(info), ctypes.sizeof(info)
    )
    if not ok:
        kernel32.CloseHandle(h)
        return None
    return h


def _assign_proc_to_job(pid: int) -> bool:
    if sys.platform != "win32" or _job_handle is None:
        return False
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    PROCESS_TERMINATE = 0x0001
    PROCESS_SET_QUOTA = 0x0100
    h_proc = kernel32.OpenProcess(PROCESS_TERMINATE | PROCESS_SET_QUOTA, False, pid)
    if not h_proc:
        return False
    ok = kernel32.AssignProcessToJobObject(_job_handle, h_proc)
    kernel32.CloseHandle(h_proc)
    return bool(ok)


_job_handle = _create_job_kill_on_close()  # closed when this process dies → kills children


# ---- spawn / shutdown ----

def _spawn_now() -> "str | None":
    """Spawn llama-server. Caller MUST hold _spawn_lock.
    Returns None on success, or an error string."""
    global _child_proc, _last_call_time

    # Already responding? Reuse it.
    if _server_ready(probe_timeout=1.0):
        _server_ready_event.set()
        _mcp_log(f"reusing existing server at {VISION_API_BASE} (already responding)")
        _last_call_time = time.time()
        return None

    exe = _find_llama_exe()
    if not exe:
        return "llama-server not found (set VISION_LLAMA_EXE or add to PATH)"
    if not VISION_LLAMA_MODEL or not os.path.isfile(VISION_LLAMA_MODEL):
        return f"VISION_LLAMA_MODEL not set or not found: {VISION_LLAMA_MODEL or '<unset>'}"
    if not VISION_LLAMA_MMPROJ or not os.path.isfile(VISION_LLAMA_MMPROJ):
        return f"VISION_LLAMA_MMPROJ not set or not found: {VISION_LLAMA_MMPROJ or '<unset>'}"

    try:
        extra = _load_args_file(VISION_LLAMA_ARGS_FILE)
    except ValueError as e:
        return str(e)

    cmd = [exe, "-m", VISION_LLAMA_MODEL, "--mmproj", VISION_LLAMA_MMPROJ] + extra
    log_path = os.path.join(_log_dir(), "llama-server.log")
    with open(log_path, "a", encoding="utf-8") as lf:
        lf.write(f"\n\n=== spawn @ {time.strftime('%Y-%m-%d %H:%M:%S')} ===\n{' '.join(cmd)}\n\n")

    _mcp_log(f"spawning llama-server (visible_console={VISION_LLAMA_VISIBLE_CONSOLE}); cmd log: {log_path}")

    popen_kwargs = {}
    file_log = None  # opened only when console is hidden
    if sys.platform == "win32":
        flags = subprocess.CREATE_NEW_PROCESS_GROUP
        if VISION_LLAMA_VISIBLE_CONSOLE:
            # Separate visible console window — user can read live output and Ctrl+C.
            # IMPORTANT: do NOT set stdin/stdout/stderr here. Setting ANY std handle makes
            # Windows turn on STARTF_USESTDHANDLES, which then routes the child's
            # stdout/stderr to THIS process's inherited handles (the MCP stdio pipes
            # OpenCode owns) instead of the new console — leaving the window blank.
            # Leaving all three unset lets llama-server attach to the new console's own
            # handles, so its logs render live (needed for performance tuning).
            flags |= subprocess.CREATE_NEW_CONSOLE
        else:
            flags |= subprocess.CREATE_NO_WINDOW
            popen_kwargs["stdin"] = subprocess.DEVNULL
            file_log = open(log_path, "a", encoding="utf-8", buffering=1)
            popen_kwargs["stdout"] = file_log
            popen_kwargs["stderr"] = file_log
        popen_kwargs["creationflags"] = flags
    else:
        popen_kwargs["stdin"] = subprocess.DEVNULL
        file_log = open(log_path, "a", encoding="utf-8", buffering=1)
        popen_kwargs["stdout"] = file_log
        popen_kwargs["stderr"] = file_log
        popen_kwargs["start_new_session"] = True

    try:
        _child_proc = subprocess.Popen(cmd, **popen_kwargs)
    except Exception as e:
        if file_log:
            file_log.close()
        return f"failed to spawn llama-server: {e}"

    # Assign to job (Windows) so the OS reaps the child if this MCP dies.
    if sys.platform == "win32":
        if _assign_proc_to_job(_child_proc.pid):
            _mcp_log(f"child pid {_child_proc.pid} attached to kill-on-close job")
        else:
            _mcp_log(f"WARN: could not attach pid {_child_proc.pid} to job; orphan possible on hard crash")

    # Readiness wait — block this thread until /v1/models responds or timeout.
    deadline = time.time() + VISION_LLAMA_STARTUP_TIMEOUT
    while time.time() < deadline:
        if _child_proc.poll() is not None:
            code = _child_proc.returncode
            _child_proc = None
            return f"llama-server exited during startup (code {code}); see {log_path}"
        if _server_ready(probe_timeout=1.5):
            _server_ready_event.set()
            _last_call_time = time.time()
            _mcp_log(f"llama-server ready at {VISION_API_BASE} (pid {_child_proc.pid})")
            return None
        time.sleep(1.0)
    return f"llama-server did not become ready within {VISION_LLAMA_STARTUP_TIMEOUT}s; see {log_path}"


def _ensure_spawned() -> "str | None":
    """Idempotent. Spawn the server if we should and it isn't already up."""
    global _child_proc

    if not VISION_SPAWN_SERVER:
        # External server expected — just confirm it's there.
        if _server_ready(probe_timeout=1.0):
            _server_ready_event.set()
            return None
        return f"VISION_SPAWN_SERVER=0 and nothing is responding at {VISION_API_BASE}"

    with _spawn_lock:
        # Drop a previous child that died (e.g. user closed its console).
        if _child_proc is not None and _child_proc.poll() is not None:
            _mcp_log(f"previous child exited (code {_child_proc.returncode}); will respawn on demand")
            _child_proc = None
            _server_ready_event.clear()
        if _server_ready_event.is_set() and (_child_proc is None or _child_proc.poll() is None):
            # Either externally reused or our child still alive — make sure it actually responds.
            if _server_ready(probe_timeout=1.0):
                return None
            # Stale event — clear and respawn.
            _server_ready_event.clear()
        return _spawn_now()


def _shutdown_child() -> None:
    global _child_proc
    if _child_proc is None or _child_proc.poll() is not None:
        _child_proc = None
        _server_ready_event.clear()
        return
    pid = _child_proc.pid
    _mcp_log(f"shutting down llama-server child pid {pid}")
    try:
        _child_proc.terminate()
        try:
            _child_proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            _child_proc.kill()
            _child_proc.wait(timeout=5)
    except Exception as e:
        _mcp_log(f"error shutting down child {pid}: {e}")
    _child_proc = None
    _server_ready_event.clear()


# ---- idle watchdog ----

def _ensure_watchdog() -> None:
    global _watchdog_started
    if VISION_LLAMA_IDLE_TIMEOUT <= 0 or not VISION_SPAWN_SERVER:
        return
    with _watchdog_lock:
        if _watchdog_started:
            return
        _watchdog_started = True
    threading.Thread(target=_watchdog_loop, name="vision-idle-watchdog", daemon=True).start()


def _watchdog_loop() -> None:
    tick = max(5, min(30, VISION_LLAMA_IDLE_TIMEOUT // 4 or 30))
    while True:
        time.sleep(tick)
        with _spawn_lock:
            if _child_proc is None or _child_proc.poll() is not None:
                continue
            with _active_lock:
                active = _active_calls
                last = _last_call_time
            if active > 0:
                continue
            idle_for = time.time() - last
            if idle_for >= VISION_LLAMA_IDLE_TIMEOUT:
                _mcp_log(
                    f"idle for {int(idle_for)}s (>{VISION_LLAMA_IDLE_TIMEOUT}s) — "
                    "shutting down llama-server to free resources"
                )
                _shutdown_child()


# ---- cleanup hooks ----

def _on_exit() -> None:
    _shutdown_child()
    # The Job Object handle (if created) is implicitly closed when the process dies,
    # which triggers KILL_ON_JOB_CLOSE for any straggler we missed. Belt and suspenders.


atexit.register(_on_exit)

try:
    import signal

    def _signal_handler(signum, frame):  # noqa: ARG001
        _on_exit()
        os._exit(0)

    for _sig in (getattr(signal, "SIGINT", None), getattr(signal, "SIGTERM", None)):
        if _sig is not None:
            try:
                signal.signal(_sig, _signal_handler)
            except (OSError, ValueError):
                pass
except Exception:
    pass


# ==================== HTTP CALL ====================

def _call_vision_model(image_url: str, question: str) -> str:
    url = f"{VISION_API_BASE.rstrip('/')}/chat/completions"
    payload = {
        "model": VISION_MODEL,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": question},
                    {"type": "image_url", "image_url": {"url": image_url}},
                ],
            }
        ],
        "max_tokens": VISION_MAX_TOKENS,
        "temperature": 0.2,
        "stream": False,
    }
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {VISION_API_KEY}",
    }
    resp = requests.post(url, json=payload, headers=headers, timeout=VISION_TIMEOUT)
    resp.raise_for_status()
    data = resp.json()
    choices = data.get("choices") or []
    if not choices:
        raise ValueError(f"no choices in response: {str(data)[:300]}")
    content = choices[0].get("message", {}).get("content")
    if not content or not str(content).strip():
        raise ValueError("model returned empty content")
    return str(content).strip()


# ==================== TOOL ====================

@server.tool()
async def analyze(image_path: str, question: str = DEFAULT_QUESTION) -> str:
    """Analyze a local image using the local vision model.

    Use this tool to SEE an image. Pass the absolute path to a PNG, JPEG, or WebP file on
    disk (the path provided in the message), or an http(s) image URL. Ask a specific
    `question` for a focused answer; omit it for a general description.

    The vision backend is started on-demand on first use, idles down after inactivity, and
    is reaped automatically when OpenCode exits — no manual server management required.

    Args:
        image_path: Absolute path to a local image file, or an http(s)/data URL.
        question: What to ask about the image. Defaults to a general description.

    Returns:
        The vision model's textual answer, or a string beginning "VISION ERROR:" on failure.
    """
    # Resolve image first (fast, requires no server).
    try:
        image_url = _resolve_image_url(image_path)
    except (FileNotFoundError, IsADirectoryError) as e:
        return _err(str(e))
    except Exception as e:  # noqa: BLE001
        return _err(f"could not read image: {e}")

    # Lazy spawn + idle watchdog (started once, on first call).
    _ensure_watchdog()
    err = await asyncio.to_thread(_ensure_spawned)
    if err:
        return _err(err)

    # Track the in-flight call so the idle watchdog doesn't kill us mid-request.
    global _active_calls, _last_call_time
    with _active_lock:
        _active_calls += 1
    try:
        return await asyncio.to_thread(_call_vision_model, image_url, question or DEFAULT_QUESTION)
    except requests.exceptions.ConnectionError:
        return _err(
            f"could not connect to vision server at {VISION_API_BASE}. "
            "Server may have crashed; check llama-server.log."
        )
    except requests.exceptions.Timeout:
        return _err(f"vision request timed out after {VISION_TIMEOUT}s")
    except requests.exceptions.HTTPError as e:
        body = e.response.text[:300] if e.response is not None else ""
        return _err(
            f"vision server HTTP {e.response.status_code if e.response else '?'}: {body}"
        )
    except Exception as e:  # noqa: BLE001
        return _err(f"unexpected failure: {e}")
    finally:
        with _active_lock:
            _active_calls -= 1
            _last_call_time = time.time()


if __name__ == "__main__":
    server.run()
