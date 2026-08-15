"""
Self-file tools — let Peter see and edit his OWN source code (this project),
so the user can hand him direct tasks like "add a joke tool to yourself" or
"change your greeting" and have Peter make the edit himself.

Hard-scoped to the project directory, and refuses to touch anything that
looks like a secret, credential, or generated/local-state file — the exact
set .gitignore already keeps out of version control (.env, tokens, the
memory store, received files, etc.). Voice is a noisy input channel; an
accidental read/edit of a credential file must never be possible, no matter
what gets misheard.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

from livekit.agents import RunContext, function_tool

_ROOT = Path(os.path.dirname(os.path.abspath(__file__))).resolve()

# Directories Peter should never walk into, list, read, or write inside.
_DENY_DIRS = {
    ".git", ".venv", "venv", "__pycache__", "memory_store",
    "received_files", "KMS", "node_modules", ".vscode", ".idea", ".claude",
}

# Specific files Peter must never read or write, regardless of extension —
# these hold live credentials or per-user local state (mirrors .gitignore).
_DENY_FILES = {
    ".env", ".env.example", "spotify_token.json", "active_file.json",
    "spotify_default_playlist.json", "pending_url.txt", ".peter_worker.lock",
}

# Only these extensions count as "source" Peter can read/edit — keeps him
# out of binaries, icons, logs, and anything not meant to be plain text.
_ALLOWED_EXT = {
    ".py", ".html", ".css", ".js", ".json", ".md", ".txt", ".yaml", ".yml",
    ".toml", ".cfg", ".ini", ".vbs",
}

_MAX_READ_CHARS = 20000
_MAX_WRITE_CHARS = 200000


def _resolve(rel_path: str) -> Path | None:
    """Resolve a relative path safely inside the project root.

    Returns None if the path escapes the root, hits a denied dir/file, or
    has a disallowed extension. This is the single choke point every
    self-file tool goes through — never bypass it.
    """
    rel = (rel_path or "").strip().lstrip("/\\")
    if not rel:
        return None
    try:
        candidate = (_ROOT / rel).resolve()
    except (OSError, ValueError):
        return None
    if candidate != _ROOT and _ROOT not in candidate.parents:
        return None  # escaped the project root (e.g. "../../secrets.txt")
    rel_to_root = candidate.relative_to(_ROOT)
    parts = rel_to_root.parts
    if not parts:
        return None
    if any(p in _DENY_DIRS for p in parts[:-1]):
        return None
    if parts[-1] in _DENY_FILES:
        return None
    if candidate.suffix.lower() not in _ALLOWED_EXT:
        return None
    return candidate


def _iter_source_files():
    for dirpath, dirnames, filenames in os.walk(_ROOT):
        dirnames[:] = sorted(d for d in dirnames if d not in _DENY_DIRS)
        for fn in sorted(filenames):
            if fn in _DENY_FILES:
                continue
            p = Path(dirpath) / fn
            if p.suffix.lower() not in _ALLOWED_EXT:
                continue
            yield p


@function_tool()
async def list_own_files(
    context: RunContext,  # type: ignore
) -> str:
    """
    List Peter's own project source files (the code that makes Peter run).

    Use this when the user wants to see what you can edit, before reading or
    editing a specific file — e.g. "what files do you have", "show me your
    code", "what can you edit yourself". Returns relative paths you can pass
    to read_own_file / write_own_file. Secret or local-state files (like
    .env or auth tokens) are hidden and can never be listed, read, or
    written.
    """
    files = [
        str(p.relative_to(_ROOT)).replace(os.sep, "/") for p in _iter_source_files()
    ]
    if not files:
        return "I couldn't find any of my own source files."
    return "My source files:\n" + "\n".join(files)


@function_tool()
async def read_own_file(
    context: RunContext,  # type: ignore
    path: str) -> str:
    """
    Read one of Peter's own source files by its relative path.

    Use this to look at your own code before explaining it or before
    changing it with write_own_file — e.g. "show me your prompts", "what's
    in tools.py", "read agent.py". `path` is a relative path like
    "prompts.py" or "mobile/index.html" (see list_own_files). Refuses to
    read anything outside the project or any secret/credential file.
    """
    p = _resolve(path)
    if p is None or not p.is_file():
        return (
            f"I can't read '{path}' — it's either outside my project, one of "
            "my protected files, or doesn't exist. Try list_own_files to see "
            "what I can actually access."
        )
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError as e:  # noqa: BLE001
        return f"I couldn't read {path}: {e}"
    if len(text) > _MAX_READ_CHARS:
        text = text[:_MAX_READ_CHARS] + "\n… [truncated]"
    rel = str(p.relative_to(_ROOT)).replace(os.sep, "/")
    return f"[{rel}]\n\n{text}"


@function_tool()
async def write_own_file(
    context: RunContext,  # type: ignore
    path: str,
    content: str) -> str:
    """
    Overwrite (or create) one of Peter's own source files with new content.

    THIS EDITS PETER'S ACTUAL RUNNING CODE. Use it when the user directly
    asks you to change your own behavior — add a tool, tweak your prompts,
    fix a bug in yourself, adjust the UI, etc. Always pass the FULL new file
    content (not a diff or partial snippet) — this REPLACES the file
    entirely, so read it first with read_own_file and edit from that so you
    don't clobber unrelated parts.

    Python file changes (agent.py, tools.py, prompts.py, self_tools.py, ...)
    only take effect after the worker restarts — call restart_self after, or
    tell the user to restart Peter, once they're happy with the change.
    Refuses to write outside the project or to any secret/credential file.
    """
    p = _resolve(path)
    if p is None:
        return (
            f"I can't write to '{path}' — it's either outside my project or "
            "one of my protected files. I won't touch those."
        )
    if p.name == "learned_notes.md":
        return (
            "learned_notes.md is managed automatically by my self-improvement "
            "pipeline (it's how I remember lessons from past conversations) — "
            "I don't overwrite it directly, even on request. I can still read "
            "it for you with read_own_file."
        )
    if not isinstance(content, str) or len(content) > _MAX_WRITE_CHARS:
        return "That content is missing or too large for me to write in one go."
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    except OSError as e:  # noqa: BLE001
        return f"I couldn't write {path}: {e}"
    rel = str(p.relative_to(_ROOT)).replace(os.sep, "/")
    logging.info(f"write_own_file: wrote {len(content)} chars to {rel}")
    if p.suffix.lower() == ".py":
        return (
            f"Done — I rewrote {rel}. That's Python, so the change won't be "
            "live until I restart — call restart_self when the user's ready."
        )
    return f"Done — I rewrote {rel}."


def _venv_python() -> str:
    """Prefer this project's own virtualenv interpreter, like peter_app.py does."""
    for sub in (".venv", "venv"):
        for sub_bin in ("Scripts/python.exe", "bin/python"):
            candidate = _ROOT / sub / sub_bin
            if candidate.exists():
                return str(candidate)
    return sys.executable


