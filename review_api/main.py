"""FastAPI review surface — the human side of the Carousel Factory review gate.

The Review Dispatcher pauses each pipeline run on a ``LongRunningFunctionTool``
(``await_human_review``) after mailing the reviewers Approve/Reject links that
point here:

* ``GET  /review/{run_id}/approve`` — renders a CONFIRM page only. NO state
  changes on GET: corporate mail scanners prefetch links, so a click in the
  mail must never decide anything by itself.
* ``GET  /review/{run_id}/reject``  — same, with a REQUIRED feedback box
  asking what exactly is not good (first visual / texts / slide design /
  CTA / structure / other).
* ``POST /review/{run_id}/submit``  — the actual decision. Loads the pending
  review (session id + paused function call id) via
  :func:`app.services.db.load_pending_review`, stores the verdict with
  :func:`app.services.db.record_verdict`, builds a ``types.Content`` carrying
  a ``types.Part(function_response=...)`` addressed to the original
  ``await_human_review`` call id, and resumes the pipeline in a background
  task via ``app.agent.build_runner().run_async(...)`` — the HTTP response
  returns immediately with a "rework/publish is underway" page.

Resume addressing convention (must match ``fetcher.fetch_news``):
``app_name = settings.app_name``, ``user_id = PIPELINE_USER_ID``,
``session_id = run_id`` (the stored ``pending_reviews.session_id`` is
authoritative). Verified against installed google-adk 2.7.0
(``google/adk/runners.py``): the root agent is a ``BaseAgent`` orchestrator,
so ``Runner.run_async(new_message=<function-response Content>)`` starts a NEW
invocation whose ``user_content`` is that function response — exactly what the
dispatcher's ``before_agent_callback`` consumes to write the verdict to state.

Run locally::

    "%CD%\\.venv\\Scripts\\python" -m uvicorn review_api.main:app --port 8080
"""

from __future__ import annotations

import asyncio
import html
import logging
import os
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Optional
from urllib.parse import quote

from fastapi import FastAPI, Form
from fastapi.responses import HTMLResponse, JSONResponse

from app.config import settings
from app.services import db

logger = logging.getLogger(__name__)

# The tool name the FunctionResponse must carry (google-adk 2.7.0 FunctionTool
# names tools after the wrapped callable). Imported from the dispatcher when
# possible so the two sides can never drift; the literal fallback keeps this
# lightweight API importable even when heavy agent deps are missing.
try:  # pragma: no cover - trivial import wiring
    from app.agents.review_dispatcher import AWAIT_REVIEW_TOOL_NAME
except Exception:  # pragma: no cover - env dependent
    AWAIT_REVIEW_TOOL_NAME = "await_human_review"

# Fixed pipeline user id — sessions are addressed by (app_name, user_id,
# session_id); the fetcher seeds sessions with this user id.
try:  # pragma: no cover - trivial import wiring
    from fetcher.fetch_news import PIPELINE_USER_ID
except Exception:  # pragma: no cover - env dependent
    PIPELINE_USER_ID = "pipeline"

#: Safety cap for one resumed pipeline leg. A resume ends either at the next
#: review pause (rework path) or at ``done`` (publish path); an hour covers
#: image generation + IG publishing with a wide margin.
RESUME_TIMEOUT_S = 3600.0

#: Strong references to in-flight resume tasks (asyncio only keeps weak ones).
_resume_tasks: set["asyncio.Task[None]"] = set()

_REJECT_QUESTION = (
    "What exactly is not good? "
    "(first visual / texts / slide design / CTA / structure / other)"
)


