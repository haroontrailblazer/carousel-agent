"""The human review gate, where two surfaces and two task registries meet.

Everything here was confirmed against the real code and survived an
adversarial refutation pass. Each test asserts the CORRECT behaviour, so red
means the defect is still open.

The theme: ``app/review/verdict.py`` was built around one genuinely careful
idea - the pending review is consumed with a single ``DELETE ... RETURNING``
so exactly one caller wins, and ``tests/test_review_verdict.py`` pins that.
But the ``_decide_without_pause`` branch, added later for runs whose Telegram
notice failed, takes none of those precautions, and the two task registries
(``app.runs.service._run_tasks`` and ``app.review.resume._resume_tasks``) each
know only half of what is running.
"""

from __future__ import annotations

import asyncio
import inspect
import unittest
from unittest.mock import AsyncMock, patch

from app.agents import review_dispatcher as dispatcher_mod
from app.review import resume as resume_mod
from app.review import verdict as verdict_mod
from app.runs import service as service_mod
from app.services import db

RUN_ID = "run-review-gate"


def _halted_run(**overrides):
    """Patches for a run parked at review because the notice never sent."""
    base = {
        "_halted": AsyncMock(return_value=True),
        "claim": AsyncMock(return_value=None),
        "set_verdict": AsyncMock(return_value=True),
        "record": AsyncMock(return_value=None),
        "get_run": AsyncMock(return_value={"run_id": RUN_ID, "phase": "review"}),
        "count": AsyncMock(return_value=0),
    }
    base.update(overrides)
    return [
        patch.object(verdict_mod, "_halted_awaiting_review", base["_halted"]),
        patch.object(db, "claim_pending_review", base["claim"]),
        patch.object(db, "set_session_verdict", base["set_verdict"]),
        patch.object(db, "record_verdict", base["record"]),
        patch.object(db, "get_run", base["get_run"]),
        patch.object(db, "count_runs_since", base["count"]),
    ]


class DecideWithoutPauseIsSingleWinnerTests(unittest.TestCase):
    """Exactly one decision may drive the run - on this branch too.

    ``submit_verdict``'s docstring calls the atomic claim "the single-winner
    gate". On the halted branch there is no gate at all:

    * ``claim_pending_review``'s return value is discarded
      (``verdict.py:136``) - it is called only to drop a stale row;
    * ``set_session_verdict`` is an unconditional UPDATE, so every concurrent
      caller gets ``True``;
    * ``resume_interrupted_run`` checks ``run_id in active_run_ids()`` and
      then performs five awaits before registering the task, so two callers
      interleave straight through the check.

    Result: two ``_drive_run`` tasks on one ADK session, interleaved state
    writes, last-write-wins on the verdict, and - because the first task's
    done-callback pops ``_run_tasks[run_id]`` - no cancel handle for the
    survivor.
    """

    def test_two_surfaces_deciding_at_once_start_only_one_run(self) -> None:
        started: list[str] = []
        recorded: dict[str, dict] = {}

        async def conditional_write(run_id, app_name, user_id, verdict):
            """Model the UPDATE ... WHERE review_verdict IS NULL.

            Postgres serialises the two statements, so exactly one sees an
            absent verdict and writes. The await is load-bearing: it forces a
            scheduler switch so the two callers genuinely interleave.
            """
            # Check and write together, with no await between them - that is
            # what makes a single UPDATE atomic, and modelling it any other
            # way tests a database nobody ships.
            already = run_id in recorded
            if not already:
                recorded[run_id] = verdict
            await asyncio.sleep(0)
            return not already

        async def fake_resume(run_id, *, requested_by=""):
            # Model the real function's awaits between the active-run check
            # and the registration, which is what lets two callers through.
            await asyncio.sleep(0)
            started.append(requested_by)
            return True

        patches = _halted_run(set_verdict=conditional_write)
        for p in patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in patches])
        resume_patch = patch.object(service_mod, "resume_interrupted_run", fake_resume)
        resume_patch.start()
        self.addCleanup(resume_patch.stop)

        async def scenario():
            return await asyncio.gather(
                verdict_mod.submit_verdict(
                    RUN_ID, "approved", "", reviewer="web@x.co", source="web"
                ),
                verdict_mod.submit_verdict(
                    RUN_ID, "rejected", "the cover is wrong", source="telegram"
                ),
                return_exceptions=True,
            )

        outcomes = asyncio.run(scenario())
        accepted = [
            o for o in outcomes if getattr(o, "result", None) == "accepted"
        ]

        self.assertEqual(
            len(started),
            1,
            f"{len(started)} concurrent callers each re-entered the run "
            f"({started!r}). Two _drive_run tasks now share one ADK session: "
            "their state writes interleave, the verdict is last-write-wins "
            "(an 'approved' a moment after a 'rejected' silently replaces "
            "it), and the first task to finish pops _run_tasks[run_id], so "
            "Stop can no longer reach the one still running.",
        )
        self.assertEqual(
            len(accepted),
            1,
            "both callers were told their decision was accepted",
        )


