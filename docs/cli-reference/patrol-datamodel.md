# patrol-datamodel

Creates or updates, from a [smart-able](https://github.com/PADAS/er-smart-able)
migration bundle, everything an EarthRanger site needs before SMART patrols
can be imported:

| Step | What it writes | Idempotency |
| --- | --- | --- |
| Patrol types | one per SMART transport, `value = patrol_<transport key>`, display in the chosen language | upsert on `value`; inactive transports are created inactive and never deactivated by the tool |
| Universal patrol fields | the default segment schema: `objective`, `mandate`, `station`, `patrol_class`, `armed`, `comment`, plus `smart_<key>` per custom patrol attribute | merged with what is on the site (admin-added fields survive); `--replace-segment-schema` overwrites; skipped with a checklist item while the `patrol_schemas` preview feature is off |
| Choices | `patrol_mandate`, `patrol_station`, `patrol_class`, `patrol_<attribute>` | upsert on value; only options the bundle names are changed. These fields are site-wide and a choice records no owner, so an option from another conservation area's bundle or added by an admin is left as it is and counted in the summary |
| Subjects | one `ranger` subject per employee with a leg membership or an active employment, `additional.smart_member_id` = SMART employee UUID; SMART logins that never went on a patrol (such as the `smart` admin every install creates) are left out | matched on `smart_member_id`, then, for a subject without one that records this conservation area, on `smart_employee_id` when that id is unique; patched when name, active flag or identity metadata differ; a subject an earlier run made for this conservation area that is no longer planned is deactivated, never deleted |
| Roster | subjects added to the patrol configuration's first member group, or to `--roster-group NAME` | only missing members are added |

It then writes `checklist.md` next to the bundle directory with the steps an
admin has to do by hand: enabling the preview feature, creating teams,
granting permissions, choosing the roster group, and picking an icon for
each patrol type (the API cannot set icons).

Station choices are keyed by the station's SMART UUID, since SMART stations
have no key of their own; the display is the label in the chosen language.
SMART lets a station exist with no name in any language. Such a station is
created as `Unnamed station <first 8 characters of its UUID>`. If no patrol
uses it, the choice is inactive, so the form does not offer it; if a patrol
does, it is active and the checklist asks for a real name.

A SMART login is left out only when it never went on a patrol. Sites often
record shared admin logins as patrol members, and the patrol import needs
those as subjects. Bundles written before smart-able flagged logins
(`has_smart_login`) leave nobody out.
In a dry run no subject gets a real id, so the summary always reports 0
subjects added to the roster group.

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

## Undoing a run

Nothing the command creates is deleted by the command. Instead, every real
run that changes anything writes `patrol-datamodel-run-<time>.json` next to
the checklist. It lists each write in order, with the values it replaced.
A dry run writes no record.

Undo only what the records list. Work from the newest record to the
oldest, and inside a record from the last change to the first. Don't undo
by searching the site. The patrol fields, patrol types and choices are
site-wide, so another conservation area's run or an admin may own objects
that look the same. A subject a run only matched or updated existed before
that run.

| Change in the record | How to undo it |
| --- | --- |
| `roster`, `added` | For each id in `subject_ids`: `DELETE /api/v1.0/subjectgroup/<group_id>/subjects/` with `[{"id": "<id>"}]`. Members that were in the group before the run are not listed. |
| `subject`, `created` | `PATCH /api/v1.0/subject/<id>/` with `{"is_active": false}`, so history stays intact. `DELETE /api/v1.0/subject/<id>/` removes it outright, which needs an admin permission. |
| `subject`, `updated` | `PATCH /api/v1.0/subject/<id>/` with the `before` object. |
| `choice`, `created` | `PATCH /api/v1.0/choices/<id>/` with `{"is_active": false}`, or delete it in Django admin > Choices if nothing uses it. If `id` is null, look it up with `GET /api/v1.0/choices/?model=activity.event&field=<field>` and match on `value`. |
| `choice`, `updated` | `PATCH /api/v1.0/choices/<id>/` with the `before` object. |
| `universal_patrol_fields`, `written` | `PUT /api/v1.0/activity/patrols/segments/types/default/` with `{"schema": <the document in before_file>}`. A null `before_file` means the site had no form, so PUT the empty envelope: `{"schema": {"json": {"$schema": "https://json-schema.org/draft/2020-12/schema", "type": "object", "properties": {}, "required": [], "unevaluatedProperties": false}, "ui": {"fields": {}, "headers": {}, "order": [], "sections": {}}}}` |
| `patrol_type`, `created` | `DELETE /api/v1.0/activity/patrols/types/<value>/` if no patrol uses the type. Otherwise `PATCH` it with `{"is_active": false}`. |
| `patrol_type`, `updated` | `PATCH /api/v1.0/activity/patrols/types/<value>/` with the `before` object. |

Teams and the roster subject group are created by an admin, not by the
command, so they are removed where they were made. A second run after a
clean-up recreates everything.