# ---------------------------------------------------------------------------
# HTML rendering (self-contained pages; no external assets)
# ---------------------------------------------------------------------------
_STYLE = """
  :root { color-scheme: light; }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font: 16px/1.55 -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    background: #f2f2f0; color: #1c1c1c; min-height: 100vh;
    display: flex; align-items: center; justify-content: center; padding: 24px;
  }
  .card {
    background: #ffffff; border: 1px solid #e2e2de; border-radius: 12px;
    padding: 32px 36px; max-width: 560px; width: 100%;
    box-shadow: 0 2px 14px rgba(0, 0, 0, 0.06);
  }
  h1 { font-size: 22px; margin-bottom: 6px; }
  p { margin: 10px 0; color: #3a3a3a; }
  .run { font-family: Consolas, "Courier New", monospace; font-size: 13px;
         color: #6b6b6b; word-break: break-all; }
  .badge { display: inline-block; font-size: 12px; font-weight: 700;
           letter-spacing: 0.06em; text-transform: uppercase;
           padding: 3px 10px; border-radius: 999px; margin-bottom: 14px; }
  .badge.approve { background: #e3f4e6; color: #1e6b2e; }
  .badge.reject { background: #fde7e4; color: #a12318; }
  .badge.info { background: #e8eef8; color: #2b4d86; }
  label { display: block; font-weight: 600; margin: 18px 0 6px; }
  textarea {
    width: 100%; min-height: 120px; resize: vertical; font: inherit;
    padding: 10px 12px; border: 1px solid #c9c9c4; border-radius: 8px;
  }
  textarea:focus { outline: 2px solid #f0862b; border-color: #f0862b; }
  .hint { font-size: 13px; color: #6b6b6b; margin-top: 4px; }
  .error { background: #fde7e4; color: #a12318; border: 1px solid #f5b8b1;
           border-radius: 8px; padding: 10px 14px; margin: 14px 0; }
  button {
    margin-top: 22px; width: 100%; font: inherit; font-weight: 700;
    padding: 12px 16px; border: 0; border-radius: 8px; cursor: pointer;
    color: #ffffff;
  }
  button.approve { background: #2e8540; }
  button.approve:hover { background: #256e35; }
  button.reject { background: #c0311f; }
  button.reject:hover { background: #a52717; }
"""


def _page(title: str, body_html: str, status_code: int = 200) -> HTMLResponse:
    """Wrap a body fragment in the shared self-contained HTML shell.

    Args:
        title: The ``<title>`` text (escaped here).
        body_html: Pre-escaped inner HTML of the card.
        status_code: HTTP status for the response.

    Returns:
        A complete :class:`HTMLResponse`.
    """
    doc = (
        "<!doctype html>\n"
        '<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        '<meta name="robots" content="noindex, nofollow">\n'
        f"<title>{html.escape(title)}</title>\n"
        f"<style>{_STYLE}</style>\n</head>\n<body>\n"
        f'<main class="card">\n{body_html}\n</main>\n</body>\n</html>\n'
    )
    return HTMLResponse(content=doc, status_code=status_code)


