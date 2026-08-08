"""
P.E.T.E.R. — Mobile backend (PWA)

A small local HTTP server that lets your iPhone talk to the same Peter setup
you run on this PC. It is intentionally LAN-local (binds to 0.0.0.0 so your
phone on the same Wi-Fi can reach it) and keeps secrets (LiveKit API key,
Spotify client secret) server-side — the phone never sees them.

Endpoints
---------
GET  /connection  -> LiveKit URL + fresh token for the room
GET  /pending     -> current pending URL (the "go peter" slot)
POST /pending     -> set the pending URL
GET  /spotify     -> Spotify auth status + default playlist
POST /spotify/auth -> start Spotify OAuth (opens on the caller)
GET  /spotify/auth_url -> return the Spotify authorize URL (for cloud)
GET  /spotify/callback -> Spotify OAuth callback (for cloud / public URL)
GET  /spotify/playlists -> list the user's playlists (must be authorized)
POST /spotify/default   -> set the default playlist
GET  /files        -> list received files + active file
POST /files/save   -> save a file (base64) and mark it active
POST /files/active -> set the active file
GET  /pwa          -> the mobile PWA (index.html)

Run with:
    python mobile_backend.py
Then, on your phone (same Wi-Fi), open:
    http://<THIS_PC_LAN_IP>:9094
and use Safari's "Share -> Add to Home Screen" to create the app shortcut.

CLOUD MODE (works without your PC being on)
--------------------------------------------
The same single server can be deployed to a cloud host (Render / Railway /
Fly.io) that keeps it running permanently. It serves the UI AND the API AND
auto-starts the Peter worker from one container, so there is exactly one
public link. Set these in the host:

    LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET
    GOOGLE_API_KEY
    AGENT_ID (default peter-assistant)
    SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET
    SPOTIFY_REDIRECT_URI=https://<your-app-url>/spotify/callback
    PUBLIC_URL=https://<your-app-url>   (optional, for building links)

In cloud mode the Peter worker is started inside the same container on boot.
Note: mem0 local storage (./memory_store) and files (./received_files) are
ephemeral on many free hosts — they reset on redeploy. For persistent
memory/files set MEM0_API_KEY so memory lives in the cloud.

Foreign phone reach: the livekit token endpoint just returns the LiveKit URL
+ a fresh token. The phone never needs your PC's IP. LiveKit connects the
phone straight to the (cloud) LiveKit room, where the worker is waiting.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
import uvicorn

from livekit import api

_HERE = Path(os.path.dirname(os.path.abspath(__file__)))

# IMPORTANT: load .env from this script's own directory, NOT the CWD.
# The Windows Run-key autostart launches us with the working directory set to
# %windir%\System32, so `load_dotenv()` (which looks in the CWD by default)
# would MISS the .env file and leave LIVEKIT_API_KEY / LIVEKIT_API_SECRET
# empty → the phone would get a LiveKit token with no valid grants and the
# PWA would freeze with "could not reach Peter". Passing the absolute path
# fixes autostart (and every other launch method) regardless of CWD.
load_dotenv(_HERE / ".env")

import file_storage
import pending
import spotify_auth

_MOBILE_DIR = _HERE / "mobile"

app = FastAPI(title="P.E.T.E.R. Mobile")

# ──────────────────────────────────────────────────────────────
# LiveKit token
# ──────────────────────────────────────────────────────────────
LIVEKIT_URL = os.getenv("LIVEKIT_URL", "wss://project-p-e-t-e-r-b7e84u03.livekit.cloud")
LIVEKIT_API_KEY = os.getenv("LIVEKIT_API_KEY", "")
LIVEKIT_API_SECRET = os.getenv("LIVEKIT_API_SECRET", "")
ROOM = "peter-mobile"


def _ensure_agent_dispatch() -> None:
    """Create a fresh agent dispatch so the worker joins the mobile room.

    Mirrors peter_app.py: LiveKit only sends the agent into a room if there's
    a dispatch for it. Dispatches are one-shot per agent session, so stale
    ones are deleted and a new one created each time. Runs fire-and-forget so
    the token endpoint returns immediately.
    """
    import asyncio
    from livekit.protocol.agent_dispatch import CreateAgentDispatchRequest

    async def _ensure() -> None:
        lkapi = api.LiveKitAPI(LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET)
        try:
            try:
                existing = await lkapi.agent_dispatch.list_dispatch(ROOM)
                for d in existing:
                    try:
                        await lkapi.agent_dispatch.delete_dispatch(d.id, ROOM)
                    except Exception:
                        pass
            except Exception:
                pass
            await lkapi.agent_dispatch.create_dispatch(
                CreateAgentDispatchRequest(
                    agent_name=os.getenv("AGENT_ID", "peter-assistant"),
                    room=ROOM,
                )
            )
        finally:
            await lkapi.aclose()

    def _runner() -> None:
        asyncio.run(_ensure())

    try:
        t = threading.Thread(target=_runner, daemon=True)
        t.start()
    except Exception:
        pass


@app.get("/connection")
def connection() -> dict:
    """Return the LiveKit URL + a fresh token for the mobile room."""
    _ensure_agent_dispatch()
    token = (
        api.AccessToken(LIVEKIT_API_KEY, LIVEKIT_API_SECRET)
        .with_identity("mobile-user")
        .with_name("Mobile User")
        .with_grants(api.VideoGrants(room_join=True, room=ROOM))
        .to_jwt()
    )
    return {"url": LIVEKIT_URL, "room": ROOM, "token": token}


# ──────────────────────────────────────────────────────────────
# Pending URL (the "go peter" slot) — shared file with the worker
# ──────────────────────────────────────────────────────────────
@app.get("/pending")
def get_pending() -> dict:
    return {"url": pending.get_pending_url()}


@app.post("/pending")
async def set_pending(request: Request) -> dict:
    body = await request.json()
    url = (body.get("url") or "").strip()
    pending.set_pending_url(url)
    return {"saved": True, "url": url}


# ──────────────────────────────────────────────────────────────
# Spotify
# ──────────────────────────────────────────────────────────────
def _spotify_status() -> dict:
    import spotify_tools
    try:
        authorized = False
        try:
            authorized = spotify_auth.is_authorized()
        except Exception:
            authorized = False
        default = spotify_tools.get_default_playlist()
        return {
            "authorized": authorized,
            "default": default,
            "client_id_set": bool(os.getenv("SPOTIFY_CLIENT_ID", "").strip()),
        }
    except Exception as e:  # noqa: BLE001
        return {"authorized": False, "default": {}, "client_id_set": False, "error": str(e)}


@app.get("/spotify")
def spotify_status() -> dict:
    return _spotify_status()


@app.post("/spotify/auth")
def spotify_auth_start() -> dict:
    """Open Spotify OAuth on the PC (the caller must be on the same machine
    or the user completes it in their PC browser)."""
    result = {"started": False, "ok": False}
    try:
        def _run():
            try:
                result["ok"] = spotify_auth.authorize()
            except Exception as e:  # noqa: BLE001
                result["ok"] = False
                result["error"] = str(e)
        threading.Thread(target=_run, daemon=True).start()
        result["started"] = True
    except Exception as e:  # noqa: BLE001
        result["error"] = str(e)
    return result


@app.get("/spotify/playlists")
def spotify_playlists() -> dict:
    if not spotify_auth.is_authorized():
        return {"ok": False, "error": "NOT_AUTHORIZED", "playlists": []}
    try:
        client = spotify_auth.get_client()
        playlists = []
        results = client.current_user_playlists(limit=50)
        for item in results.get("items", []):
            playlists.append({"id": item["id"], "uri": item["uri"], "name": item["name"]})
        return {"ok": True, "playlists": playlists}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e), "playlists": []}


@app.post("/spotify/default")
async def spotify_set_default(request: Request) -> dict:
    import spotify_tools
    body = await request.json()
    pid = (body.get("playlist_id") or "").strip()
    name = (body.get("name") or "").strip()
    if ":" in pid:
        pid = pid.split(":")[-1]
    spotify_tools.set_default_playlist(pid, name)
    return {"ok": True}


@app.get("/spotify/auth_url")
def spotify_auth_url() -> dict:
    """Return the Spotify authorization URL for the caller to open.

    Used on a public (cloud) deployment: the phone opens this URL in a new
    tab, the user approves, and Spotify redirects back to /spotify/callback.
    """
    try:
        return {"ok": True, "auth_url": spotify_auth.get_authorize_url()}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}


@app.get("/spotify/callback")
def spotify_callback(code: str = "", error: str = "") -> HTMLResponse:
    """Handle the Spotify OAuth redirect for public (cloud) deployments.

    This is the Redirect URI you register in the Spotify dashboard as
    https://<your-app-url>/spotify/callback. On success we exchange the code
    for a refresh token and show a friendly "done" page; the PWA then refresh
    its /spotify status (polling) to pick up the newly-authorized state.
    """
    ok = bool(code) and not error and spotify_auth.handle_callback(code)
    msg = (
        "🎉 Peter connected to Spotify! You can close this tab."
        if ok
        else "⚠️ Spotify authorization failed. Please try again from the P.E.T.E.R. app."
    )
    html = (
        "<!DOCTYPE html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<title>P.E.T.E.R. — Spotify</title>"
        "<style>body{background:#000;color:#fff;font-family:system-ui,sans-serif;"
        "display:flex;align-items:center;justify-content:center;min-height:100vh;"
        "margin:0;text-align:center}</style></head><body><div>"
        f"<h2>{msg}</h2>"
        "<p><a href='/'>Back to P.E.T.E.R.</a></p></div></body></html>"
    )
    return HTMLResponse(html)


# ──────────────────────────────────────────────────────────────
# Files (receive + pick a file for Peter, from the phone)
# ──────────────────────────────────────────────────────────────
@app.get("/files")
def files_status() -> dict:
    """List received files and which one is the active file Peter reads."""
    try:
        files = file_storage.list_files()
        active = file_storage.get_active_file()
        return {"ok": True, "files": files, "active": active}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "files": [], "active": "", "error": str(e)}


@app.post("/files/save")
async def files_save(request: Request) -> dict:
    """Save a file (base64 ``data``) from the phone and mark it active."""
    body = await request.json()
    filename = (body.get("filename") or body.get("name") or "").strip()
    data = body.get("data") or ""
    if not filename or not data:
        return {"ok": False, "error": "filename and data (base64) required."}
    try:
        result = file_storage.save_file(filename, data)
        if result.get("ok"):
            file_storage.set_active_file(result["name"])
        return result
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}


@app.post("/files/active")
async def files_set_active(request: Request) -> dict:
    """Set which received file Peter should read as the active file."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    filename = (body.get("filename") or "").strip()
    ok = file_storage.set_active_file(filename)
    return {"ok": ok, "active": filename if ok else ""}


