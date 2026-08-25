"""Deciding a human verdict on a paused run - the one implementation.

Two surfaces can decide the same run: the Approve/Reject links in a Telegram
message, and the buttons in the web console. They must not be two
implementations of "what a verdict means", because the interesting part is not
the happy path - it is the race, the double-submit, and the restore-on-failure.
Duplicating that is how two surfaces quietly drift apart.

So this module owns the decision and returns a plain ``VerdictOutcome``; each
surface owns only how to render it. Nothing here imports a web framework, which
is what lets the scheduler and the CLI decide a verdict too.

The single-winner property, since it is the whole point: the pending review is
consumed with ``db.claim_pending_review``, a single ``DELETE ... RETURNING``.
Postgres serialises two concurrent deletes, so exactly one caller receives the
row and every other caller receives ``None`` and resumes nothing. An earlier
load-then-clear pair here let both callers pass the "is it pending?" check and
resume the same invocation twice - which publishes the carousel to Instagram
twice. See ``tests/test_review_verdict.py``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal, Optional

from app.review.resume import spawn_resume
from app.services import db

logger = logging.getLogger(__name__)

#: The question a reject must answer. The wording is not cosmetic: these are the
#: categories ``feedback_router`` maps onto rework targets, so steering the
#: reviewer into this vocabulary directly improves which agents get re-run.
REJECT_QUESTION = (
    "What exactly is not good? "
    "(facts / first visual / texts / slide design / CTA / structure / other)"
)

REJECT_FEEDBACK_REQUIRED_MESSAGE = (
    "Feedback is required to reject. Please say what exactly is not good "
    "(facts / first visual / texts / slide design / CTA / structure / other) "
    "so the right agents redo their work."
)

#: Where a verdict came from. Recorded on the feedback row, because with two
#: surfaces "approved" alone no longer says who decided.
VerdictSource = Literal["telegram", "web", "api"]

VerdictResult = Literal[
    "accepted",           # the caller won the claim; a resume is under way
    "not_pending",        # unknown run, already decided, or lost the race
    "invalid_status",     # neither "approved" nor "rejected"
    "feedback_required",  # rejected with no feedback text
    "incomplete",         # the stored row could never be resumed
    "db_error",           # the database is unreachable / unconfigured
]


@dataclass(frozen=True)
class VerdictOutcome:
    """What happened to a verdict submission.

    Callers branch on ``result``, never on ``detail`` - ``detail`` is prose for
    a human and is expected to be reworded.
    """

    result: VerdictResult
    run_id: str
    status: str = ""
    feedback: str = ""
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.result == "accepted"


async def pending_review(run_id: str) -> Optional[dict]:
    """Read the pending review WITHOUT consuming it.

    For rendering a confirmation page only. Deciding must go through
    :func:`submit_verdict`, which consumes the row atomically.
    """
    return await db.load_pending_review(run_id)


async def submit_verdict(
    run_id: str,
    status: str,
    feedback: str,
    *,
    reviewer: str = "",
    source: VerdictSource = "telegram",
) -> VerdictOutcome:
    """Record a human verdict and resume the paused run.

    Order matters and is load-bearing:

    1. Validate. A rejected-without-feedback submission must be re-renderable,
       so nothing is consumed before the input is known to be usable.
    2. Claim the pending review atomically - this is the single-winner gate.
    3. Record the verdict (best effort; a feedback-table hiccup must not block
       the resume, since the dispatcher writes the verdict to session state
       regardless).
    4. Spawn the resume in the background and return immediately. The caller
       answers the human now; the pipeline runs for minutes afterwards.

    Args:
        run_id: The run being decided.
        status: ``"approved"`` or ``"rejected"`` (case/space tolerant).
        feedback: Reviewer text. Required to reject, optional to approve.
        reviewer: Who decided - an email from the web console, empty for a
            Telegram link (those are capability URLs and carry no identity).
        source: Which surface decided.

    Returns:
        A :class:`VerdictOutcome`. Never raises for an expected condition;
        infrastructure failures come back as ``db_error``.
    """
    clean_status = (status or "").strip().lower()
    clean_feedback = (feedback or "").strip()

    if clean_status not in ("approved", "rejected"):
        return VerdictOutcome(
            result="invalid_status",
            run_id=run_id,
            detail="The verdict must be either 'approved' or 'rejected'.",
        )

    if clean_status == "rejected" and not clean_feedback:
        return VerdictOutcome(
            result="feedback_required",
            run_id=run_id,
            status=clean_status,
            detail=REJECT_FEEDBACK_REQUIRED_MESSAGE,
        )

    try:
        claimed = await db.claim_pending_review(run_id)
    except Exception:
        logger.exception("Could not claim pending review for run %s.", run_id)
        return VerdictOutcome(
            result="db_error",
            run_id=run_id,
            detail=(
                "The review database is unreachable or not configured "
                "(DATABASE_URL). Details are in the service log."
            ),
        )

    if claimed is None:
        return VerdictOutcome(result="not_pending", run_id=run_id)

    session_id = str(claimed.get("session_id") or "")
    function_call_id = str(claimed.get("function_call_id") or "")
    if not session_id or not function_call_id:
        # The claim already consumed the row, and that is the right outcome: an
        # incomplete row can never be resumed, so leaving it would only invite
        # the reviewer to click forever. The dispatcher writes a fresh row on
        # the next review round.
        logger.error(
            "pending_reviews row for run %s was incomplete and has been "
            "discarded: %r",
            run_id,
            claimed,
        )
        return VerdictOutcome(
            result="incomplete",
            run_id=run_id,
            detail="The stored pending review is incomplete and cannot be resumed.",
        )

    try:
        await db.record_verdict(
            run_id,
            clean_status,
            clean_feedback,
            decided_by=reviewer,
            source=source,
        )
    except Exception as exc:
        logger.warning("record_verdict failed for run %s: %s", run_id, exc)

    spawn_resume(run_id, session_id, function_call_id, clean_status, clean_feedback)
    logger.info(
        "Verdict '%s' accepted for run %s from %s%s; resume dispatched.",
        clean_status,
        run_id,
        source,
        f" ({reviewer})" if reviewer else "",
    )
    return VerdictOutcome(
        result="accepted",
        run_id=run_id,
        status=clean_status,
        feedback=clean_feedback,
    )


__all__ = [
    "REJECT_FEEDBACK_REQUIRED_MESSAGE",
    "REJECT_QUESTION",
    "VerdictOutcome",
    "VerdictResult",
    "VerdictSource",
    "pending_review",
    "submit_verdict",
]
