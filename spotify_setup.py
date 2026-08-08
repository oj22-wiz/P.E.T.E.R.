"""
P.E.T.E.R. — Guided Spotify setup.

Walks you through creating a fresh Spotify app and linking Peter to it, all
from one script. Run:

    python spotify_setup.py

What it does:
  1. Tells you exactly what to create in the Spotify Developer Dashboard.
  2. Writes your Client ID / Secret / Redirect URI into .env for you.
  3. Launches the one-time authorization flow and saves the refresh token.

Why a fresh app fixes the "redirect_uri: Insecure" error:
  That error appears when the Redirect URI you're using is NOT registered for
  the Client ID being sent. If you deleted the old app (or its redirect URI),
  the old credentials no longer match anything, so Spotify rejects every URI.
  A brand-new app with fresh credentials + a registered Redirect URI resolves
  it immediately.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import set_key

import spotify_auth

_HERE = Path(os.path.dirname(os.path.abspath(__file__)))
_ENV_FILE = _HERE / ".env"

# A fresh, clean redirect URI to register.
# Spotify rejects "http://localhost" as insecure but ACCEPTS the loopback IP
# "http://127.0.0.1" on a port. Use the IP form.
NEW_REDIRECT = "http://127.0.0.1:9090/callback"


def _update_env(client_id: str, client_secret: str, redirect: str) -> None:
    """Write/update the Spotify credentials in .env."""
    set_key(_ENV_FILE, "SPOTIFY_CLIENT_ID", client_id)
    set_key(_ENV_FILE, "SPOTIFY_CLIENT_SECRET", client_secret)
    set_key(_ENV_FILE, "SPOTIFY_REDIRECT_URI", redirect)
    print("  Wrote SPOTIFY_CLIENT_ID / SPOTIFY_CLIENT_SECRET /"
          " SPOTIFY_REDIRECT_URI to .env")


def main() -> None:
    print()
    print("=" * 64)
    print("  P.E.T.E.R. - SPOTIFY SETUP")
    print("=" * 64)
    print()
    print("STEP 1 of 2 - Create a FRESH Spotify app (2 minutes, free):")
    print()
    print("  1. Open:  https://developer.spotify.com/dashboard")
    print("  2. Click  'Create app'  (top-right).")
    print("  3. App name:      Peter Assistant  (anything)")
    print("  4. App description: My AI assistant  (anything)")
    print("  5. Redirect URI:  ENTER THIS EXACTLY:")
    print(f"       {NEW_REDIRECT}")
    print("  6. Under 'Which API/s are you using?', check  Web API")
    print("  7. Accept the terms and click  'Create'.")
    print()
    print("  IMPORTANT: If the dashboard asks you to verify your account,")
    print("  do that first - it only takes a minute and is required to use")
    print("  the Web API.")
    print()

    val = input("  Have you created the app and set the Redirect URI above? [yes] ")
    if val.strip().lower() not in ("yes", "y", ""):
        print("  OK, no problem. Run this script again once you've created the app.")
        return

    print()
    print("  Next, go to your app's page:")
    print("  1. You'll see the green  'Client ID'.")
    print("  2. Click  'Show client secret'  to reveal the  'Client secret'.")
    print()
    client_id = input("  Paste your Client ID: ").strip()
    client_secret = input("  Paste your Client secret: ").strip()

    if not client_id or not client_secret:
        print("\n  Both Client ID and Client secret are required. Please try again.")
        return

    _update_env(client_id, client_secret, NEW_REDIRECT)

    # Reload env vars so the freshly-written .env is picked up.
    os.environ["SPOTIFY_CLIENT_ID"] = client_id
    os.environ["SPOTIFY_CLIENT_SECRET"] = client_secret
    os.environ["SPOTIFY_REDIRECT_URI"] = NEW_REDIRECT

    print()
    print("STEP 2 of 2 - Authorize Peter to access your Spotify:")
    print("  A browser tab will open. Log in and click 'Agree'.")
    ok = spotify_auth.authorize()
    print()
    if ok:
        print("  Spotify linked! You can now ask Peter to play your music.")
    else:
        print("  Authorization did not complete - see the message above.")


if __name__ == "__main__":
    main()