def _confirm_page(run_id: str, status: str, error: str = "") -> HTMLResponse:
    """Render the approve/reject CONFIRM page (a form; changes nothing itself).

    Args:
        run_id: The run being reviewed (display + form action).
        status: ``"approved"`` or ``"rejected"`` — decides the form variant.
        error: Optional validation error to show (e.g. missing reject feedback).

    Returns:
        The confirm page; HTTP 400 when re-rendered with a validation error.
    """
    rid = html.escape(run_id)
    action = f"/review/{quote(run_id, safe='')}/submit"
    error_html = f'<div class="error">{html.escape(error)}</div>' if error else ""

    if status == "approved":
        badge = '<span class="badge approve">Approve</span>'
        heading = "Approve this carousel?"
        blurb = (
            "Confirming will resume the pipeline and <strong>publish the "
            "carousel to Instagram</strong>. You will get a confirmation "
            "mail with the post link."
        )
        field = (
            '<label for="feedback">Any feedback for next time? (optional)</label>'
            '<textarea id="feedback" name="feedback" '
            'placeholder="Optional — noted for future carousels."></textarea>'
        )
        button = '<button type="submit" class="approve">Approve &amp; publish</button>'
    else:
        badge = '<span class="badge reject">Reject</span>'
        heading = "Reject this carousel?"
        blurb = (
            "Confirming will send the carousel back for <strong>rework</strong>. "
            "Your feedback decides which parts get redone, so please be specific."
        )
        field = (
            f'<label for="feedback">{html.escape(_REJECT_QUESTION)}</label>'
            '<textarea id="feedback" name="feedback" required autofocus '
            'placeholder="e.g. the first visual is not good — the clip is '
            'unrelated to the story"></textarea>'
            '<p class="hint">Feedback is required to reject — it is routed '
            "verbatim to the agents that must redo their work.</p>"
        )
        button = (
            '<button type="submit" class="reject">Reject &amp; send back '
            "for rework</button>"
        )

    body = (
        f"{badge}\n<h1>{heading}</h1>\n"
        f'<p class="run">run {rid}</p>\n'
        f"<p>{blurb}</p>\n{error_html}\n"
        f'<form method="post" action="{action}">\n'
        f'<input type="hidden" name="status" value="{status}">\n'
        f"{field}\n{button}\n</form>"
    )
    return _page(f"Review {run_id}", body, status_code=400 if error else 200)


def _done_page(run_id: str, status: str) -> HTMLResponse:
    """Render the post-submit page (the resume runs in the background)."""
    rid = html.escape(run_id)
    if status == "approved":
        badge = '<span class="badge approve">Approved</span>'
        heading = "Approved — publishing is underway"
        blurb = (
            "The pipeline has resumed and is publishing the carousel to "
            "Instagram. A confirmation mail with the post link will arrive "
            "shortly. You can close this page."
        )
    else:
        badge = '<span class="badge reject">Rejected</span>'
        heading = "Rejected — rework is underway"
        blurb = (
            "The pipeline has resumed: your feedback is being routed to the "
            "responsible agents and the affected parts are being redone. A "
            "fresh review mail will arrive when the new version is ready. "
            "You can close this page."
        )
    body = f'{badge}\n<h1>{heading}</h1>\n<p class="run">run {rid}</p>\n<p>{blurb}</p>'
    return _page(f"Review {run_id}", body)


def _not_pending_page(run_id: str) -> HTMLResponse:
    """Render the graceful unknown-run / double-submit page."""
    rid = html.escape(run_id)
    body = (
        '<span class="badge info">Nothing pending</span>\n'
        "<h1>No review is pending for this run</h1>\n"
        f'<p class="run">run {rid}</p>\n'
        "<p>Either this verdict was already submitted (the pipeline is "
        "processing it), or the link is stale or unknown. If a newer review "
        "mail exists for this run, use the links from that mail instead. "
        "No action was taken now.</p>"
    )
    return _page(f"Review {run_id}", body)


def _error_page(run_id: str, detail: str) -> HTMLResponse:
    """Render a 500 page for infrastructure errors (details go to the log)."""
    rid = html.escape(run_id)
    body = (
        '<span class="badge reject">Error</span>\n'
        "<h1>The review service hit a problem</h1>\n"
        f'<p class="run">run {rid}</p>\n'
        f"<p>{html.escape(detail)}</p>\n"
        "<p>Nothing was decided. Re-open the link from the review mail to "
        "try again once the problem is fixed.</p>"
    )
    return _page("Review service error", body, status_code=500)


