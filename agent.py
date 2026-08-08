"""
P.E.T.E.R. — Your voice-first AI assistant built with LiveKit Agents + Gemini Realtime.

This is a speech-to-speech assistant: Gemini Realtime handles both understanding
your voice and speaking back — no separate STT/TTS models needed.

Based on the YouTube tutorial by Thanh-y David Nguyen:
    "How to Build Your Own JARVIS AI Agent 100% Free! | LiveKit Tutorial"
    https://www.youtube.com/watch?v=An4NwL8QSQ4
    https://github.com/ruxakK/friday_jarvis

Run with:  python agent.py dev
"""

from __future__ import annotations

import os

from dotenv import load_dotenv

# Load environment variables from .env FIRST — before importing modules that
# read env vars during import time (tools.py builds the memory client using
# GOOGLE_API_KEY / MEM0_API_KEY at import).
load_dotenv()

from livekit import agents, rtc
from livekit.agents import (
    Agent,
    AgentSession,
    JobContext,
    RunContext,
    WorkerOptions,
    cli,
)
from livekit.plugins import google, noise_cancellation

from prompts import AGENT_INSTRUCTION, SESSION_INSTRUCTION
from tools import (
    fact_check,
    fetch_url,
    get_active_file,
    get_current_date,
    get_pending_url,
    get_weather,
    retrieve_memory,
    search_recent_news,
    search_web,
    store_memory,
)
from spotify_tools import (
    list_playlists,
    play_my_music,
    play_playlist,
)


# ──────────────────────────────────────────────────────────────
# Latency-optimized model config
# ──────────────────────────────────────────────────────────────
# gemini-3.1-flash-live-preview is the fastest realtime speech-to-speech
# model available on the Gemini API (works with GOOGLE_API_KEY). It is
# noticeably snappier than the older "-preview-12-2025" build.
#
# Note: the GA build "gemini-live-2.5-flash-native-audio" is VertexAI-only in
# this SDK version — we stay on the Gemini API model above.
FAST_MODEL = "gemini-3.1-flash-live-preview"


def _build_model() -> google.realtime.RealtimeModel:
    """Build the Gemini Realtime model with latency-friendly settings."""
    return google.realtime.RealtimeModel(
        model=FAST_MODEL,
        voice="Puck",
        temperature=0.8,
    )


# ──────────────────────────────────────────────────────────────
# Agent Definition
# ──────────────────────────────────────────────────────────────
class PeterAgent(Agent):
    def __init__(self) -> None:
        super().__init__(
            instructions=AGENT_INSTRUCTION,
            # Register the FunctionTool objects from tools.py directly.
            # These expect (context, param) signatures — handled by LiveKit.
            tools=[
                get_weather,
                get_current_date,
                search_web,
                search_recent_news,
                fact_check,
                store_memory,
                retrieve_memory,
                get_pending_url,
                fetch_url,
                get_active_file,
                list_playlists,
                play_playlist,
                play_my_music,
            ],
            # Gemini Realtime — end-to-end speech model
            # Voice options (male): Charon, Fenrir, Orus, Puck
            # Voice options (female): Aoede, Kore, Leda, Zephyr
            llm=_build_model(),
        )

# ──────────────────────────────────────────────────────────────
# Session Entrypoint
# ──────────────────────────────────────────────────────────────
async def entrypoint(ctx: JobContext) -> None:
    """Main entrypoint — creates the agent session and connects to the room."""

    # Build the agent
    agent = PeterAgent()

    # Gemini Realtime is a full speech-to-speech model — no STT/TTS/VAD needed.
    # Latency optimizations:
    #   - preemptive_generation=True  → Peter starts forming his answer WHILE
    #     you're still talking, so the reply lands almost instantly after you
    #     finish.
    #   - min_endpointing_delay=0.2   → as soon as you pause (~200ms), Peter
    #     jumps in. The default endpointing waits longer to be sure you're
    #     done, which adds noticeable delay.
    #   - max_endpointing_delay=0.6   → hard cap so he never dawdles.
    session = AgentSession(
        preemptive_generation=True,
        min_endpointing_delay=0.2,
        max_endpointing_delay=0.6,
        allow_interruptions=True,
    )

    # Connect to the LiveKit room
    await ctx.connect()

    # Start the agent session with camera vision + noise cancellation enabled.
    # video_enabled=True lets the agent receive BOTH a camera feed and the
    # screen-share track published from the desktop app's "Share screen"
    # button (screen shares arrive as regular video). Note: we intentionally
    # do NOT set screen_enabled=True here — when Gemini Realtime has a
    # dedicated screen input it waits for a screen frame before speaking, so
    # normal voice calls would hang with no reply until a screen is shared.
    await session.start(
        agent=agent,
        room=ctx.room,
        room_input_options=agents.RoomInputOptions(
            video_enabled=True,
            noise_cancellation=noise_cancellation.BVC(),
        ),
    )

    # Greet the user when the session starts.
    await session.generate_reply(instructions=SESSION_INSTRUCTION)


# ──────────────────────────────────────────────────────────────
# Worker CLI
# ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    cli.run_app(
        WorkerOptions(
            entrypoint_fnc=entrypoint,
            agent_name=os.getenv("AGENT_ID", "peter-assistant"),
            # Prewarm the faster GA model at startup to reduce first-join latency
            prewarm_fnc=lambda proc: _build_model(),
        )
    )

