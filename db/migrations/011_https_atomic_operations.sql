-- Atomic operations used by the Supabase HTTPS backend. No caller-supplied SQL.
BEGIN;
CREATE SCHEMA IF NOT EXISTS carousel_internal;
REVOKE ALL ON SCHEMA carousel_internal FROM PUBLIC,anon,authenticated;

CREATE OR REPLACE FUNCTION carousel_internal.state_object(value jsonb) RETURNS jsonb
LANGUAGE sql IMMUTABLE SET search_path=pg_catalog AS $$
 SELECT CASE WHEN jsonb_typeof(value)='string' THEN (value #>> '{}')::jsonb
             WHEN jsonb_typeof(value)='object' THEN value ELSE '{}'::jsonb END
$$;

CREATE OR REPLACE FUNCTION public.carousel_config_compare_swap(
 config_key text, expected jsonb, replacement jsonb, expected_exists boolean) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE affected bigint;
BEGIN
 IF expected_exists THEN
  UPDATE public.app_config SET value=replacement,updated_at=now()
   WHERE key=config_key AND value IS NOT DISTINCT FROM expected;
 ELSE
  INSERT INTO public.app_config(key,value) VALUES(config_key,replacement) ON CONFLICT(key) DO NOTHING;
 END IF;
 GET DIAGNOSTICS affected=ROW_COUNT;
 RETURN affected=1;
END $$;

CREATE OR REPLACE FUNCTION public.carousel_delete_run(app text,usr text,sid text) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE news text; n bigint; output jsonb:='{}'; t text;
BEGIN
 SELECT news_id INTO news FROM public.runs WHERE run_id=sid FOR UPDATE;
 FOREACH t IN ARRAY ARRAY['run_events','pending_reviews','feedback','runs'] LOOP
  EXECUTE format('DELETE FROM public.%I WHERE run_id=$1',t) USING sid;
  GET DIAGNOSTICS n=ROW_COUNT; output:=output||jsonb_build_object(t,n);
 END LOOP;
 DELETE FROM public.memory_entries WHERE app_name=app AND user_id=usr AND session_id=sid;
 GET DIAGNOSTICS n=ROW_COUNT; output:=output||jsonb_build_object('memory_entries',n);
 DELETE FROM public.events WHERE app_name=app AND user_id=usr AND session_id=sid;
 GET DIAGNOSTICS n=ROW_COUNT; output:=output||jsonb_build_object('events',n);
 DELETE FROM public.sessions WHERE app_name=app AND user_id=usr AND id=sid;
 GET DIAGNOSTICS n=ROW_COUNT; output:=output||jsonb_build_object('sessions',n);
 IF news IS NOT NULL THEN
  UPDATE public.news_queue SET status='queued' WHERE id=news AND status='processing';
  GET DIAGNOSTICS n=ROW_COUNT; output:=output||jsonb_build_object('requeued',n);
 END IF;
 RETURN output;
END $$;

CREATE OR REPLACE FUNCTION public.carousel_replace_designs(owner text,designs jsonb) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE item jsonb; position bigint;
BEGIN
 IF jsonb_typeof(designs) IS DISTINCT FROM 'array' THEN RAISE EXCEPTION 'Expected designs array'; END IF;
 -- Serialize replacement of one library, including the initially empty case.
 PERFORM pg_advisory_xact_lock(hashtextextended('carousel-designs:'||owner,0));
 DELETE FROM public.carousel_designs WHERE owner_email=owner
  AND NOT (design_id IN (SELECT value->>'id' FROM jsonb_array_elements(designs)));
 FOR item,position IN SELECT value,ordinality-1 FROM jsonb_array_elements(designs) WITH ORDINALITY LOOP
  INSERT INTO public.carousel_designs(owner_email,design_id,name,payload,sort_order)
   VALUES(owner,item->>'id',item->>'name',item,position)
   ON CONFLICT(owner_email,design_id) DO UPDATE SET name=EXCLUDED.name,payload=EXCLUDED.payload,
     sort_order=EXCLUDED.sort_order,updated_at=now();
 END LOOP;
END $$;

CREATE OR REPLACE FUNCTION public.carousel_replace_memory(app text,usr text,sid text,entries jsonb) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE item jsonb;
BEGIN
 IF jsonb_typeof(entries) IS DISTINCT FROM 'array' THEN RAISE EXCEPTION 'Expected memory array'; END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended('carousel-memory:'||jsonb_build_array(app,usr,sid)::text,0));
 DELETE FROM public.memory_entries WHERE app_name=app AND user_id=usr AND session_id=sid;
 FOR item IN SELECT value FROM jsonb_array_elements(entries) LOOP
  INSERT INTO public.memory_entries(app_name,user_id,session_id,event_id,author,role,text_content,event_ts)
   VALUES(app,usr,sid,item->>3,item->>4,item->>5,item->>6,item->>7)
   ON CONFLICT(app_name,user_id,session_id,event_id) DO UPDATE SET
    text_content=EXCLUDED.text_content,author=EXCLUDED.author,role=EXCLUDED.role,event_ts=EXCLUDED.event_ts;
 END LOOP;
