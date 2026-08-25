"""Turning ADK events into a timeline the console can render.

One function does the consuming - :func:`consume_invocation` - and BOTH a fresh
run and a review resume go through it. That is the point. If the resume path
kept its own loop, approving from Telegram would advance the pipeline while an
open browser tab showed nothing, and the page would look dead exactly when the
interesting part (rework, then publishing) was happening.

Two rules about what gets recorded:

* **Structure comes from the state delta, never from the log text.** The
  orchestrator emits human lines like ``[phase] generate -> qa`` and
  ``[rework] round 2/5: re-running planner``, but it also puts the real values
  in ``event.actions.state_delta`` (``phase``, ``rework_round``,
  ``review_round``). The frontend reads the delta; the text is display copy
  only. Regex-parsing the prose would couple the UI to log wording and break
  the moment someone improves a message.
* **This is a distilled timeline, not a transcript.** The full ADK event log
  already lives in ADK's own ``events`` table and is what the ``/dev``
  inspector is for. Persisting every function-call payload again would bloat
  the database for no one's benefit.
"""

from __future__ import annotations

import itertools
import logging
from datetime import datetime, timezone
from typing import Any, Optional

from app.runs.bus import (
    BUS,
    KIND_ERROR,
    KIND_PHASE,
    KIND_PROGRESS,
    KIND_TOOL,
    RunEvent,
)
from app.services import db
from app.state import K_PHASE, K_REVIEW_ROUND, K_REWORK_ROUND, K_TOKEN_USAGE

logger = logging.getLogger(__name__)

#: State-delta keys worth forwarding to the browser. Everything else in the
#: delta is pipeline payload (the plan, the bundle, rendered slides) that the
#: console fetches from the run detail endpoint instead - putting it on the
#: event stream would push megabytes through every open tab.
_FORWARDED_STATE_KEYS = (K_PHASE, K_REWORK_ROUND, K_REVIEW_ROUND, K_TOKEN_USAGE)


def summarize_event(event: Any) -> str:
    """One human-readable line for a pipeline event ('' when there is none).

    Moved here from ``fetcher.fetch_news`` so the CLI, the run service and the
    resume path all describe an event the same way.
    """
    content = getattr(event, "content", None)
    parts = content.parts if content is not None and content.parts else []
    fragments: list[str] = []
    for part in parts:
        text = getattr(part, "text", None)
        if text and text.strip():
            fragments.append(text.strip())
        elif getattr(part, "function_call", None) is not None:
            fragments.append(f"-> tool {part.function_call.name}")
        elif getattr(part, "function_response", None) is not None:
            fragments.append(f"<- tool {part.function_response.name}")
    if not fragments:
        return ""
    author = getattr(event, "author", "") or "?"
    return f"[{author}] " + " | ".join(fragments)


def _state_delta(event: Any) -> dict:
    """The event's state delta as a plain dict (empty when it has none)."""
    actions = getattr(event, "actions", None)
    delta = getattr(actions, "state_delta", None) if actions is not None else None
    return dict(delta) if delta else {}


def _tool_names(event: Any) -> tuple[list[str], list[str]]:
    """Tool calls and tool responses named in this event."""
    content = getattr(event, "content", None)
    parts = content.parts if content is not None and content.parts else []
    calls, responses = [], []
    for part in parts:
        call = getattr(part, "function_call", None)
        if call is not None and getattr(call, "name", None):
            calls.append(call.name)
        resp = getattr(part, "function_response", None)
        if resp is not None and getattr(resp, "name", None):
            responses.append(resp.name)
    return calls, responses


