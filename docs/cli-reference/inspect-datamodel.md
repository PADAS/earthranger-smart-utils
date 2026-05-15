# `inspect-datamodel`

Print what `datamodel` would create or update in EarthRanger — without making
writes.

```bash
er-smart-sync inspect-datamodel [OPTIONS]
```

## Required (one of)

- `--from-file PATH` + `--ca-identifier ID`
- `--smart-ca-uuid UUID` (with SMART credentials)

## Options

| Flag | Default | Description |
|---|---|---|
| `--config FILE` | — | YAML config file. |
| `--from-file PATH` | — | Local SMART data model XML. |
| `--cm-from-file PATH` | — | Configurable model overlay. |
| `--cm-uuid UUID` | zero UUID | CM UUID. |
| `--ca-identifier ID` | required for `--from-file` | 2–30 alphanumeric/dash/underscore. |
| `--ca-label LABEL` | `[INSPECT]` | Used in the printed "CA:" header. |
| `--smart-ca-uuid UUID` | — | Triggers API-based inspect. |
| `--event-type-version {v1,v2}` | from config or v2 | Which schema shape to print. |
| `--smart-*` | from config | SMART credentials for API-based inspect. |
| `--er-*` | from config | EarthRanger credentials (used for nothing in inspect, but accepted for symmetry). |

## Invocations

**File-based, v2:**

```bash
er-smart-sync inspect-datamodel \
  --from-file ~/datamodel.xml \
  --ca-identifier JKPERU
```

**File-based, v1:**

```bash
er-smart-sync inspect-datamodel \
  --from-file ~/datamodel.xml \
  --ca-identifier JKPERU \
  --event-type-version v1
```

**API-based:**

```bash
er-smart-sync inspect-datamodel \
  --config sync.yaml \
  --smart-ca-uuid 0a1b2c3d-4e5f-6789-abcd-ef0123456789
```

## Reading the output

See [Workflow: Inspect a data model](../workflows/inspect-datamodel.md) for
annotated examples of v1 and v2 output.

## See also

- [Workflow: Inspect a data model](../workflows/inspect-datamodel.md)
- [`datamodel`](datamodel.md)