class DecideWithoutPauseReportsTheTruthTests(unittest.TestCase):
    """Do not confirm a decision that started nothing."""

    def test_accepted_is_not_returned_when_the_run_was_not_re_entered(self) -> None:
        patches = _halted_run()
        for p in patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in patches])
        # resume_interrupted_run returns False when the run is already being
        # driven - e.g. the reviewer pressed "Send again" a moment earlier.
        resume_patch = patch.object(
            service_mod, "resume_interrupted_run", AsyncMock(return_value=False)
        )
        resume_patch.start()
        self.addCleanup(resume_patch.stop)

        outcome = asyncio.run(
            verdict_mod.submit_verdict(
                RUN_ID, "approved", "", reviewer="a@b.co", source="web"
            )
        )

        self.assertNotEqual(
            outcome.result,
            "accepted",
            "_decide_without_pause computes `started` and then ignores it, "
            "returning result='accepted' unconditionally (verdict.py:173). "
            "The console says the carousel was approved and is publishing; "
            "nothing was started. The verdict sits in session state with the "
            "run recorded 'running' and no process behind it.",
        )


class PauseOnlyWhenItIsAnswerableTests(unittest.TestCase):
    """Never pause on a call nobody can address.

    ``await_human_review`` returns ``None`` - which is exactly what makes ADK
    end the invocation paused - even when ``db.save_pending_review`` raised
    and no ``pending_reviews`` row exists. Without that row the review API
    cannot build a ``FunctionResponse`` addressed to this call, so no verdict
    from any surface can ever resume the run.

    It is worse than a plain stall: the orchestrator stamps
    ``K_REVIEW_NOTICE_FAILED=False`` on the pause (``orchestrator.py:566``),
    which disables the one fallback built for "the carousel is ready but the
    reviewer could not be told" (``verdict.py:230``). Every verdict submit
    answers ``not_pending``, and startup reconcile skips the run because
    ``ACTIVE_PHASES`` excludes ``review``.
    """

    def test_a_failed_pending_save_does_not_pause_the_run(self) -> None:
        class _Ctx:
            state = {"run_id": RUN_ID}
            session = type("S", (), {"id": RUN_ID})()
            function_call_id = "call-abc"

        with patch.object(
            db,
            "save_pending_review",
            AsyncMock(side_effect=ConnectionError("pooler dropped it")),
        ):
            result = asyncio.run(dispatcher_mod.await_human_review(_Ctx()))

        self.assertIsNotNone(
            result,
            "await_human_review swallowed the save failure and still "
            "returned None. google-adk builds no function-response event for "
            "a falsy long-running result, so the invocation ends PAUSED on a "
            "call that has no pending_reviews row to answer it - the run is "
            "stuck at phase 'review' forever. Return a non-falsy error dict "
            "(which suppresses the pause) or retry the save before giving up.",
        )

    def test_the_pause_is_not_reached_when_the_send_failed(self) -> None:
        """The 'only pause if the mail went out' rule is prose, not code."""
        source = inspect.getsource(dispatcher_mod.await_human_review)
        self.assertTrue(
            "sent" in source or "review_sent" in source,
            "Nothing in await_human_review checks whether "
            "send_review_request actually succeeded - the guard exists only "
            "as an instruction to a small utility model, on a round-2 prompt "
            "that simultaneously insists 'a NEW review request is required "
            "and expected'. When the model pauses after a failed send, a "
            "pending row is written and the run waits for a human who was "
            "never told anything. Have send_review_request stamp a temp: "
            "flag and have this tool refuse to pause without it.",
        )