END $$;

CREATE TABLE IF NOT EXISTS carousel_internal.job_leases(
 job text PRIMARY KEY, owner text NOT NULL, expires_at timestamptz NOT NULL
);
ALTER TABLE carousel_internal.job_leases ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON carousel_internal.job_leases FROM PUBLIC,anon,authenticated;
CREATE OR REPLACE FUNCTION public.carousel_job_lease(job text,owner text,action text) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE n bigint;
BEGIN
 IF action='acquire' THEN
  INSERT INTO carousel_internal.job_leases AS lease(job,owner,expires_at)
   VALUES(carousel_job_lease.job,carousel_job_lease.owner,clock_timestamp()+interval '120 seconds')
   ON CONFLICT ON CONSTRAINT job_leases_pkey DO UPDATE SET owner=EXCLUDED.owner,expires_at=EXCLUDED.expires_at
    WHERE lease.expires_at<clock_timestamp();
 ELSIF action='renew' THEN
  UPDATE carousel_internal.job_leases l SET expires_at=clock_timestamp()+interval '120 seconds'
   WHERE l.job=carousel_job_lease.job AND l.owner=carousel_job_lease.owner AND l.expires_at>clock_timestamp();
 ELSIF action='release' THEN
  DELETE FROM carousel_internal.job_leases l WHERE l.job=carousel_job_lease.job AND l.owner=carousel_job_lease.owner;
 ELSE RAISE EXCEPTION 'Unknown lease action'; END IF;
 GET DIAGNOSTICS n=ROW_COUNT; RETURN n=1;
END $$;

-- One snapshot for merged session state, event selection and revision marker.
CREATE OR REPLACE FUNCTION public.carousel_session_get(app text,usr text,sid text,
 recent integer DEFAULT NULL,after_ts double precision DEFAULT NULL) RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE s public.sessions%ROWTYPE; events_json jsonb; app_json jsonb; user_json jsonb;
BEGIN
 SELECT * INTO s FROM public.sessions WHERE app_name=app AND user_id=usr AND id=sid;
 IF NOT FOUND THEN RETURN NULL; END IF;
 SELECT carousel_internal.state_object(state) INTO app_json FROM public.app_states WHERE app_name=app;
 SELECT carousel_internal.state_object(state) INTO user_json FROM public.user_states WHERE app_name=app AND user_id=usr;
 SELECT COALESCE(jsonb_agg(to_jsonb(e) ORDER BY e.timestamp,e.id),'[]') INTO events_json FROM (
  SELECT id,invocation_id,timestamp,event_data FROM public.events
   WHERE app_name=app AND user_id=usr AND session_id=sid
    AND (after_ts IS NULL OR timestamp>=to_timestamp(after_ts) AT TIME ZONE 'UTC')
   ORDER BY timestamp DESC,id DESC LIMIT recent
 ) e;
 RETURN jsonb_build_object('id',s.id,'app_name',s.app_name,'user_id',s.user_id,
  'state',carousel_internal.state_object(s.state),'app_state',COALESCE(app_json,'{}'),
  'user_state',COALESCE(user_json,'{}'),'events',events_json,
  'updated',extract(epoch FROM s.update_time),'revision',s.update_time::text);
END $$;

