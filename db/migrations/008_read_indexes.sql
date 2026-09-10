-- Additive indexes for the actual console read paths. No data is rewritten.
-- A short lock timeout fails safely if another deployment is changing schema.
BEGIN;
SET LOCAL lock_timeout = '3s';
SET LOCAL statement_timeout = '60s';

-- Trace ordering includes id to break equal timestamps deterministically.
CREATE INDEX IF NOT EXISTS idx_events_session_timeline
    ON public.events (app_name, user_id, session_id, timestamp, id);

-- Filtered task history: WHERE status = ... ORDER BY created_at DESC.
CREATE INDEX IF NOT EXISTS idx_runs_status_created
    ON public.runs (status, created_at DESC);

-- Sign-in checks normalize email; the existing case-sensitive PK cannot
-- satisfy this expression. Non-unique to preserve any existing mixed-case rows.
CREATE INDEX IF NOT EXISTS idx_app_users_lower_email
    ON public.app_users (lower(email));
COMMIT;
