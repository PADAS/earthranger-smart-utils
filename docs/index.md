# er-smart-sync

**SMART ↔ EarthRanger synchronization and migration**, packaged as a Python CLI
for the PADAS support team and project managers.

## What it does

Five flows in one tool:

1. **Push a data model** (SMART → ER) — turn a SMART conservation area's data model
   and configurable model overlays into EarthRanger event categories, event types,
   and the underlying Choice records that v2 event types reference.
2. **Sync events** (ER → SMART) — poll EarthRanger events and forward them through
   the message broker for routing back into SMART.
3. **Sync patrols** (ER → SMART) — poll EarthRanger patrols (with track points,
   segment events, and attached files) and forward them the same way.
4. **Migrate patrols** (SMART backup → ER) — move a conservation area's patrol
   setup and history into EarthRanger's patrol model from a migration bundle that
   [smart-able](https://github.com/PADAS/er-smart-able) writes from a SMART backup,
   with no SMART Connect server involved. Three subcommands, run in order:
   `patrol-datamodel` (patrol types, universal patrol fields, choices, ranger
   subjects), `patrol-preflight` (read-only readiness check), `import-patrols`
   (patrols, their events and tracks). Every step is safe to re-run.
5. **Copy an event type** (ER → ER) — copy one event type, with the choice lists
   its schema references, from one EarthRanger site to another.

## Who this is for

- **Support staff** rolling out new conservation areas or troubleshooting a sync.
- **Project managers** verifying that data flows match expectations.

If you're a developer working on the codebase itself, see the
[GitHub repository](https://github.com/PADAS/earthranger-smart-utils) and the
[USAGE.md](https://github.com/PADAS/earthranger-smart-utils/blob/main/USAGE.md)
reference.

## Start here

- **First time using this tool?** → [Installation](getting-started/install.md) →
  [First run](getting-started/first-run.md)
- **Need to push a SMART data model into ER?** →
  [Push a data model](workflows/push-datamodel.md)
- **Moving a site's patrol history from SMART into ER?** →
  [Migrate SMART patrols](workflows/migrate-patrols.md)
- **Hitting an error?** → [Troubleshooting](troubleshooting.md)
- **Looking up a specific flag?** → [CLI reference](cli-reference/overview.md)

## How this site is organized

| Section | What's there |
|---|---|
| **Getting started** | Install, first-run smoke test, configuration |
| **Workflows** | Step-by-step procedures for each sync flow |
| **CLI reference** | Every flag for every subcommand |
| **Concepts** | Background on what's being synced and why (no Python required) |
| **Troubleshooting** | Common errors and their fixes |