def classify_event(event: Any) -> tuple[str, dict]:
    """Decide an event's kind and the structured payload to send with it.

    Returns:
        ``(kind, data)`` where kind is one of the bus KIND_* constants.
    """
    data: dict = {}
    delta = _state_delta(event)
    for key in _FORWARDED_STATE_KEYS:
        if key in delta:
            data[key] = delta[key]

    if getattr(event, "error_message", None):
        data["error"] = str(event.error_message)
        return KIND_ERROR, data

    # A phase transition is the single most useful thing the UI can know, so it
    # wins over the tool/progress classification below.
    if K_PHASE in delta:
        return KIND_PHASE, data

    calls, responses = _tool_names(event)
    if calls or responses:
        if calls:
            data["tool_calls"] = calls
        if responses:
            data["tool_responses"] = responses
        return KIND_TOOL, data

    # A paused review is not an error and not a phase change; flag it so the
    # console can switch to the approval card without polling.
    if getattr(event, "long_running_tool_ids", None):
        data["awaiting_review"] = True
        return KIND_PROGRESS, data

    return KIND_PROGRESS, data


async def record_event(
    run_id: str,
    seq: int,
    kind: str,
    author: str = "",
    text: str = "",
    data: Optional[dict] = None,
) -> RunEvent:
    """Persist a timeline event and hand it to every watching browser.

    Persistence first, then publish: a browser that receives an event and then
    reloads must find that event in the replay. The other order would let a
    reload appear to LOSE something the user had already seen.

    Database failures are logged, never raised. Losing a log line must not kill
    a run that is otherwise fine, and the run's real state lives in the ADK
    session either way.
    """
    event = RunEvent(
        run_id=run_id,
        seq=seq,
        kind=kind,
        author=author,
        text=text,
        data=dict(data or {}),
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    try:
        await db.append_run_event(
            run_id, seq, kind, author=author, text=text, data=event.data
        )
    except Exception as exc:
        logger.warning("Could not persist event %s for run %s: %s", seq, run_id, exc)
    await BUS.publish(event)
    return event


async def consume_invocation(
    runner: Any,
    *,
    run_id: str,
    session_id: str,
    user_id: str,
    new_message: Any,
    seq_start: Optional[int] = None,
) -> dict:
    """Drive one invocation to its end, recording the timeline as it goes.

    Used for a fresh run AND for a review resume, which is what lets a verdict
    submitted in Telegram stream its rework and publish progress into a browser
    tab that is already open on that run.

    An invocation ends either at the next review pause or at ``done``; both are
    normal. Nothing here decides what happens next - the caller does.

    Args:
        runner: A built ADK Runner.
        run_id: The run being driven (timeline key).
        session_id: The ADK session id.
        user_id: The ADK user id (the fixed pipeline user).
        new_message: The Content starting this invocation.
        seq_start: Sequence to continue from. Defaults to the run's current
            maximum, so a resumed leg continues the numbering instead of
            restarting at 1 and colliding with the first leg's events.

    Returns:
        ``{"events": int, "last_seq": int, "paused": bool, "phase": str|None}``
    """
    if seq_start is None:
        try:
            seq_start = await db.max_run_seq(run_id)
        except Exception as exc:
            logger.warning(
                "Could not read the event cursor for run %s (%s); starting at 0.",
                run_id,
                exc,
            )
            seq_start = 0

    counter = itertools.count(seq_start + 1)
    count = 0
    last_seq = seq_start
    paused = False
    phase: Optional[str] = None

    async for event in runner.run_async(
        user_id=user_id, session_id=session_id, new_message=new_message
    ):
        count += 1
        kind, data = classify_event(event)
        text = summarize_event(event)
        author = getattr(event, "author", "") or ""

        if data.get(K_PHASE):
            phase = str(data[K_PHASE])
        if data.get("awaiting_review"):
            paused = True
        if kind == KIND_ERROR:
            logger.warning(
                "Run %s: event from %s reported an error: %s",
                run_id,
                author or "?",
                data.get("error"),
            )

        # Events with no text and nothing structured say nothing a human or the
        # UI can use; recording them would pad the timeline with blanks.
        if not text and not data:
            continue

        last_seq = next(counter)
        await record_event(
            run_id, last_seq, kind, author=author, text=text, data=data
        )

    return {"events": count, "last_seq": last_seq, "paused": paused, "phase": phase}


__all__ = [
    "classify_event",
    "consume_invocation",
    "record_event",
    "summarize_event",
]
