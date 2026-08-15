"""
P.E.T.E.R. — Agenda / calendar storage helper.

Two modes, chosen automatically by whether CALENDAR_API_URL is set:

- LOCAL (default): stores events in a small JSON file at the project root,
  agenda.json. Mirrors the pending-URL / active-file pattern (file_storage.py,
  pending.py) — a plain file that both the desktop app (peter_app.py, for the
  UI panel) and the agent worker (calendar_tools.py, for Peter) read/write.
  This is how mobile_backend.py's OWN process always runs — it's the
  canonical store the phone talks to over HTTP via /calendar/* in
  mobile_backend.py.

- REMOTE: when CALENDAR_API_URL is set (to a deployed mobile_backend.py,
  e.g. your Render URL) this module transparently proxies every call to that
  service's /calendar/* endpoints instead of touching a local file. Point the
  desktop app's .env at your cloud deployment this way and its Agenda panel
  (and anything Peter adds by voice through a locally-running worker) reads
  and writes the SAME calendar the phone sees — a local file can't be shared
  across two machines, but an HTTPS endpoint can. Never set this on the
  Render deployment's own environment — it must stay in LOCAL mode there,
  since it *is* the shared store, not a proxy to itself.

Colors are deliberately restricted to a fixed palette (red/green/blue/yellow)
so the UI can render a simple, consistent dot per event without arbitrary
color input.
"""

from __future__ import annotations

import json
import logging
import os
import re
import uuid
from datetime import datetime
from pathlib import Path

import requests

_HERE = Path(os.path.dirname(os.path.abspath(__file__)))
_AGENDA_FILE = _HERE / "agenda.json"

ALLOWED_COLORS = {"red", "green", "blue", "yellow"}

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_TIME_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")

# Set this (in the desktop machine's .env) to a deployed mobile_backend.py's
# public URL to make this a thin client of that service instead of writing a
# local file — see module docstring above.
_REMOTE_BASE = os.getenv("CALENDAR_API_URL", "").strip().rstrip("/")
_REMOTE_TIMEOUT = 8


def _remote_get(path: str, params: dict | None = None) -> dict:
    resp = requests.get(_REMOTE_BASE + path, params=params, timeout=_REMOTE_TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def _remote_post(path: str, body: dict) -> dict:
    resp = requests.post(_REMOTE_BASE + path, json=body, timeout=_REMOTE_TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def _load() -> list:
    try:
        data = json.loads(_AGENDA_FILE.read_text(encoding="utf-8"))
        events = data.get("events", [])
        return events if isinstance(events, list) else []
    except (OSError, ValueError):
        return []


def _save(events: list) -> None:
    try:
        _AGENDA_FILE.write_text(
            json.dumps({"events": events}, indent=2), encoding="utf-8"
        )
    except OSError as e:  # noqa: BLE001
        logging.warning(f"Couldn't save agenda: {e}")


def _sort_key(ev: dict):
    return (ev.get("date", ""), ev.get("time") or "99:99")


# ──────────────────────────────────────────────────────────────
# Validation
# ──────────────────────────────────────────────────────────────
def valid_date(date: str) -> bool:
    if not _DATE_RE.match(date or ""):
        return False
    try:
        datetime.strptime(date, "%Y-%m-%d")
        return True
    except ValueError:
        return False


def valid_time(time: str) -> bool:
    if not time:
        return True  # blank = all-day event
    return bool(_TIME_RE.match(time))


# ──────────────────────────────────────────────────────────────
# CRUD
# ──────────────────────────────────────────────────────────────
def add_event(date: str, time: str, name: str, color: str) -> dict:
    """Add an event. Returns {ok, event} or {ok: False, error}."""
    date = (date or "").strip()
    time = (time or "").strip()
    name = (name or "").strip()
    color = (color or "").strip().lower()

    if _REMOTE_BASE:
        try:
            return _remote_post("/calendar/add", {"date": date, "time": time, "name": name, "color": color})
        except Exception as e:  # noqa: BLE001
            logging.warning(f"remote add_event failed: {e}")
            return {"ok": False, "error": "REMOTE_UNAVAILABLE"}

    if not valid_date(date):
        return {"ok": False, "error": "INVALID_DATE"}
    if not valid_time(time):
        return {"ok": False, "error": "INVALID_TIME"}
    if not name:
        return {"ok": False, "error": "MISSING_NAME"}
    if color not in ALLOWED_COLORS:
        return {"ok": False, "error": "INVALID_COLOR"}

    event = {
        "id": uuid.uuid4().hex[:8],
        "date": date,
        "time": time,
        "name": name[:120],
        "color": color,
    }
    events = _load()
    events.append(event)
    events.sort(key=_sort_key)
    _save(events)
    return {"ok": True, "event": event}


def list_events(date: str = "") -> list:
    """All events, optionally filtered to one date (YYYY-MM-DD). Sorted."""
    if _REMOTE_BASE:
        try:
            res = _remote_get("/calendar/events", {"date": date} if date else None)
            return res.get("events", []) if isinstance(res, dict) else []
        except Exception as e:  # noqa: BLE001
            logging.warning(f"remote list_events failed: {e}")
            return []

    events = _load()
    if date:
        events = [e for e in events if e.get("date") == date]
    return sorted(events, key=_sort_key)


def list_events_for_month(year: int, month: int) -> list:
    """All events falling within a given calendar month."""
    if _REMOTE_BASE:
        try:
            res = _remote_get("/calendar/month", {"year": year, "month": month})
            return res.get("events", []) if isinstance(res, dict) else []
        except Exception as e:  # noqa: BLE001
            logging.warning(f"remote list_events_for_month failed: {e}")
            return []

    prefix = f"{year:04d}-{month:02d}-"
    return sorted(
        (e for e in _load() if str(e.get("date", "")).startswith(prefix)),
        key=_sort_key,
    )


def find_events(name: str = "", date: str = "") -> list:
    """Case-insensitive substring match on name, optionally also on date.

    Built on top of list_events() (rather than reading local state directly)
    so it works transparently in both local and remote mode.
    """
    events = list_events(date.strip() if date else "")
    name_q = (name or "").strip().lower()
    if name_q:
        events = [e for e in events if name_q in str(e.get("name", "")).lower()]
    return events


def remove_event(event_id: str) -> bool:
    """Delete an event by id. Returns True if something was removed."""
    if _REMOTE_BASE:
        try:
            res = _remote_post("/calendar/remove", {"id": event_id})
            return bool(res.get("ok")) if isinstance(res, dict) else False
        except Exception as e:  # noqa: BLE001
            logging.warning(f"remote remove_event failed: {e}")
            return False

    events = _load()
    remaining = [e for e in events if e.get("id") != event_id]
    if len(remaining) == len(events):
        return False
    _save(remaining)
    return True
