"""
P.E.T.E.R. Desktop App — a compact Siri-style overlay that pops up at the
top-right of the screen.

Run with:
    python peter_app.py     (or double-click the "P.E.T.E.R" desktop shortcut)

A small frameless, transparent, always-on-top orb appears at the top-right of
the screen. The app listens from the moment it opens: click the orb to
connect to Peter's voice assistant, or clap twice in quick succession to
connect hands-free. The orb's ring + mask animate while Peter is talking.

If the agent worker isn't already running, launching this app starts it
automatically in a "PETER-WORKER" console window — no separate step needed.
"""

from __future__ import annotations

import os
import subprocess
import sys
from datetime import datetime
from typing import Optional

from dotenv import load_dotenv
from livekit import api

import pending
import file_storage

load_dotenv()

# The clap-to-connect listener runs an AudioContext the instant the app
# boots, before the user has made any gesture (click/tap). Chromium's
# autoplay policy suspends a fresh AudioContext until a user gesture occurs,
# which would silently starve it of data forever. WebView2 honors this env
# var (read by its own loader, independent of pywebview) to disable that
# gate for this app's WebView2 instance only — set before any window is
# created. Respect an existing value if the user has already set one.
os.environ.setdefault("WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS", "--autoplay-policy=no-user-gesture-required")

_UI_LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ui_debug.log")

_window = None  # the live pywebview window (for moving / closing)
_worker_proc = None  # the launched agent worker process (for kill/minimize)
_geometry = None  # desired (x, y, w, h) snapped to the work area in main()


