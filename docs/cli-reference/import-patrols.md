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
| `--workers N` | Import N patrols at a time, each on its own connection. Default 1. On current das more than one is slower, not faster: das creates a site's events one at a time (see Speed). Dry runs always use one worker. |

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
- A leg day whose recorded times are exactly 00:00 to 23:59:59 counts as having no times. SMART stores that when nobody entered times, so the track, then the waypoints, set that day's range instead.
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

## Speed

The import runs in two phases. The first creates every patrol with its
events and photos; the second posts every patrol's tracks. A patrol whose
events fail still gets its tracks in the second phase and is reported
failed. A stop during the first phase (Ctrl-C, or a library caller's
`stop=`) leaves the tracks for the next run, which skips the finished
events from the ledger and goes straight to them.

A patrol costs a handful of requests: the patrol, its events in lists of
20 (parents of multi-observation waypoints first), its attachments, and,
in the second phase, its leader's track in batches of 1,000 points posted
before the source is assigned to the leader. Re-run safety comes from
deterministic ids and from reading the patrol (which lists the events
already on it) before writing; a list das rejects is redone one event at a
time for that patrol only.

What limits the rate is the server. das creates a site's events one at a
time, holding a per-site lock for each list, and does full per-record work
on each: about 0.7 to 0.9 seconds an event on the sites measured in October
2026. Track points cost 0.005 to 0.1 seconds each depending on what else
the site is doing. A load takes roughly

    (events × 0.8 s + track points × 0.03 s) / 3600 hours

so a region with 70,000 observations takes about a day. More workers do not
help: their event lists queue on the same lock. Posting the points before
the assignment spares das a lookup and a queued task per point, and keeping
all tracks in the second phase keeps large point batches from slowing the
event writes. The measurements, das's per-request work and the das-side
options are in smart-able's `docs/load-performance.md`. Run long imports from
the site's cloud region, in the foreground or under `setsid` (a `nohup … &`
job from a script ignores Ctrl-C).

A second Ctrl-C during shutdown skips the wait for running patrols and retires members immediately; those patrols may then fail on das's active-member check — re-run to finish them.

## Identity and re-runs

Every ER id is derived from the SMART uuid: a uuid5 in a namespace made of the ER host and the conservation area. A re-run, or a second operator, addresses the same rows. `earthranger.patrol_namespace` in `sync.yaml` overrides the namespace; set it only for a site seeded under another.

Library callers can pass `progress=` and `stop=`. `progress` is called with `{phase, total, done, failed, already_imported}`: once with `done` 0 when each phase (`events`, then `tracks`) starts, then per patrol. `done` and `failed` count within the phase; in the tracks phase `failed` counts every patrol with a failure so far. `stop` is a `threading.Event`; once set, no further patrol starts, the tracks phase does not start, and `ImportResult.stopped_early` is true.

The patrol is read before it is written, and that read lists the events already on its segments, so existing events are skipped. Events are created in lists, without a read per event. das answers a duplicate id with a 500, not a 409; the import recovers from it by reading that one event. Attachments are guarded by the ledger and, without it, by the event's own file list. das also discards a track point it already has.

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
