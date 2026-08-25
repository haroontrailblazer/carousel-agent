"""The console's run API: start, watch, review, recover.

Two design notes that shape most of this file.

**Session state is the source of truth for content; Postgres for lifecycle.**
The plan, the bundle and the QA report live in the ADK session because that is
what the pipeline resumes from. The ``runs`` table mirrors phase and status so
the history screen can list and filter without loading a session per row.

**Streaming must never pass through anything that buffers.** The SSE endpoint
returns a generator and the auth layer is raw ASGI, deliberately - a
``BaseHTTPMiddleware`` anywhere in the chain would hold the whole response and
the live trace would appear only once the run had already finished.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, AsyncIterator, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app import runtime
from app.config import settings
from app.review.verdict import REJECT_QUESTION, submit_verdict
from app.runs.bus import BUS
from app.runs.service import (
    RunRefused,
    active_run_ids,
    cancel_run,
    resume_interrupted_run,
    start_run,
)
from app.services import db
from app.state import (
    AGENT_CTA,
    AGENT_FEEDBACK_ROUTER,
    AGENT_FIRST_PAGE_VISUAL,
    AGENT_LEARNER,
    AGENT_PHRASING,
    AGENT_PLANNER,
    AGENT_PUBLISHER,
    AGENT_RESEARCH,
    AGENT_REVIEW_DISPATCHER,
    AGENT_STITCH_VERIFY,
    AGENT_TEMPLATE_DESIGN,
    K_BUNDLE,
    K_NEWS_ITEM,
    K_PHASE,
    K_PUBLISH_RESULT,
    K_QA_REPORT,
    K_REVIEW_ROUND,
    K_REWORK_ROUND,
    K_TOKEN_USAGE,
    K_VERDICT,
    PHASE_DONE,
    PHASE_GENERATE,
    PHASE_PUBLISH,
    PHASE_QA,
    PHASE_REVIEW,
    PHASE_REWORK,
    REWORKABLE_AGENTS,
)
from web_api.auth import Identity
from web_api.deps import current_identity

logger = logging.getLogger(__name__)

router = APIRouter()

#: Fixed pipeline user id; sessions are addressed by
#: (app_name, user_id, session_id).
try:  # pragma: no cover - trivial import wiring
    from fetcher.fetch_news import PIPELINE_USER_ID
except Exception:  # pragma: no cover - env dependent
    PIPELINE_USER_ID = "pipeline"

#: How long a signed artifact URL lasts in the browser.
#:
#: Much shorter than the 24h default used for publishing - Instagram's fetcher
#: needs a long window, a page being looked at right now does not, and a URL
#: that leaks out of a screenshot should stop working the same hour.
ARTIFACT_URL_TTL_S = 3600

#: Heartbeat interval for the SSE stream. Render's proxy closes idle
#: connections, and this pipeline routinely produces no events for minutes at a
#: time while an image model works, so a silent stream is normal and must be
#: kept alive explicitly.
SSE_KEEPALIVE_S = 15.0


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------
class StartRunRequest(BaseModel):
    source: str = Field("topic", pattern="^(topic|url|queue)$")
    topic: str = ""
    url: str = ""
    news_id: str = ""


class VerdictRequest(BaseModel):
    status: str = Field(..., pattern="^(approved|rejected)$")
    feedback: str = ""


class EnqueueRequest(BaseModel):
    url: str


# ---------------------------------------------------------------------------
# Session reading
# ---------------------------------------------------------------------------
async def _session_state(run_id: str) -> dict:
    """Read a run's ADK session state, or ``{}`` when there is none."""
    try:
        session = await runtime.session_service().get_session(
            app_name=settings.app_name,
            user_id=PIPELINE_USER_ID,
            session_id=run_id,
        )
    except Exception as exc:
        logger.warning("Could not load session for run %s: %s", run_id, exc)
        return {}
    return dict(session.state) if session is not None else {}


def _news_summary(state: dict) -> dict:
    news = state.get(K_NEWS_ITEM) or {}
    return {
        "title": news.get("title", ""),
        "summary": news.get("summary", ""),
        "source_name": news.get("source_name", ""),
        "source_url": news.get("source_url", ""),
    }


