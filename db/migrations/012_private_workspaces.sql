-- Apply once, in a transaction, before deploying the multi-user application.
-- Existing shared data is assigned explicitly to haroon@closefuture.io.
BEGIN;
CREATE TABLE public.carousel_workspaces (
 id uuid PRIMARY KEY REFERENCES auth.users(id), email text NOT NULL UNIQUE,
 enabled boolean NOT NULL DEFAULT true, created_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE public.carousel_workspaces ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.carousel_workspaces FROM PUBLIC,anon,authenticated;

DO $$ BEGIN
 IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='carousel_worker') THEN
  CREATE ROLE carousel_worker NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS;
 END IF;
END $$;
DO $$ BEGIN
 EXECUTE format('GRANT carousel_worker TO %I',current_user);
END $$;
GRANT USAGE, CREATE ON SCHEMA public,carousel_internal TO carousel_worker;

CREATE FUNCTION carousel_internal.workspace_id() RETURNS uuid
LANGUAGE sql STABLE SET search_path=pg_catalog AS $$
 SELECT nullif(current_setting('carousel.workspace',true),'')::uuid
$$;
REVOKE ALL ON FUNCTION carousel_internal.workspace_id() FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION carousel_internal.workspace_id() TO carousel_worker;

INSERT INTO public.carousel_workspaces(id,email,enabled)
 SELECT u.id,lower(u.email),NOT COALESCE(a.disabled,false)
 FROM auth.users u LEFT JOIN public.app_users a ON lower(a.email)=lower(u.email)
 WHERE u.email_confirmed_at IS NOT NULL;

-- Keep every existing row, including paused sessions and encrypted settings.
DO $$ DECLARE legacy uuid; t text; c record; cols text; BEGIN
 SELECT id INTO STRICT legacy FROM public.carousel_workspaces WHERE email='haroon@closefuture.io';
 PERFORM set_config('carousel.workspace',legacy::text,true);
 -- The session FK must be rebuilt with account ownership included.
 FOR c IN SELECT conname FROM pg_constraint WHERE conrelid='public.events'::regclass AND contype='f' LOOP
  EXECUTE format('ALTER TABLE public.events DROP CONSTRAINT %I',c.conname);
 END LOOP;
 FOREACH t IN ARRAY ARRAY['news_queue','runs','feedback','pending_reviews','run_events',
  'app_config','sessions','events','app_states','user_states','memory_entries','instagram_accounts','carousel_designs'] LOOP
  EXECUTE format('ALTER TABLE public.%I ADD COLUMN tenant_id uuid NOT NULL DEFAULT carousel_internal.workspace_id() REFERENCES public.carousel_workspaces(id)',t);
  -- All uniqueness boundaries include the tenant, even URL hashes and presets.
  FOR c IN SELECT conname,contype,pg_get_constraintdef(oid) AS definition FROM pg_constraint
    WHERE conrelid=format('public.%I',t)::regclass AND contype IN ('p','u') LOOP
   cols:=substring(c.definition FROM '\(([^)]+)\)');
   EXECUTE format('ALTER TABLE public.%I DROP CONSTRAINT %I',t,c.conname);
   EXECUTE format('ALTER TABLE public.%I ADD CONSTRAINT %I %s (tenant_id,%s)',t,c.conname,
    CASE c.contype WHEN 'p' THEN 'PRIMARY KEY' ELSE 'UNIQUE' END,cols);
  END LOOP;
  EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY',t);
  EXECUTE format('CREATE POLICY workspace_owner ON public.%I TO carousel_worker USING (tenant_id=carousel_internal.workspace_id()) WITH CHECK (tenant_id=carousel_internal.workspace_id())',t);
  EXECUTE format('GRANT SELECT,INSERT,UPDATE,DELETE ON public.%I TO carousel_worker',t);
  EXECUTE format('CREATE INDEX ON public.%I (tenant_id)',t);
 END LOOP;
 ALTER TABLE public.events ADD CONSTRAINT events_workspace_session_fkey
  FOREIGN KEY(tenant_id,app_name,user_id,session_id) REFERENCES public.sessions(tenant_id,app_name,user_id,id) ON DELETE CASCADE;
 -- Pre-existing personal design libraries retain their actual owner.
 UPDATE public.carousel_designs d SET tenant_id=w.id FROM public.carousel_workspaces w WHERE lower(d.owner_email)=w.email;
 ALTER TABLE carousel_internal.job_leases ADD COLUMN tenant_id uuid NOT NULL DEFAULT carousel_internal.workspace_id() REFERENCES public.carousel_workspaces(id);
 ALTER TABLE carousel_internal.job_leases DROP CONSTRAINT job_leases_pkey;
 ALTER TABLE carousel_internal.job_leases ADD PRIMARY KEY(tenant_id,job);
 CREATE POLICY workspace_owner ON carousel_internal.job_leases TO carousel_worker
  USING(tenant_id=carousel_internal.workspace_id()) WITH CHECK(tenant_id=carousel_internal.workspace_id());
 GRANT SELECT,INSERT,UPDATE,DELETE ON carousel_internal.job_leases TO carousel_worker;
END $$;
GRANT USAGE,SELECT ON ALL SEQUENCES IN SCHEMA public TO carousel_worker;
GRANT SELECT ON public.carousel_workspaces TO carousel_worker;
CREATE POLICY workspace_owner ON public.carousel_workspaces TO carousel_worker USING(id=carousel_internal.workspace_id());

