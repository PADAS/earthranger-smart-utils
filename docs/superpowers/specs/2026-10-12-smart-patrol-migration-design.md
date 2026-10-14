# SMART → EarthRanger patrol migration from a smart-able bundle

Date: 2026-10-12. Status: draft for review.
Jira: epic ERA-14136; stories ERA-14137 (patrol configuration), ERA-14138
(readiness check), ERA-14139 (patrol history), ERA-14140 (waypoints as
events), ERA-14141 (track points); spike ERA-14142 (historical patrol read
access). Builds on Joshua Krautwurst's technical plan on the epic
(2026-09-29). Companion spec in PADAS/er-smart-able:
`docs/superpowers/specs/2026-10-12-datamodel-export-and-migration-bundle-design.md`.

## Spike close-out: ERA-14142

**Decision.** Historical patrols, track points, and the patrol configuration
are read from a *migration bundle* that smart-able produces from a SMART
system backup or Conservation Area export zip. er-smart-sync does not call
SMART Connect for any part of the patrol migration, and the SMART Connect
client is not extended. Sites are expected to hand over a backup or export
zip; we assume every migrating site can and will.

**Why.** smartconnect-client can fetch one patrol by id but cannot list a
CA's patrols or read track points, and the Connect query API would need a
live server to verify. The backup, which smart-able already parses, carries
every table the import stories need. Verified on the Lawin backup
(SMART 7.5.6, four CAs):

| Data | SMART tables | Rows |
| --- | --- | --- |
| Patrols | `patrol` | 2,570 |
| Legs, leg days, leg members with leader flag | `patrol_leg`, `patrol_leg_day`, `patrol_leg_members` | 3,597 leg days, 14,800 memberships |
| Track geometry per leg day | `track` (WKB LineString) | 2,444 |
| Waypoints and observations with typed attribute values | `waypoint`, `wp_observation_group`, `wp_observation`, `wp_observation_attributes` | 30,791 waypoints, 31,670 observations |
| Waypoint attachments | `wp_attachments` plus the decrypted filestore | 1,641 |
| Patrol metadata lists | `patrol_transport`, `patrol_mandate`, `station`, `patrol_type`, `patrol_attribute` | all present |
| Staff | `employee`, `employee_team`, `rank`, `agency` | 287 employees |

The field shapes the import stories assumed are now known from the tables
rather than from the Connect JSON: leg dates are day-granular, leg days
carry start and end times, members are `(employee_uuid, is_leader,
is_pilot)`, waypoint positions are WGS 84, and track points carry a
per-point timestamp in the geometry's Z coordinate (local wall-clock as
epoch milliseconds; the bundle converts it to RFC 3339 with offset).

**What this changes in the plan.** The `PatrolSource` protocol stays, with
one implementation: the bundle reader. `download_patrolmodel` is no longer
the source of the patrol configuration; `reference.json` in the bundle is.
The existing `sync_patrol_datamodel` (subjects from the Connect patrol
model) is kept for Connect-connected sites and is not touched.

## Goal

Three subcommands that take a SMART CA from backup zip to a fully
populated ER site, in order, each idempotent:

```
er-smart-sync datamodel --from-file <bundle>/../datamodel.xml …   (existing)
er-smart-sync patrol-datamodel --bundle <dir> --config sync.yaml
er-smart-sync patrol-preflight --bundle <dir> --config sync.yaml
er-smart-sync import-patrols   --bundle <dir> --config sync.yaml [filters]
```

Success is the epic's success criteria: a pilot CA's patrols render in the
new ER patrol UI with legs, universal fields, team and members, attached
events and tracks; re-running any step creates nothing new; everything the
tool cannot do itself comes out as an admin checklist.

## Non-goals

- Changes to das. Follow-up tickets only (team write API, provenance fields,
  bulk import, server-default segment schema).
- ER → SMART patrol sync. Unchanged; migrated sites cut over.
- Profiles and Entities.
- Reading anything from SMART Connect for this feature.

## The bundle contract