# ---------------------------------------------------------------------------
# Runs
# ---------------------------------------------------------------------------
@router.post("/runs", status_code=status.HTTP_202_ACCEPTED)
async def create_run(
    payload: StartRunRequest, identity: Identity = Depends(current_identity)
) -> dict:
    """Start a run and return immediately.

    202, not 200: the pipeline runs for minutes after this responds. The client
    gets a run id and watches the event stream.
    """
    news: Optional[dict] = None
    if payload.source == "queue":
        if not payload.news_id:
            raise HTTPException(400, {"code": "no_news_id", "message": "Pick an item."})
        item = await db.next_queued_news_by_id(payload.news_id)
        if item is None:
            raise HTTPException(
                404,
                {
                    "code": "queue_item_gone",
                    "message": "That item is no longer queued - it may already "
                               "have been picked up.",
                },
            )
        news = item

    try:
        started = await start_run(
            source=payload.source,  # type: ignore[arg-type]
            topic=payload.topic,
            url=payload.url,
            news=news,
            requested_by=identity.email,
        )
    except RunRefused as exc:
        # 409 for "not now" (a run is going, the cap is reached); 400 for
        # "not like that" (bad URL, empty topic). The SPA renders them
        # differently, so the distinction has to reach it.
        code = 409 if exc.code in ("too_many_active_runs", "daily_limit_reached") else 400
        raise HTTPException(code, {"code": exc.code, "message": exc.detail}) from exc

    return {
        "run_id": started.run_id,
        "news_id": started.news_id,
        "title": started.title,
        "phase": PHASE_GENERATE,
        "status": db.RUN_STATUS_RUNNING,
    }


@router.get("/runs")
async def list_runs(
    limit: int = Query(25, ge=1, le=100),
    before: Optional[str] = None,
    phase: Optional[str] = None,
    run_status: Optional[str] = Query(None, alias="status"),
    _identity: Identity = Depends(current_identity),
) -> dict:
    """Run history, newest first.

    Everyone signed in sees every run - this is a shared workspace, and the
    Telegram channel already broadcasts to the whole team anyway.
    """
    rows = await db.list_runs(
        limit=limit, before=before, phase=phase, status=run_status
    )
    live = active_run_ids()
    for row in rows:
        row["is_live"] = row["run_id"] in live
    return {
        "items": rows,
        "next_cursor": rows[-1]["created_at"] if len(rows) == limit else None,
    }


@router.get("/runs/{run_id}")
async def get_run(
    run_id: str, _identity: Identity = Depends(current_identity)
) -> dict:
    """Everything the run detail screen needs, in one request."""
    run = await db.get_run(run_id)
    if run is None:
        raise HTTPException(404, {"code": "no_such_run", "message": "Unknown run."})

    state = await _session_state(run_id)
    bundle = state.get(K_BUNDLE) or {}
    qa = state.get(K_QA_REPORT) or {}
    verdict = state.get(K_VERDICT) or {}
    publish = state.get(K_PUBLISH_RESULT) or {}

    try:
        pending = await db.load_pending_review(run_id)
    except Exception:
        pending = None

    return {
        **run,
        "is_live": run_id in active_run_ids(),
        "news": _news_summary(state),
        "phase_state": state.get(K_PHASE, run.get("phase")),
        "rework_round": state.get(K_REWORK_ROUND, 0),
        "review_round": state.get(K_REVIEW_ROUND, run.get("review_round", 0)),
        "caption": bundle.get("caption", ""),
        "slide_count": len(bundle.get("slides") or []),
        "qa": {"passed": qa.get("passed"), "issues": qa.get("issues", [])},
        "verdict": verdict or None,
        "publish": {
            "media_id": publish.get("media_id"),
            "permalink": publish.get("permalink"),
            "error": publish.get("message") if publish.get("status") == "error" else None,
        },
        "token_usage": state.get(K_TOKEN_USAGE, {}),
        "last_seq": await db.max_run_seq(run_id),
        # The authoritative "is a decision still wanted?" flag. NOT monotonic:
        # a failed resume restores the pending row, so a run can go pending ->
        # not pending -> pending again. Any UI that reads this once will get
        # stuck showing the wrong thing.
        "pending_review": pending is not None,
    }


