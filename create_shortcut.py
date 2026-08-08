"""
Generate a clickable "P.E.T.E.R" shortcut on the Desktop.

Run once with:
    python create_shortcut.py

This does two things:
  1. Creates `peter.ico` — a Spider-Man-style spider-mask app icon (Pillow).
  2. Creates `P.E.T.E.R.lnk` on the Desktop that launches the desktop app via
     the virtualenv's pythonw.exe (no console window), with the project
     directory as the working directory so relative imports always resolve.

Re-run anytime (it overwrites the previous shortcut/icon).
"""

from __future__ import annotations

import os
import sys

from PIL import Image, ImageDraw


# ──────────────────────────────────────────────────────────────
# 1) Draw the spider-mask icon → peter.ico
# ──────────────────────────────────────────────────────────────
def make_icon(size: int = 256) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    def px(v: float) -> int:
        return int(v * size)

    # Circular background
    inset = px(0.04)
    d.ellipse([inset, inset, size - inset, size - inset],
              fill=(230, 36, 41, 255))  # spider red

    # Web pattern (cross-hatch) — darker red lines
    web = (140, 16, 20, 255)
    lines = [0.25, 0.40, 0.50, 0.60, 0.75]
    for l in lines:
        d.line([px(l), inset, px(l), size - inset], fill=web, width=px(0.012))
        d.line([inset, px(l), size - inset, px(l)], fill=web, width=px(0.012))

    # Spider mask (two dark eye lenses) — slight ellipse
    eye_fill = (235, 240, 255, 255)  # bright white/blue
    lens_w, lens_h = px(0.34), px(0.20)
    y_top = px(0.36)
    left = ((size - lens_w) // 2) - px(0.13)
    right = ((size - lens_w) // 2) + px(0.13)
    d.ellipse([left, y_top, left + lens_w, y_top + lens_h], fill=eye_fill)
    d.ellipse([right, y_top, right + lens_w, y_top + lens_h], fill=eye_fill)

    # Black "nose" bridge between the lenses
    d.rectangle([(size // 2) - px(0.02), y_top + px(0.04),
                 (size // 2) + px(0.02), y_top + lens_h - px(0.04)],
                fill=(20, 20, 30, 255))

    return img


def ensure_icon() -> str:
    icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "peter.ico")
    if os.path.exists(icon_path):
        return icon_path
    img = make_icon()
    img.save(icon_path, sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print(f"  ✓ Icon created: {icon_path}")
    return icon_path


# ──────────────────────────────────────────────────────────────
# 2) Create the Desktop shortcut
# ──────────────────────────────────────────────────────────────
def create_shortcut() -> str:
    import pythoncom
    from win32com.client import Dispatch

    project_dir = os.path.dirname(os.path.abspath(__file__))
    venv_pythonw = os.path.join(project_dir, ".venv", "Scripts", "pythonw.exe")
    if not os.path.exists(venv_pythonw):
        venv_pythonw = os.path.join(project_dir, "venv", "Scripts", "pythonw.exe")
    if not os.path.exists(venv_pythonw):
        raise FileNotFoundError(
            "Could not find pythonw.exe in .venv or venv. Run the app with `python peter_app.py` instead."
        )

    icon_path = ensure_icon()
    desktop = os.path.join(os.path.expanduser("~"), "OneDrive", "Desktop")
    if not os.path.isdir(desktop):
        desktop = os.path.join(os.path.expanduser("~"), "Desktop")
    shortcut_path = os.path.join(desktop, "P.E.T.E.R.lnk")

    shell = Dispatch("WScript.Shell")
    lnk = shell.CreateShortCut(shortcut_path)
    lnk.TargetPath = venv_pythonw
    lnk.Arguments = "peter_app.py"
    lnk.WorkingDirectory = project_dir
    lnk.IconLocation = f"{icon_path},0"
    lnk.Description = "Talk to Peter — your friendly neighborhood AI assistant"
    lnk.WindowStyle = 7  # Minimized (pythonw doesn't show a console anyway)
    lnk.Save()

    print(f"  ✓ Shortcut created: {shortcut_path}")
    return shortcut_path


if __name__ == "__main__":
    print("P.E.T.E.R — creating desktop shortcut…")
    try:
        path = create_shortcut()
        print(f"Done! Double-click '{path}' to talk to Peter.")
    except Exception as e:  # noqa: BLE001
        print(f"ERROR: {e}")
        sys.exit(1)

