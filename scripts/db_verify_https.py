"""Live HTTPS regression checks with isolated, explicitly cleaned fixtures.

Only cleanup uses the temporary admin connection. All application operations
run through the same HTTPS services used by the web app.
"""
import argparse
import asyncio
import json
import os
from pathlib import Path
import sys
import uuid

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
import asyncpg
from dotenv import dotenv_values
import httpx
from google.adk.events import Event, EventActions
from google.adk.sessions.base_session_service import GetSessionConfig
from google.adk.errors._stale_session_error import StaleSessionError
from google.adk.errors.already_exists_error import AlreadyExistsError
from google.genai import types
from app.config import settings
from app.services import db
from app.services.session_service import SupabaseSessionService
from app.services.memory_service import PostgresMemoryService
from app.services.supabase_db import SupabaseDatabase, DatabaseAPIError


async def main(args):
    dsn = os.environ.get(args.dsn_env) or dotenv_values(ROOT/'.env').get(args.dsn_env)
    if not dsn:
        raise RuntimeError('A temporary admin connection is required for fixture cleanup')
    cleanup = await asyncpg.connect(dsn.replace('+asyncpg','',1),statement_cache_size=0)
    marker = '__https_test_'+uuid.uuid4().hex
    client = SupabaseDatabase()
    db._pool = client
    service = SupabaseSessionService(client)
    ids = {'app_name':marker,'user_id':marker,'session_id':marker}
    try:
        # Real existing data reads, without printing records or changing them.
        assert isinstance(await db.list_runs(limit=3), list)
        assert isinstance(await db.list_queued_news(limit=3), list)
        session = await service.create_session(**ids, state={'keep':{'nested':True},'app:theme':'dark','user:lang':'en','temp:discard':1})
        assert session.state == {'keep':{'nested':True},'app:theme':'dark','user:lang':'en'}
        try:
            await service.create_session(**ids)
            raise AssertionError('Duplicate session accepted')
        except AlreadyExistsError:
            pass
        event = Event(author='user',invocation_id=marker,content=types.Content(role='user',parts=[types.Part(text='HTTPS memory verification')]),
            actions=EventActions(state_delta={'title':'Test','user:lang':'fr','temp:scratch':'local'}))
        await service.append_event(session,event)
        assert session.state['temp:scratch']=='local'
        loaded = await SupabaseSessionService(client).get_session(**ids)
        assert loaded.state['keep']=={'nested':True} and 'temp:scratch' not in loaded.state
        assert loaded.events[-1].content.parts[0].text=='HTTPS memory verification'
        stale = await service.get_session(**ids)
        await service.append_event(loaded,Event(author='user',actions=EventActions(state_delta={'next':2})))
        try:
            await service.append_event(stale,Event(author='user',actions=EventActions(state_delta={'lost':True})))
            raise AssertionError('Stale event accepted')
        except StaleSessionError:
            pass
        assert (await service.get_user_state(app_name=marker,user_id=marker))['lang']=='fr'
        assert len((await service.get_session(**ids,config=GetSessionConfig(num_recent_events=1))).events)==1
        assert not (await service.get_session(**ids,config=GetSessionConfig(num_recent_events=0))).events
        assert not (await service.get_session(**ids,config=GetSessionConfig(after_timestamp=9e9))).events
        assert len((await service.list_sessions(app_name=marker,user_id=marker)).sessions)==1
        memory = PostgresMemoryService()
        await memory.add_session_to_memory(loaded)
        assert (await memory.search_memory(app_name=marker,user_id=marker,query='HTTPS')).memories
        # The cover edit changes only its bundle, preserving unseen session fields.
        cover_query = next(v['sql'] for v in client.catalog.values() if 'ARRAY[$5::text]' in v['sql'])
        await client.execute(cover_query,marker,marker,marker,{'cover':'image'},'bundle')
        assert (await service.get_session(**ids)).state['keep']=={'nested':True}
        await db.set_config(marker, {'count':0})
        await asyncio.gather(*(db.update_config(marker,lambda value:{'count':value['count']+1}) for _ in range(5)))
        assert (await db.get_config(marker))['count']==5
        queued = await db.enqueue_news({'id':marker,'title':'HTTPS fixture','source_url':'https://example.invalid/'+marker})
        claims = await asyncio.gather(*(db.claim_news_by_url_hash(queued['url_hash']) for _ in range(3)))
        assert sum(result is not None for result in claims)==1
        designs=[{'id':marker,'name':'Fixture','handle':'test'}]
        await db.replace_carousel_designs(marker,designs)
        try:
            await db.replace_carousel_designs(marker,[{'id':marker+'bad','name':None}])
            raise AssertionError('Invalid design was accepted')
        except DatabaseAPIError:
            pass
        assert await db.list_carousel_designs(marker)==designs
        # No access to the service-role dispatcher through the browser credential.
        anon = dotenv_values(ROOT/'.env').get('SUPABASE_ANON_KEY')
        async with httpx.AsyncClient() as browser:
            denied = await browser.post(settings.supabase_url+'/rest/v1/rpc/carousel_session_get',
                headers={'apikey':anon,'Authorization':'Bearer '+anon},
                json={'app':marker,'usr':marker,'sid':marker})
            assert denied.status_code in (401,403,404), denied.status_code
        await service.delete_session(**ids)
        assert await service.get_session(**ids) is None
        assert await cleanup.fetchval('SELECT count(*) FROM public.events WHERE app_name=$1',marker)==0
        print('PASS: real HTTPS reads, session restart/scopes/events/stale writes, memory, cover preservation, concurrent config/queue claims, design rollback, browser denial and session deletion.')
    finally:
        # Exact generated fixture identity; never a prefix wildcard or live app name.
        async with cleanup.transaction():
            for table in ('events','memory_entries','sessions','app_states','user_states'):
                await cleanup.execute(f'DELETE FROM public.{table} WHERE app_name=$1',marker)
            await cleanup.execute('DELETE FROM public.app_config WHERE key=$1',marker)
            await cleanup.execute('DELETE FROM public.news_queue WHERE id=$1',marker)
            await cleanup.execute('DELETE FROM public.carousel_designs WHERE owner_email=$1',marker)
        await cleanup.close()
        await db.close_pool()
        print('Removed all isolated verification fixtures.')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dsn-env',default='MIGRATION_DATABASE_URL')
    asyncio.run(main(parser.parse_args()))