Produced by `smart-able export`; see the companion spec for the full field
list. Four JSON files in one directory: `manifest.json` (CA identity, SMART
version, timezone, counts, warnings, `bundle_schema_version`),
`reference.json` (patrol types, transports, mandates, stations, custom
patrol attributes, agencies, ranks, teams, employees), `patrols.json`
(patrols → legs → days, members, waypoints → observation groups →
observations with typed attribute values, attachments), `tracks.json` (one
GeoJSON LineString per leg day), plus `attachments/`.

Rules er-smart-sync relies on: SMART UUIDs are hyphenated; timestamps carry
the CA's UTC offset; coordinates are `[lon, lat]`; LIST values are SMART
option keys and TREE values are hkey paths, the same identifiers the
datamodel sync used to build ER choices; labels are per language with a
default-language `label`.

`src/er_smart_sync/bundle/schema.json` is a vendored copy of smart-able's
`export/bundle.schema.json`. `bundle.py` validates each file on load with
`jsonschema` and refuses a `bundle_schema_version` it does not know. The
copy is refreshed by hand; a test asserts the vendored schema's version
matches the loader's supported version.

## Architecture

New modules under `src/er_smart_sync/`, following the plan's split between
pure builders and I/O:

| File | Role |
| --- | --- |
| `bundle/__init__.py`, `bundle/schema.json` | Load and validate the four files into frozen dataclasses (`Bundle`, `Patrol`, `Leg`, `LegDay`, `Waypoint`, `Observation`, `Employee`, …). No ER knowledge. |
| `patrol_source.py` | `PatrolSource` protocol: `conservation_area()`, `reference()`, `iter_patrols(filters)`, `track_for(leg_day_uuid)`, `open_attachment(path)`. `BundlePatrolSource` is the only implementation. |
| `smart_to_er_patrol_schema.py` | Pure builders from `reference.json`: ER patrol types, the universal segment schema (JSON Schema draft 2020-12 plus UI schema, choice fields as `anyOf $ref choices.json?field=patrol_<name>`), the patrol choice sets, member subjects, and the team requirements list. |
| `er_patrol_api.py` | Thin raw-API layer over `ERClient._get/_post/_patch/_put` for what erclient lacks: patrol config read, patrol type upsert, segment schema get/put, patrol PATCH, subject sources, incident collections. Honors `DryRunERClient`. |
| `smart_to_er_patrols.py` | Pure transform: one bundle patrol → an `ERPatrolPlan` (patrol payload, segments, notes, events per segment, observation batches per leader). Deterministic ids, provenance block. |
| `preflight.py` | Read-only checks returning `Finding(level, code, message, fix)` rows and a text or JSON report. |
| `patrol_import.py` | Orchestrates `import-patrols`: ledger, per-patrol isolation, counts. |
| `synchronizer.py`, `cli.py`, `config.py`, `state.py` (modify) | Wire the three subcommands; `smart.timezone` and `earthranger.patrol_namespace` config keys; ledger storage via `StateStore`. |

`config.py` gains `BundleConfig(path: str)` so `--bundle` can also live in
`sync.yaml`. The `smart:` endpoint and credentials become optional when a
bundle is given.

## patrol-datamodel (ERA-14137)

Reads `reference.json` and writes, in this order, each step idempotent:

1. **Patrol types.** One per active transport: `value = patrol_<sanitized
   transport key>`, `display = transport label` in the chosen language. Upsert
   keyed on `value`. Icons are not set: das's patrol-type serializer exposes
   `icon_id` read-only (verified 2026-10-13), so the checklist asks the admin
   to pick one per created type instead. Inactive transports are created inactive if absent and left
   alone if present, so historical legs can still reference them.
2. **Universal segment schema.** Built from the CA: `objective` (text),
   `mandate` (choice), `station` (choice), `patrol_class` (choice, from
   `patrol_types`: Ground, Air, Marine), `armed` (boolean), `comment`
   (textarea), plus one field per active custom patrol attribute keyed
   `smart_<attribute key>`. Choice fields reference
   `choices.json?field=patrol_<name>`. Update semantics: GET the current
   schema on `patrols/segments/types/default/`, merge so our canonical keys
   win and admin-added properties and UI order entries survive, then PUT.
   `--replace-segment-schema` overwrites instead. A no-change merge skips
   the PUT.
