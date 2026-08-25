"""HTML review pages - the surface a Telegram Approve/Reject link opens.

Presentation only. The decision itself lives in :mod:`app.review.verdict`, so
this module and the web console's JSON endpoint cannot drift apart on what a
verdict means, on the single-winner claim, or on restore-after-failure. Here we
only map a :class:`~app.review.verdict.VerdictOutcome` onto a page.

Routing rules that are not arbitrary:

* ``GET  /review/{run_id}/approve`` and ``.../reject`` render a CONFIRM page and
  change NOTHING. Mail scanners and chat clients prefetch links, so a GET must
  never be able to decide anything by itself.
* ``GET  .../reject`` asks a REQUIRED question, and the wording matches the
  categories ``feedback_router`` maps onto rework targets.
* ``POST /review/{run_id}/submit`` is the only mutating route.

These routes stay reachable without authentication: the links are opened from a
Telegram message where there is nobody to type a password. That is safe because
the URLs are capability URLs keyed on an unguessable ``run_id``, the GET pages
are inert, and the POST is single-use once the pending review is claimed.
"""

from __future__ import annotations

import html
import logging
from typing import Optional
from urllib.parse import quote

from fastapi import APIRouter, Form
from fastapi.responses import HTMLResponse, JSONResponse

from app.review.verdict import (
    REJECT_QUESTION,
    VerdictOutcome,
    pending_review,
    submit_verdict,
)

logger = logging.getLogger(__name__)

router = APIRouter()


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
        status: ``"approved"`` or ``"rejected"`` - decides the form variant.
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
            'placeholder="Optional - noted for future carousels."></textarea>'
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
            f'<label for="feedback">{html.escape(REJECT_QUESTION)}</label>'
            '<textarea id="feedback" name="feedback" required autofocus '
            'placeholder="e.g. the first visual is not good - the clip is '
            'unrelated to the story"></textarea>'
            '<p class="hint">Feedback is required to reject - it is routed '
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
        heading = "Approved - publishing is underway"
        blurb = (
            "The pipeline has resumed and is publishing the carousel to "
            "Instagram. A confirmation mail with the post link will arrive "
            "shortly. You can close this page."
        )
    else:
        badge = '<span class="badge reject">Rejected</span>'
        heading = "Rejected - rework is underway"
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
# Routes
# ---------------------------------------------------------------------------
async def _render_confirm(run_id: str, status: str) -> HTMLResponse:
    """Render the confirm page, or explain that nothing is pending.

    Reads the pending review without consuming it - a GET must stay inert.
    """
    try:
        pending = await pending_review(run_id)
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


@router.get("/", include_in_schema=False)
async def index() -> JSONResponse:
    """Service banner (also doubles as a cheap reachability check)."""
    return JSONResponse({"service": "carousel-factory-review-api", "status": "ok"})


@router.get("/healthz", include_in_schema=False)
async def healthz() -> JSONResponse:
    """Liveness probe: touches no dependencies, so it cannot restart-loop."""
    return JSONResponse({"status": "ok"})


@router.get("/review/{run_id}/approve", response_class=HTMLResponse)
async def approve_page(run_id: str) -> HTMLResponse:
    """Confirm page for an approval. Changes nothing."""
    return await _render_confirm(run_id, "approved")


@router.get("/review/{run_id}/reject", response_class=HTMLResponse)
async def reject_page(run_id: str) -> HTMLResponse:
    """Confirm page for a rejection, with the required feedback box."""
    return await _render_confirm(run_id, "rejected")


@router.post("/review/{run_id}/submit", response_class=HTMLResponse)
async def submit_verdict_page(
    run_id: str,
    status: str = Form(...),
    feedback: str = Form(""),
) -> HTMLResponse:
    """Record the verdict and resume the run, then render the outcome.

    All of the logic is in :func:`app.review.verdict.submit_verdict`; what is
    left here is the mapping from outcome to page. ``source="telegram"`` because
    these pages are only ever reached from a review message - the web console
    posts its verdict to the JSON API with ``source="web"``.
    """
    outcome: VerdictOutcome = await submit_verdict(
        run_id, status, feedback, source="telegram"
    )

    if outcome.result == "accepted":
        return _done_page(run_id, outcome.status)
    if outcome.result == "not_pending":
        return _not_pending_page(run_id)
    if outcome.result == "feedback_required":
        return _confirm_page(run_id, "rejected", error=outcome.detail)
    if outcome.result == "invalid_status":
        return _page(
            "Invalid review submission",
            '<span class="badge reject">Error</span>'
            "<h1>Invalid verdict</h1>"
            f'<p class="run">run {html.escape(run_id)}</p>'
            "<p>The verdict must be either <strong>approved</strong> or "
            "<strong>rejected</strong>. Use the buttons from the review "
            "message.</p>",
            status_code=400,
        )
    # incomplete | db_error - both are infrastructure faults the reviewer
    # cannot act on, so they get the same 500 page with the detail spelled out.
    return _error_page(run_id, outcome.detail)


__all__ = ["router"]