# ──────────────────────────────────────────────────────────────
# Token generation (exposed to the frontend as `window.python`)
# ──────────────────────────────────────────────────────────────
class PeterApi:
    """Bridge between the HTML UI and Python (exposed via pywebview)."""

    def __init__(self) -> None:
        self._url = os.getenv("LIVEKIT_URL", "wss://project-p-e-t-e-r-b7e84u03.livekit.cloud")
        self._api_key = os.getenv("LIVEKIT_API_KEY", "")
        self._api_secret = os.getenv("LIVEKIT_API_SECRET", "")

    def get_connection_info(self) -> dict:
        """Return the LiveKit URL + a fresh token the frontend can use."""
        room = "peter-desktop"
        # CRITICAL: tell LiveKit to send the agent worker into this room.
        # Without an explicit agent dispatch, the worker registers but never
        # gets assigned to the room, so Peter never joins the call.
        self._ensure_agent_dispatch(room)
        token = (
            api.AccessToken(self._api_key, self._api_secret)
            .with_identity("desktop-user")
            .with_name("Desktop User")
            .with_grants(api.VideoGrants(room_join=True, room=room))
            .to_jwt()
        )
        return {"url": self._url, "room": room, "token": token}

    def _ensure_agent_dispatch(self, room: str) -> None:
        """Create a fresh agent dispatch so the worker joins the room.

        LiveKit only sends an agent into a room if there's a dispatch for it.
        The Playground UI creates this automatically; the desktop app must too.
        Dispatches are one-shot per agent session, so stale ones are deleted
        and a new one created each time. Runs in a dedicated thread so it works
        even when called from inside an active asyncio loop (pywebview bridge,
        test harnesses).
        """
        import asyncio
        import threading
        from livekit.protocol.agent_dispatch import CreateAgentDispatchRequest

        async def _ensure() -> None:
            lkapi = api.LiveKitAPI(self._url, self._api_key, self._api_secret)
            try:
                # Delete any existing dispatches for this room first so the
                # new one always re-triggers the agent (dispatches don't
                # re-fire after the previous agent session ends).
                try:
                    existing = await lkapi.agent_dispatch.list_dispatch(room)
                    for d in existing:
                        try:
                            await lkapi.agent_dispatch.delete_dispatch(d.id, room)
                        except Exception:
                            pass
                except Exception:
                    pass
                await lkapi.agent_dispatch.create_dispatch(
                    CreateAgentDispatchRequest(
                        agent_name="peter-assistant",
                        room=room,
                    )
                )
            finally:
                await lkapi.aclose()

        def _runner() -> None:
            asyncio.run(_ensure())

        # Fire-and-forget: never block the caller (the pywebview bridge thread)
        # on LiveKit network calls. get_connection_info() must return the token
        # immediately so the UI doesn't freeze. The worker picks up the room in
        # the background; the UI already waits up to 60s for the agent.
        try:
            t = threading.Thread(target=_runner, daemon=True)
            t.start()
        except Exception:
            # Best-effort: if dispatch creation fails the worker may still
            # pick up the room; never break the UI flow over it.
            pass

    def launch_agent_worker(self) -> str:
        """Launch the agent worker in a background console if it's not running."""
        # Use the venv's console python.exe (not pythonw.exe) so the worker
        # logs to a visible console and keeps sys.stdout available. The
        # console starts minimized so it doesn't pop over the desktop app.
        subprocess.Popen(
            [_venv_python(), "agent.py", "dev"],
            cwd=os.path.dirname(os.path.abspath(__file__)),
            creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0),
            startupinfo=_minimized_startupinfo(),
        )
        return "Worker launch requested."

    # ── Pending URL (the "go peter" slot) ─────────────────────
    def set_pending_url(self, url: str) -> dict:
        """Save the URL the user typed into the little box.

        Called from the UI whenever the value changes. Blank clears the slot.
        This is the shared file the agent worker reads when Peter hears
        "go peter".
        """
        saved = pending.set_pending_url(url)
        return {"saved": saved}

    def get_pending_url(self) -> dict:
        """Return the currently-stored pending URL (for UI restore)."""
        return {"url": pending.get_pending_url()}

    def clear_pending_url(self) -> dict:
        """Empty the pending URL slot (used after 'go peter' is acted on)."""
        pending.clear_pending_url()
        return {"cleared": True}

    # ── Files integration (receive + pick a file for Peter) ──
    def files_status(self) -> dict:
        """Return the list of received files and which one is active."""
        try:
            files = file_storage.list_files()
            active = file_storage.get_active_file()
            return {"ok": True, "files": files, "active": active}
        except Exception as e:  # noqa: BLE001
            print(f"files_status error: {e}")
            return {"ok": False, "files": [], "active": "", "error": str(e)}

    def files_save(self, filename: str, data: str) -> dict:
        """Save a received file (data is base64). Adds it and marks it active."""
        try:
            result = file_storage.save_file(filename, data)
            if result.get("ok"):
                # Newly dropped/picked file becomes the active one automatically.
                file_storage.set_active_file(result["name"])
            return result
        except Exception as e:  # noqa: BLE001
            print(f"files_save error: {e}")
            return {"ok": False, "error": str(e)}

    def files_set_active(self, filename: str) -> dict:
        """Set which received file is the active one Peter should read."""
        try:
            ok = file_storage.set_active_file(filename)
            return {"ok": ok, "active": filename}
        except Exception as e:  # noqa: BLE001
            print(f"files_set_active error: {e}")
            return {"ok": False, "error": str(e)}

    def files_choose(self) -> dict:
        """Open a native file-picker and save the chosen file for Peter.

        Returns info about the saved file so the UI can refresh.
        """
        import base64 as _b64
        try:
            import webview as _wv
            choose = _wv.create_file_dialog(
                _wv.OPEN_DIALOG,
                file_types=("All Files (*.*)", "Text files (*.txt;*.md)", "PDF (*.pdf)", "Word (*.docx;*.doc)"),
            )
            if not choose:
                return {"ok": False, "cancelled": True}
            path = choose[0] if isinstance(choose, (list, tuple)) else choose
            name = os.path.basename(path)
            with open(path, "rb") as f:
                data = _b64.b64encode(f.read()).decode("ascii")
            result = file_storage.save_file(name, data)
            if result.get("ok"):
                file_storage.set_active_file(result["name"])
            return result
        except Exception as e:  # noqa: BLE001
            print(f"files_choose error: {e}")
            return {"ok": False, "error": str(e)}

    # ── CAD designs (open/reveal the files Peter creates) ────
    def open_cad_design(self, name: str, kind: str) -> dict:
        """Open one of a design's files (stl/png/scad) with its default app.

        Called from the file card in the chat log when the user taps
        "Open STL" / "Preview". `name`/`kind` are resolved through
        cad_tools.get_design_paths, which only ever returns paths that
        actually exist under cad_designs/ — nothing else is ever opened.
        """
        import cad_tools
        try:
            path = cad_tools.get_design_paths(name).get(kind)
            if not path:
                return {"ok": False, "error": "That file doesn't exist."}
            os.startfile(path)  # noqa: S606 — path came from get_design_paths, not user input
            return {"ok": True}
        except Exception as e:  # noqa: BLE001
            print(f"open_cad_design error: {e}")
            return {"ok": False, "error": str(e)}

    def reveal_cad_design(self, name: str) -> dict:
        """Open Explorer with a design's STL (or scad, if no STL) selected."""
        import cad_tools
        try:
            paths = cad_tools.get_design_paths(name)
            path = paths.get("stl") or paths.get("scad")
            if not path:
                return {"ok": False, "error": "That file doesn't exist."}
            subprocess.Popen(["explorer", "/select,", path])
            return {"ok": True}
        except Exception as e:  # noqa: BLE001
            print(f"reveal_cad_design error: {e}")
            return {"ok": False, "error": str(e)}

    # ── Spotify integration ─────────────────────────────────
    def spotify_status(self) -> dict:
        """Return whether Spotify is authorized, plus the default playlist."""
        import spotify_auth
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
            print(f"spotify_status error: {e}")
            return {"authorized": False, "default": {}, "client_id_set": False, "error": str(e)}

    def spotify_authorize(self) -> dict:
        """Kick off the Spotify OAuth flow (opens a browser tab)."""
        import spotify_auth
        import threading
        result = {"started": False, "ok": False}
        try:
            def _run():
                try:
                    ok = spotify_auth.authorize()
                    result["ok"] = ok
                except Exception as e:  # noqa: BLE001
                    result["ok"] = False
            threading.Thread(target=_run, daemon=True).start()
            result["started"] = True
        except Exception as e:  # noqa: BLE001
            print(f"spotify_authorize error: {e}")
            result["error"] = str(e)
        return result

    def spotify_get_playlists(self) -> dict:
        """Return the user's Spotify playlists (id + name + uri)."""
        import spotify_auth
        try:
            if not spotify_auth.is_authorized():
                return {"ok": False, "error": "NOT_AUTHORIZED", "playlists": []}
            client = spotify_auth.get_client()
            playlists = []
            results = client.current_user_playlists(limit=50)
            for item in results.get("items", []):
                playlists.append(
                    {"id": item["id"], "uri": item["uri"], "name": item["name"]}
                )
            return {"ok": True, "playlists": playlists}
        except Exception as e:  # noqa: BLE001
            print(f"spotify_get_playlists error: {e}")
            return {"ok": False, "error": str(e), "playlists": []}

    def spotify_set_default_playlist(self, playlist_id: str, name: str) -> dict:
        """Persist the user's chosen default playlist for 'play my music'."""
        import spotify_tools
        try:
            pid = (playlist_id or "").strip()
            if ":" in pid:  # accept a uri too
                parts = pid.split(":")
                pid = parts[-1]
            spotify_tools.set_default_playlist(pid, name or "")
            return {"ok": True}
        except Exception as e:  # noqa: BLE001
            print(f"spotify_set_default_playlist error: {e}")
            return {"ok": False, "error": str(e)}

    def client_log(self, message: str) -> dict:
        """Append a message from peter_ui.html's JS to ui_debug.log.

        The app is normally launched via the desktop shortcut (no console
        attached) and devtools are disabled, so a log file — not stdout — is
        the only reliable way to see what's happening in the page, e.g. the
        clap listener's mic/permission lifecycle. The file is reset at the
        start of each run (see main()) so it always reflects the latest
        session.
        """
        try:
            with open(_UI_LOG_PATH, "a", encoding="utf-8") as f:
                f.write(f"{datetime.now().strftime('%H:%M:%S')}  {message}\n")
        except Exception:
            pass
        return {"ok": True}

    def hide_window(self) -> dict:
        """Collapse the orb back down off-screen (Siri-style dismiss)."""
        global _window
        try:
            if _window is not None:
                _window.hide()
        except Exception as e:
            # Best-effort: never break the UI if hiding fails.
            print(f"hide_window error: {e}")
        return {"hidden": True}

    def minimize_window(self) -> dict:
        """Minimize both the orb and the worker console to the taskbar."""
        global _window
        _minimize_worker_console()  # minimize Peter's terminal too
        try:
            if _window is not None:
                _window.minimize()
        except Exception as e:
            # Best-effort: never break the UI if minimizing fails.
            print(f"minimize_window error: {e}")
        return {"minimized": True}

    def close_window(self) -> dict:
        """Close the app AND shut down Peter (worker + its console).

        Pressing X is a full exit: the orb window is destroyed and the agent
        worker process (and the console it launched in) is terminated, so
        both close together.
        """
        global _window
        # Kill the worker + its terminal first so it doesn't keep running.
        _kill_worker()
        try:
            if _window is not None:
                _window.destroy()
        except Exception as e:
            # Best-effort: never break the UI if closing fails.
            print(f"close_window error: {e}")
        return {"closed": True}


