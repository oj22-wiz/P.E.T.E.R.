"""
Generate a LiveKit access token so you can connect to P.E.T.E.R. from your phone.

Usage:
    python make_token.py            # prints a token for a random room
    python make_token.py myroom     # prints a token for a specific room

Then open https://agents-playground.livekit.io on your phone, paste the room
name and token, and connect. Make sure the agent worker (python agent.py dev)
is running on your PC.
"""

from __future__ import annotations

import os
import sys
from datetime import timedelta

from dotenv import load_dotenv
from livekit import api

load_dotenv()

# ── Read LiveKit credentials from .env ──────────────────────
url = os.getenv("LIVEKIT_URL", "")
api_key = os.getenv("LIVEKIT_API_KEY", "")
api_secret = os.getenv("LIVEKIT_API_SECRET", "")


def main() -> None:
    if not (api_key and api_secret):
        print("ERROR: LIVEKIT_API_KEY / LIVEKIT_API_SECRET missing in .env")
        sys.exit(1)

    room = sys.argv[1] if len(sys.argv) > 1 else "peter-room"

    token = (
        api.AccessToken(api_key, api_secret)
        .with_identity("phone-user")
        .with_name("Phone User")
        .with_grants(api.VideoGrants(room_join=True, room=room))
        .with_ttl(timedelta(hours=6))
        .to_jwt()
    )

    print("=" * 62)
    print("  P.E.T.E.R. — Phone Connection Details")
    print("=" * 62)
    print(f"  LiveKit URL : {url}")
    print(f"  Room name   : {room}")
    print("=" * 62)
    print()
    print("  TOKEN:")
    print(token)
    print()
    print("  On your phone, open https://agents-playground.livekit.io")
    print(f"  URL  : {url}")
    print(f"  Room : {room}")
    print("  Paste the token above, then Connect.")
    print("=" * 62)


if __name__ == "__main__":
    main()

