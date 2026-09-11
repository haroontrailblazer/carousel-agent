"""Apply and verify application access policies without exposing credentials.

Default is a rollback-only rehearsal. Pass --apply to commit the migration.
Uses an explicit --dsn admin connection and the fixed application media bucket. No PAT or new
server key is needed. Existing application rows are never modified.
"""
from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
import sys

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import asyncpg
from app.config import settings

TABLES = (
    'news_queue', 'runs', 'feedback', 'pending_reviews', 'memory_entries',
    'run_events', 'app_users', 'app_config', 'sessions', 'events', 'app_states',
    'user_states', 'adk_internal_metadata', 'instagram_accounts', 'carousel_designs',
)


async def verify(conn):
    policies = await conn.fetch("""SELECT tablename FROM pg_policies
        WHERE schemaname='public' AND policyname='carousel_server_only'
        AND permissive='RESTRICTIVE' AND cmd='ALL'
        AND qual='false' AND with_check='false'
        AND roles @> ARRAY['anon','authenticated']::name[]""")
    assert {r['tablename'] for r in policies} == set(TABLES), 'Missing restrictive table policy'
    failures = await conn.fetch("""SELECT t, r, p FROM unnest($1::text[]) t
        CROSS JOIN unnest(ARRAY['anon','authenticated']) r
        CROSS JOIN unnest(ARRAY['SELECT','INSERT','UPDATE','DELETE','TRUNCATE','REFERENCES','TRIGGER']) p
        WHERE has_table_privilege(r,'public.'||t,p)
        OR (p IN ('SELECT','INSERT','UPDATE','REFERENCES') AND
            has_any_column_privilege(r,'public.'||t,CASE WHEN p IN ('SELECT','INSERT','UPDATE','REFERENCES') THEN p ELSE 'SELECT' END))""", list(TABLES))
    assert not failures, 'Unexpected browser privileges'
    assert await conn.fetchval("SELECT count(*) FROM pg_class WHERE oid=ANY($1::regclass[]) AND relrowsecurity", ['public.'+t for t in TABLES]) == len(TABLES)
    # Each DO block executes 15 real reads as a browser role in one round trip.
    probes = '\n'.join(
        f"BEGIN PERFORM count(*) FROM public.\"{t}\"; RAISE EXCEPTION 'Unexpected browser access'; "
        "EXCEPTION WHEN insufficient_privilege THEN NULL; END;" for t in TABLES)
    for role in ('anon', 'authenticated'):
        tx = conn.transaction()
        await tx.start()
        try:
            await conn.execute('SET LOCAL ROLE '+role)
            await conn.execute('DO $probe$ BEGIN '+probes+' END $probe$;')
        finally:
            await tx.rollback()
    assert not await conn.fetchval('SELECT public FROM storage.buckets WHERE id=$1', settings.media_bucket)
    for table, policy in [('objects','carousel_media_server_only'), ('buckets','carousel_media_bucket_server_only')]:
        assert await conn.fetchval("""SELECT count(*) FROM pg_policies WHERE schemaname='storage'
            AND tablename=$1 AND policyname=$2 AND permissive='RESTRICTIVE' AND cmd='ALL'""", table, policy) == 1
    assert not await conn.fetchval("""SELECT EXISTS(SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='public' AND (has_function_privilege('anon',p.oid,'EXECUTE')
        OR has_function_privilege('authenticated',p.oid,'EXECUTE')))""")
    # Prove that the restrictive guard survives an accidental future grant /
    # permissive policy. These probe policies/grants are ALWAYS rolled back
    # and are never visible to other connections.
    probe = conn.transaction()
    await probe.start()
    try:
        await conn.execute('GRANT SELECT, INSERT ON public.runs TO anon, authenticated')
        for schema, table in [('public','runs'), ('storage','objects'), ('storage','buckets')]:
            await conn.execute(f'CREATE POLICY carousel_policy_probe ON {schema}.{table} '
                               'FOR ALL TO anon, authenticated USING (true) WITH CHECK (true)')
        for role in ('anon', 'authenticated'):
            role_tx = conn.transaction()
            await role_tx.start()
            try:
                await conn.execute('SET LOCAL ROLE '+role)
                assert await conn.fetchval('SELECT count(*) FROM public.runs') == 0
                assert await conn.fetchval('SELECT count(*) FROM storage.objects WHERE bucket_id=$1', settings.media_bucket) == 0
                assert await conn.fetchval('SELECT count(*) FROM storage.buckets WHERE id=$1', settings.media_bucket) == 0
                await conn.execute("""DO $probe$ BEGIN
                    BEGIN
                        INSERT INTO public.runs(run_id) VALUES ('carousel-policy-denied-insert-probe');
                        RAISE EXCEPTION 'Restrictive insert policy did not block the write';
                    EXCEPTION WHEN insufficient_privilege THEN NULL;
                    END;
                END $probe$""")
            finally:
                await role_tx.rollback()
    finally:
        await probe.rollback()
    print('Verified: restrictive policies override permissive probes; browser inserts denied.')
    print('Verified: 15 restrictive table policies, 30 denied browser reads, no browser table/column/function grants, private Storage policies.')


async def main(apply, dsn):
    conn = await asyncpg.connect(dsn.replace('+asyncpg','',1), statement_cache_size=0, timeout=20)
    try:
        sql = (REPO / 'db/migrations/009_enforce_access_policies.sql').read_text(encoding='utf-8')
        # The runner owns the transaction so verification must pass before commit.
        sql = sql.replace('\nBEGIN;\n', '\n', 1).removesuffix('COMMIT;\n')
        tx = conn.transaction()
        await tx.start()
        try:
            await conn.execute("SELECT set_config('carousel.media_bucket',$1,true)", settings.media_bucket)
            count_sql = 'SELECT jsonb_object_agg(name,n)::text FROM (' + ' UNION ALL '.join(f"SELECT '{t}' AS name,count(*) AS n FROM public.\"{t}\"" for t in TABLES) + ') counts'
            before = await conn.fetchval(count_sql)
            await conn.execute(sql)
            await verify(conn)
            after = await conn.fetchval(count_sql)
            assert before == after, 'Application row counts changed'
            if apply:
                await tx.commit()
                print('Committed migration 009. All application row counts preserved.')
            else:
                await tx.rollback()
                print('Rehearsal passed; all policy changes rolled back. Use --apply to commit.')
        except BaseException:
            await tx.rollback()
            raise
    finally:
        await conn.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--dsn', required=True)
    args = parser.parse_args()
    asyncio.run(main(args.apply, args.dsn))