# ──────────────────────────────────────────────────────────────
# Worker auto-start
# ──────────────────────────────────────────────────────────────
def _lock_path() -> str:
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), ".peter_worker.lock")


def _is_worker_running() -> bool:
    """Return True if an `agent.py` LiveKit worker is already running."""
    # 1) Scan the process list for any python running agent.py.
    try:
        import psutil
        base = os.path.basename(sys.executable).lower()
        for proc in psutil.process_iter(["name", "cmdline"]):
            try:
                cmdline = proc.info.get("cmdline") or []
                name = (proc.info.get("name") or "").lower()
                if not any(p.lower().find("agent.py") >= 0 for p in cmdline):
                    continue
                # Only match python/pythonw executables to avoid false positives.
                if name not in ("python.exe", "pythonw.exe") and base not in name:
                    continue
                return True
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
    except Exception:
        pass

# 2) Backstop: if no worker is visible in the process list yet, check the
    #    lock file. A live PID means another process already launched the
    #    worker and it's still booting — don't double-launch. Only treat the
    #    lock as valid if the PID is genuinely a python process running
    #    agent.py. Anything else (VS Code, a stale PID, a recycled PID) is
    #    treated as stale and removed so a fresh worker can always launch.
    try:
        with open(_lock_path()) as f:
            pid = int(f.read().strip() or 0)
        if pid > 0:
            import psutil
            if psutil.pid_exists(pid):
                try:
                    proc = psutil.Process(pid)
                    cmdline = proc.cmdline() or []
                    name = (proc.name() or "").lower()
                    # Only trust the lock if this PID is actually running
                    # our agent worker (python + agent.py). Otherwise it's a
                    # stale/recycled lock from a previous run — clean it up.
                    is_agent = any(
                        arg.lower().find("agent.py") >= 0 for arg in cmdline
                    ) and (
                        name in ("python.exe", "pythonw.exe")
                        or "python" in name
                    )
                    if is_agent:
                        return True
                except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                    pass
            # Stale lock: the PID is gone, isn't our agent worker, or the
            # process can't be inspected. Clean up so we can launch fresh.
            try:
                os.remove(_lock_path())
            except OSError:
                pass
    except (ValueError, FileNotFoundError, OSError):
        pass
    return False


