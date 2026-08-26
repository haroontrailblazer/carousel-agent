"""Starting and supervising pipeline runs inside a long-lived process.

The CLI could afford to be simple: pop an item, drive it, exit. A web service
cannot. A run started from a browser has to outlive the HTTP request that asked
for it, survive the user closing the tab, be visible while it happens, be
cancellable, and be recoverable when the process dies mid-way.

So a run here is a detached ``asyncio.Task``, held by a strong reference (the
event loop only keeps weak ones, so an unreferenced task can be garbage
collected mid-run). The HTTP handler returns as soon as the session exists.

Deliberately NOT built on ADK's ``/run_sse``: that endpoint cancels the agent
run when the client disconnects, so closing a tab would kill a generation that
has already spent real money.

Cost control lives here too, because this is the only place a run can begin.
Every carousel costs image and reasoning credits, so a bug or an impatient
click must not be able to start ten.
"""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Literal, Optional

from app.config import settings
from app.runs.bus import KIND_ERROR, KIND_TERMINAL
from app.runs.stream import consume_invocation, record_event
from app.services import db
from app.state import (
    K_NEWS_ITEM,
    K_RUN_ID,
    PHASE_DONE,
    PHASE_GENERATE,
    PHASE_REWORK,
)

logger = logging.getLogger(__name__)

#: Where a run came from. Recorded on the run so history can distinguish a
#: typed topic from a scheduled pick.
RunSource = Literal["topic", "url", "queue", "schedule"]

#: Fixed pipeline user id - sessions are addressed by
#: (app_name, user_id, session_id) and every surface must agree.
try:  # pragma: no cover - trivial import wiring
    from fetcher.fetch_news import PIPELINE_USER_ID
except Exception:  # pragma: no cover - env dependent
    PIPELINE_USER_ID = "pipeline"

#: How many runs may be in flight at once. One by default: this pipeline is
#: CPU- and network-heavy (ffmpeg, image generation) and the service runs on a
#: single small instance, so concurrency buys latency, not throughput.
MAX_CONCURRENT_RUNS = int(os.getenv("MAX_CONCURRENT_RUNS", "1"))

#: Ceiling on runs started in a rolling 24 hours. A runaway loop that starts
#: carousels is not a slow bug - it is an invoice.
MAX_RUNS_PER_DAY = int(os.getenv("MAX_RUNS_PER_DAY", "10"))

#: Strong references to in-flight run tasks, keyed by run id.
_run_tasks: dict[str, "asyncio.Task[None]"] = {}


class RunRefused(Exception):
    """A run was not started, for a reason the caller should show the user.

    Carries a machine-readable ``code`` so the API can branch on it and the UI
    can render it without matching on message text.
    """

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class StartedRun:
    run_id: str
    news_id: str
    title: str


def active_run_ids() -> set[str]:
    """Runs currently being driven by this process."""
    return {rid for rid, task in _run_tasks.items() if not task.done()}


async def _check_limits() -> None:
    """Refuse a run that would exceed the concurrency or daily cap."""
    active = active_run_ids()
    if len(active) >= MAX_CONCURRENT_RUNS:
        raise RunRefused(
            "too_many_active_runs",
            f"A run is already in progress ({', '.join(sorted(active))}). "
            "Wait for it to reach review before starting another.",
        )
    if MAX_RUNS_PER_DAY > 0:
        since = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        try:
            started = await db.count_runs_since(since)
        except Exception as exc:
            # Refusing every run because the counter is unreachable would be
            # worse than briefly missing the cap.
            logger.warning("Could not check the daily run cap: %s", exc)
            return
        if started >= MAX_RUNS_PER_DAY:
            raise RunRefused(
                "daily_limit_reached",
                f"{started} runs have already started in the last 24 hours "
                f"(limit {MAX_RUNS_PER_DAY}). Raise MAX_RUNS_PER_DAY to allow more.",
            )


