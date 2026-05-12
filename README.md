# er-smart-sync

Synchronize [SMART Connect](https://smartconservationtools.org/) wildlife-monitoring data with [EarthRanger](https://www.earthranger.com/).

`er-smart-sync` is both a Python library and a CLI. It runs in two modes:

- **Standalone CLI** — used by the support team to push data models, validate credentials, and run one-off syncs from a workstation.
- **Library** — used inside the Gundi platform with injected `MessagePublisher`, `FileStorage`, `StateStore`, and `TracingProvider` implementations for production scheduled syncs.

## What it does

Three sync flows:

1. **Datamodel sync** (SMART → ER) — turn SMART data models and configurable models into EarthRanger event categories and event types.
2. **Event sync** (ER → SMART) — poll EarthRanger events and publish them via a message broker for routing back to SMART.
3. **Patrol sync** (ER → SMART) — poll EarthRanger patrols (with track points, segment events, and attached files) and publish them.

## Install

Requires Python 3.10+.

```bash
uv pip install -e ".[dev]"      # editable install with dev tooling
# or .[gcp] for the Pub/Sub + GCS extras used in production
```

The CLI is registered as `er-smart-sync`.

## Quick start

```bash
# 1. Write a config template, fill in credentials.
er-smart-sync config-template > sync.yaml
$EDITOR sync.yaml

# 2. Confirm both ends are reachable.
er-smart-sync validate-config --config sync.yaml

# 3. Preview the data model conversion before touching ER.
er-smart-sync inspect-datamodel --config sync.yaml \
  --smart-ca-uuid 0a1b2c3d-4e5f-6789-abcd-ef0123456789

# 4. Dry-run the real push.
er-smart-sync --dry-run datamodel --config sync.yaml

# 5. Push for real.
er-smart-sync datamodel --config sync.yaml
```

Subcommands: `datamodel`, `events`, `patrols`, `validate-config`, `list-cas`, `inspect-datamodel`, `config-template`. Run `er-smart-sync <cmd> --help` for the full flag set.

See [USAGE.md](USAGE.md) for the complete CLI reference, configuration shape, and recommended workflows.

## Architecture

`ERSmartSynchronizer` is the core class. It accepts pluggable implementations of four protocols (`src/er_smart_sync/protocols.py`):

| Protocol | Purpose | CLI default | Gundi default |
|---|---|---|---|
| `MessagePublisher` | Publish events/patrols to a message broker | `NullPublisher` (logs) | GCP Pub/Sub |
| `FileStorage` | Store downloaded attachment files | `LocalFileStorage` | GCS |
| `StateStore` | Persist last-poll timestamps | `JsonFileStateStore` (atomic writes) | Gundi-provided |
| `TracingProvider` | Distributed tracing spans | `NullTracing` | OpenTelemetry |

The CLI wires up the local defaults; the Gundi platform injects production implementations.

Key modules:

- `src/er_smart_sync/synchronizer.py` — `ERSmartSynchronizer` plus the retry helper.
- `src/er_smart_sync/smart_to_er.py` — owned SMART data-model → ER event-type conversion (replaces the legacy mapping in `smartconnect.er_sync_utils`; adds TIME / DATETIME / ATTACHMENT support and fixes multi-value LIST/MLIST).
- `src/er_smart_sync/cli.py` — Click CLI.
- `src/er_smart_sync/defaults.py` — null/local implementations of the four protocols plus `DryRunERClient`.
- `src/er_smart_sync/config.py` — `SyncConfig` / `SmartConnectConfig` / `EarthRangerConfig` (Pydantic v1).

## Development

```bash
uv pip install -e ".[dev]"

pytest                 # full suite
pytest tests/test_smart_to_er.py::test_mlist_emits_array_of_enum  # single test

ruff check src tests   # lint
ruff format src tests  # format
ty check               # type check
```

### Conventions

- Pydantic v1 API (`parse_obj_as`, `.json()`, `.dict()`). Do not use v2 patterns.
- SMART versions below 7.5.3 require patching events with `smart_observation_uuid`; this is version-gated in the synchronizer.
- Conservation-area labels are expected to contain a bracketed short code, e.g. `"Foasf Reserve [FOASF]"`. The bracketed code is extracted and used as the event-category identifier.
- ER normalizes event-type values to lowercase on write — `smart_to_er.build_event_types` lowercases values client-side to keep round-trip comparisons consistent.

## License

MIT. See `pyproject.toml`.