def _venv_python() -> str:
    """Return the venv's console python.exe (prefer it over pythonw.exe).

    The app may be launched through the desktop shortcut, which uses
    pythonw.exe (no console / no stdout). For the agent worker we want the
    console python.exe so LiveKit logs are visible and nothing chokes on a
    missing sys.stdout.
    """
    base = os.path.dirname(os.path.abspath(__file__))
    for sub in (".venv", "venv"):
        candidate = os.path.join(base, sub, "Scripts", "python.exe")
        if os.path.exists(candidate):
            return candidate
    return sys.executable  # fallback (e.g. system python running the app)


def _minimized_startupinfo():
    """Return a STARTUPINFO that launches the worker console minimized.

    The agent worker is launched in its own console window so its LiveKit
    logs stay visible, but by default it starts minimized so it doesn't pop
    over the desktop app. The user can restore it from the taskbar anytime.
    Only meaningful on Windows; harmless elsewhere.
    """
    try:
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = 6  # SW_MINIMIZE
        return si
    except AttributeError:
        return None


def _minimize_worker_console() -> None:
    """Minimize the agent worker's console window (if any).

    The worker was launched with CREATE_NEW_CONSOLE, so it owns a console
    window. We enumerate all top-level windows, find the one whose process ID
    belongs to an `agent.py` worker, and send it SW_MINIMIZE so the terminal
    minimizes together with the orb.
    """
    import ctypes
    try:
        user32 = ctypes.windll.user32

        # Collect the PIDs of the agent worker(s).
        pids = set()
        if _worker_proc is not None and _worker_proc.poll() is None:
            pids.add(_worker_proc.pid)
        try:
            import psutil
            for proc in psutil.process_iter(["pid", "cmdline"]):
                cmdline = proc.info.get("cmdline") or []
                if any(a.lower().find("agent.py") >= 0 for a in cmdline):
                    pids.add(proc.info["pid"])
        except Exception:
            pass
        if not pids:
            return

        # Enumerate top-level windows and minimize any belonging to a worker PID.
        WNDENUMPROC = ctypes.WINFUNCTYPE(
            ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p
        )
        found = []

        @WNDENUMPROC
        def _cb(hwnd, _lparam):
            pid = ctypes.wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value in pids and user32.IsWindowVisible(hwnd):
                found.append(hwnd)
            return True

        try:
            user32.EnumWindows(_cb, 0)
        except Exception:
            pass
        for hwnd in found:
            user32.ShowWindow(hwnd, 6)  # SW_MINIMIZE
    except Exception as e:  # noqa: BLE001
        # Best-effort — never break the UI if minimizing the console fails.
        print(f"minimize worker console error: {e}")


