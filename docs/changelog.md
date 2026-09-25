# Changelog

Notable changes by version. Every release is also published to
[PyPI](https://pypi.org/project/er-smart-sync/) and tagged on
[GitHub Releases](https://github.com/PADAS/earthranger-smart-utils/releases),
which carry the full commit-level history.

## Unreleased

### Fixed

**Inactive SMART options no longer migrate as active (ERCS-8246).** The
v2 choices builder hardcoded `is_active=true` on the base-datamodel path,
so options deactivated in SMART arrived active in EarthRanger (366 of 470
species options on one reported migration). Choices now carry the SMART
active flag; existing ER records are deactivated in place on the next
sync. The v1 builder excludes inactive options from its inline enums,
mirroring what its CM path already did.

**TREE attributes migrate identically from the base datamodel and a
Configurable Model (ERCS-8246).** The CM path previously surfaced only
top-level parent nodes — dropping every nested child option — while the
base path surfaced leaves only. smartconnect-client ≥ 1.13.0 flattens CM
tree curations to CM-leaves keyed by the same dotted paths as base-DM
options, and emits effective active flags (a leaf under a deactivated
branch arrives inactive). It also fixes MLIST attributes losing their
options entirely in file-based datamodel parses. The smartconnect-client
pin is now `>=1.13.0`, activating the converged behavior.

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
