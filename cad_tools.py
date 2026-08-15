"""
CAD design tools — let Peter design real, 3D-printable parts (mounts,
brackets, adapters, enclosures, clips) the same way he edits his own source
code: he writes the design himself, as an OpenSCAD script (a small,
parametric CAD language well-suited to exactly this kind of mechanical
part), and this module saves it and — if OpenSCAD is installed — renders it
to an STL file ready to slice and 3D print, plus a quick PNG preview.

OpenSCAD (https://openscad.org) is a free, separate application. If it isn't
installed, Peter still saves the .scad script (openable in OpenSCAD later,
or handed to someone who has it) but can't render a preview/STL
automatically.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
from pathlib import Path

from livekit.agents import RunContext, function_tool

_ROOT = Path(os.path.dirname(os.path.abspath(__file__))).resolve()
_DESIGNS_DIR = _ROOT / "cad_designs"

_MAX_SCAD_CHARS = 20000
_RENDER_TIMEOUT_SECS = 90


def _sanitize_name(name: str) -> str | None:
    """Turn an arbitrary part name into a safe filename stem, or None."""
    name = (name or "").strip().lower()
    name = re.sub(r"[^a-z0-9_-]+", "_", name).strip("_")
    if not name:
        return None
    return name[:60]


def _find_openscad() -> str | None:
    exe = shutil.which("openscad") or shutil.which("OpenSCAD")
    if exe:
        return exe
    for candidate in (
        r"C:\Program Files\OpenSCAD\openscad.exe",
        r"C:\Program Files (x86)\OpenSCAD\openscad.exe",
    ):
        if os.path.exists(candidate):
            return candidate
    return None


@function_tool()
async def design_cad_part(
    context: RunContext,  # type: ignore
    name: str,
    scad_code: str,
) -> str:
    """
    Design a real, physical part — a mount, bracket, adapter, enclosure,
    clip, etc. — as a 3D-printable CAD file.

    Write the design YOURSELF as an OpenSCAD script (a small parametric CAD
    language — use a named variable for every dimension the user gave you,
    e.g. `strap_width_mm = 32;`, so the part is easy to tweak later). Keep
    all units in millimeters, and ask the user for any key measurement you
    don't have rather than guessing wildly. `name` is a short filename like
    "helmet_mount" (no extension, no spaces).

    This saves the script to cad_designs/{name}.scad, and if OpenSCAD is
    installed, also renders it to cad_designs/{name}.stl (ready to slice and
    3D print) and cad_designs/{name}.png (a quick preview). Tell the user
    where the files ended up and whether the STL rendered; if it failed,
    there's likely a syntax error in the script — read the error and fix it,
    then call this again with the same name to re-render.
    """
    safe_name = _sanitize_name(name)
    if not safe_name:
        return "I need a short, simple name for this part (letters, numbers, dashes)."
    if not isinstance(scad_code, str) or not scad_code.strip():
        return "I don't have any design code to save — write the OpenSCAD script first."
    if len(scad_code) > _MAX_SCAD_CHARS:
        return "That design script is too large for me to save in one go."

    _DESIGNS_DIR.mkdir(parents=True, exist_ok=True)
    scad_path = _DESIGNS_DIR / f"{safe_name}.scad"
    try:
        scad_path.write_text(scad_code, encoding="utf-8")
    except OSError as e:  # noqa: BLE001
        return f"I couldn't save the design: {e}"

    openscad = _find_openscad()
    if not openscad:
        return (
            f"Design saved as cad_designs/{safe_name}.scad. OpenSCAD isn't "
            "installed on this machine, so I can't render it to an STL or "
            "preview automatically — install it free from openscad.org, "
            "then open that file, or ask me to try rendering again later."
        )

    stl_path = _DESIGNS_DIR / f"{safe_name}.stl"
    png_path = _DESIGNS_DIR / f"{safe_name}.png"

    stl_ok = False
    stl_err = ""
    try:
        result = subprocess.run(
            [openscad, "-o", str(stl_path), str(scad_path)],
            capture_output=True,
            text=True,
            timeout=_RENDER_TIMEOUT_SECS,
        )
        stl_ok = result.returncode == 0 and stl_path.exists()
        stl_err = (result.stderr or "").strip()[-500:]
    except subprocess.TimeoutExpired:
        stl_err = "the render timed out"
    except OSError as e:  # noqa: BLE001
        stl_err = str(e)

    png_ok = False
    try:
        subprocess.run(
            [openscad, "--imgsize=800,600", "-o", str(png_path), str(scad_path)],
            capture_output=True,
            text=True,
            timeout=_RENDER_TIMEOUT_SECS,
        )
        png_ok = png_path.exists()
    except Exception:  # noqa: BLE001
        pass  # preview is a nice-to-have — never fail the whole tool over it

    logging.info(f"design_cad_part: {safe_name} stl_ok={stl_ok} png_ok={png_ok}")

    if stl_ok:
        msg = f"Design saved and rendered — cad_designs/{safe_name}.stl is ready to slice and 3D print"
        msg += f", with a preview at cad_designs/{safe_name}.png." if png_ok else "."
        return msg
    return (
        f"Design saved as cad_designs/{safe_name}.scad, but the STL render "
        "failed — there's likely a syntax error in the OpenSCAD script"
        + (f": {stl_err}" if stl_err else ".")
        + " Fix the script and call design_cad_part again with the same name."
    )


def get_design_paths(name: str) -> dict:
    """Absolute path strings for a design's scad/stl/png files (None for any
    that don't exist), keyed "scad"/"stl"/"png".

    Not a function_tool — Peter doesn't call this directly. It's a plain
    helper the desktop UI (peter_app.py) uses to safely resolve which real
    files exist for a design named in a peter-files broadcast, so it can
    open or reveal them without re-deriving path logic.
    """
    safe_name = _sanitize_name(name)
    if not safe_name:
        return {"scad": None, "stl": None, "png": None}
    result: dict = {}
    for ext in ("scad", "stl", "png"):
        p = _DESIGNS_DIR / f"{safe_name}.{ext}"
        result[ext] = str(p) if p.exists() else None
    return result


@function_tool()
async def list_cad_designs(
    context: RunContext,  # type: ignore
) -> str:
    """
    List the physical/CAD part designs Peter has already created.

    Use this when the user asks what you've designed so far, or before
    revising an existing part (so you know what name to reuse).
    """
    if not _DESIGNS_DIR.exists():
        return "I haven't designed any parts yet."
    names = sorted({p.stem for p in _DESIGNS_DIR.glob("*.scad")})
    if not names:
        return "I haven't designed any parts yet."
    return "Parts I've designed: " + ", ".join(names)
