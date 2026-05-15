# `choices`

Upsert SMART option sets as EarthRanger `Choice` records. Run as a standalone
step or let `datamodel` invoke it inline.

```bash
er-smart-sync choices [OPTIONS]
```

## Current limitations

Only file-based runs are supported (`--from-file` is required). SMART API
options are accepted on the command line for symmetry with other subcommands
but are not used by `choices` yet; `--smart-language` is the exception
(consulted for parsing the XML).

## Required

- `--from-file PATH`
- EarthRanger credentials (`--er-endpoint` + `--er-token`, or login/password)

## Options

| Flag | Default | Description |
|---|---|---|
| `--config FILE` | — | YAML config file path. |
| `--from-file PATH` | **required** | Local SMART data model XML. |
| `--cm-from-file PATH` | — | Configurable model overlay XML. |
| `--cm-uuid UUID` | zero UUID | Configurable-model UUID. |
| `--smart-language CODE` | `en` | Language code for XML parsing. |
| `--er-endpoint URL` | from config | EarthRanger API root. |
| `--er-token TOKEN` | from config | EarthRanger token. |
| `--er-username` / `--er-password` | from config | Alternative to token. |
| `--er-id ID` | `cli` | Integration ID. |

(Other SMART API flags are present in `--help` for symmetry but ignored.
A warning is logged if they're passed.)

## Invocations

**Pre-warm a new tenant:**

```bash
er-smart-sync choices \
  --config sync.yaml \
  --from-file ~/datamodel.xml \
  --cm-from-file ~/datamodel.cm.xml
```

**Dry-run:**

```bash
er-smart-sync --dry-run choices \
  --config sync.yaml \
  --from-file ~/datamodel.xml
```

## Exit codes

- **0** — success.
- **non-zero** — at least one Choice operation failed, OR the usage was
  invalid (e.g. missing `--from-file`).

## See also

- [Workflow: Populate choices](../workflows/populate-choices.md)
- [Concept: ER Choice records](../concepts/choices.md)
