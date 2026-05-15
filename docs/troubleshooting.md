# Troubleshooting

Common errors, their causes, and fixes. If you hit something not listed
here, check the verbose logs (`er-smart-sync -v ...`) and look for a stack
trace or warning above the failure point.

## `400: ... is not one of the available choices`

```
WARNING: ERClient: Fail attempt 1 of 1: {"field": ["Select a valid choice.
et5e6b96f4_sector is not one of the available choices."]}
```

**Cause:** ER's `/choices/?field=<X>` endpoint validates the `field=` query
value against the set of field names already in the database. For a fresh
tenant, no Choice has the derived `field` name yet, so the GET returns 400.

**Status: handled.** `er-smart-sync` catches this specific 400 and treats it
as "no existing records for this field", then proceeds to POST new records.
The warning still appears in the log but is benign — the sync continues
normally.

If you see this in your log, watch for the choices summary at the end:

```
Choices done: created=929 updated=0 unchanged=0 deactivated=0 errored=0
```

A non-zero `created` count confirms the workaround did its job.

## Every event type shows as `updated` on re-runs

**Cause:** ER serializes the v2 schema as a JSON string on GET (not a dict).
Earlier versions of `er-smart-sync` didn't parse the string back to a dict
before comparing, so the dict-vs-string comparison always reported a diff.

**Status: fixed in 5648b65.** Make sure you're on a current build:

```bash
git pull
uv pip install -e ".[dev]"
```

After the fix, a re-run of `datamodel` against unchanged input should
report:

```
event_types_unchanged: 18
event_types_updated: 0
event_types_created: 0
```

## The sync hangs silently after a few choice sets

**Cause:** A stalled HTTP request with no library-level timeout. Earlier
versions didn't bound socket operations, so a slow ER response or a TCP
connection drop would hang indefinitely.

**Status: fixed.** A process-wide socket timeout (default 600s) is set in
`_set_network_timeout`. After 600s of no activity on any socket, requests
raises and our `_retry` wrapper takes over.

If 600s is too short for your tenant (e.g., very large data models), bump
it:

```bash
er-smart-sync --network-timeout 1800 datamodel ...   # 30 minutes
```

Or set the env var:

```bash
export ER_SMART_SYNC_NETWORK_TIMEOUT=1800
```

Set to `0` to disable our timeout entirely and rely on each library's own.

## `Could not extract a CA identifier from SMART label ...`

```
ValueError: Could not extract a CA identifier from SMART label 'My Reserve'
(ca_uuid=some-ca-uuid). The label must contain a bracketed short code, e.g.
'Foasf Reserve [FOASF]'. Fix the label in SMART Connect, or use --from-file
with an explicit --ca-identifier.
```

**Cause:** API-based runs derive the identifier from the bracketed code in
the SMART CA label. If the label has no `[CODE]`, there's nothing to
extract.

**Two fixes:**

1. **Edit the label in SMART Connect** to add the bracketed code.
2. **Fall back to file-based:**

   ```bash
   er-smart-sync datamodel \
     --from-file ~/datamodel.xml \
     --ca-identifier MYRESERVE
   ```

See [Concept: CA identifier](concepts/ca-identifier.md) for the full story.

## Duplicate-key conflict on event-type POST

```
WARNING: Event type value 'jkperu_incidents_caza_furtiva' exists in this
tenant as v1; skipping the v1 push to avoid cross-version corruption.
Convert it via POST /api/v2.0/activity/eventtypes/migrate/ before retrying
v1.
```

**Cause:** ER's event-type `value` is unique tenant-wide *across* v1 and
v2. If you previously pushed v1 and now try v2 (or vice versa), the same
`value` exists under the other version.

**Fix:** call EarthRanger's server-side migrate endpoint:

```
POST /api/v2.0/activity/eventtypes/migrate/
```

This converts v1 records to v2 (or vice versa) without deleting them.
`er-smart-sync` doesn't wrap this endpoint yet — use `curl` or the
EarthRanger admin UI.

## CHOICE_LIST field rendered as plain text

**Cause:** Before a fix in commit `82c8fec`, if a configurable-model marked
all options of a LIST/MLIST/TREE attribute as inactive, `er-smart-sync`
would fall back to emitting a `TEXT` field. That changed the field's wire
type and broke tenants with historical events stored under the choice
schema.

**Status: fixed.** Choice-bearing SMART types now always emit `CHOICE_LIST`
with a `$ref`, even when the CM has deactivated every option. The Choice
records are still upserted (just marked `is_active=False`); ER renders an
empty dropdown until ops re-activate them.

If you're seeing this on an old build, update:

```bash
git pull && uv pip install -e ".[dev]"
```

## Authentication fails on EarthRanger

```
ERClient: 401 Bad credentials
```

**Causes (in rough order of likelihood):**

1. Wrong or expired `token`.
2. `endpoint` is correct but missing `/api/v1.0`.
3. Username/password used with the wrong `client_id`.

**Diagnose with `validate-config`:**

```bash
er-smart-sync validate-config --config sync.yaml
```

Prints OK/FAIL for each service, with the specific error message on FAIL.

## How to get more logs

For everything `er_smart_sync` emits at DEBUG level:

```bash
er-smart-sync -v datamodel ...
```

This adds detailed event-type-by-event-type diff information, choice-set
progress lines, and the HTTP responses that triggered each retry/fallback.

## Reporting a problem

When something goes wrong that isn't covered here, capture:

1. The exact command that triggered the issue.
2. The full output with `-v` (verbose) enabled.
3. The configuration YAML (with credentials redacted).
4. The SMART data model XML file or CA UUID being synced.

Open an issue at
https://github.com/PADAS/earthranger-smart-utils/issues with those four
pieces of context.