3. **Choices.** `patrol_mandate`, `patrol_station`, `patrol_class`, and one
   set per custom LIST attribute, through the existing `choices.py` upsert
   (`model="activity.event"`, which the segment schema `$ref` resolves
   field-only). Values are sanitized with `sanitize_choice_value`;
   display is the label in the chosen language.
4. **Subjects.** One ranger subject per employee with a leg membership in
   the bundle or an active employment. `subject_subtype = ranger`,
   `name = "Given Family"`, `additional.smart_member_id = employee uuid`,
   `additional.smart_employee_id = employee.id`, `additional.smart_ca_uuid`.
   Match on `smart_member_id` first (the key `sync_patrol_datamodel`
   already uses), then on `smart_employee_id` for subjects created by an
   earlier Connect-based run; create when neither matches. A matched
   subject is PATCHed whenever any of name, `is_active`, or the identity
   metadata (`smart_member_id`, `smart_employee_id`, `smart_ca_uuid`)
   differs from what the bundle implies, so a legacy subject matched only by
   `smart_employee_id` always leaves the run carrying `smart_member_id`;
   everything downstream (preflight, import) resolves by `smart_member_id`
   alone. Subjects are created for every employee with a leg membership,
   including staff whose employment has ended (see "Historical members"
   below). Multi-CA sites share subjects by `smart_member_id`, so a second
   CA's run adds no duplicates.
5. **Checklist.** Printed at the end and written as `checklist.md` beside
   the bundle: enable the `patrol_schemas` preview; create each
   `PatrolConfigurationTeam` named after a SMART team, with its roster;
   put every subject created above in the patrol roster subject group;
   grant the operator `change_segmenttype` if the schema PUT was refused.
   Each item names the exact ER admin page and the values to enter.

Summary line: counts of types, fields, choices, subjects created, updated,
unchanged; checklist item count.

## patrol-preflight (ERA-14138)

Read-only. Checks, each a `Finding` with a `fix` string:

| Code | Level | Check |
| --- | --- | --- |
| `feature_flag` | blocking | `GET status` reports `preview_features.patrol_schemas` on |
| `permissions` | blocking | `GET user/me`: superuser, or add/change/view on `patrol` (das's `PatrolConfigurationPermission` also needs `view_patrol`), add on `eventrelatedsegments`, and add on each event category the resolved event types use (das checks `activity.<category>_create` per event; das has no dry-run patrol POST) |
| `patrol_types` | blocking | every transport referenced by a leg in the bundle has an active patrol type (looked up with `include_inactive`) |
| `segment_schema` | blocking | every canonical field is in the default segment schema |
| `choices` | warning | every mandate, station, class and custom option used by a patrol exists as a choice |
| `subjects` | blocking | every employee with a leg membership exists as a subject matched by `smart_member_id`, and every active one is on the roster das enforces (`GET patrols/config/default/resolved`); inactive subjects are handled by `inactive_members` |
| `inactive_members` | warning | subjects for retired staff that segments will reference; the import reactivates them for the run. Blocking for a retired subject in no configured member group |
| `teams` | blocking | every SMART team referenced by a patrol exists and is active as a `PatrolConfigurationTeam` (matched by name in the configuration's full team list; `/resolved` omits inactive teams) |
| `event_types` | blocking | every category hkey used by an observation has an event type from the datamodel sync (`event_type_value_for`), matched only in the value shapes that sync writes; split-mode configurable model variants get their own blocking finding |
| `leg_span` | warning | legs with `placeholder_dates` (1970-01-01 start or more than a year of leg days); shows how many leg days will be clamped |
| `time_coverage` | warning | leg days whose segment times will be whole days (`manifest.time_coverage.date_only > 0`); prints the coverage table |
| `timezone` | warning | bundle timezone was inferred rather than configured |
| `language` | warning | the chosen language lacks labels for some elements; reports the count and the fallback used |
| `bundle_warnings` | warning | relays `manifest.warnings` |
| `check_failed` | blocking | a check could not run (403, 500, timeout); the others still run |

Output: text grouped by level with the fix under each finding, or `--json`.
Exit code 0 clean, 2 blocking findings, 1 error. `import-patrols` runs the
same checks first and stops on blocking findings unless
`--allow-blocking` is given, in which case the affected patrols are skipped
individually with the finding code as the reason.

## import-patrols (ERA-14139, ERA-14140, ERA-14141)

**Identity.** All ER ids are `uuid5(ns, f"{kind}:{smart_uuid}")` with
`ns = uuid5(NAMESPACE_URL, f"{er host}/{ca uuid}")`, configurable as
`earthranger.patrol_namespace` for sites that were pre-seeded. Raw SMART
UUIDs are never reused as ER ids, because a SMART `client_uuid` can point
back at an ER-originated patrol from the outbound sync. Kinds: `patrol`,
`segment`, `note`, `event`, `incident`, `file`, `source`.

**Patrol.** One ER patrol per bundle patrol: `title = patrol id`,
`priority = 0`, `objective = patrol.objective`, one note holding the
patrol comment when present. Segments, one per leg, in order:
`patrol_type` from the leg transport, `time_range` derived as in "Patrol
times" below, `leader`
and `members` resolved through `smart_member_id`, `team` resolved by name
from `GET patrols/config/default`, and `segment_details`:

```json
{ "objective": "…", "mandate": "<choice>", "station": "<choice>", "patrol_class": "<choice>",
  "armed": false, "comment": "…", "smart_<attr>": …,
  "smart_": { "patrol_uuid", "patrol_id", "leg_uuid", "leg_id", "ca_uuid", "time_source", "bundle_generated_at", "importer_version" } }
```

The `smart_` block is provenance (ERA-14139's traceability criterion);
`segment_details` validation is advisory so unknown keys persist.
Write: `POST activity/patrols/` with nested segments and notes; on 409,
`PATCH patrols/{id}` with the same body, which upserts nested rows by id.
Past end dates make ER mark the patrol done automatically.

**Events (ERA-14140).** Per waypoint, per observation: an event of type
`event_type_value_for(category hkey)` with `time`, `location`,
`event_details` from the attribute values, mapped with the same rules the
datamodel sync used to build the schema (LIST → sanitized choice value,
TREE → sanitized leaf key, MLIST → array, NUMERIC/BOOLEAN/DATE/TEXT
as-is), `provenance` in `event_details.smart_` (`waypoint_uuid`,
`observation_uuid`, `group_uuid`), and `patrol_segments = [segment id]`. A
waypoint with more than one observation becomes an incident collection
whose children are the observation events. Attachments are posted once to
the first event of the waypoint. Dedupe: client-supplied event ids are
tried first; if ER refuses an id on POST, the importer falls back to
querying by `event_details.smart_.observation_uuid` before creating.

**Tracks (ERA-14141).** Per leg leader: ensure a Source with
`manufacturer_id = smart:{smart_member_id}` and a subject source spanning
the patrol window; then `post_observation` batches of the leg day's
track, one observation per vertex with `recorded_at` taken from the
bundle's `parts[].times` (the real per-point timestamps, present whenever
the SMART track carried them) and `additional.smart_leg_day_uuid` on each.
Only when a track has no `times` (the bundle omits them when Z was not a
timestamp) are the vertices spread evenly across the leg day's range, and
the segment provenance records `track_times: interpolated` so nobody
mistakes them for recorded movement.
Dedupe: diff against existing observations for that source
in the window by `recorded_at` before posting. `--skip-tracks` skips this
step.

**Selection and resumption.** `--since/--until` on patrol start date,
`--patrol <id or uuid>` repeatable, `--skip-events`, `--skip-tracks`,
`--limit N`. The ledger, stored through `StateStore` under
`patrol_import/<er id>/<ca uuid>/<patrol uuid>`, records per patrol the
stage reached (patrol, events, tracks) and the ER ids written. A re-run
skips completed stages and finishes partial ones. One failing patrol is
logged with its reason and skipped; the run ends with imported, skipped
and failed counts and the reason per failure, and exits non-zero if any
failed.

## Historical members

Patrol history includes staff who have since left. The bundle marks them
`active: false` (employment end date in the past); `patrol-datamodel`
still creates a subject for each of them, with `is_active` mirroring the
bundle, so every leg membership has a subject. das requires segment
`members` to be active subjects in a configured group (verify-first item 6
checks whether that also applies to historical, already-ended segments).
If it does, `import-patrols` reactivates the affected subjects for the
duration of the run and sets them back to `is_active=false` at the end, in
its ledger, so a failed run can finish the retirement on the next run; the
preflight's `inactive_members` warning lists exactly which subjects that
will touch. If das accepts inactive members on past segments, no
reactivation happens. Either way, a missing subject is the only blocking
condition.

## Patrol times

SMART patrols and legs carry dates only; nothing on them is a timestamp.
ER needs a start and end on every segment, so the importer derives each
segment's `time_range` from its leg days, each leg day from the most
precise source it has, in this order:

1. **Track points.** The leg day's track carries a timestamp per point in
   its Z coordinate: local wall-clock encoded as epoch milliseconds, which
   smart-able decodes, anchors to the bundle timezone and writes as
   `tracks[].parts[].times` (RFC 3339 with offset). Day start = first
   point, end = last point.
2. **Leg-day start and end times.** `patrol_leg_day.start_time`/`end_time`,
   local wall-clock, already anchored to the bundle timezone as `days[].start`
   and `days[].end`. Used when there is no timed track. A leg day stored
   as exactly 00:00 to 23:59:59 counts as unset (SMART's default when
   nobody entered times), so the track or the waypoints decide its range.
3. **Waypoint times.** The earliest and latest `waypoints[].time` on that leg
   day. Used when the leg day has neither a track nor start/end times; it
   bounds the activity even though the day started before the first
   observation.
Legs the bundle marks `placeholder_dates: true` (SMART stores an unset
start date as 1970-01-01 and then generates a leg-day row for every day up
to the end date; one export had 13 such legs with 93,697 leg days) are
clamped to activity evidence: only leg days that have a track (timed or
not) or at least one waypoint are imported. Generated rows dated after
1970 carry no evidence and are dropped with the rest. The segment range
comes from the retained days using the normal precedence, except that a
placeholder leg's own `start_time`/`end_time` are never trusted (SMART
generated them too): a retained day with an untimed track and no timed
waypoint takes the calendar day, its track points are spread across it,
and the provenance records `track_times: interpolated` as in Tracks. When a placeholder leg has no evidence at all, it is
imported as a single segment on the leg's `end_date` (the one date SMART
did not generate), range `00:00` to `23:59:59` in the bundle timezone,
with `time_source: placeholder` in the provenance so it can be found and
corrected later. The preflight lists every placeholder leg under a
`leg_span` warning with the number of leg days it will drop and whether it
falls back to the end date.

