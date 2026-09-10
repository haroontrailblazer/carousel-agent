"""Server-only HTTPS database access through fixed Supabase RPC operations.

SQL is matched locally against a checked-in catalog. Only operation IDs and
bound values cross the wire; the server never accepts caller-supplied SQL.
Multi-statement writes use explicit atomic RPCs, not pretend HTTP transactions.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import date, datetime
import hashlib
import json
from pathlib import Path
import re

import httpx

from app.config import settings


def normalize_sql(sql: str) -> str:
    return re.sub(r'\s+', ' ', sql).strip().rstrip(';')


def operation_id(sql: str) -> str:
    return 'q_' + hashlib.sha256(normalize_sql(sql).encode()).hexdigest()[:20]


class DatabaseAPIError(RuntimeError):
    def __init__(self, code, status):
        self.code = code
        self.status = status
        super().__init__(f'Supabase database operation failed ({code}, HTTP {status})')


def _json(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    raise TypeError(f'Unsupported database value type: {type(value).__name__}')


class Record(dict):
    def __init__(self, value, columns):
        super().__init__(value)
        self.columns = columns

    def __getitem__(self, key):
        return super().__getitem__(self.columns[key] if isinstance(key, int) else key)


class SupabaseDatabase:
    def __init__(self, *, url=None, key=None, transport=None):
        url = (url or settings.supabase_url).rstrip('/')
        key = key or settings.supabase_storage_key
        if not url or not key:
            raise RuntimeError('Set SUPABASE_URL and the server-only Supabase key')
        headers = {'apikey': key}
        if not key.startswith('sb_secret_'):
            headers['Authorization'] = 'Bearer '+key
        self.http = httpx.AsyncClient(base_url=url+'/rest/v1/rpc/', headers=headers,
            timeout=httpx.Timeout(45, connect=10), transport=transport, follow_redirects=False)
        self.catalog = json.loads(Path(__file__).with_name('db_operations.json').read_text())

    async def rpc(self, name, args=None):
        if not re.fullmatch(r'carousel_[a-z0-9_]+', name):
            raise ValueError('Unrecognized application RPC')
        # No automatic write retries: a lost response may follow a commit.
        response = await self.http.post(name, content=json.dumps(args or {}, default=_json),
                                        headers={'Content-Type':'application/json'})
        if not response.is_success:
            try:
                code = response.json().get('code', 'unknown')
            except (ValueError, AttributeError):
                code = 'unknown'
            raise DatabaseAPIError(code, response.status_code)
        return response.json() if response.content else None

    async def _query(self, sql, args):
        identifier = operation_id(sql)
        spec = self.catalog.get(identifier)
        if not spec or spec['sql'] != normalize_sql(sql):
            raise RuntimeError('Unregistered database query; regenerate and deploy the RPC catalog: '+identifier)
        if len(args) != len(spec['parameters']):
            raise ValueError('Incorrect database argument count')
        value = await self.rpc('carousel_query', {'operation':identifier, 'params':list(args)})
        columns = [c['name'] for c in spec['columns']]
        rows = []
        for raw in value.get('rows', []):
            for c in spec['columns']:
                if raw.get(c['name']) is not None and c['type'] in ('timestamp','timestamptz','date'):
                    raw[c['name']] = datetime.fromisoformat(raw[c['name']].replace('Z','+00:00'))
            rows.append(Record(raw, columns))
        return rows, value.get('count', 0), spec['command']

    async def fetch(self, sql, *args, **kwargs):
        return (await self._query(sql, args))[0]

    async def fetchrow(self, sql, *args, **kwargs):
        rows = await self.fetch(sql, *args)
        return rows[0] if rows else None

    async def fetchval(self, sql, *args, **kwargs):
        row = await self.fetchrow(sql, *args)
        return row[0] if row is not None else None

    async def execute(self, sql, *args, **kwargs):
        _, count, command = await self._query(sql, args)
        return f'{command} {count}'

    async def executemany(self, sql, args, **kwargs):
        identifier = operation_id(sql)
        spec = self.catalog.get(identifier)
        if not spec or spec['sql'] != normalize_sql(sql):
            raise RuntimeError('Unregistered batch database query')
        if args:
            await self.rpc('carousel_query_batch', {'operation':identifier, 'batches':[list(a) for a in args]})

    @asynccontextmanager
    async def acquire(self):
        # Compatibility for independent read calls, never a DB transaction.
        yield self

    def transaction(self):
        raise RuntimeError('Use one atomic Supabase RPC for transactional writes')

    async def close(self):
        await self.http.aclose()