def _kill_worker() -> None:
    """Terminate the agent worker process (and close its console).

    Kills the worker we launched (if still alive) and also any other
    python agent.py process, so pressing X fully shuts Peter down.
    """
    global _worker_proc
    try:
        if _worker_proc is not None and _worker_proc.poll() is None:
            _worker_proc.terminate()
            try:
                _worker_proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                _worker_proc.kill()
            _worker_proc = None
    except Exception as e:  # noqa: BLE001
        print(f"terminate worker error: {e}")
    # Clean up any lingering agent.py worker processes (e.g. launched by a
    # previous app run that we didn't start in this process).
    try:
        import psutil
        for proc in psutil.process_iter(["pid", "cmdline"]):
            try:
                cmdline = proc.info.get("cmdline") or []
                if any(a.lower().find("agent.py") >= 0 for a in cmdline):
                    proc.terminate()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
    except Exception:
        pass
    # Remove the stale lock file so a fresh worker can launch next time.
    try:
        os.remove(_lock_path())
    except OSError:
        pass


def _ensure_worker() -> None:
    """Start the LiveKit agent worker in a new console window if not running."""
    global _worker_proc
    if _is_worker_running():
        return

    # Guard against duplicate starts. pywebview may run the app in a
    # separate child process, and both could call _ensure_worker() before
    # either worker shows up in the process list. A lock file holding the
    # worker's PID makes sure only one worker is ever launched.
    #
    # Stale-lock cleanup (a leftover PID recycled by another app) is already
    # handled safely by _is_worker_running() above — it validates the PID
    # actually belongs to an agent.py process before removing the lock.
    # DO NOT also blind-remove the lock file here: two _ensure_worker() calls
    # racing (e.g. a double-click launching peter_app.py twice) would each
    # delete the OTHER's freshly-created lock right before their own
    # O_EXCL create, so both succeed and both launch a worker — exactly the
    # duplicate-worker bug this lock exists to prevent.
    base = os.path.dirname(os.path.abspath(__file__))
    lock_path = _lock_path()

    try:
        lock_fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return  # another process is already starting the worker
    try:
        # Launch with the project directory as CWD so relative imports
        # (agent.py, tools.py) and ./memory_store resolve correctly, and use
        # a fresh console (CREATE_NEW_CONSOLE) so the user can see Peter's
        # worker boot up and any errors. The console starts minimized so it
        # doesn't pop over the desktop app; the user can restore it from the
        # taskbar to watch the LiveKit logs.
        proc = subprocess.Popen(
            [_venv_python(), "agent.py", "dev"],
            cwd=base,
            creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0),
            startupinfo=_minimized_startupinfo(),
        )
        # Keep a handle to the worker process so X can kill it and − can
        # minimize its console together with the orb.
        _worker_proc = proc
        # Record the *worker's* PID in the lock file. The lock persists for
        # the worker's lifetime (removed automatically when the PID dies),
        # which prevents a second launch during the boot window.
        os.write(lock_fd, str(proc.pid).encode())
        os.close(lock_fd)
    except Exception:
        # Clean up the lock so a failed launch doesn't block future ones.
        try:
            os.close(lock_fd)
        except OSError:
            pass
        try:
            os.remove(lock_path)
        except OSError:
            pass
        raise


