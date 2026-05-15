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

## License

Apache License 2.0. See [LICENSE](LICENSE).
