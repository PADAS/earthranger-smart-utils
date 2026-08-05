# Changelog

Notable changes by version. Every release is also published to
[PyPI](https://pypi.org/project/er-smart-sync/) and tagged on
[GitHub Releases](https://github.com/PADAS/earthranger-smart-utils/releases),
which carry the full commit-level history.

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

**`--smart-version` is no longer ignored when `--config` is used.** Same cause
— the flag's `7.0` default always won over the config file. This one affected
behavior rather than presentation: SMART versions below 7.5.3 need
`smart_observation_uuid` patched onto outgoing events, so a dropped version
could change what the sync actually sent.

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