async def fetch_url_item(url: str) -> dict:
    """Turn a pasted article URL into a NewsItem payload.

    Reuses the fetcher's extraction helpers rather than reimplementing them, so
    a URL typed into the console produces the same shape as an RSS pick and the
    downstream agents cannot tell the difference.

    Raises:
        RunRefused: when the page cannot be fetched or has no usable text.
    """
    import requests
    from fetcher.fetch_news import (
        MAX_BODY_CHARS,
        _html_to_text,
        _media_urls_from_html,
    )
    from app.schemas import NewsItem

    clean = (url or "").strip()
    if not clean.lower().startswith(("http://", "https://")):
        raise RunRefused("invalid_url", "Enter a full http(s) URL.")

    try:
        resp = await asyncio.to_thread(
            requests.get,
            clean,
            timeout=30,
            headers={"User-Agent": "carousel-factory/1.0"},
        )
        resp.raise_for_status()
    except Exception as exc:
        raise RunRefused(
            "url_unreachable", f"Could not fetch that URL: {exc}"
        ) from exc

    markup = resp.text
    text = _html_to_text(markup)
    if len(text.strip()) < 200:
        raise RunRefused(
            "url_has_no_text",
            "That page has almost no readable text - it may be a paywall, a "
            "login screen, or a JavaScript-rendered app. Paste the article "
            "text as a topic instead.",
        )

    title = ""
    lowered = markup.lower()
    if "<title" in lowered:
        start = lowered.index("<title")
        start = markup.index(">", start) + 1
        end = lowered.index("</title>", start)
        title = _html_to_text(markup[start:end]).strip()

    item = NewsItem(
        id=f"url-{db.url_hash(clean)[:16]}",
        title=(title or text.strip().splitlines()[0])[:150],
        summary=text.strip()[:2000],
        body=text[:MAX_BODY_CHARS],
        source_name="web",
        source_url=clean,
        media_urls=_media_urls_from_html(markup),
        tags=["url"],
    )
    return item.model_dump(mode="json")


async def start_run(
    *,
    source: RunSource,
    topic: str = "",
    url: str = "",
    news: Optional[dict] = None,
    requested_by: str = "",
) -> StartedRun:
    """Create a run, seed its session, and start driving it in the background.

    Returns as soon as the session exists - the pipeline itself runs for
    minutes afterwards and is watched over the event stream.

    Args:
        source: How this run was triggered.
        topic: Free text, for ``source="topic"``. The orchestrator synthesises
            a NewsItem from it (see ``_phase_init``).
        url: Article URL, for ``source="url"``.
        news: A ready NewsItem payload, for ``source="queue"``/``"schedule"``.
        requested_by: Email of the person who asked, for the audit trail.

    Raises:
        RunRefused: cap exceeded, or the input could not be turned into a run.
    """
    await _check_limits()

    if source == "url":
        news = await fetch_url_item(url)
    elif source == "topic":
        cleaned = (topic or "").strip()
        if len(cleaned) < 3:
            raise RunRefused("empty_topic", "Type a topic to generate a carousel.")
        news = None  # the orchestrator synthesises the NewsItem from the message
    elif news is None:
        raise RunRefused("no_input", "Nothing to run.")

    run_id = f"run-{uuid.uuid4().hex[:12]}"
    news_id = str((news or {}).get("id") or run_id)
    title = str((news or {}).get("title") or (topic or "").strip()[:150] or "Untitled")

    state: dict[str, Any] = {K_RUN_ID: run_id}
    if news is not None:
        state[K_NEWS_ITEM] = news

    from app.agent import build_runner

    runner = build_runner()
    await runner.session_service.create_session(
        app_name=settings.app_name,
        user_id=PIPELINE_USER_ID,
        session_id=run_id,
        state=state,
    )
    await db.create_run(run_id, news_id)
    await db.set_run_meta(run_id, title=title, source=source, requested_by=requested_by)
    await db.set_run_status(run_id, db.RUN_STATUS_RUNNING)

    first_message = (
        topic.strip()
        if source == "topic"
        else f"Create an Instagram carousel for the queued news item: {title}"
    )
    spawn_run(run_id, runner=runner, first_message=first_message)
    logger.info("Started run %s (%s) for %r.", run_id, source, title)
    return StartedRun(run_id=run_id, news_id=news_id, title=title)


def spawn_run(run_id: str, *, runner: Any, first_message: str) -> "asyncio.Task[None]":
    """Drive a run in the background, holding a strong reference to the task."""
    from google.genai import types

    message = types.Content(role="user", parts=[types.Part(text=first_message)])
    task = asyncio.get_running_loop().create_task(
        _drive_run(run_id, runner, message), name=f"run-{run_id}"
    )
    _run_tasks[run_id] = task
    task.add_done_callback(lambda _t: _run_tasks.pop(run_id, None))
    return task


#: How often a running task says "still here". Must stay well below the idle
#: threshold recovery uses (db.interrupted_run_candidates), or a healthy run
#: gets reclaimed while it is working.
HEARTBEAT_INTERVAL_S = float(os.getenv("RUN_HEARTBEAT_S", "30"))


async def _heartbeat(run_id: str) -> None:
    """Touch the run row on a timer until cancelled.

    Runs alongside the pipeline rather than inside it, so a phase that does
    fifteen minutes of ffmpeg and image generation without a single database
    write still looks alive to startup recovery.
    """
    while True:
        try:
            await asyncio.sleep(HEARTBEAT_INTERVAL_S)
            await db.touch_run(run_id)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # A missed heartbeat is not worth killing a run over; the next one
            # is thirty seconds away.
            logger.debug("Heartbeat failed for run %s: %s", run_id, exc)


