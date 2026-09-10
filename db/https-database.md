# Supabase HTTPS database

The app uses `SUPABASE_URL` for database RPCs, saved ADK sessions, memory,
Auth and native Storage. `DATABASE_URL` is not read by the application.

Required Supabase settings:

| Setting | Purpose |
| --- | --- |
| `SUPABASE_URL` | Project HTTPS endpoint |
| `SUPABASE_ANON_KEY` | Browser Auth only |
| `SUPABASE_SERVICE_ROLE_KEY` or `SUPABASE_SECRET_KEY` | Backend database and Storage access; use one |
| `MEDIA_BUCKET` | Existing private media bucket |

Keep the server key out of frontend responses and `VITE_*` variables. RLS
continues to deny browser roles access to the application tables. Authorization
is enforced by the backend routes before the service-role database operations.

## Database setup

On an empty Supabase project, apply baseline `005` and migrations `006` through
`011` in order through the Dashboard SQL Editor. Migration `009` needs the
configured private bucket; see [access policies](access-policies.md).

On an existing project with `005`–`009`, apply `010_https_query_operations.sql`
and `011_https_atomic_operations.sql`. The existing table data and ADK event
format are retained. Migration `010` also adds the existing missing
`runs.title_locked` column when needed.

The fixed-query dispatcher accepts an operation ID and typed values, never
caller-supplied SQL. Its query bodies are stored in the unexposed
`carousel_internal` schema. Public RPC execution is granted only to
`service_role`; `PUBLIC`, `anon` and `authenticated` have no grants.

Multi-step changes execute inside a single RPC transaction. Config updates use
compare-and-swap; session events check an exact persisted revision before
updating state and inserting the event. Stale sessions return HTTP 409 and
must be reloaded. Writes are not automatically retried after a lost response.
Job leases expire after 120 seconds and renew every 30 seconds while held;
only the current owner can renew or release them.

Session errors use PostgREST's explicit `PT409` status. A business conflict
must not use `40001`, which can trigger automatic transaction retries.
[Supabase's explanation](https://supabase.com/docs/guides/troubleshooting/high-cpu-and-infinite-transaction-retries-when-using-custom-error-codes-in-rpc-functions-77326b).

## Maintenance and verification

Database administration and SQL backup/import tools still require a temporary
administrator connection. This is not application configuration: supply `--dsn`
to those tools, or a temporary `MIGRATION_DATABASE_URL` environment variable
to the RPC migration utilities. Do not save it in the app's `.env` or deployment.

```powershell
# Rehearse all query bodies and atomic operations; roll back every change.
.venv/Scripts/python.exe -B scripts/db_apply_https.py
# Install the migrations after the same checks pass.
.venv/Scripts/python.exe -B scripts/db_apply_https.py --apply
# Exercise the real HTTPS services with isolated, automatically cleaned fixtures.
.venv/Scripts/python.exe -B scripts/db_verify_https.py
```

When changing a static app query, run `scripts/build_db_rpc.py` with the
temporary migration variable available to regenerate the typed catalog and
fixed SQL. Deploy the matching SQL migration before the application code.
The catalog coverage regression test detects missing query registrations.

The standalone `adk web` development server manages its own sessions. Use
the application console or `app.agent.build_runner()` for shared HTTPS-backed
sessions and production review/resume behavior.

Changing the transport does not itself reduce Supabase egress. Existing
projections, revision caches and signed-media URL reuse remain in place.