CREATE OR REPLACE FUNCTION public.carousel_session_create(app text,usr text,sid text,
 session_state jsonb,app_delta jsonb,user_delta jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
BEGIN
 INSERT INTO public.app_states(app_name,state,update_time) VALUES(app,COALESCE(app_delta,'{}'),clock_timestamp() AT TIME ZONE 'UTC')
  ON CONFLICT(app_name) DO UPDATE SET state=carousel_internal.state_object(app_states.state)||EXCLUDED.state,update_time=EXCLUDED.update_time;
 INSERT INTO public.user_states(app_name,user_id,state,update_time) VALUES(app,usr,COALESCE(user_delta,'{}'),clock_timestamp() AT TIME ZONE 'UTC')
  ON CONFLICT(app_name,user_id) DO UPDATE SET state=carousel_internal.state_object(user_states.state)||EXCLUDED.state,update_time=EXCLUDED.update_time;
 INSERT INTO public.sessions(app_name,user_id,id,state,create_time,update_time)
  VALUES(app,usr,sid,COALESCE(session_state,'{}'),clock_timestamp() AT TIME ZONE 'UTC',clock_timestamp() AT TIME ZONE 'UTC');
 RETURN public.carousel_session_get(app,usr,sid,0,NULL);
END $$;

CREATE OR REPLACE FUNCTION public.carousel_session_append(app text,usr text,sid text,
 expected_revision text,event jsonb,session_delta jsonb,app_delta jsonb,user_delta jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE s public.sessions%ROWTYPE; updated timestamp;
BEGIN
 SELECT * INTO s FROM public.sessions WHERE app_name=app AND user_id=usr AND id=sid FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'Session not found' USING ERRCODE='P0002'; END IF;
 -- A lost HTTP response requires reloading the session before another write.
 -- Never advance an old in-memory session past events it has not observed.
 IF expected_revision IS NULL OR s.update_time::text<>expected_revision THEN
  RAISE EXCEPTION 'Stale session' USING ERRCODE='PT409';
 END IF;
 UPDATE public.app_states SET state=carousel_internal.state_object(state)||COALESCE(app_delta,'{}'),update_time=clock_timestamp() AT TIME ZONE 'UTC'
  WHERE app_name=app AND app_delta<>'{}'::jsonb;
 UPDATE public.user_states SET state=carousel_internal.state_object(state)||COALESCE(user_delta,'{}'),update_time=clock_timestamp() AT TIME ZONE 'UTC'
  WHERE app_name=app AND user_id=usr AND user_delta<>'{}'::jsonb;
 updated:=greatest(clock_timestamp() AT TIME ZONE 'UTC',s.update_time+interval '1 microsecond');
 UPDATE public.sessions SET state=carousel_internal.state_object(state)||COALESCE(session_delta,'{}'),update_time=updated
  WHERE app_name=app AND user_id=usr AND id=sid;
 INSERT INTO public.events(id,app_name,user_id,session_id,invocation_id,timestamp,event_data)
  VALUES(event->>'id',app,usr,sid,COALESCE(event->>'invocation_id',''),
    to_timestamp((event->>'timestamp')::double precision) AT TIME ZONE 'UTC',event);
 RETURN jsonb_build_object('updated',extract(epoch FROM updated),'revision',updated::text);
END $$;

CREATE OR REPLACE FUNCTION public.carousel_session_list(app text,usr text DEFAULT NULL) RETURNS jsonb
LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
 SELECT COALESCE(jsonb_agg(public.carousel_session_get(app,s.user_id,s.id,0,NULL) ORDER BY s.update_time,s.id),'[]')
 FROM public.sessions s WHERE s.app_name=app AND (usr IS NULL OR s.user_id=usr)
$$;
CREATE OR REPLACE FUNCTION public.carousel_session_delete(app text,usr text,sid text) RETURNS void
LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
 DELETE FROM public.sessions WHERE app_name=app AND user_id=usr AND id=sid
$$;
CREATE OR REPLACE FUNCTION public.carousel_user_state(app text,usr text) RETURNS jsonb
LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
 SELECT COALESCE((SELECT carousel_internal.state_object(state) FROM public.user_states WHERE app_name=app AND user_id=usr),'{}')
$$;

DO $$ DECLARE fn regprocedure; BEGIN
 FOR fn IN SELECT p.oid::regprocedure FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
  WHERE n.nspname='public' AND p.proname IN (
   'carousel_config_compare_swap','carousel_delete_run','carousel_replace_designs','carousel_replace_memory',
   'carousel_job_lease','carousel_session_get','carousel_session_create','carousel_session_append',
   'carousel_session_list','carousel_session_delete','carousel_user_state') LOOP
  EXECUTE format('REVOKE ALL ON FUNCTION %s FROM PUBLIC,anon,authenticated',fn);
  EXECUTE format('GRANT EXECUTE ON FUNCTION %s TO service_role',fn);
 END LOOP;
END $$;
NOTIFY pgrst,'reload schema';
COMMIT;