# ──────────────────────────────────────────────────────────────
# App entry
# ──────────────────────────────────────────────────────────────
def _make_opaque_black(native_form) -> None:
    """Make the host window's own background solid black.

    The HTML `body` is already painted opaque black, so this just ensures the
    native Form behind the WebView2 control is also black (no white flash /
    border). No chroma keying is applied — the window is fully opaque.
    """
    try:
        import clr  # noqa: F401
        from System.Drawing import Color
        native_form.BackColor = Color.FromArgb(255, 0, 0, 0)  # black
    except Exception as e:  # noqa: BLE001
        # Best-effort: never break the UI if the setup fails.
        print(f"opaque background setup error: {e}")


def _allow_mic_and_camera(native_form) -> None:
    """Auto-grant microphone/camera permission requests, instantly.

    WebView2 (the engine behind pywebview on Windows) shows its own native
    "Allow microphone?" popup the first time the page calls getUserMedia
    (i.e. the moment the user taps the orb to talk). For a voice assistant
    that prompt is pure friction — hook CoreWebView2's PermissionRequested
    event and grant mic/camera in code so the browser popup never appears
    and talking to Peter works the instant the app opens, every launch.
    """
    try:
        from System import Action
        from Microsoft.Web.WebView2.Core import (
            CoreWebView2PermissionKind,
            CoreWebView2PermissionState,
        )

        webview_ctrl = getattr(native_form, "webview", None)
        if webview_ctrl is None:
            return

        def _attach() -> None:
            try:
                core = webview_ctrl.CoreWebView2
                if core is None:
                    return

                def _on_permission_requested(_sender, args):
                    try:
                        kind = args.PermissionKind
                        if kind in (
                            CoreWebView2PermissionKind.Microphone,
                            CoreWebView2PermissionKind.Camera,
                        ):
                            # Setting State alone isn't enough — WebView2 only
                            # honors it (and skips its own popup) once Handled
                            # is also set. Miss this and the popup shows anyway.
                            args.State = CoreWebView2PermissionState.Allow
                            args.Handled = True
                    except Exception as e:  # noqa: BLE001
                        print(f"mic permission grant error: {e}")

                core.PermissionRequested += _on_permission_requested
            except Exception as e:  # noqa: BLE001
                print(f"mic permission attach error: {e}")

        # CoreWebView2 is a COM/STA object reachable only from the WinForms UI
        # thread, but window.events.loaded fires off-thread — touching it
        # directly here throws an InvalidCastException that silently no-ops
        # the hook. Marshal the actual attachment back onto the control's own
        # thread via Invoke.
        if webview_ctrl.InvokeRequired:
            webview_ctrl.Invoke(Action(_attach))
        else:
            _attach()
    except Exception as e:  # noqa: BLE001
        # Best-effort: if the hook can't attach, the user just sees the
        # normal one-time browser permission prompt instead of a crash.
        print(f"mic permission hook error: {e}")


def main() -> None:
    import webview
    import ctypes

    # Reset the UI debug log so it only ever reflects the current session.
    try:
        open(_UI_LOG_PATH, "w", encoding="utf-8").close()
    except OSError:
        pass

    # Make it one-click: if the agent worker isn't up yet, start it now so
    # clicking "Talk to Peter" works immediately.
    _ensure_worker()

    api_bridge = PeterApi()
    ui_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "peter_ui.html")

