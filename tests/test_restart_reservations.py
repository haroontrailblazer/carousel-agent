"""A rerun must hand its reservation to its driver, never block on itself."""
import asyncio
import unittest
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import FastAPI

from app.runs import service
from app.services import db
from web_api.auth import Identity
from web_api.deps import current_identity
from web_api.routes_runs import router

RUN = 'run-restart-reservation'


class RestartReservationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.release = asyncio.Event()
        self.row = {'run_id': RUN, 'phase': 'generate', 'status': 'failed'}
        self.get_run = self.stack.enter_context(patch.object(db, 'get_run', AsyncMock(return_value=self.row)))
        self.rewind = self.stack.enter_context(patch.object(db, 'rewind_session_for_restart', AsyncMock()))
        self.stack.enter_context(patch.object(db, 'set_run_status', AsyncMock()))
        self.stack.enter_context(patch.object(db, 'max_run_seq', AsyncMock(return_value=3)))
        self.stack.enter_context(patch.object(service, 'record_event', AsyncMock()))
        self.deleted = self.stack.enter_context(patch.object(db, 'delete_run', AsyncMock(return_value={})))
        self.runner = self.stack.enter_context(patch('app.agent.build_configured_runner', AsyncMock(return_value=SimpleNamespace())))
        self.driver = self.stack.enter_context(patch.object(service, '_drive_run', AsyncMock(side_effect=self.drive)))
        app = FastAPI()
        app.include_router(router, prefix='/api')
        app.dependency_overrides[current_identity] = lambda: Identity(email='reader@example.test', subject='reader@example.test')
        self.api = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test')

    async def drive(self, *args):
        await self.release.wait()

    async def asyncTearDown(self):
        self.release.set()
        task = service._run_tasks.get(RUN)
        if task:
            await task
        service._run_tasks.pop(RUN, None)
        service._reserved.discard(RUN)
        await self.api.aclose()

    async def test_rerun_starts_once_then_allows_delete_after_driver_finishes(self):
        response = await self.api.post(f'/api/runs/{RUN}/rerun')
        self.assertEqual(response.status_code, 202, response.text)
        self.assertNotIn(RUN, service._reserved)
        self.assertIn(RUN, service.active_run_ids())
        self.assertEqual((await self.api.post(f'/api/runs/{RUN}/rerun')).status_code, 409)
        self.assertEqual((await self.api.delete(f'/api/runs/{RUN}')).status_code, 409)
        self.rewind.assert_awaited_once()
        self.release.set()
        await service._run_tasks[RUN]
        response = await self.api.delete(f'/api/runs/{RUN}')
        self.assertEqual(response.status_code, 200, response.text)
        self.deleted.assert_awaited_once()

    async def test_setup_failure_releases_reservation_for_retry(self):
        self.runner.side_effect = RuntimeError('Temporary setup failure')
        with self.assertRaisesRegex(RuntimeError, 'Temporary setup failure'):
            await service.restart_run(RUN)
        self.assertNotIn(RUN, service.active_run_ids())
        self.runner.side_effect = None
        self.assertTrue(await service.restart_run(RUN))

    async def test_disappearing_run_releases_reservation(self):
        self.get_run.side_effect = [self.row, None]
        self.assertFalse(await service.restart_run(RUN))
        self.assertNotIn(RUN, service.active_run_ids())

    async def test_two_requests_before_reservation_cannot_both_rewind(self):
        arrivals = 0
        both_read = asyncio.Event()
        async def get_run(*args):
            nonlocal arrivals
            arrivals += 1
            if arrivals == 2:
                both_read.set()
            await both_read.wait()
            return self.row
        self.get_run.side_effect = get_run
        results = await asyncio.gather(service.restart_run(RUN), service.restart_run(RUN), return_exceptions=True)
        self.assertEqual(sum(result is True for result in results), 1)
        self.rewind.assert_awaited_once()

    async def test_handoff_does_not_bypass_a_real_running_driver(self):
        task = asyncio.create_task(self.drive())
        service._run_tasks[RUN] = task
        service._reserved.add(RUN)
        self.assertFalse(await service.resume_interrupted_run(RUN, slot_held=True))
        self.runner.assert_not_awaited()