class StopReachesBothRegistriesTests(unittest.TestCase):
    """Stop must finish the job in whichever registry it found the task.

    A rework driven by a rejection lives in
    ``app.review.resume._resume_tasks``, not ``_run_tasks``. ``cancel_run``
    knows that and calls ``cancel_resume`` - but then returns immediately,
    skipping the status write and the terminal event that the ``_run_tasks``
    path gets from ``_finish_badly``.
    """

    def test_stopping_a_rework_records_a_terminal_status(self) -> None:
        statuses: list[str] = []

        async def fake_set_status(run_id, status):
            statuses.append(status)

        patches = [
            patch.object(db, "set_run_status", fake_set_status),
            patch.object(db, "max_run_seq", AsyncMock(return_value=7)),
            patch.object(
                db,
                "get_run",
                AsyncMock(
                    return_value={"run_id": RUN_ID, "status": db.RUN_STATUS_RUNNING}
                ),
            ),
            patch.object(service_mod, "record_event", AsyncMock(return_value=None)),
            patch.object(resume_mod, "cancel_resume", lambda run_id: True),
        ]
        for p in patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in patches])

        stopped = asyncio.run(service_mod.cancel_run(RUN_ID))

        self.assertTrue(stopped)
        self.assertTrue(
            statuses,
            "cancel_run cancelled the resume task and returned True without "
            "writing any status. runs.status stays 'running' and no terminal "
            "event is recorded, so the timeline just stops and the console "
            "keeps a pulsing live task. task-actions.tsx shows Stop only for "
            "'running', so the user's only exit is pressing Stop again - "
            "which is itself a no-op until the cancelled task has finished.",
        )


class ConcurrencyCapCountsEveryDriverTests(unittest.TestCase):
    """A rework burning credits must count as an active run.

    ``active_run_ids`` reads ``_run_tasks`` only. When a run pauses for
    review its ``_drive_run`` task completes and is popped, so the process
    looks idle - while the resumed leg driving the rework and the Instagram
    publish lives in ``_resume_tasks``, invisible to ``_check_limits``.
    """

    def test_a_resume_in_flight_counts_toward_the_concurrency_cap(self) -> None:
        async def scenario() -> bool:
            async def forever() -> None:
                await asyncio.Event().wait()

            task = asyncio.get_running_loop().create_task(
                forever(), name=f"resume-{RUN_ID}"
            )
            resume_mod._resume_tasks.add(task)
            try:
                return RUN_ID in service_mod.active_run_ids()
            finally:
                task.cancel()
                resume_mod._resume_tasks.discard(task)

        counted = asyncio.run(scenario())

        self.assertTrue(
            counted,
            "A rework driven by app.review.resume is not counted by "
            "active_run_ids, so _check_limits lets a brand-new run start on "
            "top of it. MAX_CONCURRENT_RUNS=1 exists because this pipeline "
            "is ffmpeg- and image-heavy on one small instance, and the same "
            "blind spot lets two drivers touch one ADK session.",
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()


class VerdictIsConsumedOnceTests(unittest.TestCase):
    """A decision applies to the carousel it was made about, and no other.

    Within one invocation the run can loop review -> rework -> review, and the
    round-1 ``FunctionResponse`` is still the invocation's ``user_content`` on
    the round-2 entry. ``capture_verdict_on_resume`` guards against
    re-consuming it; ``set_verdict`` used to read the raw response instead of
    the consumed-aware one, so a model calling it on a SEND_MAIL turn would
    re-record the old decision. An old "approved" sends the reworked carousel
    straight to publish with no human ever seeing it.
    """

    def test_an_already_consumed_verdict_is_not_recorded_again(self) -> None:
        from google.genai import types

        from app.state import K_VERDICT

        response = types.FunctionResponse(
            id="call-round-1",
            name=dispatcher_mod.AWAIT_REVIEW_TOOL_NAME,
            response={"status": "approved", "feedback": ""},
        )
        content = types.Content(
            role="user", parts=[types.Part(function_response=response)]
        )

        class _Ctx:
            state = {
                "run_id": RUN_ID,
                # capture_verdict_on_resume already handled this one.
                "temp:review_consumed_call_ids": ["call-round-1"],
            }
            user_content = content

        ctx = _Ctx()
        result = asyncio.run(
            dispatcher_mod.set_verdict("approved", "", tool_context=ctx)
        )

        self.assertEqual(
            result.get("status"),
            "error",
            "set_verdict re-recorded a verdict that had already been "
            "consumed, so the round-1 decision now applies to a carousel "
            "that was reworked after it was made.",
        )
        self.assertIsNone(
            ctx.state.get(K_VERDICT),
            "K_VERDICT was overwritten with the stale decision",
        )
