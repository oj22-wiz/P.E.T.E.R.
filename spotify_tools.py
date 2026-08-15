"""
P.E.T.E.R. — Spotify tools for the agent (accessed via voice).

These are LiveKit function tools so Peter can control your Spotify:
  - list_playlists    — show your playlists (names)
  - play_playlist     — start playing a specific playlist by name
  - play_my_music     — play your "default" playlist chosen in the desktop UI

Playback uses Spotify Connect: Peter calls the API and playback starts on
whatever device currently has an active Spotify session (your phone, your
PC's Spotify app, a speaker, etc.).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path

from livekit.agents import RunContext, function_tool

import spotify_auth
from spotify_auth import get_client, is_authorized

_HERE = Path(os.path.dirname(os.path.abspath(__file__)))
_DEFAULT_PLAYLIST_FILE = _HERE / "spotify_default_playlist.json"


# ──────────────────────────────────────────────────────────────
# Default playlist (picked in the desktop UI)
# ──────────────────────────────────────────────────────────────
def set_default_playlist(playlist_id: str, name: str) -> None:
    """Save the playlist the user picked in the UI as the 'play my music' default."""
    try:
        _DEFAULT_PLAYLIST_FILE.write_text(
            json.dumps({"id": playlist_id, "name": name}),
            encoding="utf-8",
        )
    except OSError as e:
        logging.warning(f"Could not save default playlist: {e}")


def get_default_playlist() -> dict:
    """Return the saved default playlist (id + name), or {} if none set."""
    try:
        return json.loads(_DEFAULT_PLAYLIST_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _find_local_spotify_device(client) -> dict:
    """Return the user's *computer* Spotify device among active devices, or {}.

    Used only as a fallback when no phone/other active device is present.
    """
    try:
        devices = client.devices().get("devices", [])
        for d in devices:
            name_lower = (d.get("name") or "").lower()
            t = (d.get("type") or "").lower()
            if t == "computer" or "computer" in name_lower:
                return d
        return {}
    except Exception:  # noqa: BLE001
        return {}


def _find_phone_spotify_device(client) -> dict:
    """Return an active *phone* Spotify device, or {} if none.

    Phones usually report type "smartphone" or a name containing phone/iPhone/
    Android. We use this first so mobile playback isn't hijacked by the PC app.
    """
    try:
        devices = client.devices().get("devices", [])
        for d in devices:
            name_lower = (d.get("name") or "").lower()
            t = (d.get("type") or "").lower()
            if t == "smartphone" or "smartphone" in name_lower \
               or "iphone" in name_lower or "android" in name_lower:
                return d
        return {}
    except Exception:  # noqa: BLE001
        return {}


def _launch_local_spotify() -> bool:
    """Try to open the Spotify desktop app on this computer (Windows/Mac/Linux).

    Returns True once we've attempted to launch it. On Windows we try common
    install locations of Spotify.exe; elsewhere we try `open`/`xdg-open`.
    """
    import shutil

    candidates = []
    if os.name == "nt":
        # Common Windows install paths.
        candidates = [
            os.path.join(os.getenv("APPDATA", ""), "Spotify", "Spotify.exe"),
            os.path.join(
                os.getenv("LOCALAPPDATA", ""), "Microsoft", "WindowsApps", "Spotify.exe"
            ),
            r"C:\Program Files\Spotify\Spotify.exe",
            r"C:\Program Files (x86)\Spotify\Spotify.exe",
        ]
        # Via registry start-menu shortcut path is unreliable; also check
        # `where spotify` (available if Spotify is on PATH).
        import subprocess
        for cand in candidates:
            if os.path.exists(cand):
                try:
                    subprocess.Popen([cand])
                    return True
                except Exception:  # noqa: BLE001
                    continue
        # Fallback: try the "start" command which finds the app by its
        # registered protocol handler / Start Menu shortcut.
        try:
            subprocess.Popen(
                ["cmd", "/c", "start", "", "spotify:"],
                creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0),
            )
            return True
        except Exception:  # noqa: BLE001
            pass
    else:
        # macOS / Linux: open the app via `open` or `xdg-open`.
        shell = shutil.which("open") or shutil.which("xdg-open")
        if shell:
            try:
                subprocess.Popen([shell, "spotify:"])
                return True
            except Exception:  # noqa: BLE001
                pass
    return False


def _minimize_peter_window() -> None:
    """Minimize the P.E.T.E.R. orb window (by title) on Windows.

    The worker process (this module) runs separately from the desktop app, so
    we find the "P.E.T.E.R." top-level window and send it SW_MINIMIZE. That
    way the orb gets out of the way when Spotify opens to play music.
    """
    if os.name != "nt":
        return
    try:
        import ctypes
        import ctypes.wintypes as wt

        user32 = ctypes.windll.user32

        WNDENUMPROC = ctypes.WINFUNCTYPE(
            wt.BOOL, wt.HWND, wt.LPARAM
        )

        found = []

        @WNDENUMPROC
        def _cb(hwnd, _lparam):
            length = user32.GetWindowTextLengthW(hwnd)
            if length > 0:
                buf = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buf, length + 1)
                title = buf.value
                if title.strip().upper().find("P.E.T.E.R") >= 0:
                    found.append(hwnd)
            return True

        user32.EnumWindows(_cb, 0)
        for hwnd in found:
            user32.ShowWindow(hwnd, 6)  # SW_MINIMIZE
    except Exception as e:  # noqa: BLE001
        logging.warning(f"Couldn't minimize P.E.T.E.R window: {e}")


def _do_play_uri(client, uri: str, context_name: str) -> str:
    """Actually start playback — launching the local app if needed.

    If an active Spotify Connect device exists we play on it. Otherwise we
    try to open the user's local Spotify desktop app (even if it's closed),
    wait for it to register as a device, then start playback on it.
    """
    import time as _time

    def _start(device_id, device_name):
        client.start_playback(device_id=device_id, context_uri=uri)
        # Playback started — tuck the P.E.T.E.R orb away so Spotify is in view.
        _minimize_peter_window()
        return f"Playing {context_name} on {device_name}."

    try:
        # Prefer an active *phone* device first, so commands issued from the
        # phone (or "play my music" while the desktop app is closed) play on
        # the phone rather than being hijacked by the PC's Spotify app.
        phone = _find_phone_spotify_device(client)
        if phone:
            try:
                return _start(phone["id"], phone["name"])
            except Exception:  # noqa: BLE001
                pass

        # Otherwise prefer the local computer as a fallback.
        local = _find_local_spotify_device(client)
        if local:
            try:
                return _start(local["id"], local["name"])
            except Exception:  # noqa: BLE001
                pass

        devices = client.devices().get("devices", [])
        if devices:
            d = devices[0]
            return _start(d["id"], d["name"])

        # No active device — try to launch the local Spotify desktop app.
        launched = _launch_local_spotify()
        if launched:
            # Poll up to ~12s for the local app to appear as a Connect device.
            for _ in range(24):
                _time.sleep(0.5)
                local = _find_local_spotify_device(client)
                if local:
                    try:
                        return _start(local["id"], local["name"])
                    except Exception:  # noqa: BLE001
                        pass
                devices = client.devices().get("devices", [])
                if devices:
                    d = devices[0]
                    try:
                        return _start(d["id"], d["name"])
                    except Exception:  # noqa: BLE001
                        pass

        return (
            "I opened the Spotify app on your computer. If it just launched, "
            "give it a couple of seconds, then ask me to play your music again."
            if launched else
            "I couldn't find an active Spotify device to play on. "
            "Please open the Spotify app on your computer (or phone) and try again."
        )
    except Exception as e:  # noqa: BLE001
        logging.error(f"play_uri error: {e}")
        return f"I couldn't start playback: {e}"


def _play_uri(client, uri: str, context_name: str) -> str:
    """Return an immediate acknowledgment and start playback in the background.

    The tool returns right away so Peter can speak a natural confirmation
    ("On it — starting your music!") while the actual playback (and any
    Spotify app launch + ~12s wait) happens in a background thread. This
    makes the response feel instant and conversational instead of silently
    waiting through the whole launch.
    """
    import threading
    try:
        t = threading.Thread(
            target=_do_play_uri,
            args=(client, uri, context_name),
            daemon=True,
        )
        t.start()
    except Exception as e:  # noqa: BLE001
        logging.error(f"Couldn't start playback thread: {e}")
        return "I couldn't start your music. Give it another try in a moment."
    return f"On it — I'm starting {context_name} now."


# ──────────────────────────────────────────────────────────────
# LiveKit function tools
# ──────────────────────────────────────────────────────────────
@function_tool()
async def list_playlists(
    context: RunContext,  # type: ignore
) -> str:
    """List the user's Spotify playlists (names only).

    Call this to see what playlists the user has so you can name one to play.
    """
    if not is_authorized():
        return "SPOTIFY_NOT_AUTHORIZED"
    try:
        # spotipy is synchronous. This tool runs on the same event loop as
        # the live voice session, so calling it directly would freeze
        # Peter's audio while it talks to Spotify's API.
        def _list() -> list:
            client = get_client()
            results = client.current_user_playlists(limit=50)
            return [item["name"] for item in results.get("items", [])]

        names = await asyncio.to_thread(_list)
        if not names:
            return "You don't have any playlists I can see."
        return "Your playlists: " + "; ".join(names)
    except Exception as e:  # noqa: BLE001
        logging.error(f"list_playlists error: {e}")
        return f"I couldn't list your playlists: {e}"


@function_tool()
async def play_playlist(
    context: RunContext,  # type: ignore
    name: str) -> str:
    """Play a Spotify playlist by its name.

    Call this when the user asks to play a specific playlist.
    """
    if not is_authorized():
        return "SPOTIFY_NOT_AUTHORIZED"
    try:
        def _find() -> tuple:
            client = get_client()
            results = client.current_user_playlists(limit=50)
            for item in results.get("items", []):
                if item["name"].strip().lower() == name.strip().lower():
                    return client, item
            return client, None

        client, chosen = await asyncio.to_thread(_find)
        if chosen is None:
            return f"I couldn't find a playlist named '{name}'. Ask me to list your playlists."
        return _play_uri(client, chosen["uri"], chosen["name"])
    except Exception as e:  # noqa: BLE001
        logging.error(f"play_playlist error: {e}")
        return f"I couldn't play that playlist: {e}"


@function_tool()
async def play_my_music(
    context: RunContext,  # type: ignore
) -> str:
    """Play the user's default Spotify playlist.

    Call this when the user says "play my music". The default playlist is the
    one they selected in the Spotify menu.
    """
    if not is_authorized():
        return "SPOTIFY_NOT_AUTHORIZED"
    default = get_default_playlist()
    if not default:
        return (
            "You haven't picked a default playlist yet. "
            "Open the Spotify menu in the app and choose a playlist, then ask me to play my music."
        )
    try:
        client = await asyncio.to_thread(get_client)
        pid = default["id"]
        # The desktop UI saves a bare playlist ID. Spotify's start_playback
        # needs a full context URI like "spotify:playlist:<id>", so build it
        # from the ID (handle the case where a URI was saved instead).
        pid = pid.strip()
        if ":" not in pid:
            pid = f"spotify:playlist:{pid}"
        return _play_uri(client, pid, default["name"])
    except Exception as e:  # noqa: BLE001
        logging.error(f"play_my_music error: {e}")
        return f"I couldn't play your music: {e}"
