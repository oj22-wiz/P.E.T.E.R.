# Deploy P.E.T.E.R. to the Cloud (one public link, works without your PC)

This turns your mobile P.E.T.E.R. into a **single public web app** that:
- serves the phone UI **and** the backend API **and** runs the Peter voice
  worker — all from **one container / one link**,
- keeps working **even when your computer is off** (LiveKit runs on the
  cloud, and so does the worker),
- is installable on your iPhone via **Add to Home Screen**.

> Screen sharing is intentionally **not** included (you can't share your phone
> screen the same way, and you asked to drop it). Everything else — voice,
> URL "go peter", Spotify, files, memory, chat — is included.

---

## What you'll deploy

| File | Purpose |
|------|---------|
| `Dockerfile` | Builds one container with the backend + worker + deps. |
| `.dockerignore` | Keeps secrets/local state out of the image. |
| `render.yaml` | Render Blueprint that wires up the env vars for you. |
| `mobile_backend.py` | FastAPI server (already cloud-aware: reads `PORT`). |
| `mobile/` | The installable PWA shell. |

---

## Option A — Render (easiest, free)

1. **Push this folder to a GitHub repo** (Render needs a repo).
2. On [render.com](https://render.com) click **New → Blueprint** and select
   that repo. Render auto-reads `render.yaml` and creates a `peter` web
   service (Docker).
3. **Set the secret env vars** in the Render dashboard (Service → Environment):
   - `LIVEKIT_API_KEY`
   - `LIVEKIT_API_SECRET`
   - `GOOGLE_API_KEY`
   - `SPOTIFY_CLIENT_ID`
   - `SPOTIFY_CLIENT_SECRET`
   - optional `MEM0_API_KEY` (for persistent cloud memory)
4. **Find your app's public URL** — Render shows it after the first deploy.
   It looks like `https://peter-xxxx.onrender.com`.
5. **Register the redirect URI in Spotify**: In the
   [Spotify dashboard](https://developer.spotify.com/dashboard) for your app,
   add exactly `https://<your-app>.onrender.com/spotify/callback` as a
   Redirect URI and Save. (The app auto-derives this redirect URI from the
   request — there are no placeholders to edit in `render.yaml`.)
6. Let Render deploy. When it's healthy, open `https://<your-app>.onrender.com`
   on your iPhone → **Share → Add to Home Screen**.

Done — one link, works from anywhere, your PC can be off.

---

## Option B — Railway / Fly.io (Docker)

Same `Dockerfile` works. Set the same env vars (including `PORT` if the host
needs it — Railway/Fly inject `PORT` automatically). Expose the web port, then
open the public URL on your phone.

---

## Important notes

- **Secrets**: never commit `.env` (it's git-ignored / docker-ignored). Set
  them as host env vars instead.
- **Memory & files** on free Render are **ephemeral** (reset on redeploy). For
  Peter to remember you across redeploys, set `MEM0_API_KEY` (Mem0 cloud).
- **Spotify playback** uses Spotify Connect, so it plays on whatever device
  has Spotify open (your phone, etc.).
- The **worker runs inside the same container** as the web server, so there's
  genuinely one process and one link.

---

## Verifying it works

1. Open the public URL on your phone.
2. Tap the orb → you should hear Peter greet you.
3. Say **"play my music"** (after linking Spotify) or **"go peter"** with a URL.
4. Tap the 📁 zone to upload a file Peter can read.
