# er-smart-sync

**SMART Connect ↔ EarthRanger synchronization**, packaged as a Python CLI for the
PADAS support team and project managers.

## What it does

Three sync flows in one tool:

1. **Push a data model** (SMART → ER) — turn a SMART conservation area's data model
   and configurable model overlays into EarthRanger event categories, event types,
   and the underlying Choice records that v2 event types reference.
2. **Sync events** (ER → SMART) — poll EarthRanger events and forward them through
   the message broker for routing back into SMART.
3. **Sync patrols** (ER → SMART) — poll EarthRanger patrols (with track points,
   segment events, and attached files) and forward them the same way.

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
