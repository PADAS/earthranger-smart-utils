# CLI overview

Every `er-smart-sync` invocation starts with the same shape:

```bash
er-smart-sync [GLOBAL FLAGS] <subcommand> [SUBCOMMAND FLAGS]
```

## Global flags

| Flag | Description |
|---|---|
| `--verbose`, `-v` | Enable DEBUG logging from `er_smart_sync` (noisy libraries stay at WARNING). |
| `--dry-run` | Log intended writes without sending them to ER or the message broker. Reads still hit EarthRanger. |
| `--network-timeout SECONDS` | Process-wide ceiling on blocking socket operations. Default 600s. Pass `0` to disable and rely on each library's own timeouts. Also configurable via `ER_SMART_SYNC_NETWORK_TIMEOUT`. |
| `--help` | Show help for `main` or any subcommand. |

## Subcommands

| Subcommand | What it does |
|---|---|
| [`datamodel`](datamodel.md) | Push a SMART data model into EarthRanger as event categories and event types. |
| [`choices`](choices.md) | Upsert SMART option sets as EarthRanger `Choice` records (v2 prerequisite). |
| [`inspect-datamodel`](inspect-datamodel.md) | Preview what `datamodel` would push, without making writes. |
| [`events`](events.md) | Poll events from EarthRanger and publish them via the message broker. |
| [`patrols`](patrols.md) | Poll patrols from EarthRanger and publish them via the message broker. |
| [`validate-config`](validate-config.md) | Check that SMART and EarthRanger credentials work. |
| [`list-cas`](list-cas.md) | List the conservation areas available on a SMART server. |
| [`config-template`](config-template.md) | Print a fully-commented YAML config template. |

## Common patterns

**Get help on a subcommand:**

```bash
er-smart-sync datamodel --help
```

**Run with verbose logging:**

```bash
er-smart-sync -v datamodel ...
```

**Preview before writing:**

```bash
er-smart-sync --dry-run datamodel ...
```

**Override the network timeout for a long-running sync:**

```bash
er-smart-sync --network-timeout 1800 datamodel ...   # 30 minutes
```

## Exit codes

- **0** — success (or success with skipped/errored counts > 0; check the
  summary line).
- **non-zero** — usage error, fatal exception, or `choices`/`datamodel` had
  errored counts that triggered an explicit ClickException.
