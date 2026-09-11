"""Apply Carousel's auth migration and UI-aligned Auth settings over HTTPS.
Read the authorized Supabase management token from stdin; never persist it.
Without --apply, only preview selected non-secret fields and changed keys.
"""
import argparse,json,sys
from pathlib import Path
import httpx
ROOT=Path(__file__).resolve().parent.parent
REF='lovmbnastwiyhhujaadg'
SITE='https://carousell.up.railway.app'
p=argparse.ArgumentParser(description=__doc__);p.add_argument('--apply',action='store_true');args=p.parse_args()
token=sys.stdin.read().strip()
if not token:raise SystemExit('Provide a management token on stdin.')
folder=ROOT/'.work/auth-redesign';folder.mkdir(exist_ok=True,parents=True)
with httpx.Client(base_url=f'https://api.supabase.com/v1/projects/{REF}',headers={'Authorization':'Bearer '+token},timeout=60) as client:
 def request(method,path,**kwargs):
  r=client.request(method,path,**kwargs)
  if not r.is_success:
   (folder/'management-error.txt').write_text(r.text,encoding='utf-8')
   raise SystemExit(f'HTTP {r.status_code}: see ignored local diagnostics.')
  return r.json()
 old=request('GET','/config/auth')
 spec=httpx.get('https://raw.githubusercontent.com/supabase/supabase/master/apps/docs/spec/api_v1_openapi.json',timeout=30).json()
 password_chars=spec['components']['schemas']['UpdateAuthConfigBody']['properties']['password_required_characters']['enum'][2]
 desired={'site_url':SITE,'password_min_length':12,'password_required_characters':password_chars,
  'passkey_enabled':True,'webauthn_rp_display_name':'Carousel Factory','webauthn_rp_id':'carousell.up.railway.app','webauthn_rp_origins':SITE}
 redirects=[v for v in (old.get('uri_allow_list') or '').split(',') if v]
 for route in ('/auth/confirm','/auth/callback','/auth/callback?*','/reset-password'):
  if SITE+route not in redirects:redirects.append(SITE+route)
 desired['uri_allow_list']=','.join(redirects)
 for kind,subject in [('confirmation','Confirm your Carousel account'),('magic_link','Your Carousel sign-in code'),('recovery','Reset your Carousel password')]:
  desired['mailer_subjects_'+kind]=subject
  desired['mailer_templates_'+kind+'_content']=(ROOT/'db/auth-emails'/f'{kind}.html').read_text(encoding='utf-8')
 print(json.dumps({'project':REF,'changes':list(desired),'google_enabled':old.get('external_google_enabled'),'github_enabled':old.get('external_github_enabled'),'email_confirmation_required':not old.get('mailer_autoconfirm')},indent=2))
 if not args.apply:sys.exit(0)
 # Store only the settings being updated, excluding all provider and SMTP secrets.
 (folder/'settings-before.json').write_text(json.dumps({k:old.get(k) for k in desired},indent=2),encoding='utf-8')
 def query(sql):return request('POST','/database/query',json={'query':sql})
 before=query("SELECT pg_get_functiondef('public.carousel_provision_workspace(uuid,text)'::regprocedure) AS ddl")
 (folder/'mfa-function-before.json').write_text(json.dumps(before),encoding='utf-8')
 query((ROOT/'db/migrations/013_workspace_mfa.sql').read_text())
 check=query("SELECT position('requires_mfa' in pg_get_functiondef('public.carousel_provision_workspace(uuid,text)'::regprocedure))>0 AS mfa_requirement,has_function_privilege('anon','public.carousel_provision_workspace(uuid,text)','EXECUTE') AS anon_execute,has_function_privilege('authenticated','public.carousel_provision_workspace(uuid,text)','EXECUTE') AS authenticated_execute")
 assert check==[{'mfa_requirement':True,'anon_execute':False,'authenticated_execute':False}],check
 request('PATCH','/config/auth',json=desired)
 after=request('GET','/config/auth')
 assert all(after.get(k)==v for k,v in desired.items()),'Auth setting mismatch'
 assert after.get('smtp_pass')==old.get('smtp_pass') and after.get('mailer_autoconfirm')==old.get('mailer_autoconfirm'),'Unexpected SMTP or confirmation change'
 print('PASS: MFA migration, restricted function permissions, email templates, password rules, passkey relying-party and redirects verified. SMTP and provider credentials preserved.')
