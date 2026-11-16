# Import the prepared relay demo

This command copies existing content to Supabase. The lecture API now uses the
imported records and private media automatically when Supabase credentials are
configured. See [runtime setup](../contracts/LECTURE_DEMO.md#supabase-runtime).
Keep the original local content as a backup.

Install the project dependencies in the chosen project environment.
The central config loader accepts DATABASE_URL, SUPABASE_URL and
SUPABASE_SECRET_KEY (or legacy SUPABASE_SERVICE_ROLE_KEY), from the source root's
.env/.env.local or process environment. A [YOUR-PASSWORD] URI placeholder is
replaced with the percent-encoded SUPABASE_DB_PASSWORD. If DATABASE_URL is absent,
the loader derives the standard direct PostgreSQL endpoint from a hosted
NEXT_PUBLIC_SUPABASE_URL (or SUPABASE_URL) and SUPABASE_DB_PASSWORD. This requires
network access to that endpoint, normally over IPv6. An explicit DATABASE_URL
can instead select the dashboard's session pooler for IPv4-only environments.
The publishable key is not used by this server-side importer. Credentials are never
printed; use a server secret, not a browser publishable key.

Run from this checkout, replacing SOURCE_ROOT with the original project folder:

```bash
PYTHONPATH=app/server python tools/import_supabase.py --root SOURCE_ROOT
PYTHONPATH=app/server python tools/import_supabase.py --root SOURCE_ROOT --apply
```

The default run only inventories and validates local files. Apply creates the
reviewed application schema if absent, creates/uses a private rahbar-content
bucket, uploads and downloads every object to verify SHA-256, then transactionally
inserts course/unit/lesson/segment records and anonymous demo session progress.
Existing differing objects or course releases are not overwritten. Retry the same
command after an interrupted upload; matching objects and existing records are
reused. No local source is deleted. Storage and PostgreSQL are not one transaction;
a failed database import can leave verified but unreferenced storage objects.

The export includes the baked lesson JSON, authored lesson source/provenance,
seven referenced WAVs and checksums, two PNG/SVG view pairs, part/view metadata,
asset manifest, checksum manifest, and a separate snapshot of the four current
local demo sessions. Session counts depend on the source database at run time.
Only files referenced by this prepared lesson are imported; raw curriculum,
provider credentials, test recordings, and obsolete audio caches are excluded.

Demo progress is imported under host_key=rahbar-local-demo with one anonymous
principal per original session. These are not learner accounts. Sessions begin
paused with fresh connection epochs; original states remain in the private JSON
snapshot. Existing imported progress is never overwritten on repeat runs.

The course release's manifest_ref is a supabase:// bucket/path locator. The
manifest route_map maps existing local API audio/asset paths to storage keys;
it is not a public URL or an implementation of remote playback. Draft review
status remains unchanged. Reports are written under SOURCE_ROOT/.cache/supabase-migration.
