# Supabase storage and database reads

Egress is data sent out of Supabase, including database query results and
Storage downloads. Stored bytes and egress are different measurements.
Using the native Storage API instead of S3 does not itself reduce egress.

## Configuration

| Setting | Used by | Purpose |
| --- | --- | --- |
| `SUPABASE_URL` | Browser and server | Project URL |
| `SUPABASE_ANON_KEY` | Browser | Supabase Auth; public by design |
| `DATABASE_URL` | Server only | PostgreSQL session and application tables |
| `SUPABASE_SECRET_KEY` or `SUPABASE_SERVICE_ROLE_KEY` | Server only | Background Storage uploads, private reads, signing and deletion |
| `MEDIA_BUCKET` | Server | Existing private bucket; preserve the configured spelling |

Do not put management personal access tokens in application configuration.
Native Storage rejects anon, publishable and management tokens as its server
credential. Secret and service-role keys grant elevated access; keep them in
the deployment's server environment, never in `VITE_*` or frontend responses.

The native adapter keeps existing object keys and ADK metadata. Version URIs
remain `s3://bucket/key` as stable identifiers, even when transport is HTTP.
The artifact service and media backup/restore utilities select native Storage
when a server Storage key is present. Legacy S3 credentials continue working
until that key has been configured and the new connection verified.

## Changes that reduce transfers

- Trace reads validate a compact PostgreSQL MVCC revision fingerprint before
  fetching event payloads again. Inserts, updates and deletions invalidate the
  snapshot. Cache keys include the database pool, app, user, session and page.
  Each process retains at most 64 entries / 16 MiB of serialized payloads;
  actual Python object memory is larger. Restarting a worker clears its cache.
- Lifecycle merging selects only terminal/error records with no agent author,
  avoiding repeated downloads of the entire second event log.
- Console session reads project only the state fields used by the routes.
  Both object and legacy JSON-string state encoding are supported.
- Versioned media URLs are reused until shortly before expiration, with
  separate preview and publishing lifetimes. New uploads have one-hour private
  cache headers; replacing an artifact creates a new version/path.
- Migration `008_read_indexes.sql` adds indexes for trace ordering, filtered
  task history and case-insensitive email lookup. Indexes reduce database work;
  they do not directly reduce the number of response bytes.

## Live verification on 2026-09-10

The configured project had 15 application tables, all with RLS enabled and no
direct anon/authenticated SELECT grants. Its private media bucket contained
28 objects totaling 22,403,737 bytes. No access policies were loosened.

Accumulated query statistics showed 2,837 transcript reads returning 93,457
rows, and 5,991 full-state reads. These statistics cover the database's current
statistics window, not necessarily the billing month, and do not establish a
precise billed-egress total.

The projected session states totaled 15,659 bytes versus 37,506 bytes before
projection (58% fewer bytes for this snapshot). Two consecutive reads of a
17-event transcript fetched the 62,649-byte payload once. These are query-level
measurements, not a promised percentage reduction in the Supabase bill.

Migration 008 was applied to the configured database and its three indexes
verified; the event row count remained 141. The application optimizations
require deploying the updated code. Native Storage activation still requires
approval to retrieve/store its privileged server credential and a live smoke
test; the existing S3 configuration remains in use until then.

## Rollout and monitoring

1. Configure one server Storage key in the local/deployment secret environment.
2. Keep the existing private bucket and existing S3 keys during validation.
3. Verify native upload, download, metadata, listing, signing and deletion with
   a uniquely named test object. Verify an existing carousel and profile avatar.
4. Deploy the application changes, then remove the unused S3 configuration.
5. Compare Supabase Usage egress by product over equal traffic/time windows.
   Keep cached and uncached Storage egress separate. Do not delete transcripts
   or media as an automatic cleanup policy without an agreed retention period.

References: [Egress usage](https://supabase.com/docs/guides/platform/manage-your-usage/egress),
[API keys](https://supabase.com/docs/guides/getting-started/api-keys),
[Storage access control](https://supabase.com/docs/guides/storage/security/access-control).