# ---------------------------------------------------------------------------
# Pipeline resume (background task)
# ---------------------------------------------------------------------------
def _build_resume_content(
    function_call_id: str, status: str, feedback: str
) -> Any:
    """Build the ``types.Content`` that answers the paused review tool call.

    google-genai types (verified in the installed package):
    ``types.FunctionResponse(id=..., name=..., response=dict)`` wrapped in
    ``types.Part(function_response=...)`` inside a role-``user`` Content.

    Args:
        function_call_id: The original ``await_human_review`` call id.
        status: ``"approved"`` or ``"rejected"``.
        feedback: Reviewer feedback text (may be empty on approve).

    Returns:
        The ``types.Content`` to pass as ``Runner.run_async(new_message=...)``.
    """
    from google.genai import types  # deferred: keep module import light

    return types.Content(
        role="user",
        parts=[
            types.Part(
                function_response=types.FunctionResponse(
                    id=function_call_id,
                    name=AWAIT_REVIEW_TOOL_NAME,
                    response={"status": status, "feedback": feedback},
                )
            )
        ],
    )


async def _restore_pending_review(
    run_id: str, session_id: str, function_call_id: str
) -> None:
    """Re-persist a consumed pending review after a failed/timed-out resume.

    ``submit_verdict`` consumes the ``pending_reviews`` row before the resume
    is known to have succeeded; if the resume then dies the run would be
    stranded with no retry path (the mail links would render "Nothing
    pending"). Restoring the row lets the reviewer simply re-submit — a
    duplicate resume is harmless because the dispatcher's consumed-id guard
    turns it into a no-op for an already-recorded verdict.

    The restore is skipped when a NEWER pending row already exists (a resumed
    leg that reached the next review pause saved a fresh function call id
    before failing later) so the newer id is never clobbered by the stale one.
    """
    try:
        if await db.load_pending_review(run_id) is None:
            await db.save_pending_review(run_id, session_id, function_call_id)
            logger.info(
                "Restored pending review for run %s so the reviewer can "
                "re-submit the verdict.",
                run_id,
            )
        else:
            logger.info(
                "A newer pending review already exists for run %s; "
                "not restoring the consumed one.",
                run_id,
            )
    except Exception:
        logger.exception(
            "Could not restore the pending review for run %s (session %s, "
            "call %s); the run may need a manual pending_reviews insert.",
            run_id,
            session_id,
            function_call_id,
        )


async def _resume_pipeline(
    run_id: str,
    session_id: str,
    function_call_id: str,
    status: str,
    feedback: str,
) -> None:
    """Resume the paused run with the reviewer's verdict (background work).

    Builds a fresh Runner (``app.agent.build_runner`` — same app_name and
    session database as the paused run) and streams the resumed invocation to
    completion: rejected verdicts end at the next review pause, approved ones
    at ``done``. Errors are logged, never raised — the HTTP response has long
    been sent by the time this runs. On failure or timeout the consumed
    ``pending_reviews`` row is restored so the reviewer can re-submit.

    Args:
        run_id: The run being resumed (logging only).
        session_id: The ADK session id from ``pending_reviews``.
        function_call_id: The paused ``await_human_review`` call id.
        status: ``"approved"`` or ``"rejected"``.
        feedback: The reviewer's feedback text.
    """
    logger.info(
        "Resuming run %s (session %s, call %s) with verdict '%s'.",
        run_id,
        session_id,
        function_call_id,
        status,
    )
    runner = None
    try:
        # Deferred import: building the agent tree is heavy and needs the full
        # agent/tool dependency stack — the API itself must start without it.
        from app.agent import build_runner

        runner = build_runner()
        content = _build_resume_content(function_call_id, status, feedback)

        async def _consume() -> int:
            count = 0
            async for event in runner.run_async(
                user_id=PIPELINE_USER_ID,
                session_id=session_id,
                new_message=content,
            ):
                count += 1
                author = getattr(event, "author", "") or "?"
                if getattr(event, "error_message", None):
                    logger.warning(
                        "Run %s event from %s reported an error: %s",
                        run_id,
                        author,
                        event.error_message,
                    )
                elif getattr(event, "long_running_tool_ids", None):
                    logger.info(
                        "Run %s paused again for review (author %s).",
                        run_id,
                        author,
                    )
            return count

        events = await asyncio.wait_for(_consume(), timeout=RESUME_TIMEOUT_S)
        logger.info(
            "Resume for run %s finished after %d event(s).", run_id, events
        )
    except asyncio.TimeoutError:
        logger.error(
            "Resume for run %s timed out after %.0f s; check the run logs.",
            run_id,
            RESUME_TIMEOUT_S,
        )
        await _restore_pending_review(run_id, session_id, function_call_id)
    except Exception:
        logger.exception("Resume for run %s failed.", run_id)
        await _restore_pending_review(run_id, session_id, function_call_id)
    finally:
        if runner is not None:
            try:
                await runner.close()
            except Exception as exc:
                logger.warning("Runner close failed for run %s: %s", run_id, exc)