# ──────────────────────────────────────────────────────────────
# Static PWA
# ──────────────────────────────────────────────────────────────
@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(_MOBILE_DIR / "index.html")


@app.get("/manifest.json", include_in_schema=False)
def manifest() -> FileResponse:
    return FileResponse(_MOBILE_DIR / "manifest.json")


@app.get("/sw.js", include_in_schema=False)
def sw() -> FileResponse:
    return FileResponse(_MOBILE_DIR / "sw.js")


def _lan_ip() -> str:
    """Return this PC's primary LAN IP (for the phone to connect to)."""
    try:
        import socket
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


# ──────────────────────────────────────────────────────────────
# Auto-start the Peter voice worker (so the phone doesn't need the
# desktop app open to talk to Peter)
# ──────────────────────────────────────────────────────────────
def _venv_python() -> str:
    """Return the Python executable for launching the worker.

    On Windows prefer the venv's console python.exe; on Linux/macOS use the
    venv's bin/python (or the current interpreter). Falls back to the current
    interpreter if no venv exists.
    """
    base = os.path.dirname(os.path.abspath(__file__))
    for sub in (".venv", "venv"):
        if os.name == "nt":
            candidate = os.path.join(base, sub, "Scripts", "python.exe")
        else:
            candidate = os.path.join(base, sub, "bin", "python")
        if os.path.exists(candidate):
            return candidate
    return sys.executable


