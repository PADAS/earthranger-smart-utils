# er-smart-sync

Synchronize [SMART Connect](https://smartconservationtools.org/) wildlife-monitoring
data with [EarthRanger](https://www.earthranger.com/).

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

## Quick install

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

- **[USAGE.md](USAGE.md)** — developer-oriented CLI reference, architecture notes, Pydantic v1 conventions, ER version-gating details, and the bracketed-CA-label convention as it affects the codebase.
- **[CLAUDE.md](CLAUDE.md)** — codebase guidance for Claude Code sessions.
- **[docs/superpowers/specs/](docs/superpowers/specs/)** — design specs for non-trivial features (v2 event types, choices population, etc.).
- **[docs/superpowers/plans/](docs/superpowers/plans/)** — implementation plans for recent feature work.

The user-facing documentation at the link above covers what `er-smart-sync` does and how to use it; the references in this section cover how it's built.

## License

Apache License 2.0. See [LICENSE](LICENSE).