async def _drive_run(run_id: str, runner: Any, message: Any) -> None:
    """Consume one invocation and record how it ended.

    Never raises: this is a detached task with nobody to catch it, and an
    unhandled exception here would be logged by asyncio and then lost, leaving
    the run row saying "running" forever.
    """
    beat = asyncio.get_running_loop().create_task(
        _heartbeat(run_id), name=f"heartbeat-{run_id}"
    )
    try:
        result = await consume_invocation(
            runner,
            run_id=run_id,
            session_id=run_id,
            user_id=PIPELINE_USER_ID,
            new_message=message,
        )
        if result["paused"]:
            status = db.RUN_STATUS_AWAITING_REVIEW
        elif result.get("phase") == "done":
            status = db.RUN_STATUS_DONE
        else:
            # The invocation ENDED without pausing and without reaching done -
            # the orchestrator halted mid-phase. Recording RUNNING here was
            # wrong twice over: nothing was running, so the console showed a
            # live task forever with a pulsing trace and a Stop button that
            # answered "not running"; and it overwrote whatever status the
            # halting phase had just decided for itself.
            #
            # So respect a status the orchestrator already moved off running
            # (the review-notice halt sets awaiting_review, because the
            # carousel is ready and a human IS needed), and otherwise call it
            # what it is: stopped part-way, and resumable.
            current = ""
            try:
                row = await db.get_run(run_id)
                current = str((row or {}).get("status") or "")
            except Exception as exc:  # pragma: no cover - status read is advisory
                logger.warning("Could not re-read status for %s: %s", run_id, exc)
            status = (
                current
                if current and current != db.RUN_STATUS_RUNNING
                else db.RUN_STATUS_INTERRUPTED
            )
        await db.set_run_status(run_id, status)
        await record_event(
            run_id,
            result["last_seq"] + 1,
            KIND_TERMINAL,
            text=(
                "Waiting for your review."
                if result["paused"]
                else "Run finished."
                if status == db.RUN_STATUS_DONE
                else f"Run stopped ({status})."
            ),
            data={"status": status, "paused": result["paused"]},
        )
    except asyncio.CancelledError:
        await _finish_badly(run_id, db.RUN_STATUS_CANCELLED, "Run cancelled.")
        raise
    except Exception as exc:
        logger.exception("Run %s failed.", run_id)
        await _finish_badly(run_id, db.RUN_STATUS_FAILED, f"Run failed: {exc}")
    finally:
        beat.cancel()
        try:
            await runner.close()
        except Exception as exc:  # pragma: no cover - cleanup best effort
            logger.warning("Closing the runner for %s failed: %s", run_id, exc)


async def _finish_badly(run_id: str, status: str, text: str) -> None:
    """Record an unhappy ending without letting the recording itself fail."""
    try:
        await db.set_run_status(run_id, status)
        seq = await db.max_run_seq(run_id)
        await record_event(
            run_id, seq + 1, KIND_ERROR, text=text, data={"status": status}
        )
    except Exception as exc:  # pragma: no cover - shutdown / DB down
        logger.warning("Could not record the end of run %s: %s", run_id, exc)


async def resume_interrupted_run(run_id: str, *, requested_by: str = "") -> bool:
    """Re-enter an interrupted run at whatever phase it stopped in.

    This works because the orchestrator is a re-entrant phase machine: it reads
    the phase back out of persisted session state and starts that phase again
    from the top. Some work inside the interrupted phase is repeated, but the
    output is correct and the publisher is idempotent, so an interrupted run is
    genuinely recoverable rather than merely restartable.

    Returns:
        True if a resume was started; False if the run is unknown or already
        being driven.
    """
    if run_id in active_run_ids():
        return False
    run = await db.get_run(run_id)
    if run is None:
        return False

    await _check_limits()

    from app.agent import build_runner
    from google.genai import types

    runner = build_runner()
    message = types.Content(
        role="user",
        parts=[types.Part(text="Continue the interrupted run from its current phase.")],
    )
    await db.set_run_status(run_id, db.RUN_STATUS_RUNNING)
    seq = await db.max_run_seq(run_id)
    await record_event(
        run_id, seq + 1, KIND_TERMINAL,
        text=f"Resuming interrupted run at phase '{run.get('phase')}'.",
        data={"resumed_by": requested_by} if requested_by else {},
    )
    task = asyncio.get_running_loop().create_task(
        _drive_run(run_id, runner, message), name=f"resume-run-{run_id}"
    )
    _run_tasks[run_id] = task
    task.add_done_callback(lambda _t: _run_tasks.pop(run_id, None))
    return True



