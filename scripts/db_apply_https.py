"""Rehearse or apply HTTPS RPC migrations using a temporary admin connection.

The application never uses this connection. Supply its environment-variable
name with --dsn-env; no credential is printed or stored by this utility.
"""
import argparse
import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sys
import uuid

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import asyncpg
from dotenv import dotenv_values


async def main(args):
    dsn = os.environ.get(args.dsn_env) or dotenv_values(ROOT/'.env').get(args.dsn_env)
    if not dsn:
        raise RuntimeError('Set the temporary migration connection variable')
    conn = await asyncpg.connect(dsn.replace('+asyncpg', '', 1), statement_cache_size=0)
    tx = conn.transaction()
    await tx.start()
    try:
        for name in ('010_https_query_operations.sql', '011_https_atomic_operations.sql'):
            sql = (ROOT/'db/migrations'/name).read_text()
            sql = re.sub(r'^\s*(BEGIN|COMMIT);\s*$', '', sql, flags=re.M)
            await conn.execute(sql)
        catalog = json.loads((ROOT/'app/services/db_operations.json').read_text())
        marker = '__https_probe_'+uuid.uuid4().hex
        for op, spec in catalog.items():
            params = []
            for typ in spec['parameters']:
                if typ.endswith('[]') or typ.startswith('_'): value = []
                elif typ in ('json', 'jsonb'): value = {}
                elif typ in ('int2', 'int4', 'int8', 'float4', 'float8', 'numeric'): value = 1
                elif typ in ('timestamp', 'timestamptz', 'date'): value = datetime.now(timezone.utc).isoformat()
                elif typ == 'bool': value = False
                else: value = marker
                params.append(value)
            probe = conn.transaction()
            await probe.start()
            try:
                await conn.fetchval('SELECT public.carousel_query($1,$2::jsonb)', op, json.dumps(params))
            except asyncpg.IntegrityConstraintViolationError:
                # Arbitrary fixture values may fail domain constraints; SQL must compile.
                pass
            except Exception as exc:
                raise RuntimeError(f'Operation {op} ({spec["source"]}) failed: {exc}') from None
            finally:
                await probe.rollback()
        print(f'Validated {len(catalog)} fixed operations in rollback probes.')
        probe = conn.transaction()
        await probe.start()
        try:
            raw = json.loads(await conn.fetchval("SELECT public.carousel_session_create($1,$1,$1,'{}','{}','{}')", marker))
            event = {'id':marker,'author':'user','invocation_id':marker,'timestamp':datetime.now(timezone.utc).timestamp()}
            await conn.fetchval("SELECT public.carousel_session_append($1,$1,$1,$2,$3::jsonb,'{}','{}','{}')", marker,raw['revision'],json.dumps(event))
            for action, owner, expected in [('acquire','one',True),('acquire','two',False),('release','two',False),('renew','one',True),('release','one',True)]:
                assert await conn.fetchval('SELECT public.carousel_job_lease($1,$2,$3)',marker,owner,action) == expected
            assert await conn.fetchval("SELECT public.carousel_config_compare_swap($1,'{}','{}',false)",marker)
            await conn.execute("SELECT public.carousel_replace_designs($1,'[]')", marker)
            await conn.execute("SELECT public.carousel_replace_memory($1,$1,$1,'[]')", marker)
            await conn.fetchval('SELECT public.carousel_delete_run($1,$1,$1)',marker)
            for role in ('anon','authenticated'):
                assert not await conn.fetchval("SELECT has_function_privilege($1,'public.carousel_query(text,jsonb)','EXECUTE')",role)
                assert not await conn.fetchval("SELECT has_schema_privilege($1,'carousel_internal','USAGE')",role)
        finally:
            await probe.rollback()
        print('Validated atomic session writes, leases, replacement operations and browser RPC restrictions.')
        if args.apply:
            await tx.commit()
            print('Committed HTTPS migrations; validation fixtures were rolled back.')
        else:
            await tx.rollback()
            print('Rehearsal passed; migrations rolled back. Use --apply to install.')
    except BaseException:
        await tx.rollback()
        raise
    finally:
        await conn.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dsn-env', default='MIGRATION_DATABASE_URL')
    parser.add_argument('--apply', action='store_true')
    asyncio.run(main(parser.parse_args()))
