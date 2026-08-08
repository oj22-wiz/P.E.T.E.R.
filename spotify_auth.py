"""
P.E.T.E.R. — Spotify authorization & playback helper.

Handles the Spotify OAuth **authorization code flow** (one-time) and stores a
refresh token so Peter can act on your playlists across sessions without
re-authorizing.

This is the most user-friendly flow: click the 🎧 button (or run
`python spotify_auth.py`), your browser opens to Spotify, you log in and click
"Agree", and Peter captures the callback automatically — no code to type.

Setup (one-time, ~1 minute):
1. Go to https://developer.spotify.com/dashboard and open your app (or create
   a free one).
2. In the app settings, add a Redirect URI:
       http://127.0.0.1:9090/callback
   and click Save.
3. Copy the Client ID and Client Secret into `.env`:
       SPOTIFY_CLIENT_ID=...
       SPOTIFY_CLIENT_SECRET=...
4. Authorize once from the desktop app (🎧 button) or run:
       python spotify_auth.py
   A browser opens, you log in + approve, and the refresh token is saved to
   spotify_token.json. No codes to type.

Playback: Peter uses Spotify Connect. Playback starts on whatever device has
Spotify active (your phone, your PC's Spotify app, your TV, etc.).
"""

from __future__ import annotations

import json
import logging
import os
import socket
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import spotipy
from dotenv import load_dotenv
from spotipy.cache_handler import CacheFileHandler
from spotipy.oauth2 import SpotifyOAuth

load_dotenv()

_HERE = Path(os.path.dirname(os.path.abspath(__file__)))
_TOKEN_FILE = _HERE / "spotify_token.json"

DEFAULT_REDIRECT = "http://127.0.0.1:9090/callback"

SCOPES = (
    "user-library-read "
    "playlist-read-private "
    "playlist-read-collaborative "
    "user-modify-playback-state "
    "user-read-playback-state "
    "user-read-currently-playing"
)

# ──────────────────────────────────────────────────────────────
# Tiny local HTTP server to capture the OAuth redirect
# ──────────────────────────────────────────────────────────────
_capture = {}

PORT = 9090


def _get_credentials():
    """Read Spotify credentials from the environment."""
    client_id = os.getenv("SPOTIFY_CLIENT_ID", "").strip()
    client_secret = os.getenv("SPOTIFY_CLIENT_SECRET", "").strip()
    redirect = os.getenv("SPOTIFY_REDIRECT_URI", DEFAULT_REDIRECT).strip() or DEFAULT_REDIRECT
    if not client_id or not client_secret:
        raise RuntimeError(
            "Missing SPOTIFY_CLIENT_ID / SPOTIFY_CLIENT_SECRET. "
            "Create a free app at https://developer.spotify.com/dashboard and "
            "add both to your .env file. See spotify_auth.py docstring."
        )
    return client_id, client_secret, redirect


def _build_auth(redirect: str = "") -> SpotifyOAuth:
    client_id, client_secret, default_redirect = _get_credentials()
    return SpotifyOAuth(
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri=redirect or default_redirect,
        scope=SCOPES,
        cache_path=str(_TOKEN_FILE),
    )


