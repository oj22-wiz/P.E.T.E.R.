"""
Agenda / calendar tools for the Peter AI assistant.

- add_calendar_event    — put an event on the user's calendar
- list_calendar_events  — check what's on the calendar
- remove_calendar_event — delete/cancel an event

Backed by calendar_store.py (a small JSON file at the project root shared
with the desktop app's own Agenda panel — see peter_app.py), so an event
added by voice shows up in the UI's calendar instantly, and vice versa.

Same stash-by-call_id / pop-on-tool-end pattern as tools.py's show_in_chat:
a successful add/remove stashes a flag here, and agent.py's
tool_call_ended handler pops it and broadcasts a "calendar_updated" message
over the LiveKit data channel so the desktop Agenda panel (if open) refreshes
live instead of only on next open.
"""

from __future__ import annotations

import logging

from livekit.agents import RunContext, function_tool

import calendar_store

_pending_calendar_update: dict[str, bool] = {}


def pop_pending_calendar_update(call_id: str) -> bool:
    """Retrieve (and clear) the update flag stashed for a finished tool call."""
    return _pending_calendar_update.pop(call_id, False)


def _flag_update(context) -> None:
    try:
        call_id = context.function_call.call_id
    except Exception:
        return
    _pending_calendar_update[call_id] = True


@function_tool()
async def add_calendar_event(
    context: RunContext,  # type: ignore
    date: str,
    time: str,
    name: str,
    color: str,
) -> str:
    """
    Add an event to the user's personal calendar/agenda.

    ALWAYS ask the user for all four details BEFORE calling this tool — never
    guess or invent any of them:
      1. The date of the event.
      2. The time (or confirm it's an all-day event with no specific time).
      3. The name/title of the event.
      4. The color to file it under — it MUST be exactly one of: red, green,
         blue, yellow. If the user doesn't specify a color, ask them to pick
         one of those four.

    `date` must be an absolute calendar date in YYYY-MM-DD format. If the
    user gives a relative date like "tomorrow", "next Friday", or "the 3rd",
    call get_current_date() first and work out the actual date yourself —
    never guess. `time` must be 24-hour HH:MM (e.g. "14:30"), or an empty
    string "" for an all-day event with no specific time. `color` must be
    exactly one of: red, green, blue, yellow (lowercase).
    """
    result = calendar_store.add_event(date, time, name, color)
    if not result.get("ok"):
        err = result.get("error")
        if err == "INVALID_DATE":
            return "That date isn't valid — I need it as an exact YYYY-MM-DD calendar date. What's the actual date?"
        if err == "INVALID_TIME":
            return "That time isn't valid — I need it as 24-hour HH:MM (or just tell me it's an all-day event)."
        if err == "MISSING_NAME":
            return "I need a name for the event before I can add it — what should I call it?"
        if err == "INVALID_COLOR":
            return "The color has to be one of: red, green, blue, or yellow — which one?"
        return f"I couldn't add that to the calendar: {err}"
    _flag_update(context)
    ev = result["event"]
    when = ev["date"] + (f" at {ev['time']}" if ev["time"] else " (all day)")
    logging.info(f"add_calendar_event: '{ev['name']}' on {when} [{ev['color']}]")
    return f"Added '{ev['name']}' to your calendar on {when}, filed under {ev['color']}."


@function_tool()
async def list_calendar_events(
    context: RunContext,  # type: ignore
    date: str = "",
) -> str:
    """
    Look up what's on the user's personal calendar/agenda.

    Pass a specific date in YYYY-MM-DD format to check just that day (resolve
    relative dates like "tomorrow" via get_current_date() first). Leave
    `date` blank to see everything currently on the calendar.

    Use this when the user asks things like "what's on my calendar", "do I
    have anything on Friday", "what do I have coming up", or "what's my
    schedule".
    """
    events = calendar_store.list_events(date.strip() if date else "")
    if not events:
        return (
            f"Nothing on the calendar for {date}." if date
            else "The calendar is empty — nothing on it yet."
        )
    lines = []
    for e in events:
        when = e["date"] + (f" {e['time']}" if e.get("time") else "")
        lines.append(f"- {when} — {e['name']} ({e['color']})")
    return "Here's what's on the calendar:\n" + "\n".join(lines)


@function_tool()
async def remove_calendar_event(
    context: RunContext,  # type: ignore
    name: str,
    date: str = "",
) -> str:
    """
    Remove/cancel an event from the user's calendar.

    Match it by its name (a substring match, case-insensitive) — pass `date`
    (YYYY-MM-DD) too if you know it, to disambiguate between similarly-named
    events. If more than one event matches, nothing is deleted — this
    returns the matching events instead so you can read them back to the
    user and ask which one they mean, then call this again with a more
    specific name or date.
    """
    matches = calendar_store.find_events(name=name, date=date.strip() if date else "")
    if not matches:
        return "I couldn't find any event on the calendar matching that."
    if len(matches) > 1:
        lines = [
            f"- {e['date']}" + (f" {e['time']}" if e.get("time") else "") + f" — {e['name']}"
            for e in matches
        ]
        return (
            "That matches more than one event, so I didn't delete anything. "
            "Ask which one they mean, then call this again with a more "
            "specific name or date:\n" + "\n".join(lines)
        )
    ev = matches[0]
    ok = calendar_store.remove_event(ev["id"])
    if not ok:
        return "I found that event but couldn't remove it — try again."
    _flag_update(context)
    logging.info(f"remove_calendar_event: removed '{ev['name']}' on {ev['date']}")
    return f"Removed '{ev['name']}' on {ev['date']} from the calendar."