async def restart_run(run_id: str, *, requested_by: str = "") -> bool:
    """Re-run a stopped task IN PLACE, with its rework budget reset.

    Not a new run. Everything the task already earned - the researched story,
    the plan, the approved copy, the rendered slides, the reviewer's feedback,
    the whole transcript - lives in this session, and starting a fresh one
    throws all of it away to redo work that was never the problem. When a
    carousel dies rendering its last slide, what you want is that slide again,
    not another twenty minutes of research.

    Two things are reset before re-entering, and only two:

    * ``rework_round`` to 0, because the round cap is usually WHY the run
      stopped; leaving it exhausted would stop it again immediately.
    * ``phase`` back to where the work stopped, because the hard stop writes
      DONE into session state to end the orchestrator's loop.

    Applies to every origin - chat, newsroom, schedule - since none of them
    changes what a session holds.

    Returns:
        True when a restart was started; False if the run is unknown or
        already being driven.
    """
    if run_id in active_run_ids():
        return False
    run = await db.get_run(run_id)
    if run is None:
        return False

    from app.review.resume import PIPELINE_USER_ID

    # The phase recorded on the run is where it actually stopped; session
    # state may say DONE because that is how the loop was ended.
    phase = str(run.get("phase") or "") or PHASE_GENERATE
    if phase == PHASE_DONE:
        phase = PHASE_REWORK
    await db.rewind_session_for_restart(
        run_id, settings.app_name, PIPELINE_USER_ID, phase
    )
    logger.info(
        "Restarting run %s in place at phase '%s' (rework budget reset) for %s.",
        run_id,
        phase,
        requested_by or "unknown",
    )
    return await resume_interrupted_run(run_id, requested_by=requested_by)

async def cancel_run(run_id: str) -> bool:
    """Stop whatever this process is running for ``run_id``.

    TWO task registries, because a run is driven from two places: a fresh or
    resumed invocation lives in ``_run_tasks`` here, while the rework that
    follows a rejection lives in ``app.review.resume._resume_tasks``. Checking
    only the first made Stop silently do nothing during a rework - the button
    reported success and the agents kept running.

    Returns False when nothing was running, which the API turns into a 409.
    """
    from app.review.resume import cancel_resume

    task = _run_tasks.get(run_id)
    stopped = False
    if task is not None and not task.done():
        task.cancel()
        stopped = True
    if cancel_resume(run_id):
        stopped = True
    if stopped:
        return True

    # Nothing in either registry - but the run may still be RECORDED as
    # running. Both registries are per-process and in-memory, so a restart
    # empties them while the database keeps whatever the run last wrote. That
    # left a phantom: a task pulsing "running" in the console that Stop
    # refused to touch, answering "that run is not currently running" while
    # the page insisted it was.
    #
    # This deployment is single-instance by design (see the run bus and the
    # startup reconcile, which rely on the same fact), so if this process is
    # not driving the run, nothing is. The status is stale, and the honest
    # thing Stop can do is say so.
    run = await db.get_run(run_id)
    if run is None:
        return False
    if str(run.get("status") or "") != db.RUN_STATUS_RUNNING:
        return False

    await db.set_run_status(run_id, db.RUN_STATUS_CANCELLED)
    try:
        seq = await db.max_run_seq(run_id)
        await record_event(
            run_id,
            seq + 1,
            KIND_TERMINAL,
            text=(
                "Stopped. No agent was still running for this task - the "
                "service had restarted since it started - so its status was "
                "corrected rather than a process being cancelled."
            ),
            data={"status": db.RUN_STATUS_CANCELLED, "stale": True},
        )
    except Exception as exc:  # pragma: no cover - the status change is the point
        logger.warning("Could not record the stop event for %s: %s", run_id, exc)
    logger.info("Stopped stale run %s (no in-process task).", run_id)
    return True


async def drain_run_tasks(timeout: float = 10.0) -> None:
    """Give in-flight runs a moment at shutdown, then cancel them.

    Not a drain to completion, deliberately: a generate phase runs for minutes
    and no platform grace period covers that, so waiting only guarantees being
    SIGKILLed mid-write. Cancelling marks the run cancelled and leaves it
    re-enterable, which is a better end state than being killed silently.
    """
    tasks = [t for t in _run_tasks.values() if not t.done()]
    if not tasks:
        return
    logger.info(
        "Shutdown: waiting up to %.0f s for %d in-flight run(s).", timeout, len(tasks)
    )
    _, pending = await asyncio.wait(set(tasks), timeout=timeout)
    for task in pending:
        task.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)


__all__ = [
    "HEARTBEAT_INTERVAL_S",
    "MAX_CONCURRENT_RUNS",
    "MAX_RUNS_PER_DAY",
    "PIPELINE_USER_ID",
    "RunRefused",
    "RunSource",
    "StartedRun",
    "active_run_ids",
    "cancel_run",
    "drain_run_tasks",
    "fetch_url_item",
    "resume_interrupted_run",
    "spawn_run",
    "start_run",
]
