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

import asyncio
import json
import logging
import os
import sys

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
    JobExecutorType,
    JobProcess,
    RunContext,
    WorkerOptions,
    cli,
)
from livekit.plugins import google, noise_cancellation

import learning
from prompts import AGENT_INSTRUCTION
from tools import (
    fact_check,
    fetch_url,
    get_active_file,
    get_current_date,
    get_pending_url,
    get_weather,
    pop_pending_chat_message,
    pop_pending_links,
    recall_knowledge,
    retrieve_memory,
    search_recent_news,
    search_web,
    show_in_chat,
    store_memory,
)
from spotify_tools import (
    list_playlists,
    play_my_music,
    play_playlist,
)
from self_tools import (
    close_everything,
    list_own_files,
    pop_pending_close_signal,
    read_own_file,
    restart_self,
    write_own_file,
)
from cad_tools import (
    design_cad_part,
    get_design_paths,
    list_cad_designs,
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

# Human-readable labels for the progress bar shown in the UI while a tool runs.
TOOL_LABELS = {
    "get_weather": "Checking the weather",
    "get_current_date": "Checking the date",
    "search_web": "Searching the web",
    "search_recent_news": "Searching recent news",
    "fact_check": "Fact-checking",
    "store_memory": "Saving to memory",
    "retrieve_memory": "Recalling memory",
    "recall_knowledge": "Checking what I already know",
    "get_pending_url": "Reading the shared link",
    "fetch_url": "Reading the page",
    "get_active_file": "Reading your file",
    "list_playlists": "Loading playlists",
    "play_playlist": "Starting Spotify",
    "play_my_music": "Starting Spotify",
    "list_own_files": "Looking at my own files",
    "read_own_file": "Reading my own code",
    "write_own_file": "Editing my own code",
    "restart_self": "Restarting myself",
    "design_cad_part": "Engineering the part",
    "list_cad_designs": "Checking my designs",
    "show_in_chat": "Writing that in chat",
    "close_everything": "Wrapping up for today",
}


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
def _build_instructions() -> str:
    """AGENT_INSTRUCTION plus any lessons Peter learned from past sessions.

    learning.distill_session() (run at the end of every session, see
    entrypoint below) appends short lessons to learned_notes.md. Folding the
    most recent ones back into the system prompt here is what actually
    closes the self-improvement loop — Peter behaves differently next time
    instead of just logging lessons nobody reads.
    """
    notes = learning.get_learned_notes_snippet()
    if not notes:
        return AGENT_INSTRUCTION
    return (
        AGENT_INSTRUCTION
        + "\n\n# Self-improvement notes (learned automatically from past conversations)\n"
        "These are short lessons you picked up from earlier sessions with this "
        "user — apply them naturally, don't read them out loud or mention this "
        "section unless asked how you're learning:\n"
        + notes
    )


class PeterAgent(Agent):
    def __init__(self) -> None:
        super().__init__(
            instructions=_build_instructions(),
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
                recall_knowledge,
                get_pending_url,
                fetch_url,
                get_active_file,
                list_playlists,
                play_playlist,
                play_my_music,
                list_own_files,
                read_own_file,
                write_own_file,
                restart_self,
                design_cad_part,
                list_cad_designs,
                show_in_chat,
                close_everything,
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

    # ────────────────────────────────────────────────────────
    # Self-improvement — once this session ends, reflect on the transcript
    # and remember a few lessons for next time (see learning.py). Runs
    # AFTER the call is already over, so it can never add latency or risk
    # breaking a live conversation.
    # ────────────────────────────────────────────────────────
    async def _on_shutdown(reason: str) -> None:
        try:
            await learning.distill_session(session.history)
        except Exception as e:  # noqa: BLE001
            logging.warning(f"self-improvement distillation failed: {e}")

    ctx.add_shutdown_callback(_on_shutdown)

    # Connect to the LiveKit room
    await ctx.connect()

    # Start the agent session with camera vision + noise cancellation enabled
    # on desktop (both skipped in the cloud deploy — see below).
    # video_enabled=True lets the agent receive BOTH a camera feed and the
    # screen-share track published from the desktop app's "Share screen"
    # button (screen shares arrive as regular video). Note: we intentionally
    # do NOT set screen_enabled=True here — when Gemini Realtime has a
    # dedicated screen input it waits for a screen frame before speaking, so
    # normal voice calls would hang with no reply until a screen is shared.
    #
    # BVC noise cancellation runs real-time ML inference on every audio
    # frame — fine on a desktop's own CPU, but on a small shared-CPU cloud
    # container (Render free tier, running the web server + this worker in
    # the same box) it was competing with the live Gemini audio stream for
    # CPU and causing choppy/glitchy audio. PORT is only set by the cloud
    # host (see mobile_backend.py's own cloud-mode check), so skip BVC there
    # and keep it for local desktop calls where CPU isn't the bottleneck.
    _in_cloud = bool(os.getenv("PORT"))
    # Phone calls never publish a camera/screen-share track (the mobile PWA
    # only ever publishes a mic), so video_enabled buys nothing there but
    # does still set up a video consumer path in RoomIO. Keep it only where
    # it's actually used: desktop calls, which have the camera-vision and
    # screen-share features. Every bit of avoidable work matters on the
    # cloud container's tiny CPU allocation.
    input_kwargs: dict = {"video_enabled": not _in_cloud}
    if not _in_cloud:
        input_kwargs["noise_cancellation"] = noise_cancellation.BVC()

    await session.start(
        agent=agent,
        room=ctx.room,
        room_input_options=agents.RoomInputOptions(**input_kwargs),
    )

    # ────────────────────────────────────────────────────────
    # Progress bar feed — broadcast Peter's live activity (thinking /
    # running a tool / listening / speaking) to the UI over the room's
    # data channel so it can show a "Peter is working…" progress bar
    # instead of leaving the user guessing whether he's stuck.
    # ────────────────────────────────────────────────────────
    tool_call_names: dict[str, str] = {}
    tool_call_args: dict[str, str] = {}

    def _broadcast(payload: dict, topic: str) -> None:
        async def _send() -> None:
            try:
                await ctx.room.local_participant.publish_data(
                    json.dumps(payload).encode("utf-8"),
                    reliable=True,
                    topic=topic,
                )
            except Exception as e:  # noqa: BLE001
                # Best-effort UI hint — never break the call over this, but
                # DO log it: a silently-swallowed publish_data failure is
                # exactly why a UI hint can go missing with no visible cause.
                logging.warning(f"_broadcast failed (topic={topic}): {e}")

        asyncio.create_task(_send())

    def _broadcast_status(payload: dict) -> None:
        _broadcast(payload, "peter-status")

    def _broadcast_cad_design(args_json: str) -> None:
        """Tell the desktop UI a CAD part just got designed so it can drop a
        file card in the chat (see peter_ui.html's peter-files handler).

        Re-derives which files actually exist on disk via
        cad_tools.get_design_paths rather than trusting the tool's return
        string, so the card only ever shows files that are really there.
        """
        try:
            design_name = (json.loads(args_json).get("name") or "").strip()
        except Exception as e:  # noqa: BLE001
            logging.warning(f"_broadcast_cad_design: couldn't parse tool args: {e}")
            return
        if not design_name:
            logging.warning("_broadcast_cad_design: tool args had no 'name'")
            return
        paths = get_design_paths(design_name)
        logging.info(f"_broadcast_cad_design: '{design_name}' -> {paths}")
        # Send the real absolute paths (not just booleans) so the UI can
        # render an actual clickable file:// hyperlink, not just a button
        # that calls back into Python.
        _broadcast(
            {
                "type": "cad_design",
                "name": design_name,
                "scad": paths.get("scad"),
                "stl": paths.get("stl"),
                "png": paths.get("png"),
            },
            "peter-files",
        )

    def _broadcast_search_links(name: str, call_id: str) -> None:
        """Drop the real links from a search's results into the chat.

        Peter is a VOICE assistant — he never reads full URLs out loud (see
        prompts.py), so without this, a job search's actual links would
        never reach the user at all. tools.py's search_web /
        search_recent_news / fact_check stash {title, url} pairs by
        call_id as they run; here we pop them and broadcast, independent
        of whatever Peter actually says.
        """
        links = pop_pending_links(call_id)
        logging.info(f"_broadcast_search_links: '{name}' call {call_id} -> {len(links)} link(s)")
        if links:
            _broadcast({"type": "search_links", "tool": name, "links": links}, "peter-files")

    def _broadcast_close_app() -> None:
        """Tell the desktop UI to close everything down.

        peter_ui.html forwards this straight into
        window.pywebview.api.close_window() — the same call the X button
        makes — which kills the worker process and destroys the orb window
        together. Delayed briefly so Peter's spoken goodbye (see
        self_tools.close_everything) actually finishes playing before the
        room/UI goes away.
        """
        async def _send() -> None:
            await asyncio.sleep(2.5)
            try:
                await ctx.room.local_participant.publish_data(
                    json.dumps({"type": "close_app"}).encode("utf-8"),
                    reliable=True,
                    topic="peter-control",
                )
            except Exception as e:  # noqa: BLE001
                logging.warning(f"_broadcast_close_app failed: {e}")

        asyncio.create_task(_send())

    def _broadcast_chat_message(call_id: str) -> None:
        """Drop a show_in_chat() result into the UI as a message from Peter."""
        msg = pop_pending_chat_message(call_id)
        if msg and msg.get("text"):
            _broadcast(
                {"type": "chat_message", "title": msg.get("title") or "", "text": msg["text"]},
                "peter-files",
            )

    @session.on("agent_state_changed")
    def _on_agent_state_changed(ev) -> None:
        _broadcast_status({"state": ev.new_state})

    @session.on("tool_execution_updated")
    def _on_tool_execution_updated(ev) -> None:
        update = ev.update
        if update.type == "tool_call_started":
            name = update.function_call.name
            tool_call_names[update.function_call.call_id] = name
            tool_call_args[update.function_call.call_id] = update.function_call.arguments
            _broadcast_status(
                {"state": "tool", "tool": name, "label": TOOL_LABELS.get(name, name)}
            )
        elif update.type == "tool_call_ended":
            name = tool_call_names.pop(update.call_id, None)
            args_json = tool_call_args.pop(update.call_id, None)
            _broadcast_status({"state": "tool_done", "tool": name})
            if name == "design_cad_part":
                logging.info(
                    f"design_cad_part ended: status={update.status} args_present={bool(args_json)}"
                )
                if update.status == "done" and args_json:
                    _broadcast_cad_design(args_json)
            elif name in ("search_web", "search_recent_news", "fact_check"):
                if update.status == "done":
                    _broadcast_search_links(name, update.call_id)
            elif name == "show_in_chat":
                if update.status == "done":
                    _broadcast_chat_message(update.call_id)
            elif name == "close_everything":
                if update.status == "done" and pop_pending_close_signal(update.call_id):
                    _broadcast_close_app()

    # NOTE: Intentionally NO `session.generate_reply(...)` here.
    #
    # The Gemini realtime model we use (`gemini-3.1-flash-live-preview`) has
    # mutable_chat_context=False, so the Google plugin RAISES a fatal
    # `RealtimeError("generate_reply is not compatible with ...")` the moment
    # this is called. That exception aborts entrypoint() AFTER the agent joins
    # the room, which is why Peter would connect but stay completely silent.
    # The same model also has supports_say=False, so `session.say(...)` can't
    # be used either. Instead, the opening greeting is baked into Peter's
    # agent instructions (prompts.py -> AGENT_INSTRUCTION), which the model
    # honors at startup and speaks automatically as its first line. The agent
    # then responds to the user's voice normally.


# ──────────────────────────────────────────────────────────────
# Worker CLI
# ──────────────────────────────────────────────────────────────
def _disable_console_quick_edit() -> None:
    """Disable Windows console "QuickEdit" mode for this worker's terminal.

    QuickEdit lets a stray click/drag inside the console window select text,
    which SUSPENDS the process until Escape/Enter is pressed — the whole
    worker silently freezes with no error, and a restart of the desktop app
    won't fix it because the frozen process is still "running" and gets
    reused. This worker runs in its own dedicated console window, so it's
    safe to tweak its own console mode here (no other process shares it).
    Best-effort and a no-op off Windows or if the console API isn't available.
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes

        STD_INPUT_HANDLE = -10
        ENABLE_EXTENDED_FLAGS = 0x0080
        ENABLE_QUICK_EDIT_MODE = 0x0040

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(STD_INPUT_HANDLE)
        mode = ctypes.c_uint32()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return
        new_mode = (mode.value & ~ENABLE_QUICK_EDIT_MODE) | ENABLE_EXTENDED_FLAGS
        kernel32.SetConsoleMode(handle, new_mode)
    except Exception:
        pass  # best-effort — never block worker startup over this


def _prewarm(proc: JobProcess) -> None:
    """Prewarm the Gemini Realtime model at process startup to reduce
    first-join latency.

    Must be a real module-level function, not a lambda: with
    job_executor_type=PROCESS the agents framework would pickle this
    callback to hand it to each job's subprocess, and lambdas have no
    importable qualified name — pickling one raises PicklingError. Kept
    as a real function since it's cheap and executor-agnostic.
    """
    _build_model()


if __name__ == "__main__":
    _disable_console_quick_edit()
    cli.run_app(
        WorkerOptions(
            entrypoint_fnc=entrypoint,
            agent_name=os.getenv("AGENT_ID", "peter-assistant"),
            prewarm_fnc=_prewarm,
            # Run jobs as threads in this same process, not separate OS
            # processes. livekit-agents defaults to JobExecutorType.PROCESS
            # on Linux, which also pre-spawns several idle warm processes —
            # each re-imports the whole Gemini/audio/mem0 stack. On a small
            # single-user deploy (e.g. Render's free 512MB tier, where this
            # worker already shares the container with the FastAPI backend)
            # that multiplied memory use enough to get the process OOM-killed
            # mid-job, which looked like the agent silently never joining the
            # room. There's only ever one user here, so we don't need the
            # OS-process isolation between jobs that PROCESS mode buys you.
            job_executor_type=JobExecutorType.THREAD,
            # Defaults to min(ceil(cpu_count()), 4) idle warm slots in prod —
            # each one runs _prewarm and holds its own live Gemini Realtime
            # connection, all day, whether or not anyone's calling. cpu_count()
            # reflects the host's total cores, not this container's actual
            # (tiny, throttled) CPU share, so on Render's free tier this meant
            # several idle connections quietly competing with the one real
            # call for the same starved CPU budget — a very plausible source
            # of the choppy audio. This only ever serves one caller, so one
            # warm slot is enough.
            num_idle_processes=1,
        )
    )