@function_tool()
async def restart_self(
    context: RunContext,  # type: ignore
) -> str:
    """
    Restart Peter's own worker process so recently-edited code takes effect.

    Call this after write_own_file changes a Python file and the user wants
    the change live now. IMPORTANT: speak your acknowledgement BEFORE calling
    this — the restart drops the current call within a couple seconds, so say
    something like "Restarting now, give me a few seconds then tap the orb
    again" first, THEN call this tool.
    """
    try:
        import psutil
    except ImportError:
        return "I can't restart myself — the psutil package isn't installed."

    old_pids: list[int] = []
    try:
        for proc in psutil.process_iter(["pid", "cmdline"]):
            try:
                cmdline = proc.info.get("cmdline") or []
                if any("agent.py" in (arg or "") for arg in cmdline):
                    old_pids.append(proc.info["pid"])
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
    except Exception as e:  # noqa: BLE001
        return f"I couldn't inspect my own process to restart: {e}"

    def _do_restart() -> None:
        # Let this tool's result actually reach the user before anything dies.
        time.sleep(2.5)
        try:
            subprocess.Popen(
                [_venv_python(), str(_ROOT / "agent.py"), "dev"],
                cwd=str(_ROOT),
                creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0),
            )
        except Exception as e:  # noqa: BLE001
            logging.error(f"restart_self: failed to spawn new worker: {e}")
            return
        time.sleep(1.5)  # give the new worker a moment to start booting
        for pid in old_pids:
            try:
                psutil.Process(pid).terminate()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass

    threading.Thread(target=_do_restart, daemon=True).start()
    return (
        "Restarting now with the updated code — give me about ten seconds, "
        "then tap the orb again to reconnect."
    )


# ──────────────────────────────────────────────────────────────
# Full shutdown — the user's daily sign-off phrase closes everything
# (orb window + worker process together, same as pressing the X button).
# Same stash-by-call_id / pop-on-tool-end pattern as tools.py's
# show_in_chat: this tool just flags the call; agent.py's
# tool_call_ended handler does the actual broadcast to the UI, which is
# what calls peter_app.py's close_window() (see peter_ui.html).
# ──────────────────────────────────────────────────────────────
_pending_close_signal: dict[str, bool] = {}


def pop_pending_close_signal(call_id: str) -> bool:
    """Retrieve (and clear) the close flag stashed for a finished tool call."""
    return _pending_close_signal.pop(call_id, False)


@function_tool()
async def close_everything(
    context: RunContext,  # type: ignore
) -> str:
    """
    Shut Peter down completely for the day — closes the orb window AND
    terminates the worker process together (same as the user clicking the
    app's X button).

    ONLY call this when the user says the exact sign-off phrase "thanks
    peter that will be all for today" (or an unmistakably equivalent full
    goodbye meaning "we're completely done for today, shut down") — never
    for a plain "bye" or "thanks" on their own.

    IMPORTANT: speak a short, warm goodbye OUT LOUD FIRST — this tool closes
    the app within a couple seconds of being called, so say goodbye BEFORE
    calling it, not after.
    """
    try:
        call_id = context.function_call.call_id
    except Exception:
        call_id = None
    if call_id:
        _pending_close_signal[call_id] = True
    return "Shutting everything down now — see you next time!"
