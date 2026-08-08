"""
P.E.T.E.R. — Pending URL helper
===============================

A tiny shared file-based "clipboard" for the URL the user typed into the
desktop app. The desktop UI writes it via `peter_app.py`'s bridge; the agent
worker (`tools.py`) reads it when Peter hears **"go peter"**.

If the box is blank the file simply contains an empty string — Peter then just
talks normally (the prompt tells him not to treat an empty URL as a task).

The file lives next to this module so both the pywebview app process and the
LiveKit worker process (started with the project dir as CWD) see the same data.
"""

from __future__ import annotations

import os
from pathlib import Path

_FILE_NAME = "pending_url.txt"
_DIR = Path(os.path.dirname(os.path.abspath(__file__)))
_PATH = _DIR / _FILE_NAME


def save_pending_url(url: str) -> str:
    """Persist the URL from the desktop UI (blank clears the slot)."""
    url = (url or "").strip()
    try:
        _PATH.write_text(url, encoding="utf-8")
        return url
    except OSError:
        # Never break the desktop UI because the file couldn't be written.
        return ""


def get_pending_url() -> str:
    """Return the URL currently in the slot, or '' if blank/missing."""
    try:
        return _PATH.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def set_pending_url(url: str) -> str:
    """Alias for `save_pending_url` (kept for name symmetry with the bridge)."""
    return save_pending_url(url)


def clear_pending_url() -> None:
    """Empty the URL slot (used after Peter has acted on it via 'go peter')."""
    save_pending_url("")

