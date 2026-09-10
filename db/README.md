# Moving the database

Everything needed to stand this service up against a different Supabase project, with
the same data and the same media, and change nothing in the application.

## What is where

| File | What it is |
|---|---|
| `schema.sql` | The original hand-written schema. Historical; `005` supersedes it. |
| `migrations/002_web_app.sql` | Console columns on `runs`, `feedback` reconciliation. |
| `migrations/003_lockdown.sql` | Row-level security. |
| `migrations/004_title_lock.sql` | `runs.title_locked`. Also ensured by migration `010`. |
| `migrations/005_transfer_baseline.sql` | **Every table, in one file.** Apply this to an empty database and you have the whole structure. |
| `migrations/006_instagram_accounts.sql` | `instagram_accounts` + `runs.account_id`. Apply after `005`. |
| `migrations/007_carousel_designs.sql` | User-owned carousel design contracts. Apply after `005`. |
| `migrations/008_read_indexes.sql` | Indexes for task history, trace ordering and email lookup. |
| `migrations/009_enforce_access_policies.sql` | Restrictive browser-role policies and private media protection. Apply with `scripts/db_enforce_policies.py --apply`. |
| `../scripts/db_enforce_policies.py` | Rehearse policy changes with rollback, or apply after verifying role denials and unchanged row counts. |
| `../scripts/db_export.py` | Dump every table to JSONL. |
| `../scripts/db_import.py` | Load a dump into a target. |
| `../scripts/db_verify_baseline.py` | Check `005` against a live database. |
| `../scripts/media_backup.py` | Mirror the storage bucket to disk. |
| `../scripts/media_restore.py` | Upload a mirror into a bucket, then verify it. |

`005` declares the operational and ADK tables used by the application.
Tables are now provisioned during setup; the HTTPS runtime performs no DDL.
Apply migrations `006` through `011` after the baseline. `010` installs the
fixed-query dispatcher and ensures `runs.title_locked`; `011` adds atomic
session, memory, configuration, design and job-lease operations.
See [HTTPS setup](https-database.md).

## Read this before you start

**The dumps contain secrets.** `app_config` holds the Telegram bot credentials
encrypted with `SECRETS_KEY`, and `sessions` holds whatever the pipeline put in
session state. `backups/` is gitignored — keep it that way, and do not paste
these files anywhere.

`instagram_accounts.token_enc` is encrypted the same way and carries the same
warning: each row is an access token that can post to somebody's Instagram.

**`SECRETS_KEY` must move with the data.** Those credentials are Fernet-encrypted
against it. Restore `app_config` into a deployment with a different key and the
rows decrypt to nothing; the console will tell you to reconnect the bot from the
profile page, which is the honest outcome but means re-entering the token. The same is true of every row in
`instagram_accounts`: a different key means every account shows as needing
reconnection, and each has to be connected again through Instagram.

**Copy `app_users` or you cannot sign in.** Auth is an allowlist. An empty table
means nobody has access, including you.

## The transfer

Take the backups while nothing is running, so the dump is not a snapshot of a
half-written run.

```bash
# 1. Back up. Both are read-only and re-runnable.
.venv/Scripts/python.exe scripts/db_export.py --dsn "$SOURCE_ADMIN_DSN" # -> backups/db-<timestamp>/
.venv/Scripts/python.exe scripts/media_backup.py       # -> backups/media/<bucket>/

# 2. Confirm the baseline still describes the source. Should print
#    "The migration covers every table and column this database has."
.venv/Scripts/python.exe scripts/db_verify_baseline.py --dsn "$SOURCE_ADMIN_DSN"

# 3. Create the structure on the target. Do this BEFORE starting the app -
#    if ADK boots first it creates its own tables, and a shape that disagrees
#    with the dump is a restore that fails halfway.
psql "$NEW_DATABASE_URL" -f db/migrations/005_transfer_baseline.sql

# 4. Rehearse the restore, then do it.
.venv/Scripts/python.exe scripts/db_import.py backups/db-<timestamp> \
    --dsn "$NEW_DATABASE_URL" --dry-run
.venv/Scripts/python.exe scripts/db_import.py backups/db-<timestamp> \
    --dsn "$NEW_DATABASE_URL"

# 5. Create the storage bucket on the new project by hand (same name as
#    MEDIA_BUCKET), then push the media and let the script verify it.
#    Point .env at the NEW project first - media_restore reads the same
#    settings the app does.
.venv/Scripts/python.exe scripts/media_restore.py backups/media --dry-run
.venv/Scripts/python.exe scripts/media_restore.py backups/media

# 6. Point the baseline check at the target. Same sentence as step 2.
.venv/Scripts/python.exe scripts/db_verify_baseline.py --dsn "$NEW_DATABASE_URL"
```

Then update `.env`: the server-only Supabase key,
`SUPABASE_URL` / `SUPABASE_ANON_KEY` if the project changed, and keep
`SECRETS_KEY` and `MEDIA_BUCKET` exactly as they were.

## Things that will bite

**Object keys are addresses, not filenames.** `media_backup` mirrors the bucket
using the keys as directory paths and `media_restore` puts them back under the
same keys. That is what lets existing bundles keep resolving — a run's
`ordered_artifacts` holds keys, not URLs, and a re-keyed object is a broken
carousel.

**Sequences travel separately from rows.** `db_import` calls `setval` after
loading, from the manifest. Skip that and the first new row collides with id 1.

**`events` references `sessions`.** Both scripts load parents first; if you
restore by hand, keep that order.

**Application traffic uses HTTPS.** Pooler ports and prepared-statement caches
are not application settings. Admin export/import utilities may still use a
temporary PostgreSQL connection appropriate for the migration host.

**Row-level security remains restrictive.** Migration `009` denies browser
roles access to application tables. Migrations `010` and `011` permit only the
backend service role to execute the application's fixed HTTPS RPCs.
