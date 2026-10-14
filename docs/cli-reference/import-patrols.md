# import-patrols

Writes a smart-able migration bundle's patrol history into EarthRanger:

- one ER patrol per SMART patrol, with a segment per leg;
- one event per observation, linked to its segment;
- each leg leader's GPS track, as location observations.

It runs after `patrol-datamodel` and a clean `patrol-preflight`. Re-running
it writes nothing new.

## Usage

```bash
er-smart-sync import-patrols --config sync.yaml --since 2024-01-01 --until 2024-01-07
er-smart-sync import-patrols --config sync.yaml                  # everything
er-smart-sync --dry-run import-patrols --config sync.yaml        # what it would write
```

| Option | Meaning |
| --- | --- |
| `--bundle DIR` | the bundle directory (holds `manifest.json`); or `bundle.path` in `--config` |
| `--smart-language CODE` | the language for labels |
| `--timezone NAME` | the zone you know the site uses. If it differs from the bundle's, the command stops |
| `--event-type-prefix PREFIX` | which data model push to import against when several match (see patrol-preflight) |
| `--event-type-version v1\|v2` | event type API version (default `earthranger.event_type_version`, v2) |
| `--since DATE`, `--until DATE` | only patrols starting in this window (`YYYY-MM-DD`, inclusive) |
| `--patrol ID` | only this patrol, by SMART id (`SMART_000123`) or uuid. Repeatable |
| `--limit N` | at most N patrols |
| `--skip-events` | do not import waypoint events |
| `--skip-tracks` | do not import track points |
| `--allow-blocking` | skip the patrols that blocking preflight findings affect, instead of stopping |
| `--ledger FILE` | the import ledger (default: `import-ledger.json` beside the bundle) |

Exit status:
- **0:** every selected patrol was imported.
- **1:** at least one patrol failed. The others were still imported, and each failure is listed with its reason.
- **2:** preflight found blocking problems and `--allow-blocking` was not given. Nothing was written.

## What it writes

**Patrols and segments.**
- Each patrol has the title of its SMART id, priority 0, the objective, and a note holding the SMART comment.
- Each leg becomes a segment. A segment carries:
  - the patrol type of its transport;
  - the team, matched by name;
  - the leader and the members, matched by `smart_member_id`;
  - its time range;
  - the universal patrol fields `patrol-datamodel` set up.
- `segment_details.smart_` records where the segment came from: the patrol and leg uuids and ids, the conservation area, the bundle's date, the importer version, and `time_source`.
- `time_source` maps each leg day to where its times came from: `track`, `leg_day_times`, `waypoints`, `date_only` or `placeholder` (see smart-able's `quirks.html#patrol-times`).
- ER marks a patrol whose segments all end in the past as done.

**Events.**
- Each observation becomes an event of the type the data model sync created for its category, at the waypoint's time and place.
- Its attribute values are stored the way that sync defined them.
- `event_details.smart_` records the waypoint, observation and group uuids.
- A waypoint with several observations becomes an incident collection that holds them. Only ER's stock `incident_collection` type is used, found in either the v1 or the v2 list. Without it, the events import ungrouped, and preflight's `collection_type` warning says so. No other collection type on the site is borrowed.
- A waypoint's attachments are posted to its first event.

**Tracks.**
- Each leg's track belongs to its leader, or to the first member when no one is marked leader.
- Each leader gets one source, `smart:<member uuid>` from provider `smart_patrol_import`, assigned to their subject for each leg day.
- Every track vertex becomes an observation at its recorded time. A track SMART stored without times has its vertices spread evenly across the day, and the segment records `track_times: interpolated`.

**Retired staff.** das accepts only active rangers on a segment. The import reactivates retired members for the run and retires them again when it ends. If a run is interrupted, the next run finishes the retirement, because the ledger records who was reactivated.

## Identity and re-runs

Every ER id is derived from the SMART uuid: a uuid5 in a namespace made of the ER host and the conservation area. A re-run, or a second operator, addresses the same rows. `earthranger.patrol_namespace` in `sync.yaml` overrides the namespace; set it only for a site seeded under another.

Before every write, the import reads the row by its id and skips it when it exists. das answers a duplicate id with a slow 500, not a 409, so this check is required. das also discards a track point it already has.

The ledger records, per site and conservation area:
- the stages each patrol finished (patrol, events, tracks);
- the patrol id;
- the waypoints whose attachments were posted;
- the rangers reactivated for the run.

A re-run skips finished stages. A dry run neither writes to ER nor writes the ledger.

Without the ledger, a re-run still writes no duplicates. Every patrol and event is looked up by id, and an attachment is skipped when its event already holds a file of that name.

A patrol found on the site without one of its segments fails. das cannot add a segment with a given id to an existing patrol, so delete that patrol in EarthRanger and run again.

## Example

On gundi-dev.staging, 2026-10-13, after `datamodel --bundle`, `patrol-datamodel` and a clean `patrol-preflight`, SMART's demo bundle was imported in two steps.

The first run used `--patrol SMART_000001`. It wrote one patrol:
- a segment with the team, the leader, three members, the universal fields and the `smart_` provenance;
- 20 events linked to the segment, one with its photo attached;
- 30 track points on the leader's source.

ER marked the patrol done.

A second run without `--patrol` added the other four patrols:

```text
Imported 5, skipped 0, failed 0 patrol(s).
  already_imported: 1
  events_created: 121
  observations_unmapped: 0
  track_points: 202
```

A third run reported `already_imported: 5` and wrote nothing. A run with an empty ledger found all 141 events and the photo already on the site, and wrote nothing either.

gundi-dev has no `incident_collection` event type, so the 18 multi-observation waypoints imported ungrouped there.

## Undoing an import

Everything the import writes can be found by its provenance:

- **Patrols** whose `segment_details.smart_.ca_uuid` is the conservation area. The ledger lists every patrol id. Delete them in EarthRanger, or with `DELETE activity/patrols/<id>`.
- **Events** whose `event_details.smart_` names the area's waypoints, including the incident collections titled `SMART waypoint <id>`.
- **Sources** `smart:<member uuid>` of provider `smart_patrol_import`, and their observations.

Delete the ledger file afterwards, or the next run will skip those patrols.