-- Fixed query functions now execute as a restricted role, not a table owner.
-- Preserve operation IDs and typed argument contracts from migrations 010/011.
DO $$ DECLARE f record; definition text; BEGIN
 FOR f IN SELECT p.oid,p.oid::regprocedure AS signature,n.nspname,p.proname FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
  WHERE (n.nspname='carousel_internal' AND p.proname LIKE 'q_%') OR
   (n.nspname='public' AND p.proname IN ('carousel_query','carousel_query_batch','carousel_config_compare_swap',
    'carousel_delete_run','carousel_replace_designs','carousel_replace_memory','carousel_job_lease',
    'carousel_session_get','carousel_session_create','carousel_session_append','carousel_session_list',
    'carousel_session_delete','carousel_user_state')) LOOP
  definition:=pg_get_functiondef(f.oid);
  IF definition !~* 'INSERT INTO app_users' THEN
   definition:=regexp_replace(definition,'ON CONFLICT\s*\(', 'ON CONFLICT (tenant_id,','gi');
  END IF;
  EXECUTE definition;
  EXECUTE format('ALTER FUNCTION %s OWNER TO carousel_worker',f.signature);
  EXECUTE format('REVOKE ALL ON FUNCTION %s FROM PUBLIC,anon,authenticated,service_role',f.signature);
 END LOOP;
END $$;
GRANT EXECUTE ON FUNCTION carousel_internal.state_object(jsonb) TO carousel_worker;
REVOKE CREATE ON SCHEMA public,carousel_internal FROM carousel_worker;

-- The server supplies the verified UUID. Browser roles cannot call this RPC.
CREATE FUNCTION public.carousel_tenant_rpc(workspace uuid,operation text,arguments jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE f record; arg text; parts text[]:='{}'; expression text; result jsonb;
BEGIN
 IF workspace IS NULL OR NOT EXISTS(SELECT 1 FROM public.carousel_workspaces WHERE id=workspace AND enabled) THEN
  RAISE EXCEPTION 'Workspace unavailable' USING ERRCODE='42501';
 END IF;
 IF operation NOT IN ('carousel_query','carousel_query_batch','carousel_config_compare_swap',
  'carousel_delete_run','carousel_replace_designs','carousel_replace_memory','carousel_job_lease',
  'carousel_session_get','carousel_session_create','carousel_session_append','carousel_session_list',
  'carousel_session_delete','carousel_user_state') OR jsonb_typeof(arguments)<>'object' THEN
  RAISE EXCEPTION 'Unknown workspace operation' USING ERRCODE='42501';
 END IF;
 PERFORM set_config('carousel.workspace',workspace::text,true);
 SELECT p.* INTO STRICT f FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
  WHERE n.nspname='public' AND p.proname=operation;
 IF EXISTS(SELECT 1 FROM jsonb_object_keys(arguments) k WHERE NOT(k=ANY(f.proargnames))) THEN
  RAISE EXCEPTION 'Unknown argument';
 END IF;
 FOR i IN 1..f.pronargs LOOP
  arg:=f.proargnames[i];
  IF NOT(arguments ? arg) THEN CONTINUE; END IF;
  expression:=CASE WHEN f.proargtypes[i-1] IN ('json'::regtype,'jsonb'::regtype)
   THEN format('($1->%L)::%s',arg,format_type(f.proargtypes[i-1],NULL))
   ELSE format('($1->>%L)::%s',arg,format_type(f.proargtypes[i-1],NULL)) END;
  parts:=array_append(parts,format('%I => %s',arg,expression));
 END LOOP;
 EXECUTE format('SELECT to_jsonb(public.%I(%s))',operation,array_to_string(parts,',')) INTO result USING arguments;
 RETURN result;
END $$;
REVOKE ALL ON FUNCTION public.carousel_tenant_rpc(uuid,text,jsonb) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.carousel_tenant_rpc(uuid,text,jsonb) TO service_role;

CREATE FUNCTION public.carousel_provision_workspace(owner uuid,address text) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE actual text; active boolean;
BEGIN
 SELECT lower(email) INTO actual FROM auth.users WHERE id=owner AND email_confirmed_at IS NOT NULL;
 IF actual IS NULL OR actual<>lower(address) THEN RAISE EXCEPTION 'Verified account required' USING ERRCODE='42501'; END IF;
 IF EXISTS(SELECT 1 FROM public.app_users WHERE lower(email)=actual AND disabled) THEN
  RETURN jsonb_build_object('enabled',false);
 END IF;
 INSERT INTO public.carousel_workspaces(id,email) VALUES(owner,actual)
  ON CONFLICT(id) DO UPDATE SET email=EXCLUDED.email;
 SELECT enabled INTO active FROM public.carousel_workspaces WHERE id=owner;
 RETURN jsonb_build_object('enabled',active);
END $$;
CREATE FUNCTION public.carousel_workspace_owners() RETURNS jsonb
LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
 SELECT COALESCE(jsonb_agg(id ORDER BY id),'[]') FROM public.carousel_workspaces WHERE enabled
$$;
REVOKE ALL ON FUNCTION public.carousel_provision_workspace(uuid,text),public.carousel_workspace_owners() FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.carousel_provision_workspace(uuid,text),public.carousel_workspace_owners() TO service_role;
-- Storage remains private and browser access remains denied. The backend
-- checks ownership and prefixes every operation with users/<verified UUID>/.
NOTIFY pgrst,'reload schema';
COMMIT;
