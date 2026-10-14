# er-smart-sync

Synchronize [SMART Connect](https://smartconservationtools.org/) wildlife-monitoring
data with [EarthRanger](https://www.earthranger.com/), and migrate a SMART
conservation area's patrol history into EarthRanger from a backup.

## 📖 Full documentation

**https://padas.github.io/earthranger-smart-utils/**

Includes install instructions, step-by-step workflows for every sync flow,
a complete CLI reference, conceptual background, and troubleshooting.

## What it does

- **Datamodel sync** (SMART → ER) — turn SMART data models into EarthRanger
  event categories and event types.
- **Event sync** (ER → SMART) — poll EarthRanger events and forward them via
  message broker.
- **Patrol sync** (ER → SMART) — poll EarthRanger patrols with track points
  and attached files.
- **Patrol migration** (SMART backup → ER) — from a migration bundle written
  by [smart-able](https://github.com/PADAS/er-smart-able), with no SMART
  Connect needed: `patrol-datamodel` creates the patrol types, universal
  patrol fields, choices and ranger subjects; `patrol-preflight` checks the
  site is ready; `import-patrols` writes the patrol history (a patrol with a
  segment per leg, an event per observation, the leaders' tracks). Re-runs
  write nothing new. See
  [Migrate SMART patrols](https://padas.github.io/earthranger-smart-utils/workflows/migrate-patrols/).
- **Event-type copy** (ER → ER) — copy a single event type (and the v2
  choices its schema references) from one EarthRanger site to another via
  the `copy-event-type` CLI subcommand.

## Quick install

```bash
uv pip install er-smart-sync
er-smart-sync --help
```

Or install from source (for development):

```bash
git clone git@github.com:PADAS/earthranger-smart-utils.git
cd earthranger-smart-utils
uv pip install -e ".[dev]"
er-smart-sync --help
```

See the [Installation page](https://padas.github.io/earthranger-smart-utils/getting-started/install/)
for prerequisites, optional extras, and verification steps.

## For contributors

Working on the codebase itself? Start with:

- **[CLAUDE.md](CLAUDE.md)** — codebase conventions: Pydantic v1 API, SMART version-gating (<7.5.3 needs `smart_observation_uuid` patching), the bracketed-CA-label convention, and the protocol-based dependency-injection pattern.
- **[USAGE.md](USAGE.md)** — developer-oriented CLI reference and the bracketed-CA-label convention as it affects the codebase.
- **[docs/superpowers/specs/](docs/superpowers/specs/)** — design specs for non-trivial features (v2 event types, choices population, etc.).
- **[docs/superpowers/plans/](docs/superpowers/plans/)** — implementation plans for recent feature work.

The user-facing documentation at the link above covers what `er-smart-sync` does and how to use it; the references in this section cover how it's built.

## License

Apache License 2.0. See [LICENSE](LICENSE).