def _spawn_resume(
    run_id: str,
    session_id: str,
    function_call_id: str,
    status: str,
    feedback: str,
) -> None:
    """Fire the resume as a fire-and-forget asyncio task (strongly referenced)."""
    task = asyncio.get_running_loop().create_task(
        _resume_pipeline(run_id, session_id, function_call_id, status, feedback),
        name=f"resume-{run_id}",
    )
    _resume_tasks.add(task)
    task.add_done_callback(_resume_tasks.discard)


# ---------------------------------------------------------------------------
# App + endpoints
# ---------------------------------------------------------------------------
@asynccontextmanager
async def _lifespan(_: FastAPI) -> AsyncIterator[None]:
    """App lifespan: nothing at startup; drain resumes + close DB on shutdown."""
    yield
    if _resume_tasks:
        logger.info(
            "Shutdown: waiting up to 30 s for %d in-flight resume task(s).",
            len(_resume_tasks),
        )
        _, pending = await asyncio.wait(set(_resume_tasks), timeout=30.0)
        for task in pending:
            task.cancel()
    try:
        await db.close_pool()
    except Exception as exc:  # pool may never have been opened
        logger.warning("DB pool close failed: %s", exc)


app = FastAPI(
    title="Carousel Factory — Review API",
    description=(
        "Human review gate: confirm pages for the Approve/Reject mail links "
        "and the submit endpoint that resumes the paused pipeline."
    ),
    lifespan=_lifespan,
)


async def _load_pending(run_id: str) -> Optional[dict]:
    """Load the pending review row; raises only on infrastructure errors."""
    return await db.load_pending_review(run_id)


async def _render_confirm(run_id: str, status: str) -> HTMLResponse:
    """Shared GET handler body: confirm page if a review pends, else info page."""
    try:
        pending = await _load_pending(run_id)
    except Exception:
        logger.exception("Could not load pending review for run %s.", run_id)
        return _error_page(
            run_id,
            "The review database is unreachable or not configured "
            "(DATABASE_URL). Details are in the service log.",
        )
    if pending is None:
        return _not_pending_page(run_id)
    return _confirm_page(run_id, status)


@app.get("/", include_in_schema=False)
async def index() -> JSONResponse:
    """Tiny service banner (also a convenient liveness probe target)."""
    return JSONResponse({"service": "carousel-factory-review-api", "status": "ok"})


@app.get("/healthz", include_in_schema=False)
async def healthz() -> JSONResponse:
    """Cloud Run / uptime health check — no dependencies touched."""
    return JSONResponse({"status": "ok"})


@app.get("/review/{run_id}/approve", response_class=HTMLResponse)
async def approve_page(run_id: str) -> HTMLResponse:
    """Render the approve CONFIRM page. NO state change happens on GET.

    Mail scanners prefetch links, so this endpoint only reads: the actual
    approval is the POST the human makes from this page.
    """
    return await _render_confirm(run_id, "approved")


@app.get("/review/{run_id}/reject", response_class=HTMLResponse)
async def reject_page(run_id: str) -> HTMLResponse:
    """Render the reject CONFIRM page (required feedback). Read-only GET."""
    return await _render_confirm(run_id, "rejected")


