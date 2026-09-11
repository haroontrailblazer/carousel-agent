// PostgreSQL integration test, running offline in PGlite under .work/tenant-db.
const { PGlite } = require('../.work/tenant-db/node_modules/@electric-sql/pglite');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const root = path.resolve(__dirname, '..');
const A = '11111111-1111-4111-8111-111111111111';
const B = '22222222-2222-4222-8222-222222222222';
(async () => {
 const db = new PGlite();
 try {
  await db.exec(`CREATE ROLE anon; CREATE ROLE authenticated; CREATE ROLE service_role;
    CREATE SCHEMA auth; CREATE TABLE auth.users(id uuid PRIMARY KEY,email text,email_confirmed_at timestamptz);
    INSERT INTO auth.users VALUES('${A}','haroon@closefuture.io',now()),('${B}','new@example.test',now());`);
  for (const file of ['005_transfer_baseline.sql','006_instagram_accounts.sql','007_carousel_designs.sql','010_https_query_operations.sql','011_https_atomic_operations.sql']) {
   let sql=fs.readFileSync(path.join(root,'db/migrations',file),'utf8');
   sql=sql.replace(/CREATE EXTENSION IF NOT EXISTS[^;]+;/g,'');
   await db.exec(sql);
  }
  await db.exec(`INSERT INTO app_config(key,value) VALUES('ai','{"private":"legacy-key"}');
    INSERT INTO news_queue(id,url_hash,payload) VALUES('story','hash','{"title":"Legacy"}');
    INSERT INTO runs(run_id,title) VALUES('legacy-run','Legacy task');`);
  await db.exec(fs.readFileSync(path.join(root,'db/migrations/012_private_workspaces.sql'),'utf8'));
  await db.exec(`CREATE TABLE auth.mfa_factors(id uuid PRIMARY KEY,user_id uuid,status text);`);
  await db.exec(fs.readFileSync(path.join(root,'db/migrations/013_workspace_mfa.sql'),'utf8'));
  const provision=async()=> (await db.query('SELECT public.carousel_provision_workspace($1,$2) AS value',[A,'haroon@closefuture.io'])).rows[0].value;
  assert.deepEqual(await provision(),{enabled:true,requires_mfa:false});
  await db.query("INSERT INTO auth.mfa_factors VALUES($1,$2,'unverified')",[B,A]);
  assert.equal((await provision()).requires_mfa,false);
  await db.query("UPDATE auth.mfa_factors SET status='verified' WHERE id=$1",[B]);
  assert.equal((await provision()).requires_mfa,true);
  await db.query('DELETE FROM auth.mfa_factors WHERE id=$1',[B]);
  assert.equal((await provision()).requires_mfa,false);
  const rpc=async(owner,operation,args={})=>(await db.query('SELECT public.carousel_tenant_rpc($1,$2,$3) AS value',[owner,operation,args])).rows[0].value;
  const catalog=JSON.parse(fs.readFileSync(path.join(root,'app/services/db_operations.json'),'utf8'));
  const query=async(owner,sql,args=[])=>{
    const entry=Object.entries(catalog).find(([id,spec])=>spec.sql===sql);
    assert(entry,'Missing query '+sql);
    return rpc(owner,'carousel_query',{operation:entry[0],params:args});
  };
  const getConfig=owner=>query(owner,'SELECT value FROM app_config WHERE key = $1',['ai']);
  assert.equal((await getConfig(A)).rows[0].value.private,'legacy-key');
  assert.deepEqual((await getConfig(B)).rows,[]);
  assert.equal(await rpc(B,'carousel_config_compare_swap',{config_key:'ai',expected:{},replacement:{private:'B-key'},expected_exists:false}),true);
  assert.equal((await getConfig(A)).rows[0].value.private,'legacy-key');
  assert.equal((await getConfig(B)).rows[0].value.private,'B-key');
  await rpc(B,'carousel_delete_run',{app:'app',usr:'pipeline',sid:'legacy-run'});
  assert.equal((await db.query("SELECT count(*)::int AS n FROM runs WHERE run_id='legacy-run'")).rows[0].n,1);
  for (const owner of [A,B]) {
    await rpc(owner,'carousel_session_create',{app:'app',usr:'pipeline',sid:'same-session',session_state:{private:owner},app_delta:{private:owner},user_delta:{private:owner}});
    await rpc(owner,'carousel_replace_designs',{owner:owner===A?'haroon@closefuture.io':'new@example.test',designs:[{id:'same-preset',name:owner}]});
    const lease=await rpc(owner,'carousel_job_lease',{job:'same-job',owner:'worker',action:'acquire'}); assert.equal(lease,true);
  }
  for (const owner of [A,B]) {
    const session=await rpc(owner,'carousel_session_get',{app:'app',usr:'pipeline',sid:'same-session'});
    assert.equal(session.state.private,owner); assert.equal(session.app_state.private,owner); assert.equal(session.user_state.private,owner);
    assert.equal((await rpc(owner,'carousel_session_list',{app:'app'})).length,1);
  }
  await rpc(B,'carousel_session_delete',{app:'app',usr:'pipeline',sid:'same-session'});
  assert.equal(await rpc(B,'carousel_session_get',{app:'app',usr:'pipeline',sid:'same-session'}),null);
  assert(await rpc(A,'carousel_session_get',{app:'app',usr:'pipeline',sid:'same-session'}));
  await assert.rejects(()=>rpc(A,'carousel_workspace_owners'));
  await assert.rejects(()=>rpc(null,'carousel_session_list',{app:'app'}));
  // Direct table and legacy RPC access stays closed, including for server-role callers.
  for (const role of ['anon','authenticated','service_role']) {
    await db.exec('SET ROLE '+role);
    try {
      await assert.rejects(()=>db.query("SELECT public.carousel_query('x','[]')"));
      if(role!=='service_role') {
        await assert.rejects(()=>db.query('SELECT * FROM public.app_config'));
        await assert.rejects(()=>rpc(A,'carousel_session_list',{app:'app'}));
      } else assert(await rpc(A,'carousel_session_get',{app:'app',usr:'pipeline',sid:'same-session'}));
    } finally { await db.exec('RESET ROLE'); }
  }
  await db.query('UPDATE carousel_workspaces SET enabled=false WHERE id=$1',[B]);
  await assert.rejects(()=>getConfig(B));
  console.log('PASS PostgreSQL: verified MFA enrollment detection; preserved legacy data; isolated settings, identical session IDs, app state, designs, leases and deletes; denied browser/direct RPC and disabled accounts.');
 } finally { await db.close(); }
})().catch(e=>{console.error(e.message);process.exitCode=1});
