# Enforced database access

Migration 009 was applied and verified on 2026-09-10 against the configured
Supabase project. It preserves the application's existing access model:
Supabase Auth signs users in, then the authenticated backend API handles data
and media access. The SPA does not query application tables directly.

- All 15 application tables have RLS enabled and a restrictive `FOR ALL`
  policy denying `anon` and `authenticated` access. The false `USING` and
  `WITH CHECK` expressions protect reads and writes even if someone later
  adds a permissive policy.
- Table and column privileges are revoked from `PUBLIC`, `anon` and
  `authenticated`. Application sequences and public functions also deny
  browser-role access. The existing RLS event trigger remains installed.
- Backend-owned future public tables and sequences start without browser
  grants. Future functions created by the migration's role have no default
  PUBLIC EXECUTE grant, and no explicit anon/authenticated grant in public.
  Defaults apply to that creating role, not Supabase-managed roles.
- The configured private media bucket has restrictive policies on both
  `storage.objects` and `storage.buckets`. They do not change other buckets'
  policies. The bucket identifier is embedded as a SQL literal, so clients
  cannot bypass the policy by changing a session setting.

The configured backend PostgreSQL role has `BYPASSRLS`. Supabase service-role
credentials and S3 access keys also bypass RLS. These policies therefore
protect browser/direct API access; server authorization remains necessary.
No server credential was retrieved, added or changed by this migration.

Run `.venv/Scripts/python.exe -B scripts/db_enforce_policies.py` to rehearse
inside a rolled-back transaction. Add `--apply` to commit after validation.
The runner uses existing `DATABASE_URL` and `MEDIA_BUCKET` settings. It checks
30 denied browser reads, verifies restrictive policies against temporary
permissive-policy probes, rejects browser inserts and compares every
application table's row count before committing. All probe changes roll back.

All existing application row counts were preserved when migration 009 committed.

References: [PostgreSQL restrictive policies](https://www.postgresql.org/docs/current/sql-createpolicy.html),
[Supabase Storage access control](https://supabase.com/docs/guides/storage/security/access-control),
[S3 authentication](https://supabase.com/docs/guides/storage/s3/authentication).
