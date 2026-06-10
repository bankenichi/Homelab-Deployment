#!/usr/bin/env python3
"""Local test harness for the vision integration.

Runs the checks defined in docs/PLAN.md without needing OpenCode:

  T0     — raw HTTP: POST a known image to :8081 and verify the model can see it.
  T1     — MCP tool: call vision_mcp.analyze() directly; expect a real description.
  T1-neg — MCP tool: bad path returns a "VISION ERROR:" string (no exception).

Usage:
    python tests/test_vision.py [path-to-image.png]

If no image path is given, the harness tries to generate a red circle on white using
Pillow (T0 then asserts the words "circle" and "red" appear). If Pillow is missing and no
path is supplied, T0 is skipped with a notice — pass a screenshot path to run it.

Honors the same env vars as the MCP server: VISION_API_BASE, VISION_MODEL, etc.
"""

import asyncio
import base64
import importlib.util
import os
import sys
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent
MCP_DIR = HERE.parent / "mcp"

# Defaults target the dedicated vision server on :8083 (see vision-server/). Override with
# the VISION_API_BASE / VISION_MODEL env vars if needed.
VISION_API_BASE = os.environ.get("VISION_API_BASE", "http://127.0.0.1:8083/v1")
VISION_MODEL = os.environ.get("VISION_MODEL", "vision-vlm")
VISION_TIMEOUT = int(os.environ.get("VISION_TIMEOUT", "300"))


# ---- load the MCP module by path (it lives in ../mcp/vision_mcp.py) ----

def load_vision_mcp():
    spec = importlib.util.spec_from_file_location("vision_mcp", MCP_DIR / "vision_mcp.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---- image helpers ----

def make_red_circle_png() -> Path | None:
    """Generate a red circle on white. Returns path, or None if Pillow is unavailable."""
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        return None
    out = HERE / "_generated_red_circle.png"
    img = Image.new("RGB", (256, 256), "white")
    draw = ImageDraw.Draw(img)
    draw.ellipse((48, 48, 208, 208), fill="red")
    img.save(out)
    return out


def to_data_uri(path: Path) -> str:
    b64 = base64.b64encode(path.read_bytes()).decode("ascii")
    suffix = path.suffix.lower()
    mime = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "webp": "image/webp"}.get(
        suffix.lstrip("."), "image/png"
    )
    return f"data:{mime};base64,{b64}"


# ---- tests ----

def test_t0_raw_http(image_path: Path, expect_words: list[str] | None) -> bool:
    print(f"\n=== T0: raw HTTP vision against {VISION_API_BASE} ===")
    url = f"{VISION_API_BASE.rstrip('/')}/chat/completions"
    payload = {
        "model": VISION_MODEL,
        "messages": [
            {"role": "user", "content": [
                {"type": "text", "text": "What shape and color is in this image? Answer briefly."},
                {"type": "image_url", "image_url": {"url": to_data_uri(image_path)}},
            ]}
        ],
        "max_tokens": 256,
        "temperature": 0.2,
        "stream": False,
    }
    try:
        resp = requests.post(url, json=payload, timeout=VISION_TIMEOUT,
                             headers={"Authorization": "Bearer sk-no-key-required"})
        resp.raise_for_status()
        text = resp.json()["choices"][0]["message"]["content"]
    except Exception as e:  # noqa: BLE001
        print(f"  FAIL — request error: {e}")
        return False

    print(f"  model said: {text!r}")
    if expect_words:
        low = text.lower()
        missing = [w for w in expect_words if w not in low]
        if missing:
            print(f"  FAIL — expected words missing: {missing}")
            return False
        print(f"  PASS — found all of {expect_words}")
    else:
        print("  PASS — got a non-empty response (manual eyeball: does it match the image?)")
    return bool(text and text.strip())


def test_t1_mcp_tool(vmod, image_path: Path) -> bool:
    print("\n=== T1: MCP analyze() returns a real description ===")
    result = asyncio.run(vmod.analyze(str(image_path), "Describe this image."))
    print(f"  analyze() -> {result[:300]!r}")
    if result.startswith("VISION ERROR:"):
        print("  FAIL — got an error string")
        return False
    if not result.strip():
        print("  FAIL — empty result")
        return False
    print("  PASS")
    return True


def test_t1_negative(vmod) -> bool:
    print("\n=== T1-neg: bad path returns VISION ERROR (no exception) ===")
    try:
        result = asyncio.run(vmod.analyze("C:/does/not/exist_xyz.png"))
    except Exception as e:  # noqa: BLE001
        print(f"  FAIL — raised instead of returning a string: {e}")
        return False
    print(f"  analyze(missing) -> {result!r}")
    ok = result.startswith("VISION ERROR:")
    print("  PASS" if ok else "  FAIL — expected a 'VISION ERROR:' prefix")
    return ok


def main() -> int:
    arg_path = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    expect_words: list[str] | None = None

    if arg_path:
        if not arg_path.exists():
            print(f"Provided image does not exist: {arg_path}")
            return 2
        image_path = arg_path
    else:
        image_path = make_red_circle_png()
        if image_path is None:
            print("No image path given and Pillow is not installed; cannot run T0/T1.")
            print("Install Pillow (pip install pillow) or pass an image path:")
            print("    python tests/test_vision.py C:/path/to/screenshot.png")
            return 2
        expect_words = ["circle", "red"]
        print(f"Generated test image: {image_path}")

    vmod = load_vision_mcp()
    print(f"Loaded MCP server '{vmod.server.name}' "
          f"(VISION_API_BASE={VISION_API_BASE}, VISION_MODEL={VISION_MODEL})")

    # The MCP server lazy-spawns the vision backend on first analyze() call. T0 below makes
    # a raw HTTP call BEFORE T1, so without a pre-spawn it would fail with connection-refused
    # when no run-vision.ps1 is running. Trigger the spawn now so T0 has something to talk to.
    if getattr(vmod, "VISION_SPAWN_SERVER", False):
        print("Pre-spawning vision backend (lazy-spawn under test)...")
        err = vmod._ensure_spawned()
        if err:
            print(f"  pre-spawn note: {err}")
            print("  (T0/T1 will likely fail unless an external server is running)")

    results = {
        "T0 raw HTTP": test_t0_raw_http(image_path, expect_words),
        "T1 MCP tool": test_t1_mcp_tool(vmod, image_path),
        "T1-neg bad path": test_t1_negative(vmod),
    }

    print("\n==================== SUMMARY ====================")
    for name, ok in results.items():
        print(f"  {'PASS' if ok else 'FAIL'}  {name}")
    all_ok = all(results.values())
    print("=================================================")
    print("ALL PASS" if all_ok else "SOME FAILED — see above")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
