# Installation

## Prerequisites

- **Python 3.10 or newer.** Check with `python3 --version`.
- **`uv`** (recommended) or `pip` for installing the package.
- **Read access to the `PADAS/earthranger-smart-utils` GitHub repository.**

## Install from the repo

Clone the repository and install in editable mode:

```bash
git clone git@github.com:PADAS/earthranger-smart-utils.git
cd earthranger-smart-utils
uv pip install -e ".[dev]"
```

The CLI is registered as `er-smart-sync`. Verify it installed:

```bash
er-smart-sync --help
```

You should see the list of subcommands (`datamodel`, `choices`, `events`,
`patrols`, `inspect-datamodel`, `validate-config`, `list-cas`,
`config-template`).

## Optional extras

| Extra | When to install | Command |
|---|---|---|
| `gcp` | Production runs using GCP Pub/Sub + GCS storage | `uv pip install -e ".[gcp]"` |
| `tracing` | OpenTelemetry tracing | `uv pip install -e ".[tracing]"` |
| `docs` | Local preview of this documentation site | `uv pip install -e ".[docs]"` |

Combine multiple: `uv pip install -e ".[dev,gcp,docs]"`.

## Verify

Run the test suite to confirm the install works end-to-end:

```bash
pytest -q
```

You should see all tests passing.

## Next

→ [First run](first-run.md) — confirm your credentials work and preview a sync
without making any writes.