def _worker_lock_path() -> str:
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), ".peter_worker.lock")


def _worker_running() -> bool:
    """Return True if an `agent.py` LiveKit worker is already running.

    Works on Windows and Linux/macOS: matches any process whose command line
    contains agent.py and whose executable name looks like a Python/sys.py
    interpreter.
    """
    try:
        import psutil
        for proc in psutil.process_iter(["name", "cmdline"]):
            try:
                cmdline = proc.info.get("cmdline") or []
                name = (proc.info.get("name") or "").lower()
                if not any(p.lower().find("agent.py") >= 0 for p in cmdline):
                    continue
                # Match python interpreters across platforms.
                if not any(tok in name for tok in ("python", "pypy")):
                    continue
                return True
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
    except Exception:
        pass
    return False


def _minimized_startupinfo():
    try:
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = 6  # SW_MINIMIZE
        return si
    except AttributeError:
        return None


def _ensure_peter_worker() -> None:
    """Start the agent worker automatically if it's not already running.

    On Windows spawns it in a minimized console window. On Linux/macOS (cloud)
    it runs as a normal background child of this process. Uses a lock file so
    only one worker is ever launched, even if multiple processes race to start
    it.
    """
    import subprocess
    import sys

    if _worker_running():
        return

    lock_path = _worker_lock_path()
    try:
        if os.path.exists(lock_path):
            os.remove(lock_path)
    except OSError:
        pass

    try:
        lock_fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return

    # Platform-specific Popen kwargs (Windows may hide/minimize the console).
    kwargs = {}
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
        kwargs["startupinfo"] = _minimized_startupinfo()

    try:
        proc = subprocess.Popen(
            [_venv_python(), "agent.py", "dev"],
            cwd=os.path.dirname(os.path.abspath(__file__)),
            **kwargs,
        )
        os.write(lock_fd, str(proc.pid).encode())
        os.close(lock_fd)
        print("  +++ Peter voice worker auto-started +++")
    except Exception as e:  # noqa: BLE001
        print(f"  Could not auto-start worker: {e}")
        try:
            os.close(lock_fd)
        except OSError:
            pass
        try:
            os.remove(lock_path)
        except OSError:
            pass


if __name__ == "__main__":
    # Cloud hosts (Render/Railway/Fly) inject PORT; fall back to the local 9094.
    port = int(os.getenv("PORT", os.getenv("MOBILE_PORT", "9094")))
    host = os.getenv("HOST", "0.0.0.0")
    print("=" * 60)
    print("  P.E.T.E.R. Mobile backend")
    print("=" * 60)
    # Start Peter's voice worker automatically so the phone can reach him
    # without the desktop app being open (works in a cloud container too).
    _ensure_peter_worker()
    if os.getenv("PORT"):
        print(f"  CLOUD MODE: serving on 0.0.0.0:{port} (public URL below)")
        print(f"  PUBLIC_URL   : {os.getenv('PUBLIC_URL', 'set PUBLIC_URL')}")
        print("  The Peter voice worker runs inside this same container.")
    else:
        print(f"  On THIS PC open : http://127.0.0.1:{port}")
        print(f"  On your PHONE   : http://{_lan_ip()}:{port}")
        print("  (Same Wi-Fi required. Not exposed to the internet.)")
    print("=" * 60)
    uvicorn.run(app, host=host, port=port, log_level="warning")
