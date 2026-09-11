"""Prepare copies, then apply the private-workspace migration using Supabase HTTPS.

Pass a temporary management-token file; it is never copied into app configuration.
Without --apply this only inventories and copies legacy media, preserving originals.
Run with no active agent invocations and deploy the matching app immediately after.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import httpx
from dotenv import dotenv_values
from app.config import settings
from app.services.supabase_storage import SupabaseStorageClient

OWNER_EMAIL = "haroon@closefuture.io"
TABLES = ['news_queue','runs','feedback','pending_reviews','run_events','app_config',
          'sessions','events','app_states','user_states','memory_entries','instagram_accounts','carousel_designs']


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--token-file', type=Path, required=True)
    parser.add_argument('--apply',action='store_true')
    args=parser.parse_args()
    ref=settings.supabase_url.split('//')[1].split('.')[0]
    management=httpx.Client(base_url=f'https://api.supabase.com/v1/projects/{ref}',
        headers={'Authorization':'Bearer '+args.token_file.read_text().strip()},timeout=120)
    folder=ROOT/'.work'/'private-workspace-migration'; folder.mkdir(parents=True,exist_ok=True)
    def query(sql):
        response=management.post('/database/query',json={'query':sql})
        if not response.is_success:
            # Save diagnostics privately, never emit arbitrary database error text.
            (folder/'last-error.json').write_text(response.text)
            raise RuntimeError(f'Management query failed: HTTP {response.status_code}; see private diagnostics')
        return response.json()
    try:
        owner=query(f"SELECT id FROM auth.users WHERE lower(email)='{OWNER_EMAIL}' AND email_confirmed_at IS NOT NULL")
        if len(owner)!=1: raise RuntimeError('The confirmed legacy owner account must exist exactly once')
        owner_id=owner[0]['id']
        active=query("SELECT count(*)::int AS n FROM public.runs WHERE status='running'")[0]['n']
        if active: raise RuntimeError(f'{active} runs are active; let them finish before migrating')
        if query("SELECT to_regclass('public.carousel_workspaces') IS NOT NULL AS migrated")[0]['migrated']:
            raise RuntimeError('Workspace migration is already applied; do not reapply')
        counts={t:query(f'SELECT count(*)::int AS n FROM public.{t}')[0]['n'] for t in TABLES}
        functions=query("SELECT pg_get_functiondef(p.oid) AS ddl FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname IN ('public','carousel_internal') AND p.prokind='f'")
        constraints=query("SELECT conrelid::regclass::text AS tbl,conname,pg_get_constraintdef(oid) AS ddl FROM pg_constraint WHERE connamespace IN ('public'::regnamespace,'carousel_internal'::regnamespace)")
        (folder/'before.json').write_text(json.dumps({'owner':owner_id,'counts':counts,'functions':functions,'constraints':constraints},indent=2))
        client=SupabaseStorageClient(settings.supabase_url,settings.supabase_storage_key)
        try:
            objects=[obj for page in client.paginate(Bucket=settings.media_bucket) for obj in page['Contents'] if not obj['Key'].startswith('users/')]
            def copy(obj):
                source=obj['Key']; destination=f'users/{owner_id}/{source}'
                # Server-side copy avoids downloading and re-uploading the media.
                response=client.http.post('object/copy',json={'bucketId':settings.media_bucket,'sourceKey':source,'destinationKey':destination})
                if not response.is_success and response.status_code not in (400,409):
                    raise RuntimeError('A legacy media copy failed')
                original=client.head_object(Bucket=settings.media_bucket,Key=source)
                copied=client.head_object(Bucket=settings.media_bucket,Key=destination)
                if original['Metadata']!=copied['Metadata'] or original['ContentType']!=copied['ContentType']:
                    raise RuntimeError('Copied media metadata did not match; original has been preserved')
                return {'source':source,'destination':destination,'size':obj['Size']}
            with ThreadPoolExecutor(max_workers=4) as workers:
                manifest=list(workers.map(copy,objects))
            (folder/'media-copies.json').write_text(json.dumps(manifest,indent=2))
            print(f'Prepared {len(manifest)} private media copies; originals retained.')
        finally: client.http.close()
        if not args.apply:
            print('Preparation complete. Database ownership has not changed.'); return
        if query("SELECT count(*)::int AS n FROM public.runs WHERE status='running'")[0]['n']:
            raise RuntimeError('A run started during preparation; wait and repeat before applying')
        sql=(ROOT/'db/migrations/012_private_workspaces.sql').read_text()
        # Preserve source preferences and learned instructions only for the legacy owner.
        old=dotenv_values(ROOT/'.env')
        sources={key:[v.strip() for v in (old.get(env) or '').split(',') if v.strip()] for key,env in [('rss_feeds','RSS_FEEDS'),('youtube_channels','YOUTUBE_CHANNELS')]}
        rules={}
        for file in (ROOT/'skills').rglob('*.md'):
            match=re.search(r'(?ms)^## Learned rules[^\n]*\n(.*?)(?=^## |\Z)',file.read_text(encoding='utf-8'))
            if match: rules[file.relative_to(ROOT/'skills').as_posix()]=[line for line in match[1].splitlines() if line.strip()]
        def literal(value): return "'"+json.dumps(value).replace("'","''")+"'::jsonb"
        additions=f"SELECT set_config('carousel.workspace','{owner_id}',true);\n"
        for key,value in [('news_sources',sources),('learned_rules',rules)]:
            additions+=f"INSERT INTO public.app_config(tenant_id,key,value) VALUES('{owner_id}','{key}',{literal(value)}) ON CONFLICT(tenant_id,key) DO NOTHING;\n"
        # Assert every existing row survives inside the same transaction.
        additions+="DO $verify$ BEGIN\n"
        for table,count in counts.items():
            expected=f'count(*) < {count}' if table=='app_config' else f'count(*) <> {count}'
            additions+=f"IF (SELECT {expected} FROM public.{table}) THEN RAISE EXCEPTION 'Row count changed in {table}'; END IF;\n"
        additions+="END $verify$;\n"
        sql=sql.replace('COMMIT;',additions+'COMMIT;')
        query(sql)
        (folder/'applied.json').write_text(json.dumps({'owner':owner_id,'at':datetime.now(timezone.utc).isoformat(),'counts':counts},indent=2))
        response=management.get('/config/auth'); response.raise_for_status(); auth=response.json()
        base='https://carousell.up.railway.app'
        redirects=set(filter(None,(auth.get('uri_allow_list') or '').split(',')))
        redirects.update([base+'/login',base+'/reset-password'])
        response=management.patch('/config/auth',json={'site_url':base,'uri_allow_list':','.join(sorted(redirects))})
        if not response.is_success: raise RuntimeError('Database migrated, but the email redirect update needs retrying')
        print('Applied private workspaces; row counts preserved; confirmation redirects point to the application.')
    finally: management.close()


if __name__=='__main__': main()
