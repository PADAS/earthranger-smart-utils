# AGENTS.md

This file provides guidance to Codex (Codex.ai/code) when working with code in this repository.

## What This Project Does

er-smart-sync synchronizes data between **SMART Connect** (wildlife conservation monitoring) and **EarthRanger** (real-time data visualization for protected areas). It supports three sync flows:

1. **Datamodel sync** (SMART → ER): Push event categories and event types from SMART conservation areas to EarthRanger
2. **Event sync** (ER → SMART): Poll EarthRanger events and publish them via message broker (Pub/Sub) for routing to SMART
3. **Patrol sync** (ER → SMART): Poll EarthRanger patrols (with track points, segment events, files) and publish them

## Commands

```bash
# Install (editable, with dev dependencies)
uv pip install -e ".[dev]"

# Run all tests
pytest

# Run a single test
pytest tests/test_synchronizer.py::test_synchronize_er_events

# Lint
ruff check src tests

# Format
ruff format src tests

# Type check
ty check
```

## Architecture

The codebase uses a **protocol-based dependency injection** pattern. `ERSmartSynchronizer` is the core class that accepts pluggable implementations of four protocols (`protocols.py`):

- `MessagePublisher` — publishes events/patrols to a message broker (e.g. Google Pub/Sub)
- `FileStorage` — stores downloaded attachment files (e.g. Google Cloud Storage)
- `StateStore` — persists last-poll timestamps for incremental sync
- `TracingProvider` — distributed tracing spans

Default (null/local) implementations live in `defaults.py` and are used by the CLI. Production implementations (GCP Pub/Sub, GCS, etc.) are injected when running in the Gundi platform.

### Key External Dependencies

- **dasclient** (`DasClient`) — EarthRanger REST API client
- **smartconnect** (`SmartClient`) — SMART Connect API client; also provides `er_sync_utils` for building ER event types from SMART data models
- **gundi-core** — shared Pydantic schemas (`EREvent`, `ERPatrol`, `ERObservation`, `ERSubject`)

All three are pinned to specific wheel releases or minimum versions. Uses Pydantic v1 (`<2.0`).

### CLI

The Click CLI (`cli.py`) has three subcommands: `datamodel`, `events`, `patrols`. Each accepts either a `--config` YAML file or individual `--smart-*` / `--er-*` flags.

## Key Conventions

- Pydantic v1 API (`parse_obj_as`, `.json()`, `.dict()`) — do not use v2 patterns
- SMART versions below 7.5.3 require patching events with `smart_observation_uuid`; this is version-gated
- Conservation area labels are expected to contain a bracketed identifier like `"Name [CODE]"` — the `[CODE]` is extracted and used as the event category value
