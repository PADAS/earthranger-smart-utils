# `datamodel`

Push a SMART data model into EarthRanger as event categories and event types.

```bash
er-smart-sync datamodel [OPTIONS]
```

## Required (one of)

- `--from-file PATH` (with `--ca-identifier ID`) — load a SMART data model
  from a local XML file
- `--smart-api URL` + SMART credentials + `--smart-ca-uuid UUID` — fetch from
  the SMART Connect API

## Options

| Flag | Default | Description |
|---|---|---|
| `--config FILE` | — | YAML config file path. CLI flags override config values. |
| `--from-file PATH` | — | Load data model from a local XML file. Requires `--ca-identifier`. |
| `--cm-from-file PATH` | — | Load a configurable model overlay. Requires `--from-file`. |
| `--cm-uuid UUID` | zero UUID | Configurable-model UUID. Required when loading multiple configurable models for the same CA, to avoid event-type value collisions. |
| `--include-base-datamodel` | off | With `--cm-from-file`, also push the base data model as its own ER category. |
| `--ca-identifier ID` | required for `--from-file` | Short alphanumeric code (2–30 chars; letters, digits, hyphens, underscores) used as the ER event-category identifier. Ignored for API-based runs (extracted from the CA label instead). |
| `--mode {both,create-only,update-only}` | `both` | Restrict to creating only new event types, updating only existing ones, or both. |
| `--event-type-version {v1,v2}` | from config or v2 | Which ER event-type API version to target. |
| `--skip-choices` | off | Skip the choices upsert phase (v2 only). Use if `er-smart-sync choices` was run separately. |
| `--smart-api URL` | from config | SMART Connect API URL. Required for API-based runs. |
| `--smart-username NAME` | from config | SMART login. |
| `--smart-password PASSWORD` | from config | SMART password. |
| `--smart-version VERSION` | from config | SMART Connect server version. |
| `--smart-language CODE` | from config | Language code (e.g. `en`, `es`). |
| `--smart-ca-uuid UUID` (multiple) | from config | Conservation area UUID(s) for API-based runs. |
| `--er-endpoint URL` | from config | EarthRanger API root. |
| `--er-token TOKEN` | from config | EarthRanger token. |
| `--er-username NAME` | from config | EarthRanger login (fallback to password auth). |
| `--er-password PASSWORD` | from config | EarthRanger password. |
| `--er-id ID` | `cli` | Integration ID (state-tracking key). |

## Common invocations

**File-based, v2 (default):**

```bash
er-smart-sync datamodel \
  --config sync.yaml \
  --from-file ~/datamodel.xml \
  --ca-identifier JKPERU
```

**File-based with configurable model:**

```bash
er-smart-sync datamodel \
  --config sync.yaml \
  --from-file ~/datamodel.xml \
  --cm-from-file ~/datamodel.cm.xml \
  --ca-identifier JKPERU \
  --include-base-datamodel
```

**API-based:**

```bash
er-smart-sync datamodel \
  --config sync.yaml \
  --smart-ca-uuid 0a1b2c3d-4e5f-6789-abcd-ef0123456789
```

**Update-only (won't create new event types):**

```bash
er-smart-sync datamodel \
  --config sync.yaml \
  --from-file ~/datamodel.xml \
  --ca-identifier JKPERU \
  --mode update-only
```

**Force v1 schemas:**

```bash
er-smart-sync datamodel \
  --config sync.yaml \
  --from-file ~/datamodel.xml \
  --ca-identifier JKPERU \
  --event-type-version v1
```

## Summary stats

Printed at the end of every run. See
[Push a data model — Reading the summary](../workflows/push-datamodel.md#reading-the-summary).

## See also

- [Workflow: Push a data model](../workflows/push-datamodel.md)
- [`choices`](choices.md)
- [`inspect-datamodel`](inspect-datamodel.md)
