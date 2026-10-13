# patrol-datamodel

Creates or updates, from a [smart-able](https://github.com/PADAS/er-smart-able)
migration bundle, everything an EarthRanger site needs before SMART patrols
can be imported:

| Step | What it writes | Idempotency |
| --- | --- | --- |
| Patrol types | one per SMART transport, `value = patrol_<transport key>`, display in the chosen language | upsert on `value`; inactive transports are created inactive and never deactivated by the tool |
| Universal patrol fields | the default segment schema: `objective`, `mandate`, `station`, `patrol_class`, `armed`, `comment`, plus `smart_<key>` per custom patrol attribute | merged with what is on the site (admin-added fields survive); `--replace-segment-schema` overwrites; skipped with a checklist item while the `patrol_schemas` preview feature is off |
| Choices | `patrol_mandate`, `patrol_station`, `patrol_class`, `patrol_<attribute>` | the same upsert as `choices`; orphans are deactivated |
| Subjects | one `ranger` subject per employee with a leg membership or an active employment, `additional.smart_member_id` = SMART employee UUID | matched on `smart_member_id`, then `smart_employee_id`; patched when name, active flag or identity metadata differ |
| Roster | subjects added to the patrol configuration's first member group, or to `--roster-group NAME` | only missing members are added |

It then writes `checklist.md` next to the bundle directory with the steps an
admin has to do by hand: enabling the preview feature, creating teams,
granting permissions, choosing the roster group.

## Usage

```bash
er-smart-sync patrol-datamodel --bundle work/export/050500/bundle \
    --er-endpoint site.pamdas.org --er-token TOKEN
er-smart-sync --dry-run patrol-datamodel --config sync.yaml   # bundle.path and earthranger from the file
```

## Options

| Option | Meaning |
| --- | --- |
| `--bundle DIR` | the bundle directory (holds `manifest.json`); or `bundle.path` in `--config` |
| `--smart-language CODE` | language for every display string written to ER; must be one of the bundle's languages (smart-able's Data Model tab lists them with coverage) |
| `--timezone NAME` | assert the bundle's timezone; a mismatch stops the run and says how to re-export |
| `--replace-segment-schema` | overwrite the universal patrol fields instead of merging |
| `--roster-group NAME` | subject group to add the subjects to |
| `--checklist PATH` | where to write the checklist (default: beside the bundle directory) |
| `--dry-run` (before the subcommand) | log every write instead of sending it |

Exit status is 0 even when the checklist has items; it is 1 when a step
failed (a 403, for example), when the bundle is invalid, or when the
timezone assertion fails.

## What the bundle timezone means

Every timestamp in the bundle carries the offset of the zone smart-able
wrote it for (`manifest.timezone`). `patrol-datamodel` never reinterprets
them. `smart.timezone` in `sync.yaml` (or `--timezone`) is an assertion:
set it to the zone you know the site uses, and the run stops if the bundle
was written for another one, telling you to re-export with
`SMART_ABLE_TIMEZONE` set. Without it, a bundle whose zone was inferred
from waypoint positions produces a warning.
