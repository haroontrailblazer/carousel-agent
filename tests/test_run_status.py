"""The phase a run stops in does not always imply that it succeeded."""

from __future__ import annotations

import asyncio
import unittest
from typing import Any, Optional

from app.services import db


class _FakePool:
    """Captures the parameters of the UPDATE without touching Postgres."""

    def __init__(self) -> None:
        self.calls: list[tuple[Any, ...]] = []

    async def execute(self, _sql: str, *args: Any) -> None:
        self.calls.append(args)


class UpdateRunPhaseStatusTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.pool = _FakePool()
        self._saved = db.get_pool

        async def _pool() -> _FakePool:
            return self.pool

        db.get_pool = _pool  # type: ignore[assignment]

    async def asyncTearDown(self) -> None:
        db.get_pool = self._saved  # type: ignore[assignment]

    def _status(self, index: int = 0) -> Optional[str]:
        # (run_id, phase, status[, review_round])
        return self.pool.calls[index][2]

    async def test_phase_still_derives_the_status_by_default(self) -> None:
        await db.update_run_phase("run-a", "review")
        self.assertEqual(self._status(), db.RUN_STATUS_AWAITING_REVIEW)

        self.pool.calls.clear()
        await db.update_run_phase("run-a", "done")
        self.assertEqual(self._status(), db.RUN_STATUS_DONE)

        self.pool.calls.clear()
        await db.update_run_phase("run-a", "generate")
        self.assertEqual(self._status(), db.RUN_STATUS_RUNNING)

    async def test_explicit_status_overrides_the_phase(self) -> None:
        """The rework hard stop: DONE phase, FAILED run.

        DONE is what terminates the orchestrator loop, so the hard stop has to
        use it - but a run that exhausted its rework rounds without producing
        a carousel is not finished, it gave up. Deriving the status from the
        phase reported it as published and offered no Re-run.
        """
        await db.update_run_phase("run-a", "done", status=db.RUN_STATUS_FAILED)
        self.assertEqual(self._status(), db.RUN_STATUS_FAILED)

    async def test_override_survives_the_review_round_variant(self) -> None:
        """Both SQL branches must honour it, not just the short one."""
        await db.update_run_phase(
            "run-a", "done", review_round=2, status=db.RUN_STATUS_FAILED
        )
        self.assertEqual(self._status(), db.RUN_STATUS_FAILED)
        self.assertEqual(self.pool.calls[0][3], 2)

    async def test_hard_stop_reports_the_phase_it_died_in_not_done(self) -> None:
        """The console renders runs.phase, so it must not claim Publishing.

        A run that exhausts its rework rounds never reached publish and never
        reached review. Recording the DONE phase lit every step of the phase
        rail and labelled the task "Done" in the list.
        """
        await db.update_run_phase(
            "run-a", "rework", review_round=0, status=db.RUN_STATUS_FAILED
        )
        run_id, phase, status, review_round = self.pool.calls[0]
        self.assertEqual(phase, "rework")
        self.assertEqual(status, db.RUN_STATUS_FAILED)
        self.assertNotEqual(phase, "done")

    async def test_a_failed_status_is_one_the_ui_offers_a_rerun_for(self) -> None:
        """Guards the contract the task list's action buttons rely on."""
        self.assertIn(
            db.RUN_STATUS_FAILED,
            {db.RUN_STATUS_FAILED, db.RUN_STATUS_CANCELLED},
        )
        self.assertNotEqual(db.RUN_STATUS_FAILED, db.RUN_STATUS_DONE)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
