# patrol-preflight

A read-only check, run before `import-patrols`, that an EarthRanger site has
everything a smart-able migration bundle needs. It writes nothing to ER.
Every finding comes with the exact fix.

## Usage

```bash
er-smart-sync patrol-preflight --config sync.yaml            # bundle.path and earthranger from the file
er-smart-sync patrol-preflight --bundle work/export/050500/bundle \
    --er-endpoint site.pamdas.org --er-token TOKEN --json      # machine-readable report
```

| Option | Meaning |
| --- | --- |
| `--bundle DIR` | the bundle directory (holds `manifest.json`); or `bundle.path` in `--config` |
| `--smart-language CODE` | the language the import will use for labels |
| `--timezone NAME` | the zone you know the site uses; a mismatch with the bundle is blocking |
| `--event-type-prefix PREFIX` | which data model push to import against when several match (see Event types) |
| `--event-type-version v1\|v2` | event type API version (default `earthranger.event_type_version`, v2) |
| `--json` | print the report as JSON: every item of each finding (the text report shows the first 10), the affected patrols, and `event_types_map`, the event type each observed category resolves to |

Exit status: **0** no blocking findings (warnings allowed), **2** blocking
findings, **1** an error before the checks could run (an invalid bundle,
bad flags, a client that cannot be created). A check that fails while
running (a 403, a 500, a timeout) becomes a blocking `check_failed` finding;
the other checks still run.

## Checks

| Code | Level | What it checks | Typical fix |
| --- | --- | --- | --- |
| `feature_flag` | blocking | the `patrol_schemas` preview feature is on (`GET status`) | enable it; the patrol-model checks below are skipped while it is off |
| `permissions` | blocking | the operator has `add`/`change`/`view` on patrols (this also covers reading the patrol configuration), `add` on segment events, and `add` on the event category of every event type the import uses, i.e. das's `<category>_create` (`GET user/me`; superusers pass) | grant them in Django admin |
| `patrol_types` | blocking | every transport the legs use has its active patrol type (inactive types are looked up too, so they are reported as inactive, not missing) | run `patrol-datamodel`, or reactivate the type |
| `segment_schema` | blocking | every universal patrol field is on the site and not deprecated | run `patrol-datamodel` |
| `choices` | warning | every mandate, station, patrol class and custom option a patrol uses exists and is active | run `patrol-datamodel` |
| `subjects` | blocking | every patrol member has a subject (matched by `smart_member_id`), and every active one is on the roster das enforces | run `patrol-datamodel --roster-group NAME`, or add them to a member group |
| `inactive_members` | warning / blocking | retired members (inactive subjects); blocking only for those in no configured member group | see Retired staff |
| `teams` | blocking | every team a patrol names exists and is active, matched by display or value against the patrol configuration's full team list | add the team under Patrol configuration > Teams |
| `event_types` | blocking / warning | every SMART category with observations has exactly one event type, active preferred; an inactive match is a warning | push the data model, or pass `--event-type-prefix`; see Event types for the other causes |
| `leg_span` | warning | legs with SMART's 1970-01-01 placeholder start | nothing; the import keeps only days with evidence |
| `time_coverage` | warning | leg days whose segments will be whole days | nothing; recorded on the segment |
| `timezone` | warning / blocking | the bundle's zone was inferred; blocking when `--timezone` disagrees | confirm with `smart.timezone`, or re-export |
| `language` | warning | labels missing in the chosen language | pick a better-covered language |
| `bundle_warnings` | warning | warnings smart-able recorded when it wrote the bundle | read them in `manifest.json` |
| `collection_type` | warning | waypoints with several observations, and no active `incident_collection` event type on the site, so their events import ungrouped | add or activate `incident_collection` |
| `check_failed` | blocking | a check could not run | fix the cause and run again |

## Example

Against gundi-dev.staging, after `patrol-datamodel`, the admin steps on its
checklist, and `datamodel --bundle` for the demo area, SMART's demo bundle
reported no blocking findings and two warnings, exit status 0:

```text
Patrol preflight for Example Conservation Area (SMART): 5 patrols, 0 blocking, 2 warning(s)

WARNING (2)
- [event_types] 2 observed categor(ies) map to an inactive event type (a SMART parent or retired category) (2)
    e.g. animals.sign.: smart-ca-import_animals_sign
    e.g. humanactivity.rocksminerals.: smart-ca-import_humanactivity_rocksminerals
- [bundle_warnings] smart-able recorded 1 warning(s) when it wrote the bundle (1)
    e.g. timezone Africa/Libreville was inferred from waypoint positions; set SMART_ABLE_TIMEZONE to override

All clear: no blocking findings.
```

(Fix lines omitted.) Before the data model push with `--bundle`, the same
run blocked on those two categories with exit status 2.

## Retired staff

das accepts only active, person-type subjects on the configured member
roster as segment members, whatever the segment's dates (its
`_validate_rosters`). So `import-patrols` reactivates retired members for
the run and retires them again when it finishes. The `inactive_members`
warning lists who that will touch. A retired member that is in no
configured member group would still be refused after reactivation, so that
case is blocking.

## Event types

`er-smart-sync datamodel` names each event type
`<prefix>_<category path>`, the path being the SMART category's key path
with dots as underscores (`threats.charcoal.` → `threats_charcoal`). The
prefix is `smart-ca-import` for `--from-file` pushes,
`smart-ca-import_<cm uuid>` with a configurable model, and the SMART CA
uuid for SMART Connect pushes. Preflight accepts only those shapes:
`<prefix>_<category path>` with one of the prefixes above, or exactly
`<--event-type-prefix>_<category path>` when the option is given. A longer
path that happens to end the same way is never matched. When a site holds
more than one push for the same categories, the match is ambiguous and
blocks those patrols; pass `--event-type-prefix` with the push to import
against. The resolved mapping is what `import-patrols` will use.

A blocking finding that names no patrols, such as a timezone mismatch or a
check that could not run, blocks every patrol. A missing event-category
permission blocks only the patrols whose observations use that category.

A category with no matching event type is blocking. It has one of three
causes:

- The data model was not pushed, or was pushed with another prefix.
- The category is a parent category, or is inactive in SMART. The data
  model sync skips those unless it is given the bundle: re-run
  `er-smart-sync datamodel ... --bundle <bundle dir>` to create them as
  inactive event types. Preflight then matches them, with a warning, and
  `import-patrols` imports those observations against them.
- The operator cannot read the category. Non-superusers only see event
  types of categories they have permission for.

A configurable model pushed in split mode (the default) creates one event
type per form variant: `<prefix>_<category path>_<form name>_<8 hex>`. A
category present only that way gets its own blocking finding, because
`import-patrols` cannot yet tell which variant an observation belongs to.
A consolidated push (`--cm-variant-mode consolidate`) is no fix either: it stores the fields under `section_N_<key>` names, and the import writes the attribute keys. These patrols cannot be imported yet; `import-patrols --allow-blocking` imports the others.