@router.get("/runs/{run_id}/artifacts")
async def run_artifacts(
    run_id: str, _identity: Identity = Depends(current_identity)
) -> dict:
    """Freshly signed URLs for every piece of the carousel.

    Signed on every request rather than stored: these expire, and a run
    reviewed the morning after would otherwise show a grid of broken images
    with no explanation.
    """
    state = await _session_state(run_id)
    bundle = state.get(K_BUNDLE)
    if not bundle:
        raise HTTPException(
            404,
            {
                "code": "no_bundle_yet",
                "message": "This run has not assembled a carousel yet.",
            },
        )

    service = runtime.artifact_service()

    async def sign(filename: str) -> Optional[dict]:
        if not filename:
            return None
        try:
            url = await service.public_url_async(
                app_name=settings.app_name,
                user_id=PIPELINE_USER_ID,
                session_id=run_id,
                filename=filename,
                expires_in=ARTIFACT_URL_TTL_S,
            )
        except Exception as exc:
            logger.warning("Could not sign %s for run %s: %s", filename, run_id, exc)
            return {"filename": filename, "url": None, "error": str(exc)}
        return {"filename": filename, "url": url}

    cover = bundle.get("cover") or {}
    cta = bundle.get("cta") or {}
    slides = bundle.get("slides") or []

    poster, video, cta_img = await asyncio.gather(
        sign(cover.get("poster_artifact", "")),
        sign(cover.get("video_artifact", "")),
        sign(cta.get("artifact", "")),
    )
    signed_slides = await asyncio.gather(
        *(sign(s.get("artifact", "")) for s in slides)
    )

    return {
        "run_id": run_id,
        "expires_in": ARTIFACT_URL_TTL_S,
        "caption": bundle.get("caption", ""),
        "cover": {
            "poster": poster,
            "video": video,
            # No clip could be sourced, so the "cover" is a still. The viewer
            # must not render a <video> element for it.
            "is_still": bool(cover.get("used_fallback_image")),
            "duration_s": cover.get("duration_s", 0),
        },
        "slides": [
            {**(signed or {}), "index": slide.get("index")}
            for slide, signed in zip(slides, signed_slides)
        ],
        "cta": {**(cta_img or {}), "cta_type": cta.get("cta_type"),
                "link_url": cta.get("link_url", "")},
        "ordered": bundle.get("ordered_artifacts", []),
    }


# ---------------------------------------------------------------------------
# Live events
# ---------------------------------------------------------------------------
def _sse(event: str, data: dict, seq: Optional[int] = None) -> str:
    """Format one SSE frame.

    The ``id:`` line is what makes reconnection free: EventSource replays it as
    Last-Event-ID, so the server knows exactly what the client already has and
    can resume without a gap or a duplicate.
    """
    lines = []
    if seq is not None:
        lines.append(f"id: {seq}")
    lines.append(f"event: {event}")
    lines.append(f"data: {json.dumps(data)}")
    return "\n".join(lines) + "\n\n"


@router.get("/runs/{run_id}/events")
async def run_events(
    run_id: str,
    request: Request,
    after: int = Query(0, ge=0),
    _identity: Identity = Depends(current_identity),
) -> StreamingResponse:
    """Stream a run's timeline: history first, then live.

    The subscribe-then-replay order is load-bearing. Subscribing to the bus
    BEFORE reading persisted rows closes the window where an event fires
    between the read and the subscribe - in the other order that event is lost
    forever, and the client has no way to know.
    """
    cursor = after
    last_event_id = request.headers.get("last-event-id")
    if last_event_id and last_event_id.isdigit():
        cursor = max(cursor, int(last_event_id))

    async def stream() -> AsyncIterator[str]:
        async with BUS.subscribe(run_id) as queue:
            emitted = cursor
            try:
                history = await db.load_run_events(run_id, after=cursor)
            except Exception as exc:
                logger.warning("Could not replay run %s: %s", run_id, exc)
                history = []
            for row in history:
                emitted = max(emitted, int(row["seq"]))
                yield _sse("run", dict(row), seq=row["seq"])

            # The client now has everything the database knows about.
            yield _sse("synced", {"last_seq": emitted})

            while True:
                if await request.is_disconnected():
                    return
                try:
                    event = await asyncio.wait_for(
                        queue.get(), timeout=SSE_KEEPALIVE_S
                    )
                except asyncio.TimeoutError:
                    # A comment frame: keeps proxies from closing a stream that
                    # is legitimately silent while an image model works.
                    yield ": keepalive\n\n"
                    continue

                # Skip anything the replay already covered - a live event can
                # arrive while history is still being read.
                if event.seq <= emitted and event.kind != "gap":
                    continue
                emitted = max(emitted, event.seq)
                yield _sse("run", event.to_dict(), seq=event.seq)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            # Render's proxy buffers responses without this, which turns a
            # live trace into one burst at the end.
            "X-Accel-Buffering": "no",
        },
    )


