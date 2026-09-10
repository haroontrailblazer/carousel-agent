"""HTTPS transport and ADK persistence boundaries, without network access."""
import asyncio
import json
from datetime import datetime, timezone
import unittest
from unittest.mock import AsyncMock, patch

import httpx
from google.adk.events import Event, EventActions
from google.adk.sessions import Session
from google.adk.errors._stale_session_error import StaleSessionError

from app.services import db
from app.services.session_service import SupabaseSessionService
from app.services.supabase_db import SupabaseDatabase, DatabaseAPIError, operation_id


class TransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_bound_values_and_typed_rows_without_sending_sql(self):
        value = "injection'); DROP TABLE runs; --"
        requests = []
        def handle(request):
            requests.append(json.loads(request.content))
            return httpx.Response(200, json={'rows':[{'created_at':'2026-09-10T00:00:00+00:00',
                'run_id':value}], 'count':1})
        client = SupabaseDatabase(url='https://test.supabase.co', key='server', transport=httpx.MockTransport(handle))
        try:
            row = await client.fetchrow('SELECT * FROM runs WHERE run_id = $1', value)
            self.assertIsInstance(row['created_at'], datetime)
            self.assertEqual(row['run_id'], value)
            self.assertEqual(requests, [{'operation':operation_id('SELECT * FROM runs WHERE run_id = $1'), 'params':[value]}])
            with self.assertRaisesRegex(RuntimeError, 'Unregistered'):
                await client.execute('DROP TABLE runs')
            self.assertEqual(len(requests), 1)
        finally:
            await client.close()

    async def test_failed_write_is_not_retried_and_error_hides_credentials(self):
        requests = []
        def handle(request):
            requests.append(request)
            return httpx.Response(403, json={'code':'42501','message':'sensitive database details'})
        client = SupabaseDatabase(url='https://test.supabase.co', key='server', transport=httpx.MockTransport(handle))
        try:
            with self.assertRaises(DatabaseAPIError) as caught:
                await client.rpc('carousel_session_delete', {'app':'a','usr':'u','sid':'s'})
            self.assertNotIn('sensitive', str(caught.exception))
            self.assertEqual(len(requests), 1)
        finally:
            await client.close()

    async def test_batch_is_one_atomic_request(self):
        requests=[]
        def handle(request):
            requests.append(json.loads(request.content))
            return httpx.Response(200, json=None)
        client = SupabaseDatabase(url='https://test.supabase.co', key='server', transport=httpx.MockTransport(handle))
        try:
            await client.executemany('DELETE FROM pending_reviews WHERE run_id = $1', [('one',),('two',)])
            self.assertEqual(requests[0]['batches'], [['one'],['two']])
            self.assertEqual(len(requests), 1)
        finally:
            await client.close()


class SessionTests(unittest.IsolatedAsyncioTestCase):
    def session(self):
        session = Session(app_name='app',user_id='user',id='session',state={'keep':1})
        session._storage_update_marker = 'revision-1'
        return session

    async def test_append_separates_scopes_and_keeps_temp_local(self):
        client = type('Client', (), {'rpc':AsyncMock(return_value={'updated':123.5,'revision':'revision-2'})})()
        service = SupabaseSessionService(client)
        session = self.session()
        event = Event(author='user', actions=EventActions(state_delta={
            'app:theme':'dark','user:language':'en','title':'News','temp:scratch':'local'}))
        await service.append_event(session,event)
        args = client.rpc.call_args.args[1]
        self.assertEqual(args['app_delta'], {'theme':'dark'})
        self.assertEqual(args['user_delta'], {'language':'en'})
        self.assertEqual(args['session_delta'], {'title':'News'})
        self.assertNotIn('temp:scratch', args['event']['actions']['state_delta'])
        self.assertEqual(session.state['temp:scratch'], 'local')
        self.assertEqual(session.state['keep'], 1)
        self.assertEqual(session._storage_update_marker, 'revision-2')

    async def test_stale_write_does_not_mutate_local_state_or_event(self):
        client = type('Client', (), {'rpc':AsyncMock(side_effect=DatabaseAPIError('40001',500))})()
        session = self.session()
        event = Event(author='user', actions=EventActions(state_delta={'temp:x':1,'keep':2}))
        with self.assertRaises(StaleSessionError):
            await SupabaseSessionService(client).append_event(session,event)
        self.assertEqual(session.state, {'keep':1})
        self.assertEqual(event.actions.state_delta['temp:x'], 1)
        self.assertEqual(session.events, [])

    async def test_partial_event_does_not_reach_storage(self):
        client = type('Client', (), {'rpc':AsyncMock()})()
        session = self.session()
        await SupabaseSessionService(client).append_event(session, Event(author='user', partial=True))
        client.rpc.assert_not_called()
        self.assertEqual(session.events, [])

    async def test_config_cas_reapplies_change_to_concurrent_update(self):
        client = type('Client', (), {
            'fetchrow':AsyncMock(side_effect=[{'value':{'old':1}}, {'value':{'old':1,'concurrent':2}}]),
            'rpc':AsyncMock(side_effect=[False,True]),
        })()
        with patch.object(db,'get_pool',AsyncMock(return_value=client)):
            value = await db.update_config('test', lambda old: {**old,'new':3})
        self.assertEqual(value, {'old':1,'concurrent':2,'new':3})

    def test_catalog_covers_every_current_application_query(self):
        from scripts.build_db_rpc import queries
        from pathlib import Path
        catalog = json.loads((Path(db.__file__).parent/'db_operations.json').read_text())
        self.assertEqual({spec['sql'] for spec in catalog.values()}, set(queries()))