4. **The calendar day.** `00:00` to `23:59:59` in the bundle timezone when
   nothing else exists. The preflight reports how many leg days fall here
   from `manifest.time_coverage.date_only`.

The segment's range is the earliest start and latest end across its leg
days. Sources 1 and 3 are union-ed with source 2 when both exist, so a
track that starts after the recorded start time does not shorten the day.
Provenance records the source used per leg day
(`segment_details.smart_.time_source`: `track`, `leg_day_times`,
`waypoints`, `date_only`) so a user can see why a segment's times look
coarse. Waypoint events keep their own times regardless; track points
become location observations with their real timestamps.

The preflight prints the `time_coverage` block as a table so the operator
sees before importing what share of leg days will carry precise times.

## Language

A SMART data model can carry labels in several languages; the user picks
which one populates EarthRanger. The existing `--smart-language` flag and
`smart.use_language_code` config key, which the `datamodel` subcommand
already honours for event type and choice display names, apply to the
three patrol subcommands too. Every display string written to ER comes
from `labels[language]`: patrol type display names, universal field
titles, choice display values, team names in the checklist, and the
patrol-class labels. Fallback order is the requested language, then the
bundle's `default_language`, then any language present, so a missing
translation never blocks a run. Subject names are personal names and are
not translated.

Validation: the requested code must appear in `manifest.languages`;
otherwise the command stops and prints the available codes with their
label coverage. The preflight adds a `language` warning when the chosen
language is missing labels (`manifest.languages[].missing > 0`) with the
count, so the user can switch to a better-covered language before
importing. Because display names are part of the upsert comparison,
re-running with a different language updates patrol type and choice
display values in place; it does not create new ones, since identity is
on `value`, not display.

