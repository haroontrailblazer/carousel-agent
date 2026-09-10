-- Apply through scripts/db_enforce_policies.py, which supplies MEDIA_BUCKET.
-- The SPA uses Supabase Auth, then the authenticated backend API. It does not
-- read application tables directly through PostgREST or the Storage API.
BEGIN;
SET LOCAL lock_timeout = '3s';
SET LOCAL statement_timeout = '60s';

DO $$
DECLARE
    t text;
    columns_sql text;
BEGIN
    FOREACH t IN ARRAY ARRAY[
        'news_queue', 'runs', 'feedback', 'pending_reviews', 'memory_entries',
        'run_events', 'app_users', 'app_config', 'sessions', 'events',
        'app_states', 'user_states', 'adk_internal_metadata',
        'instagram_accounts', 'carousel_designs'
    ] LOOP
        IF to_regclass(format('public.%I', t)) IS NULL THEN
            RAISE EXCEPTION 'Required application table is missing: %', t;
        END IF;
        EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', t);
        EXECUTE format('REVOKE ALL ON TABLE public.%I FROM PUBLIC, anon, authenticated', t);

        -- Column-level grants survive a table-level REVOKE.
        SELECT string_agg(quote_ident(attname), ', ' ORDER BY attnum)
          INTO columns_sql FROM pg_attribute
         WHERE attrelid = to_regclass(format('public.%I', t))
           AND attnum > 0 AND NOT attisdropped;
        EXECUTE format(
            'REVOKE SELECT (%s), INSERT (%s), UPDATE (%s), REFERENCES (%s) '
            'ON TABLE public.%I FROM PUBLIC, anon, authenticated',
            columns_sql, columns_sql, columns_sql, columns_sql, t);

        -- Restrictive policies AND with any future permissive policies.
        -- A permissive USING(false) policy alone would not provide that guard.
        EXECUTE format('DROP POLICY IF EXISTS carousel_server_only ON public.%I', t);
        EXECUTE format(
            'CREATE POLICY carousel_server_only ON public.%I AS RESTRICTIVE '
            'FOR ALL TO anon, authenticated USING (false) WITH CHECK (false)', t);
    END LOOP;
END $$;

REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM PUBLIC, anon, authenticated;
-- The audited public schema contains only the automatic RLS event-trigger
-- function. Trigger invocation does not need browser EXECUTE privileges.
REVOKE EXECUTE ON ALL FUNCTIONS IN SCHEMA public FROM PUBLIC, anon, authenticated;

-- Backend-owned future objects should start private. PostgreSQL's built-in
-- PUBLIC function EXECUTE default is global; a schema-only REVOKE cannot remove
-- it, so remove that default globally for this creating role, then the explicit
-- Supabase browser-role defaults in public. Existing functions are unaffected.
ALTER DEFAULT PRIVILEGES REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    REVOKE EXECUTE ON FUNCTIONS FROM anon, authenticated;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    REVOKE ALL ON TABLES FROM PUBLIC, anon, authenticated;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    REVOKE ALL ON SEQUENCES FROM PUBLIC, anon, authenticated;

DO $$
DECLARE
    bucket text := NULLIF(current_setting('carousel.media_bucket', true), '');
BEGIN
    IF bucket IS NULL THEN
        RAISE EXCEPTION 'Set carousel.media_bucket to the application MEDIA_BUCKET first';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM storage.buckets WHERE id = bucket AND NOT public) THEN
        RAISE EXCEPTION 'Configured media bucket must exist and be private';
    END IF;
    -- Only the configured bucket is restricted. Other buckets retain their
    -- own policies. Interpolate a SQL literal, never a client-settable setting.
    DROP POLICY IF EXISTS carousel_media_server_only ON storage.objects;
    EXECUTE format(
        'CREATE POLICY carousel_media_server_only ON storage.objects AS RESTRICTIVE '
        'FOR ALL TO anon, authenticated '
        'USING (bucket_id IS DISTINCT FROM %L) WITH CHECK (bucket_id IS DISTINCT FROM %L)',
        bucket, bucket);
    DROP POLICY IF EXISTS carousel_media_bucket_server_only ON storage.buckets;
    EXECUTE format(
        'CREATE POLICY carousel_media_bucket_server_only ON storage.buckets AS RESTRICTIVE '
        'FOR ALL TO anon, authenticated '
        'USING (id IS DISTINCT FROM %L) WITH CHECK (id IS DISTINCT FROM %L)', bucket, bucket);
END $$;

COMMIT;