@app.post("/review/{run_id}/submit", response_class=HTMLResponse)
async def submit_verdict(
    run_id: str,
    status: str = Form(...),
    feedback: str = Form(""),
) -> HTMLResponse:
    """Record the human verdict and resume the paused pipeline.

    Steps: validate (reject REQUIRES non-empty feedback), load + consume the
    ``pending_reviews`` row, store the verdict in the ``feedback`` table, then
    fire the pipeline resume as a background task and answer immediately with
    a done page. Unknown run ids and double-submits (row already consumed)
    get a graceful info page — no errors, no duplicate resumes.

    Args:
        run_id: The run the mail links point at.
        status: Form field — ``"approved"`` or ``"rejected"``.
        feedback: Form field — reviewer text; optional on approve only.

    Returns:
        The done page (or a re-rendered form / info / error page).
    """
    clean_status = (status or "").strip().lower()
    clean_feedback = (feedback or "").strip()

    if clean_status not in ("approved", "rejected"):
        return _page(
            "Invalid review submission",
            '<span class="badge reject">Error</span>\n'
            "<h1>Invalid verdict</h1>\n"
            f'<p class="run">run {html.escape(run_id)}</p>\n'
            "<p>The verdict must be either <strong>approved</strong> or "
            "<strong>rejected</strong>. Use the buttons from the review "
            "mail links.</p>",
            status_code=400,
        )

    if clean_status == "rejected" and not clean_feedback:
        return _confirm_page(
            run_id,
            "rejected",
            error=(
                "Feedback is required to reject. Please say what exactly is "
                "not good (first visual / texts / slide design / CTA / "
                "structure / other) so the right agents redo their work."
            ),
        )

    try:
        pending = await _load_pending(run_id)
    except Exception:
        logger.exception("Could not load pending review for run %s.", run_id)
        return _error_page(
            run_id,
            "The review database is unreachable or not configured "
            "(DATABASE_URL). Details are in the service log.",
        )
    if pending is None:
        # Unknown run id OR the review was already submitted — either way
        # there is nothing to resume, so answer gracefully.
        return _not_pending_page(run_id)

    session_id = str(pending.get("session_id") or "")
    function_call_id = str(pending.get("function_call_id") or "")
    if not session_id or not function_call_id:
        logger.error(
            "pending_reviews row for run %s is incomplete: %r", run_id, pending
        )
        return _error_page(
            run_id,
            "The stored pending review is incomplete and cannot be resumed.",
        )

    # Consume the pending review NOW so a double-submit (retry, second tab)
    # finds nothing pending instead of resuming twice. The dispatcher clears
    # it again on resume — clearing is idempotent. If the background resume
    # fails or times out, _resume_pipeline restores this row so the reviewer
    # can re-submit instead of being stranded on "Nothing pending".
    try:
        await db.clear_pending_review(run_id)
    except Exception as exc:
        logger.warning(
            "Could not consume pending review for run %s (%s); continuing.",
            run_id,
            exc,
        )

    # Store the verdict for the feedback history / learner. Best-effort: a
    # feedback-table hiccup must not block the resume (the dispatcher records
    # the verdict in session state regardless).
    try:
        await db.record_verdict(run_id, clean_status, clean_feedback)
    except Exception as exc:
        logger.warning("record_verdict failed for run %s: %s", run_id, exc)

    _spawn_resume(run_id, session_id, function_call_id, clean_status, clean_feedback)
    logger.info(
        "Verdict '%s' accepted for run %s; resume dispatched in background.",
        clean_status,
        run_id,
    )
    return _done_page(run_id, clean_status)


def main() -> None:
    """Run the review API with uvicorn (Cloud Run reads $PORT; default 8080)."""
    import uvicorn

    uvicorn.run(
        "review_api.main:app",
        host="0.0.0.0",
        port=int(os.getenv("PORT", "8080")),
        log_level="info",
    )


if __name__ == "__main__":
    main()
