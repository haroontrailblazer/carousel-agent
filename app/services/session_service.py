"""Persistent ADK sessions over Supabase HTTPS, with atomic event writes."""
from __future__ import annotations

import json
import uuid

from google.adk.events.event import Event
from google.adk.sessions import BaseSessionService, Session
from google.adk.sessions.base_session_service import GetSessionConfig, ListSessionsResponse
from google.adk.sessions import _session_util
from google.adk.errors.already_exists_error import AlreadyExistsError
from google.adk.errors.session_not_found_error import SessionNotFoundError
from google.adk.errors._stale_session_error import StaleSessionError

from app.services import db
from app.services.supabase_db import DatabaseAPIError


class SupabaseSessionService(BaseSessionService):
    def __init__(self, client=None):
        self.client = client

    async def _rpc(self, name, args):
        client = self.client or await db.get_pool()
        return await client.rpc(name, args)

    @staticmethod
    def _session(raw):
        state = dict(raw['state'])
        state.update({'app:'+k: v for k, v in raw['app_state'].items()})
        state.update({'user:'+k: v for k, v in raw['user_state'].items()})
        events = []
        for row in raw['events']:
            value = row['event_data']
            if isinstance(value, str):
                value = json.loads(value)
            events.append(Event.model_validate(value))
        session = Session(id=raw['id'], app_name=raw['app_name'], user_id=raw['user_id'],
                          state=state, events=events, last_update_time=raw['updated'])
        session._storage_update_marker = raw['revision']
        return session

    async def create_session(self, *, app_name, user_id, state=None, session_id=None):
        delta = _session_util.extract_state_delta(state or {})
        try:
            raw = await self._rpc('carousel_session_create', {
                'app': app_name, 'usr': user_id, 'sid': session_id or str(uuid.uuid4()),
                'session_state': delta['session'], 'app_delta': delta['app'], 'user_delta': delta['user'],
            })
        except DatabaseAPIError as exc:
            if exc.code == '23505':
                raise AlreadyExistsError('Session already exists') from exc
            raise
        return self._session(raw)

    async def get_session(self, *, app_name, user_id, session_id, config=None):
        config = config or GetSessionConfig()
        if config.num_recent_events is not None and config.num_recent_events < 0:
            raise ValueError('num_recent_events must be nonnegative')
        raw = await self._rpc('carousel_session_get', {'app': app_name, 'usr': user_id,
            'sid': session_id, 'recent': config.num_recent_events, 'after_ts': config.after_timestamp})
        return self._session(raw) if raw else None

    async def list_sessions(self, *, app_name, user_id=None):
        rows = await self._rpc('carousel_session_list', {'app': app_name, 'usr': user_id})
        sessions = [self._session(row) for row in rows]
        for session in sessions:
            session.state = {}
        return ListSessionsResponse(sessions=sessions)

    async def get_user_state(self, *, app_name, user_id):
        return await self._rpc('carousel_user_state', {'app': app_name, 'usr': user_id})

    async def delete_session(self, *, app_name, user_id, session_id):
        await self._rpc('carousel_session_delete', {'app': app_name, 'usr': user_id, 'sid': session_id})

    async def append_event(self, session, event):
        if event.partial:
            return event
        # Reject a reconstructed session unless its timestamp and last event
        # still match storage. Normal create/get paths carry an exact revision.
        if session._storage_update_marker is None:
            current = await self.get_session(app_name=session.app_name, user_id=session.user_id,
                session_id=session.id, config=GetSessionConfig(num_recent_events=1))
            if current is None:
                raise SessionNotFoundError('Session not found')
            latest = lambda s: s.events[-1].id if s.events else None
            if current.last_update_time != session.last_update_time or latest(current) != latest(session):
                raise StaleSessionError('Session changed; reload before appending')
            session._storage_update_marker = current._storage_update_marker
        # Work on a copy so a failed write does not consume the caller's temp delta.
        stored_event = self._trim_temp_delta_state(event.model_copy(deep=True))
        delta = _session_util.extract_state_delta(stored_event.actions.state_delta)
        try:
            result = await self._rpc('carousel_session_append', {
                'app': session.app_name, 'usr': session.user_id, 'sid': session.id,
                'expected_revision': session._storage_update_marker,
                'event': stored_event.model_dump(exclude_none=True, mode='json'),
                'session_delta': delta['session'], 'app_delta': delta['app'], 'user_delta': delta['user'],
            })
        except DatabaseAPIError as exc:
            if exc.code in ('PT409', '40001'):
                raise StaleSessionError('Session changed; reload before appending') from exc
            if exc.code == 'P0002':
                raise SessionNotFoundError('Session not found') from exc
            raise
        session.last_update_time = result['updated']
        session._storage_update_marker = result['revision']
        if any(e.id == event.id for e in session.events):
            return stored_event
        return await super().append_event(session, event)