## Timezone

Taken from `manifest.timezone`. Every timestamp in the bundle already
carries that zone's offset, so the importer passes them through unchanged
and never reinterprets them. The zone can only be corrected where the
timestamps are produced: set `SMART_ABLE_TIMEZONE` and re-run
`smart-able export`. `smart.timezone` in `sync.yaml` is therefore an
assertion, not an override: when it is set and differs from
`manifest.timezone.name`, every patrol subcommand stops with an error
naming both zones and the export command to run; when it matches, the
preflight's `timezone` warning is suppressed. When it is not set and the
manifest zone was inferred, the preflight warns.

## Testing

- Unit tests for the pure builders and transform with a committed fixture
  bundle generated by smart-able from SMART's "Example Conservation Area"
  (five patrols, demo staff, no real people). Golden assertions on the
  segment schema, patrol payloads, event payloads and observation batches.
- `bundle.py` tests: schema validation failures, unsupported version,
  missing files.
- Preflight tests against a fake ER client that returns configured
  responses per endpoint, one test per finding code.
- Idempotency test: run `import-patrols` twice against the fake client and
  assert the second run makes no writes.
- A live smoke test, skipped unless `ER_SMOKE_SERVER` is set, that runs
  the three subcommands against a sandbox site with the fixture bundle and
  checks the patrol through `GET activity/patrols/{id}`.

## Verify-first items

Checked against das before the corresponding step is implemented; each
is a task in the implementation plan:

1. Settled 2026-10-14: `POST activity/events/` (and `activity/patrols/`)
   accept a client `id`, but an existing id returns a 500 after about 14 s
   of serial-number retries, not a 409. The importer GETs by id first.
2. Settled 2026-10-14: `subjectsources` is read-only; assignments are
   `POST subject/{id}/sources` with `assigned_range: {lower, upper}`,
   back-dating allowed, get-or-create on the exact range.
3. Settled 2026-10-14: a child joins a collection with
   `"collection": {"id": parent}` in its own POST; the parent's event type
   must have `is_collection`.
4. Settled 2026-10-14: `PATCH activity/patrols/{id}` upserts nested
   segments by id (deleting none) but rejects a new segment carrying an id;
   `members` is replaced.
5. Settled 2026-10-14: das has a unique constraint on
   `(tenant, source, recorded_at)`; a list skips duplicates and answers 409
   only when nothing was new.
6. Settled 2026-10-14: das does not. `_validate_rosters` in
   `das/activity/serializers/patrols.py` accepts only active, person-type
   subjects in the configured member groups, whatever the dates. Original
   question: whether das accepts inactive subjects as `members`/`leader` of a segment
   whose `time_range` is entirely in the past; decides the reactivation
   step in "Historical members".

## Runbook (deliverable, written with the code)

`docs/workflows/migrate-patrols.md`: get the backup, run `smart-able all`,
run `datamodel --from-file`, run `patrol-datamodel`, do the checklist, run
`patrol-preflight` until clean, run `import-patrols` on a date window, check
one patrol in the UI, run the rest. Includes the manual admin steps the
checklist produces, with screenshots of the admin pages.

## Out of scope, recommended das follow-ups

Team and patrol-config write API; provenance or external-id fields on
Patrol and PatrolSegment; a bulk patrol import endpoint; a server-default
universal segment schema seeded from the canonical field set above.
