# er-smart-sync — CLI usage

`er-smart-sync` synchronizes data between [SMART Connect](https://smartconservationtools.org/) and [EarthRanger](https://www.earthranger.com/):

- **`datamodel`** — push SMART conservation-area data models into EarthRanger as event categories and event types.
- **`events`** — poll EarthRanger events and publish them for routing to SMART.
- **`patrols`** — poll EarthRanger patrols (with track points, segment events, and attached files) and publish them for routing to SMART.
- **`validate-config`**, **`list-cas`**, **`inspect-datamodel`** — read-only diagnostic commands intended for support work.

## Install

```bash
uv pip install -e ".[dev]"   # or just ".[gcp]" for production deps + GCP plumbing
```

This installs the `er-smart-sync` console script.

## The two ways to point it at a server

Every subcommand accepts either:

1. **A YAML config file** via `--config path/to/sync.yaml`, or
2. **Explicit flags** for each endpoint.

The easiest way to discover what the YAML file accepts is to print a
commented template and edit it:

```bash
er-smart-sync config-template > sync.yaml
# or write directly to a path:
er-smart-sync config-template -o sync.yaml
```

The template covers every supported key with inline comments. The relevant
shape is:

```yaml
smart:
  endpoint: https://smart.example.org/server
  login: smart-user
  password: smart-secret
  version: "7.5.7"
  use_language_code: en
  ca_uuids:
    - 0a1b2c3d-4e5f-6789-abcd-ef0123456789

earthranger:
  id: my-er-instance         # opaque, used as the state-tracking key
  endpoint: https://site.pamdas.org/api/v1.0
  token: er-api-token        # or use login + password
  client_id: das_web_client  # optional, defaults to das_web_client
```

EarthRanger auth accepts either `--er-token` **or** the `--er-username`/`--er-password` pair. Token alone is preferred.

## Global flags

```
-v / --verbose    Enable DEBUG-level logging for the er-smart-sync package
                  (each event type checked, every state checkpoint, every
                  publish). Underlying HTTP libraries stay at WARNING so the
                  output stays readable.
--dry-run         Log intended writes (event types, observations) without
                  contacting EarthRanger or the message broker. Safe to run
                  against production credentials.
```

With `-v`, a datamodel sync prints a line for each event type as it is
checked (and another for unchanged ones), so you can see exactly which event
types the tool inspected without losing the noisy underlying request/response
traffic in the output.

`--dry-run` goes **before** the subcommand:

```bash
er-smart-sync --dry-run datamodel --config sync.yaml
```

---

## Pushing a SMART data model into EarthRanger

### From the live SMART API

```bash
er-smart-sync datamodel \
  --smart-api    https://smart.example.org/server \
  --smart-username SMART-USER \
  --smart-password SMART-PASS \
  --smart-version 7.5.7 \
  --smart-ca-uuid 0a1b2c3d-... \
  --er-endpoint  https://site.pamdas.org/api/v1.0 \
  --er-token     YOUR-ER-TOKEN
```

This:

1. Authenticates against SMART and EarthRanger.
2. Fetches the data model for each `--smart-ca-uuid` (repeatable).
3. Builds the corresponding EarthRanger event category + event types.
4. Creates anything missing, updates anything stale, leaves everything else alone.
5. Prints a summary like:

   ```
   Datamodel sync summary:
     categories created: 1
     categories existing: 2
     event types created: 14
     event types updated: 3
     event types unchanged: 27
     event types skipped by mode: 0
     event types errored: 0
   ```

### From a local XML file (no SMART connection needed)

```bash
er-smart-sync datamodel \
  --from-file datamodel.xml \
  --ca-label "[FOASF]" \
  --er-endpoint https://site.pamdas.org/api/v1.0 \
  --er-token    YOUR-ER-TOKEN
```

`--ca-label` is the human-facing category label. The bracketed code (`[FOASF]`) is extracted and used as the event-category short identifier.

### Importing a Configurable Model alongside a Data Model

A configurable model overlays the base data model with a curated subset of categories, attributes, and option values. Pass both files; the configurable model becomes its own event category in EarthRanger:

```bash
er-smart-sync datamodel \
  --from-file datamodel.xml \
  --cm-from-file configurable_model.xml \
  --ca-label "[FOASF]" \
  --er-endpoint https://site.pamdas.org/api/v1.0 \
  --er-token    YOUR-ER-TOKEN
```

#### Multiple configurable models for the same SMART CA

Event-type values are namespaced as `{ca_uuid}_{cm_uuid}_{path}`. When you sync more than one configurable model for the same SMART CA into the same EarthRanger site, supply a stable, unique `--cm-uuid` for each one to keep their event types from colliding on the unique value constraint:

```bash
er-smart-sync datamodel \
  --from-file datamodel.xml \
  --cm-from-file patrols_cm.xml \
  --cm-uuid 11111111-1111-1111-1111-111111111111 \
  --ca-label "[FOASF]" \
  --er-endpoint $ER --er-token $TOKEN

er-smart-sync datamodel \
  --from-file datamodel.xml \
  --cm-from-file incidents_cm.xml \
  --cm-uuid 22222222-2222-2222-2222-222222222222 \
  --ca-label "[FOASF]" \
  --er-endpoint $ER --er-token $TOKEN
```

If you omit `--cm-uuid`, the all-zero UUID is used as a stable default. Use it only when you have exactly one configurable model per CA — multiple zero-UUID runs against the same CA will all generate the same event-type values and collide.

#### Also pushing the base data model

When you pass `--cm-from-file`, only the configurable model is pushed by default (it's a curated overlay; the CM's authors picked exactly what they wanted exposed). To **also** push the base data model as its own separate ER event category in the same run, add `--include-base-datamodel`:

```bash
er-smart-sync datamodel \
  --from-file datamodel.xml \
  --cm-from-file configurable_model.xml \
  --include-base-datamodel \
  --ca-label "[FOASF]" \
  --er-endpoint $ER --er-token $TOKEN
```

That creates two ER event categories — one for the base data model and one for the configurable model — which mirrors what the API-based sync (`datamodel` without `--from-file`) does automatically when a CA has both.

### Choosing what gets written: `--mode`

```
--mode both         (default) create new event types AND update changed ones
--mode create-only  create new event types; leave existing ones untouched
--mode update-only  update changed event types; do not create anything new
```

`update-only` is useful when you've already created categories by hand in the EarthRanger admin UI and only want this tool to keep schemas in sync.

---

## Polling events from EarthRanger

```bash
er-smart-sync events \
  --er-endpoint https://site.pamdas.org/api/v1.0 \
  --er-token    YOUR-ER-TOKEN \
  --topic       projects/my-gcp-project/topics/er-events \
  --state-file  /var/lib/er-smart-sync/state.json
```

What happens:

- Reads `event_last_poll_at` from `--state-file` (defaults to a `SyncState` 7 days back on first run).
- Fetches events `updated_since` that timestamp from EarthRanger.
- Sorts events by `updated_at` ascending.
- For each non-patrol event: downloads its attached files in parallel, publishes the event to `--topic`, and **checkpoints state after every successful publish**. If the process crashes mid-loop, the next run resumes from the last published event.
- At the end, advances state past the polling window so any patrol-only events at the tail aren't re-fetched.
- Prints a summary:

  ```
  Event sync summary: 412 read, 397 published, 15 skipped (patrol)
  ```

`--topic` defaults to the empty string. With no topic configured, the default `NullPublisher` logs what it *would* have published — useful for a smoke test.

## Polling patrols from EarthRanger

```bash
er-smart-sync patrols \
  --er-endpoint https://site.pamdas.org/api/v1.0 \
  --er-token    YOUR-ER-TOKEN \
  --topic       projects/my-gcp-project/topics/er-patrols \
  --state-file  /var/lib/er-smart-sync/state.json
```

Behavior:

- Reads `patrol_last_poll_at` from `--state-file`; defaults to 7 days back on first run.
- Fetches patrols whose updates fall inside `[last_poll, now]`.
- For each patrol:
  - Skipped entirely (no API calls for segment events or track points) if any segment is missing `start_location` or `leader`.
  - Otherwise: downloads patrol files **and** all segment-event files in parallel; fetches all segment events in a **single batched** `get_events` call (with a fallback to per-id fetches if the deployment doesn't accept comma-separated `event_ids`).
  - Fetches each segment's track points from the leader's subject observations within the polling window.
  - Skipped if `max_update < last_poll` and no track points exist.
  - Otherwise published to `--topic`.
- Prints:

  ```
  Patrol sync summary: 42 read, 38 published, 3 skipped, 1 oversized
  ```

  `oversized` patrols are dropped when the broker rejects them (e.g. Pub/Sub message size limit). The error is logged and the loop continues.

---

## Diagnostic commands

These are read-only and never write to EarthRanger.

### `validate-config` — sanity-check credentials

```bash
er-smart-sync validate-config --config sync.yaml
```

Probes EarthRanger (`get_event_categories`) and SMART (`get_conservation_area`); prints `OK` / `FAIL` for each. Non-zero exit on any failure — suitable for use in a deployment health check.

### `list-cas` — show available conservation areas

```bash
er-smart-sync list-cas \
  --smart-api      https://smart.example.org/server \
  --smart-username SMART-USER \
  --smart-password SMART-PASS \
  --smart-ca-uuid  0a1b2c3d-...
```

Prints a table:

```
UUID                                  Label                       Identifier
------------------------------------  --------------------------  ----------
0a1b2c3d-4e5f-6789-abcd-ef0123456789  Foasf Reserve [FOASF]       FOASF
```

If your SMART client supports it, `list-cas` without `--smart-ca-uuid` will enumerate everything the credentials can see. Otherwise it iterates only the UUIDs you provided.

### `inspect-datamodel` — preview what would be pushed

```bash
er-smart-sync inspect-datamodel \
  --from-file    datamodel.xml \
  --cm-from-file configurable_model.xml \
  --ca-label     "[FOASF]" \
  --er-endpoint  https://site.pamdas.org/api/v1.0 \
  --er-token     YOUR-ER-TOKEN
```

Loads the data model (and optional configurable-model overlay), runs the local SMART → ER conversion, and prints every event type that *would* be created or updated, including each field's JSON-Schema type, format, and enum values:

```
CA: [FOASF]
Event types: 32
  active:   28
  inactive: 4

- ca-uuid-placeholder_incidents_poaching
    display: Poaching
    fields:
      species: string (enum=['lion', 'tiger', 'elephant'])
      date_observed: string/date
      gps_accuracy: number
      photos: array (enum=['photo_1', 'photo_2'])
```

Use this to verify your data model converts correctly before running an actual sync.

---

## Recommended workflows

### "I'm setting up a new SMART integration"

1. `validate-config` — confirm both credentials work.
2. `list-cas` — confirm the CA UUIDs and that the labels carry bracketed identifiers (e.g. `[FOASF]`).
3. `inspect-datamodel --smart-ca-uuid <uuid>` — confirm the conversion produces sensible event types and fields.
4. `--dry-run datamodel ...` — dry-run the full push and read the summary.
5. `datamodel ...` — for real.

### "Production has drifted, I just want to update existing event types"

```bash
er-smart-sync datamodel --config sync.yaml --mode update-only
```

No new categories or types will be created; only existing event types whose schemas have changed will be patched.

### "I think the conversion is doing the wrong thing for some attribute"

Run `inspect-datamodel --from-file <local.xml>` against a saved copy of the data model — no SMART or ER connectivity needed.

---

## State file

`events` and `patrols` use a JSON state file (default `/tmp/er-smart-sync-state.json`) to remember the last-poll timestamps:

```json
{
  "my-er-instance": {
    "event_last_poll_at": "2026-05-09T18:42:11.503210+00:00",
    "patrol_last_poll_at": "2026-05-10T00:00:00+00:00"
  }
}
```

Writes are atomic (write-to-temp + `os.replace`), so an interrupted process cannot leave a partial file behind. Override the path with `--state-file`.

To force a full re-sync, delete the file (or the entry for your `--er-id`).

## Logging

Logs go to stderr in this format:

```
2026-05-10 12:34:56,789 INFO er_smart_sync.synchronizer: Syncing CA 1/3 (0a1b2c3d-...)
```

Add `-v` (before the subcommand) for DEBUG-level logging.

## Retries

EarthRanger write calls (`post_event_category`, `post_event_type`, `patch_event_type`, `patch_event`, `post_subject`) are wrapped with exponential-backoff retries (4 attempts, base delay 1s). The retry path skips permanent client errors — 404 Not Found, 401 Bad Credentials, 403 Forbidden — so a misconfigured run fails fast rather than slowly.
