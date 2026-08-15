"""
Self-improvement — Peter automatically reflects on each finished conversation
and remembers a few concrete lessons for next time (things the user
corrected him on, preferences they expressed, mistakes to avoid), without
needing to be told to store_memory.

- distill_session(chat_ctx)   — called once, at the END of a session (wired
  up via a JobContext shutdown callback in agent.py) so reflection never adds
  latency to the live conversation. Uses a quick Gemini text call to extract
  at most a few short lessons, then appends them to learned_notes.md.
- get_learned_notes_snippet() — read back the most recent lessons so agent.py
  can fold them straight into Peter's system instructions at the START of
  the NEXT session. This is the actual feedback loop: talk -> reflect ->
  remember -> behave differently next time.

learned_notes.md is a plain, human-readable file (not a vector store) on
purpose — easy to inspect, easy to trim, and Peter can read it himself via
read_own_file if the user asks "what have you learned about me".

See tools.py for the companion piece — automatic capture of what Peter
learns from his own web searches (a separate Mem0 namespace).
"""

from __future__ import annotations

import logging
import os
from datetime import datetime
from pathlib import Path

_ROOT = Path(os.path.dirname(os.path.abspath(__file__))).resolve()
_NOTES_PATH = _ROOT / "learned_notes.md"

_MAX_NOTES_CHARS = 6000
_MAX_TRANSCRIPT_CHARS = 8000
_DISTILL_MODEL = "gemini-flash-latest"

_DISTILL_PROMPT = """You are reviewing a transcript of a conversation between Peter, a voice assistant, and his one user, Orlando.

Extract at most 4 short, concrete lessons Peter should remember for FUTURE conversations — things Orlando corrected him on, preferences he expressed (tone, verbosity, topics), recurring mistakes Peter made, or how Orlando likes to be helped. Skip anything generic, obvious, or already well known. If there is truly nothing worth remembering, reply with exactly: NONE

Reply with each lesson as one short bullet line (no preamble, no numbering, just "- lesson").

Transcript:
{transcript}
"""


def _transcript_from_history(chat_ctx) -> str:
    """Flatten a LiveKit ChatContext into a plain-text transcript."""
    lines: list[str] = []
    try:
        items = chat_ctx.items
    except Exception:
        return ""
    for item in items:
        if getattr(item, "type", None) != "message":
            continue
        role = getattr(item, "role", "") or ""
        text = (getattr(item, "text_content", None) or "").strip()
        if text:
            lines.append(f"{role}: {text}")
    transcript = "\n".join(lines)
    if len(transcript) > _MAX_TRANSCRIPT_CHARS:
        transcript = transcript[-_MAX_TRANSCRIPT_CHARS:]
    return transcript


def _append_learned_notes(lessons: list[str]) -> None:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    block = f"## {ts}\n" + "\n".join(f"- {lesson}" for lesson in lessons) + "\n\n"
    try:
        existing = _NOTES_PATH.read_text(encoding="utf-8") if _NOTES_PATH.exists() else ""
    except OSError:
        existing = ""
    combined = existing + block
    if len(combined) > _MAX_NOTES_CHARS:
        combined = combined[-_MAX_NOTES_CHARS:]
        # Don't cut a lesson in half — drop back to the start of the next block.
        cut = combined.find("\n## ")
        if cut != -1:
            combined = combined[cut + 1:]
    try:
        _NOTES_PATH.write_text(combined, encoding="utf-8")
        logging.info(f"learning: recorded {len(lessons)} lesson(s) to learned_notes.md")
    except OSError as e:
        logging.warning(f"learning: failed to write learned_notes.md: {e}")


async def distill_session(chat_ctx) -> None:
    """
    Reflect on one finished session's transcript and remember what's worth
    keeping. Best-effort: any failure here (missing API key, network error,
    empty transcript, quota) is swallowed — this must never affect a live
    call, since it only ever runs after the call has already ended.
    """
    transcript = _transcript_from_history(chat_ctx)
    # A handful of lines isn't enough of a conversation to learn anything real from.
    if transcript.count("\n") < 3:
        return

    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        return

    try:
        from google import genai

        client = genai.Client(api_key=api_key)
        resp = await client.aio.models.generate_content(
            model=_DISTILL_MODEL,
            contents=_DISTILL_PROMPT.format(transcript=transcript),
        )
        text = (resp.text or "").strip()
    except Exception as e:  # noqa: BLE001
        logging.warning(f"learning: distillation call failed: {e}")
        return

    if not text or text.strip().upper() == "NONE":
        return

    lessons = []
    for line in text.splitlines():
        line = line.strip().lstrip("-•*").strip()
        if line and line.upper() != "NONE":
            lessons.append(line)
    lessons = lessons[:4]
    if lessons:
        _append_learned_notes(lessons)


def get_learned_notes_snippet(max_chars: int = 2000) -> str:
    """Most-recent learned lessons, trimmed to fit comfortably in the system prompt."""
    if not _NOTES_PATH.exists():
        return ""
    try:
        text = _NOTES_PATH.read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    if not text:
        return ""
    if len(text) > max_chars:
        text = text[-max_chars:]
        cut = text.find("\n## ")
        if cut != -1:
            text = text[cut + 1:]
    return text
