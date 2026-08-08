# 🕷️ P.E.T.E.R. — Your Personal AI Assistant

A Python-based, **100% free** voice-first AI assistant inspired by *Jarvis*, built with
**LiveKit Agents** + **Gemini Realtime** (speech-to-speech).

Meet **Peter** — your friendly neighborhood assistant. He's a brilliant, witty science nerd
(think Peter Parker) who loves a good quip and a well-timed science reference.

Capabilities:
- 🔍 **Search the web** — real-time lookups via DuckDuckGo
- 🌤️ **Weather checking** — current conditions for any city
- 🧠 **Long-term memory** — remembers your name, facts & preferences across sessions (Mem0)
- 🕷️ **Desktop overlay** — a compact Siri-style orb that pops up at the top-right of the screen (`peter_app.py`) to talk hands-free
- 📷 **Vision** — sees your camera feed
- 🖥️ **Screen sharing** — tap "Share screen" to let Peter see what's on your screen and help you with processes, settings, or anything on it
- 🗣️ **Speech** — natural voice conversation (no separate STT/TTS models!)

Based on the tutorial by **Thanh-y David Nguyen**:
🎥 [How to Build Your Own JARVIS AI Agent 100% Free! | LiveKit Tutorial](https://www.youtube.com/watch?v=An4NwL8QSQ4)
📂 [GitHub repo](https://github.com/ruxakK/friday_jarvis)

---

## ⚙️ Setup

1. **Create a virtual environment & install dependencies**
   ```bash
   python -m venv .venv
   .venv\Scripts\activate          # Windows
   # source .venv/bin/activate     # macOS/Linux
   pip install -r requirements.txt
   ```

2. **Configure environment variables**
   ```bash
   cp .env.example .env            # Windows: copy .env.example .env
   ```
   Then edit `.env` and fill in:

   | Variable | Required | Description |
   |----------|----------|-------------|
   | `LIVEKIT_URL` | ✅ | Your LiveKit server URL (`wss://...livekit.cloud`) |
   | `LIVEKIT_API_KEY` | ✅ | LiveKit API key |
   | `LIVEKIT_API_SECRET` | ✅ | LiveKit API secret |
   | `GOOGLE_API_KEY` | ✅ | Google AI Studio key for Gemini Realtime |
   | `AGENT_ID` | ⬜ | Agent name, default `peter-assistant` |

   > **Note:** `OPENAI_API_KEY` is **no longer needed** — Gemini Realtime handles speech end-to-end.

## 🔑 Getting the API Keys

### Google AI (Gemini) — REQUIRED
1. Go to **https://aistudio.google.com/apikey**
2. Click **"Create API key"** (it's free)
3. Copy the key into `.env` as `GOOGLE_API_KEY=...`

### LiveKit
Make sure your LiveKit account is set up and `LIVEKIT_URL`, `LIVEKIT_API_KEY`,
`LIVEKIT_API_SECRET` are in `.env`.

---

## 🚀 Run

```bash
python agent.py dev
```

This starts a LiveKit agent worker that connects to your LiveKit server and waits for a room to join.

> 💡 **Tip:** To talk to your agent, use the **LiveKit Agents Playground**
> (`https://agents-playground.livekit.io`) — connect to the same LiveKit server and
> you'll be in a voice call with Peter instantly.

## 📱 Mobile App (use Peter from your iPhone)

The `mobile/` folder is a phone-friendly, installable PWA that talks to the
same Peter. It has **all the features of the desktop app except screen
sharing** (which doesn't apply on a phone): voice, URL "go peter", Spotify,
files, chat, and memory.

- **Local mode** (same Wi-Fi): `python mobile_backend.py`, then open
  `http://<your-pc-ip>:9094` on your phone and **Add to Home Screen**.
- **Cloud mode** (⭐ one link, works even when your PC is off): the whole
  thing — UI + API + Peter worker — runs in a single container. See
  **[DEPLOY_CLOUD.md](DEPLOY_CLOUD.md)** for the one-click Render setup
  (`Dockerfile`, `render.yaml`, `.dockerignore` already included).

## 🖥️ Desktop App (click-to-talk Peter)

**One-click launch:** Double-click the **P.E.T.E.R** shortcut on your Desktop.

- If you don't see it yet (or want to recreate it), run once:
  ```bash
  python create_shortcut.py
  ```
  This creates a `P.E.T.E.R` shortcut on your Desktop (with a spider-mask icon)
  that launches the desktop app through your virtualenv — no console window.

- Or launch manually:
  ```bash
  python peter_app.py
  ```

The app pops up a compact, Siri-style **transparent orb** at the **top-right** of
your screen. It stays on top, so Peter is always a tap away. The orb animates as
he speaks — the mask squints while he's talking, and red sound-wave ripples pulse
outward while he's responding. Tap the orb to talk, tap again to end the call.

There's a **Share screen** button above the orb. Tap it while in a call to give
Peter a live view of your screen so he can help you with whatever's on it —
windows, settings, documents, apps, or processes. Tap it again (or the OS
"Stop sharing" bar) to stop.

There's also a tiny **URL box** above the orb where you can paste a link for the
"go peter" flow (see below). The agent worker (`python agent.py dev`) **starts
automatically** when the app launches, so it's truly one click.

## 🔗 URL Tasks ("go peter")

Peter can act on a URL you paste into the small box in the desktop app.

1. Paste a URL into the small URL box above the orb.
2. Give Peter a spoken instruction about what to do with it, ending with
   **"go peter"** — for example:
   > *"I want you to train and start speaking similarly as the character in
   > this video. Go Peter."*
3. Peter reads the URL, fetches its content, and carries out your instruction.

If the URL box is **blank**, Peter just talks normally (no special behavior) —
he won't try to invent a task from an empty box.

## 🧠 Memory

Peter has **long-term memory** powered by Mem0. Optionally set `MEM0_API_KEY` in `.env`
for cross-device cloud memory; without it he uses a local store in `./memory_store`.

- Tell him: *"My name is Orlando"* — he saves it.
- Ask later: *"Do you remember my name?"* — he recalls it.

Try it: **restart the worker**, then tell him your name, end the call, and start a new
session — he'll remember you.

## 🎛️ Using the Agent

Peter is a voice-first assistant with camera vision. Example things you can ask:

- *"What's the weather in New York?"* → triggers `get_weather`
- *"Search the web for the latest AI news."* → triggers `search_web`
- *"What do you see in front of me?"* → uses camera vision 📷

## 📋 Project Structure

| File | Purpose |
|------|---------|
| `agent.py` | LiveKit agent entrypoint — PeterAgent + Gemini Realtime session |
| `prompts.py` | `AGENT_INSTRUCTION` (persona) + `SESSION_INSTRUCTION` (greeting) |
| `tools.py` | Tools: `get_weather`, `search_web`, `store_memory`, `retrieve_memory`, `get_pending_url`, `fetch_url` |
| `pending.py` | Shared pending-URL helper — the "go peter" URL slot file read/write |
| `peter_app.py` | Desktop avatar app (pywebview) — click to talk to Peter (auto-starts worker) |
| `create_shortcut.py` | Creates the desktop `P.E.T.E.R` shortcut + `peter.ico` app icon |
| `peter_ui.html` | Animated Peter avatar + voice-client frontend for the desktop app |
| `make_token.py` | Generate a token so you can reach Peter from your phone |
| `requirements.txt` | Python dependencies |
| `.env.example` | Env var template (copy to `.env`) |

---

Built with ❤️ using [LiveKit Agents](https://docs.livekit.io/agents/) + Gemini Realtime.

