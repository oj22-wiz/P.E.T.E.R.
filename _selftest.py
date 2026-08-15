"""Quick self-test for the P.E.T.E.R. mobile backend (local check).

Starts uvicorn in a background thread, then hits /health, /connection, /pending,
/spotify, /files, and / (the PWA). Confirms the embedded worker launches and the
token endpoint returns a usable LiveKit token.

Run:  python _selftest.py
"""
import os
import threading
import time
import urllib.request
import json

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

import uvicorn

# Force embedded worker ON for this test.
os.environ["EMBEDDED_WORKER"] = "1"

import mobile_backend

PORT = 9123  # dedicated port so it can't collide with a running backend


def _start():
    uvicorn.run(mobile_backend.app, host="127.0.0.1", port=PORT, log_level="warning")


def _get(path):
    with urllib.request.urlopen(f"http://127.0.0.1:{PORT}{path}", timeout=20) as r:
        return r.status, r.read().decode("utf-8", "replace")


def _post(path, payload):
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        f"http://127.0.0.1:{PORT}{path}", data=data,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.status, r.read().decode("utf-8", "replace")


def main():
    t = threading.Thread(target=_start, daemon=True)
    t.start()
    print("Waiting for server to start...")
    # Poll until /health responds (max 30s)
    ok = False
    for _ in range(30):
        try:
            s, body = _get("/health")
            ok = True
            break
        except Exception:
            time.sleep(1)
    if not ok:
        print("!! Server did not become ready in 30s.")
        return

    print(f"\n/health -> {s}")
    h = json.loads(body)
    for k, v in h.items():
        print(f"   {k}: {v}")

    print("\n/connection ->")
    s, body = _get("/connection")
    try:
        c = json.loads(body)
        print(f"   url: {c.get('url')}")
        print(f"   room: {c.get('room')}")
        tok = c.get("token", "")
        print(f"   token present: {bool(tok)}  (len {len(tok)})")
    except Exception as e:
        print(f"   !! could not parse connection: {e}")

    print("\n/pending ->")
    s, body = _get("/pending")
    print(f"   {s} {body}")

    print("\n/spotify ->")
    s, body = _get("/spotify")
    print(f"   {s} {body[:200]}")

    print("\n/files ->")
    s, body = _get("/files")
    print(f"   {s} {body[:200]}")

    print("\n/ (PWA) ->")
    s, body = _get("/")
    print(f"   status {s}, is html: {body.lstrip().lower().startswith('<!doctype')}")

    print("\n/worker-log ->")
    s, body = _get("/worker-log")
    try:
        w = json.loads(body)
        print(f"   ok: {w.get('ok')}, running: {w.get('running')}")
        print("   tail:", (w.get('tail') or '')[-400:])
    except Exception as e:
        print(f"   !!! {e} {body[:300]}")

    print("\nSelf-test done.")


if __name__ == "__main__":
    main()