# ---------------------------------------------------------------------------
# Decisions and recovery
# ---------------------------------------------------------------------------
@router.post("/runs/{run_id}/verdict")
async def post_verdict(
    run_id: str,
    payload: VerdictRequest,
    identity: Identity = Depends(current_identity),
) -> dict:
    """Approve or reject, through the same code path as the Telegram links.

    The 409 is the interesting case: it means someone else decided this run
    first - almost always the same person, from their phone. It is a normal
    outcome, not an error, and the UI shows the decision rather than a failure.
    """
    outcome = await submit_verdict(
        run_id,
        payload.status,
        payload.feedback,
        reviewer=identity.email,
        source="web",
    )
    if outcome.ok:
        return {"result": "accepted", "run_id": run_id, "status": outcome.status}

    codes = {
        "not_pending": 409,
        "feedback_required": 400,
        "invalid_status": 400,
        "incomplete": 500,
        "db_error": 503,
    }
    raise HTTPException(
        codes.get(outcome.result, 500),
        {
            "code": outcome.result,
            "message": outcome.detail or "No review is pending for this run.",
            "reject_question": REJECT_QUESTION,
        },
    )


@router.post("/runs/{run_id}/resume")
async def resume_run(
    run_id: str, identity: Identity = Depends(current_identity)
) -> dict:
    """Re-enter an interrupted run at the phase it stopped in."""
    try:
        started = await resume_interrupted_run(run_id, requested_by=identity.email)
    except RunRefused as exc:
        raise HTTPException(409, {"code": exc.code, "message": exc.detail}) from exc
    if not started:
        raise HTTPException(
            409,
            {
                "code": "not_resumable",
                "message": "That run is unknown or already running.",
            },
        )
    return {"result": "resuming", "run_id": run_id}


@router.post("/runs/{run_id}/cancel")
async def cancel(run_id: str, _identity: Identity = Depends(current_identity)) -> dict:
    """Stop a run this process is driving."""
    if not await cancel_run(run_id):
        raise HTTPException(
            409,
            {"code": "not_running", "message": "That run is not currently running."},
        )
    return {"result": "cancelling", "run_id": run_id}


# ---------------------------------------------------------------------------
# Queue and metadata
# ---------------------------------------------------------------------------
@router.get("/queue")
async def list_queue(
    limit: int = Query(50, ge=1, le=200),
    _identity: Identity = Depends(current_identity),
) -> dict:
    """Stories the scheduler has fetched but nobody has turned into a carousel."""
    return {"items": await db.list_queued_news(limit=limit)}


@router.post("/queue", status_code=status.HTTP_201_CREATED)
async def enqueue(
    payload: EnqueueRequest, _identity: Identity = Depends(current_identity)
) -> dict:
    """Add an article URL to the queue without running it now."""
    from app.runs.service import fetch_url_item

    try:
        item = await fetch_url_item(payload.url)
    except RunRefused as exc:
        raise HTTPException(400, {"code": exc.code, "message": exc.detail}) from exc
    return await db.enqueue_news(item)


@router.get("/meta")
async def meta(_identity: Identity = Depends(current_identity)) -> dict:
    """Agent and phase vocabulary, so the UI cannot drift from the pipeline.

    The frontend needs these names to group a trace and draw the phase rail.
    Hard-coding them in TypeScript means a rename here produces blank rows in
    the UI with no error anywhere; serving them lets the client assert instead.
    """
    return {
        "agents": [
            AGENT_RESEARCH, AGENT_PLANNER, AGENT_FIRST_PAGE_VISUAL,
            AGENT_PHRASING, AGENT_TEMPLATE_DESIGN, AGENT_CTA,
            AGENT_STITCH_VERIFY, AGENT_REVIEW_DISPATCHER,
            AGENT_FEEDBACK_ROUTER, AGENT_PUBLISHER, AGENT_LEARNER,
        ],
        "reworkable_agents": list(REWORKABLE_AGENTS),
        "phases": [
            PHASE_GENERATE, PHASE_QA, PHASE_REVIEW,
            PHASE_REWORK, PHASE_PUBLISH, PHASE_DONE,
        ],
        "statuses": [
            db.RUN_STATUS_RUNNING, db.RUN_STATUS_AWAITING_REVIEW,
            db.RUN_STATUS_DONE, db.RUN_STATUS_INTERRUPTED,
            db.RUN_STATUS_FAILED, db.RUN_STATUS_CANCELLED,
        ],
        "reject_question": REJECT_QUESTION,
        "max_slides": settings.max_carousel_slides,
        "publish_configured": bool(settings.ig_user_id and settings.ig_access_token),
    }


__all__ = ["router"]