def _start_callback_server(port: int):
    """Start a local HTTP server that captures the ?code= callback.

    Binds to BOTH 127.0.0.1 (IPv4) and ::1 (IPv6) so the callback is caught
    no matter how the OS/browser resolves "localhost". On Windows, localhost
    often resolves to ::1 first, which used to cause a connection-refused
    error and the auth flow to stall.
    """
    _capture.clear()

    class Handler(BaseHTTPRequestHandler):  # noqa: N801
        def do_GET(self):  # noqa: N802
            qs = parse_qs(urlparse(self.path).query)
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            _capture["code"] = qs.get("code", [None])[0]
            _capture["error"] = qs.get("error", [None])[0]
            if _capture["code"]:
                body = (
                    "<h2 style='font-family:sans-serif'>Peter is authorized!</h2>"
                    "<p>You can close this tab and go back to Peter.</p>"
                ).encode()
            else:
                body = (
                    "<h2 style='font-family:sans-serif'>Authorization failed</h2>"
                    f"<p>{_capture['error']}</p>"
                ).encode()
            self.wfile.write(body)

        def log_message(self, *args):  # silence default logging
            pass

    def _bind_and_serve(host: str) -> HTTPServer:
        server = HTTPServer((host, port), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        return server

    servers = []
    # Prefer IPv4 (most reliable for localhost redirects).
    try:
        servers.append(_bind_and_serve("127.0.0.1"))
    except OSError:
        pass
    # Also listen on IPv6 loopback if available (covers the ::1 case).
    try:
        servers.append(_bind_and_serve("::1"))
    except OSError:
        pass
    if not servers:
        raise OSError(f"Couldn't bind callback server on port {port}")
    return servers[-1]


def _persist_token(token_info: dict) -> None:
    """Write the token dict to disk so it survives across sessions."""
    if not token_info:
        return
    try:
        with open(_TOKEN_FILE, "w", encoding="utf-8") as f:
            json.dump(token_info, f)
    except OSError as e:  # noqa: BLE001
        logging.error(f"Couldn't persist Spotify token: {e}")


def _cached_token_info():
    """Return the persisted token dict (from spotify_token.json), or None."""
    try:
        cache = CacheFileHandler(cache_path=str(_TOKEN_FILE))
        return cache.get_cached_token()
    except Exception:  # noqa: BLE001
        return None


def authorize() -> bool:
    """Run the one-time authorization-code flow.

    Opens the user's browser to Spotify's login page, waits for the local
    callback, and stores the refresh token in spotify_token.json.
    """
    sp_oauth = _build_auth()

    # Use the port from the redirect URI so the callback matches.
    port = PORT
    parsed = urlparse(sp_oauth.redirect_uri)
    if parsed.port:
        port = parsed.port

    # Print the exact values that must be whitelisted in the Spotify dashboard.
    # If Spotify shows "redirect_uri: Insecure", this URI is NOT registered
    # for the Client ID below.
    client_id, _secret, _redirect = _get_credentials()
    print("\n" + "=" * 62)
    print("  SPOTIFY ONE-TIME LINK")
    print("=" * 62)
    print(f"  Client ID   : {client_id}")
    print(f"  Redirect URI: {sp_oauth.redirect_uri}")
    print("  -" * 21)
    print("  If Spotify says 'redirect_uri: Insecure', register the")
    print("  Redirect URI in https://developer.spotify.com/dashboard")
    print("  for the app with this Client ID, then click Save.")
    print("=" * 62)

    server = None
    try:
        server = _start_callback_server(port)
        auth_url = sp_oauth.get_authorize_url()
        print("\nOpening Spotify in your browser to connect Peter to your account…")
        webbrowser.open(auth_url)

        # Wait up to 600s (10 min) for the redirect. Logging in + approving can
        # take a while, and the token can only be saved if the local server is
        # still listening when Spotify redirects back.
        waited = 0.0
        while waited < 600 and "code" not in _capture:
            time.sleep(0.25)
            waited += 0.25
            if int(waited) % 30 == 0 and int(waited) > 0:
                print(f"  Still waiting for Spotify to redirect back ({int(waited)}s)...")
    except Exception as e:  # noqa: BLE001
        logging.error(f"Spotify auth setup error: {e}")
        print(f"Couldn't start Spotify authorization: {e}")
        return False
    finally:
        if server is not None:
            server.shutdown()

    code = _capture.get("code")
    if not code:
        print(
            "\nAuthorization didn't complete. Make sure your Spotify app has "
            f"redirect URI '{sp_oauth.redirect_uri}' set in the dashboard "
            "(https://developer.spotify.com/dashboard), then try again."
        )
        return False
    try:
        token_info = sp_oauth.get_access_token(code)
        # Explicitly persist the token to disk so every future session
        # reuses it without re-prompting. spotipy's in-memory cache can
        # vanish when the process exits, so we always write it ourselves.
        if isinstance(token_info, dict):
            _persist_token(token_info)
        print("Spotify authorization SUCCESS.")
        return True
    except Exception as e:  # noqa: BLE001
        logging.error(f"Spotify token exchange error: {e}")
        print(f"Spotify authorization failed: {e}")
        return False


def get_token() -> str:
    """Return a valid Spotify access token, refreshing if needed."""
    sp_oauth = _build_auth()
    return sp_oauth.get_access_token(as_dict=False)


def is_authorized() -> bool:
    """True if a valid token is persisted (no re-auth needed)."""
    try:
        token_info = _cached_token_info()
        if not token_info:
            return False
        # A refresh token means we can always get a fresh access token
        # without re-prompting. If only an access token exists, it must not
        # be expired to count as authorized.
        if token_info.get("refresh_token"):
            return True
        expires_at = token_info.get("expires_at", 0) or 0
        if expires_at and expires_at - time.time() > 60:
            return True
        return False
    except Exception:  # noqa: BLE001
        return False


def get_client() -> spotipy.Spotify:
    """Return an authenticated spotipy client (for the worker tools)."""
    token = get_token()
    return spotipy.Spotify(auth=token)


# ──────────────────────────────────────────────────────────────
# Cloud / public-URL OAuth helpers
# ──────────────────────────────────────────────────────────────
def resolve_redirect_uri(base_url: str = "") -> str:
    """Resolve the Spotify Redirect URI for the current deployment.

    On a local machine this returns the loopback callback
    (http://127.0.0.1:9090/callback). On a deployed (cloud) app, pass the
    app's public base URL (e.g. https://my-app.onrender.com) and it returns
    https://my-app.onrender.com/spotify/callback automatically. Prefers the
    caller-provided base_url, then PUBLIC_URL, then the local default.
    """
    base = (base_url or "").strip().rstrip("/")
    if not base:
        base = os.getenv("PUBLIC_URL", "").strip().rstrip("/")
    if base:
        return f"{base}/spotify/callback"
    return os.getenv("SPOTIFY_REDIRECT_URI", DEFAULT_REDIRECT).strip() or DEFAULT_REDIRECT


def get_authorize_url(base_url: str = "") -> str:
    """Return the Spotify authorization URL the user opens in their browser.

    `base_url` (the app's public origin) is used to build the Redirect URI so
    the deployed app never needs a hardcoded SPOTIFY_REDIRECT_URI. The user
    logs into Spotify, approves, and Spotify redirects to
    <base_url>/spotify/callback, which calls handle_callback(code).
    """
    redirect = resolve_redirect_uri(base_url)
    logging.info(f"Spotify Redirect URI: {redirect}")
    sp_oauth = _build_auth(redirect=redirect)
    return sp_oauth.get_authorize_url()


def handle_callback(code: str, base_url: str = "") -> bool:
    """Exchange an authorization `code` for tokens and persist them.

    Designed for the cloud flow: the backend's /spotify/callback endpoint
    receives `?code=...`, calls this with the same base_url, the token is
    saved to spotify_token.json (same place the desktop flow writes it), so
    the worker shares the same auth state regardless of where it's running.
    """
    if not code:
        return False
    try:
        redirect = resolve_redirect_uri(base_url)
        sp_oauth = _build_auth(redirect=redirect)
        token_info = sp_oauth.get_access_token(code)
        if isinstance(token_info, dict):
            _persist_token(token_info)
        print("Spotify cloud callback SUCCESS.")
        return True
    except Exception as e:  # noqa: BLE001
        logging.error(f"Spotify cloud callback error: {e}")
        return False


if __name__ == "__main__":
    try:
        ok = authorize()
        print("Spotify authorization:", "SUCCESS" if ok else "FAILED")
    except Exception as e:  # noqa: BLE001
        print(f"Spotify authorization error: {e}")
        raise
