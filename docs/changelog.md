# Changelog

Notable changes by version. Every release is also published to
[PyPI](https://pypi.org/project/er-smart-sync/) and tagged on
[GitHub Releases](https://github.com/PADAS/earthranger-smart-utils/releases),
which carry the full commit-level history.

## Unreleased

### Added

**`run_import` accepts `progress=` and `stop=`** for library callers: a per-patrol progress callback and a `threading.Event` that ends the run before the next patrol starts (`ImportResult.stopped_early`). The CLI is unchanged.

## 0.8.0 — 2026-10-15

### Changed

**`import-patrols` makes far fewer requests per patrol** — events are created
in lists of 20 (das writes a list atomically), the patrol read replaces one
probe per event, and a leader's track source is looked up once per run — and
gains `--workers N` to import N patrols at a time. Measured on staging in
October 2026, throughput is limited by das's per-record server work, so
`--workers 4` gained at most 1.2×; see the CLI reference's Speed section.
Re-run behaviour is unchanged; a rejected list or a duplicate id falls back to
one-at-a-time posting for that patrol only, and a patrol with a rejected event
is reported failed while its other events are still imported.

### Fixed

**`import-patrols`: whole-day leg times no longer stretch segments.** SMART
stores 00:00 to 23:59:59 on a leg day when nobody entered times. The import
took those as real and unioned them with the track, so every such segment
ran the whole day. They now count as unset, and the track, then the
waypoints, set the day's range. On SMART's demo bundle this affects 4 of 13
leg days. Re-run with a fresh `--ledger` to correct patrols already
imported: existing patrols are updated in place.

## 0.7.0 — 2026-10-13

### Added

**`import-patrols`.** Writes a smart-able bundle's patrol history into
EarthRanger: a patrol per SMART patrol with a segment per leg (team, leader,
members, times, universal fields and a `smart_` provenance block), an event
per observation linked to its segment (multi-observation waypoints as
incident collections, attachments posted once), and each leader's track as
location observations. Runs the preflight checks first; `--allow-blocking`
skips only the affected patrols. Re-runs write nothing new: ids are derived
from SMART uuids and every row is looked up before it is written, and a
ledger beside the bundle records finished stages. Retired members are
reactivated for the run and retired again. Exit 0, 1 when a patrol failed,
2 when preflight blocks (ERA-14139, ERA-14140, ERA-14141). Verified on
gundi-dev.staging with SMART's demo bundle: 5 patrols, 141 events with the
attachment, 232 track points; a re-run, and a re-run without the ledger,
wrote nothing. See [import-patrols](cli-reference/import-patrols.md).

## 0.6.0 — 2026-10-13

### Added

**`datamodel --bundle`.** With a smart-able migration bundle, parent or
inactive SMART categories that the bundle's observations use also get an
event type, created inactive, with the category's own and inherited
attributes. Without it, those historical observations would have no event
type to import against. v2 only. Verified on gundi-dev with SMART's demo
bundle: `animals.sign.` (a parent) and `humanactivity.rocksminerals.`
(inactive) were created inactive. The choice lists those categories use
are pushed with them. A category with no attributes gets a schema with no
fields. With `--cm-from-file`, observed data model categories the
configurable model lacks get inactive types under the model's prefix.
Choice options those historical categories need but the configurable model
omits are retained as inactive entries, including when the model's option
list is empty. They resolve historical values without widening the active
dropdowns.
**`patrol-preflight`.** A read-only check that an EarthRanger site is ready
for a smart-able bundle's patrols: the patrol_schemas flag, operator
permissions, patrol types, universal patrol fields, choices, subjects on the
roster das enforces, retired members, teams and event types, plus the
bundle's own warnings. Text or `--json`; exit 0, 2 when something blocks, 1
on error. Each blocking finding names the patrols it affects (ERA-14138).
Verified against gundi-dev.staging with SMART's demo bundle. The patrol
types, universal fields, choices, six subjects on the roster and the team
all passed. After the demo data model was pushed with `datamodel --bundle`,
the two observed categories SMART keeps no event type for (`animals.sign.`,
a parent, and `humanactivity.rocksminerals.`, retired) matched their
inactive event types with a warning; the run exited 0, with a second
warning for the inferred timezone.
`patrol-datamodel` now finds a patrol type an admin deactivated and
reactivates it, instead of trying to create a duplicate.
See [patrol-preflight](cli-reference/patrol-preflight.md).


**`patrol-datamodel`.** From a smart-able migration bundle, creates or
updates the ER patrol types (one per SMART transport), the universal patrol
fields and their choices, and the ranger subjects, then writes an admin
checklist for what the API cannot do (ERA-14137). Idempotent; honours
`--dry-run`; reads the bundle without SMART Connect. See
[patrol-datamodel](cli-reference/patrol-datamodel.md). Verified on
2026-10-13 against gundi-dev.staging with the `patrol_schemas` preview
feature on and SMART's demo conservation area as the bundle: a real run
created two patrol types, wrote the universal patrol fields, eleven choices
and seven ranger subjects, and the run after it reported everything
unchanged and made no writes. One of the seven was SMART's default `smart`
login; with the login rule below, a fresh site gets six, and the next
gundi-dev run deactivated the seventh. The first attempt showed that das's
meta-schema requires `deprecated` on every property; the builder now sets
it. The checklist named the demo's unlisted team, the missing roster group
and the patrol-type icons.

SMART logins that never went on a patrol, such as the `smart` admin every
SMART install creates, are not made ranger subjects, and a subject an
earlier run made for one is deactivated. Logins that did patrol stay
subjects. This reads `has_smart_login`, which smart-able bundles now carry.
A station with no name in SMART gets a readable placeholder instead of
its UUID; it is inactive unless a patrol uses it, and then the checklist
asks for a name. The patrol choice fields are site-wide, so the run changes
only options its bundle names and never deactivates another conservation
area's or an admin's. A custom patrol attribute that goes inactive is
deprecated and taken off the form rather than removed, so past segments
still validate. Each step's unexpected failure is recorded and the next
step runs. A legacy subject is matched by SMART employee id only when that
id is unique, since SMART reuses ids. `--er-token`, `--er-endpoint` and the
other connection flags now override just their own setting in `--config`.
Every real run that changes anything writes `patrol-datamodel-run-<time>.json`
beside the checklist: each write in order with the values it replaced, which
is what the undo guide now works from instead of searching the site. Backup
and record file names carry microseconds and never overwrite one another.
A custom attribute key too long for ER's 40-character choice field is
shortened with a hash suffix, a field an admin moved to another section
keeps a matching `parent`, and a manifest that is not a JSON object is
reported as an invalid bundle.

### Fixed

**`datamodel` re-runs no longer re-patch every v2 event type.** The v2
builder put a `choices` block in each choice field's UI schema. das's
metaschema has no such key and drops it on save, so every re-run saw a
difference and PATCHed all event types. The builder now emits only the keys
das accepts; the dropdown options still come from the `$ref` in the JSON
schema. Verified on gundi-dev: a re-run reports 39 of 39 event types
unchanged. Sites pushed before this need nothing: das never stored the key.

## 0.5.1 — 2026-10-12

### Fixed

**A failed patrol publish no longer looks like an oversized message.** The
patrol sync caught every publish error, logged it as "message too large",
and still advanced `patrol_last_poll_at`, so a publisher outage that
outlasted its own retries silently lost those patrols. Only an exception
whose class is named `MessageTooLargeError` is treated as oversized and
skipped; any other publish failure is re-raised, the window is not
advanced, and the next run retries it (the pre-0.5.0 behaviour).

## 0.5.0 — 2026-10-12

### Added

**Colliding v1 event types are migrated before a v2 push.** With
`event_type_version: v2`, the datamodel sync now calls EarthRanger's
`POST /api/v2.0/activity/eventtypes/migrate/` for SMART-derived values
that still exist as v1, then patches them to the library's schema.
Controlled by `migrate_legacy_v1` (default on) and
`--no-migrate-legacy-v1`; skipped under `--mode create-only`. New
run-summary counters `event_types_migrated`,
`event_types_migrate_errored` and `legacy_v1_snapshot_errored`.

### Changed

**Choice value rules now come from smartconnect-client.**
`sanitize_choice_value` and the over-100-character hash shortening are
imported from `smartconnect.choices` (smartconnect-client ≥ 1.15.0) so
cdip-routing can apply the inverse lookup from one definition. Output is
byte-for-byte unchanged.

## 0.4.0 — 2026-09-28

### Fixed

**Leaf categories that re-declare an inherited attribute no longer produce
rejected event types.** SMART lets a leaf category override an attribute it
inherits from a parent (commonly `isactive="false"` to disable it per
leaf). Both builders concatenated the leaf's own attributes with the
inherited ones, so the attribute appeared twice — ER rejected every such v2
event type with `Invalid JSON Schema: ... has non-unique elements at
ui.sections.section-1.leftColumn`, and the inherited active entry clobbered
the leaf's disable-override. First declaration (the leaf's own) now wins in
both the v2 and v1 builders.

**TREE attributes migrate identically from the base datamodel and a
Configurable Model (ERCS-8246).** The CM path previously surfaced only
top-level parent nodes — dropping every nested child option — while the
base path surfaced leaves only. smartconnect-client ≥ 1.13.0 flattens CM
tree curations to CM-leaves keyed by the same dotted paths as base-DM
options, and emits effective active flags (a leaf under a deactivated
branch arrives inactive). It also fixes MLIST attributes losing their
options entirely in file-based datamodel parses. The smartconnect-client
pin is now `>=1.14.0`, activating the converged behavior.

### Added

**The EarthRanger endpoint accepts a bare domain or scheme + host.**
`site.pamdas.org`, `https://site.pamdas.org`, and the full
`https://site.pamdas.org/api/v1.0` service root all normalize to the
service-root form, everywhere an ER endpoint is accepted: the YAML
`earthranger.endpoint` key, `--er-endpoint`, and `copy-event-type`'s
`--source-endpoint`/`--dest-endpoint`. An explicit `http://` scheme is
preserved for development servers.

**`copy-event-type` is now documented** — in the
[CLI reference](cli-reference/copy-event-type.md) and USAGE.md; the
command itself shipped earlier (#12).

## 0.3.3 — 2026-09-25

### Changed

**Choice lists are now shared across the event types of a datamodel sync.**
Previously every event type got its own copy of each dropdown's Choice
records, even when the SMART data model defined one option list used by many
categories — a large CA could create thousands of duplicate Choice rows.
Choice `field` names now hash the (CA, CM) scope plus the attribute key
instead of the event-type value (`dm{8-hex}_{attr_key}` replacing
`et{8-hex}_{attr_key}`), so all event types built from one datamodel
reference a single list per attribute.

Sharing is deliberately bounded to one CA and one Configurable Model:
different CAs can define the same attribute key with different options, and
each CM curates its own option subsets, so neither shares lists with the
other. The consolidate-mode variant discriminator remains per-event-type.

Re-syncing an existing site re-points event-type schemas at the shared
lists and leaves the legacy `et*_` Choice records in place (active but
unreferenced); historical events are unaffected because stored option
values are identical under both naming schemes. See
[Concept: ER Choice records](concepts/choices.md#how-field-names-are-derived).

### Fixed

**Inactive SMART options no longer migrate as active (ERCS-8246).** The
v2 choices builder hardcoded `is_active=true` on the base-datamodel path,
so options deactivated in SMART arrived active in EarthRanger (366 of 470
species options on one reported migration). Choices now carry the SMART
active flag; existing ER records are deactivated in place on the next
sync. The v1 builder excludes inactive options from its inline enums,
mirroring what its CM path already did.

## 0.3.2 — 2026-08-05

### Fixed

**`smart.use_language_code` is now honored by file-based syncs.**
`datamodel --from-file`, `choices`, and `inspect-datamodel` built their SMART
XML parsers from the `--smart-language` flag, whose default was `en`, and never
read the value loaded from `--config`. Setting `use_language_code: es` in the
config had no effect on those commands, so a data model carrying no English
labels resolved every display name to the literal string `n/a` — and the sync
pushed those to EarthRanger as if they were real names. `--smart-language` now
overrides the config only when you pass it explicitly.
([#13](https://github.com/PADAS/earthranger-smart-utils/issues/13))

If a site was already populated with `n/a` labels, no manual cleanup is needed:
re-run with the correct language and the existing records are corrected in
place. Event types and choices are both compared on `display` and patched when
it differs.

**`--smart-version` is no longer ignored when `--config` is used.** The flag
carried a `7.0` default, so an explicitly passed value was indistinguishable
from an absent one, and the config loader never applied it on top of a config
read from file — `smart.version` from the YAML always won and the flag was
silently discarded. This is the mirror image of the `use_language_code` bug
above, where the flag won and the config value was dropped; both trace back to
the same root, a flag default that hid whether the user had passed anything.

The version one affected behavior rather than presentation: SMART versions
below 7.5.3 need `smart_observation_uuid` patched onto outgoing events, so a
silently discarded `--smart-version` could change what the sync actually sent.

### Added

**Data-model syncs warn when no display name resolves.** SMART substitutes the
literal `n/a` whenever no `<names>` entry matches the requested language; it
does not raise or warn, so an unmatched language code produced a complete,
valid-looking data model with every label set to `n/a`. `datamodel`, `choices`,
and `inspect-datamodel` now detect that and name the codes the model actually
declares:

```
WARNING: All 645 SMART display names resolved to "n/a": the data model has no
labels in language 'en'. Available language code(s): es. Set
smart.use_language_code in your config (or pass --smart-language) to one of
these and re-run.
```

The warning requires a *wholesale* failure. A single untranslated label is
normal SMART data and stays silent.

### Documentation

- [Configuration](getting-started/config.md) gained a
  [Display names and languages](getting-started/config.md#display-names-and-languages)
  section covering how to read the `<languages>` block in a data-model XML
  before the first run.
- `--smart-language` and `--smart-version` help text now states the actual
  precedence: the flag overrides `--config`, and the built-in default applies
  only when neither is set.

## Earlier releases

`v0.3.1` and earlier predate this file. See
[GitHub Releases](https://github.com/PADAS/earthranger-smart-utils/releases)
for their notes.