# Siri-style orb docked along the RIGHT edge of the screen. The width is
    # balanced so the Files drop-zone, selector, panels, and chat all fit
    # comfortably without crowding the orb. The height is sized to the
    # panel's natural content height (status pill -> dock -> files -> orb ->
    # task bar -> chat) rather than stretching to fill the whole screen — a
    # full-height window left a large dead black gap below the content since
    # the page never actually grew to match it. Shrunk from 860 after the
    # Spiderman animation strip was replaced with the more compact task bar.
    # Widened/heightened (was 370x660) to fit peter_ui.html's bigger chat
    # text (--col 300->340) and taller chat-log (116->190) at proportions
    # that still feel balanced against the taskbar, which shrank slightly.
    ORB_W = 410
    CONTENT_H = 750

    # Get the primary screen work area (excludes taskbar). Windows-only.
    screen_w, screen_h, work = 1920, 1080, None
    try:
        class RECT(ctypes.Structure):
            _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                        ("right", ctypes.c_long), ("bottom", ctypes.c_long)]
        rect = RECT()
        ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0)  # SPI_GETWORKAREA
        screen_w = rect.right - rect.left
        screen_h = rect.bottom - rect.top
        work = (rect.left, rect.top, rect.right, rect.bottom)
    except Exception:
        pass

    if work:
        left, top, right, bottom = work
        avail_h = bottom - top
        ORB_H = min(CONTENT_H, max(1, avail_h - 12))  # never exceed the visible work area
        x = right - ORB_W - 6                          # snug against the right edge of the work area
        y = top + max(0, (avail_h - ORB_H) // 2)        # vertically centered in the work area
    else:
        ORB_H = min(CONTENT_H, max(1, screen_h - 12))
        x = screen_w - ORB_W - 12
        y = max(0, (screen_h - ORB_H) // 2)

    global _window
    # Remember the exact work-area bounds so we can re-assert them after
    # pywebview creates the native window (it doesn't always honour the
    # height we pass on first show).
    _geometry = (x, y, ORB_W, ORB_H)
    _window = webview.create_window(
        "P.E.T.E.R.",
        ui_path,
        js_api=api_bridge,
        width=ORB_W,
        height=ORB_H,
        x=x,
        y=y,
        frameless=True,
        transparent=False,
        on_top=True,
        easy_drag=True,
        resizable=False,
        shadow=False,
        background_color="#000000",
    )

    # Make the host Form's background solid black (no white flash).
    try:
        _window.events.loaded += lambda: _make_opaque_black(_window.native)
    except Exception as e:  # noqa: BLE001
        print(f"opaque background hook error: {e}")

    # Auto-grant the microphone (and camera) so talking to Peter never waits
    # on a browser permission popup.
    try:
        _window.events.loaded += lambda: _allow_mic_and_camera(_window.native)
    except Exception as e:  # noqa: BLE001
        print(f"mic permission hook registration error: {e}")

    # After the window is up, force it to the exact work-area rectangle so it
    # spans from the very top of the screen down to just above the taskbar.
    # Without this, WebView/WinForms can ignore the requested height on show.
    try:

        def _snap_geometry():
            global _window
            try:
                if _window is not None:
                    _window.move(x, y)
                    _window.resize(ORB_W, ORB_H)
            except Exception as e:  # noqa: BLE001
                print(f"snap geometry error: {e}")

        _window.events.loaded += _snap_geometry

        # Re-assert the geometry again shortly after show, in case the native
        # window settled at a different size than we asked for.
        def _delayed_snap():
            import time
            time.sleep(0.4)
            try:
                _snap_geometry()
            except Exception as e:  # noqa: BLE001
                print(f"delayed snap error: {e}")

        import threading
        threading.Thread(target=_delayed_snap, daemon=True).start()
    except Exception as e:  # noqa: BLE001
        print(f"geometry hook error: {e}")

    webview.start(debug=False)


if __name__ == "__main__":
    main()
