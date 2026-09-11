"""Research must not freeze the API or depend on a browser staying connected."""

import asyncio
import threading
import unittest
from contextlib import ExitStack
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import FastAPI
from google.adk.tools import FunctionTool
from starlette.requests import Request

from app import tenancy
from app.agents import research
from app.runs import service
from app.runs.bus import BUS
from app.services import ai_config, db
from web_api import routes_runs
from web_api.auth import Identity
from web_api.deps import current_identity

OWNER = '00000000-0000-0000-0000-000000000001'


class RunResponsivenessTests(unittest.IsolatedAsyncioTestCase):
    async def test_research_allows_requests_and_survives_trace_disconnect(self):
        entered, release, finished = threading.Event(), threading.Event(), threading.Event()
        observed = {}

        def slow_provider(**kwargs):
            observed['owner'] = tenancy.current()
            observed['config'] = ai_config.current()
            observed['thread'] = threading.get_ident()
            entered.set()
            # Safety ceiling makes this fail instead of deadlocking if the
            # synchronous provider runs on the server thread again.
            release.wait(3)
            finished.set()
            return SimpleNamespace(output_text='Verified research.', output=[])

        client = SimpleNamespace(responses=SimpleNamespace(create=slow_provider))
        tool = FunctionTool(research.search_web)
        result = {}

        async def consume(*args, **kwargs):
            result.update(await tool.run_async(args={'query': 'Test announcement'}, tool_context=SimpleNamespace()))
            return {'paused': True, 'last_seq': 1, 'phase': 'review'}

        app = FastAPI()
        app.include_router(routes_runs.router, prefix='/api')
        identity = Identity(email='reader@example.test', subject=OWNER)
        app.dependency_overrides[current_identity] = lambda: identity
        runner = SimpleNamespace(close=AsyncMock())
        config = replace(ai_config.current(), api_key='test-only-not-a-real-key')
        main_thread = threading.get_ident()

        with ExitStack() as stack:
            stack.enter_context(tenancy.bind(OWNER))
            stack.enter_context(ai_config.bind(config))
            stack.enter_context(patch.object(research.research_tools, '_client', return_value=client))
            stack.enter_context(patch.object(service, 'consume_invocation', side_effect=consume))
            stack.enter_context(patch.object(service, '_bind_brand_identity', AsyncMock()))
            stack.enter_context(patch.object(service, 'record_event', AsyncMock()))
            status = stack.enter_context(patch.object(db, 'set_run_status', AsyncMock()))
            stack.enter_context(patch.object(db, 'list_queued_news', AsyncMock(return_value=[])))
            deleted = stack.enter_context(patch.object(db, 'delete_queued_news', AsyncMock(return_value=True)))
            stack.enter_context(patch.object(routes_runs, '_fetching_now', return_value=False))
            stack.enter_context(patch.object(routes_runs, 'load_trace', AsyncMock(return_value=[])))
            task = service.spawn_run('responsive-run', runner=runner, first_message='Research this')
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 2))
                self.assertFalse(finished.is_set(), 'Research blocked the event loop until the provider finished')
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as api:
                    listing, deletion = await asyncio.wait_for(asyncio.gather(
                        api.get('/api/queue'), api.delete('/api/queue/unrelated-story'),
                    ), timeout=1)
                self.assertEqual(listing.status_code, 200)
                self.assertEqual(deletion.status_code, 200)
                deleted.assert_awaited_once_with('unrelated-story')
                self.assertFalse(finished.is_set())

                # Page changes close the old stream; refresh opens a new one.
                # Both only subscribe to the server-owned task.
                for _ in range(2):
                    request = Request({'type': 'http', 'headers': []})
                    response = await routes_runs.run_events('responsive-run', request, after=0, _identity=identity)
                    stream = response.body_iterator
                    await anext(stream)
                    self.assertEqual(BUS.subscriber_count('responsive-run'), 1)
                    await stream.aclose()
                    self.assertEqual(BUS.subscriber_count('responsive-run'), 0)
                    self.assertFalse(task.done())
                self.assertEqual(observed['owner'], OWNER)
                self.assertIs(observed['config'], config)
                self.assertNotEqual(observed['thread'], main_thread)
            finally:
                release.set()
                await asyncio.wait_for(task, timeout=3)
            self.assertEqual(result['status'], 'ok')
            status.assert_awaited_with('responsive-run', db.RUN_STATUS_AWAITING_REVIEW)
            runner.close.assert_awaited_once()
