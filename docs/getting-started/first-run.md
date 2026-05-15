# First run

This page walks through a no-writes "does it work" check against a test
EarthRanger tenant and a local SMART data model XML file. Every step here is
safe — nothing is POSTed to EarthRanger until the very last optional step,
and even then only if you remove `--dry-run`.

## 1. Generate a config template

```bash
er-smart-sync config-template > sync.yaml
```

This writes a fully-commented YAML template to `sync.yaml`. Open it in your
editor.

## 2. Fill in your credentials

At minimum, set the `earthranger:` section:

```yaml
earthranger:
  id: my-tenant            # any identifier; used for state tracking
  endpoint: https://your-tenant.pamdas.org/api/v1.0
  token: "<your token>"    # OR provide login + password
```

If you'll also be syncing from the SMART API (vs a local file), fill in the
`smart:` section similarly.

See [Configuration](config.md) for the full reference.

## 3. Validate the credentials

```bash
er-smart-sync validate-config --config sync.yaml
```

This makes one read-only request to each service to confirm the credentials
work. Expected output: `OK` lines for each. If you see `FAIL`, the message
will tell you what's wrong (typically a wrong token or endpoint URL).

## 4. Preview a data model push

If you have a SMART data model XML file:

```bash
er-smart-sync inspect-datamodel \
  --config sync.yaml \
  --from-file path/to/datamodel.xml \
  --ca-identifier FOASF
```

This prints exactly what would be created in EarthRanger — event types,
choices, fields — without writing anything. Read the output and verify it
matches what you expect.

See [Inspect a data model](../workflows/inspect-datamodel.md) for details on
reading the output.

## 5. Dry-run the real push

```bash
er-smart-sync --dry-run datamodel \
  --config sync.yaml \
  --from-file path/to/datamodel.xml \
  --ca-identifier FOASF
```

`--dry-run` intercepts every write and logs what would have happened. Reads
still hit EarthRanger (so the synchronizer can plan correct intended writes),
but nothing changes on the server.

Expected output: log lines for each event type and choice that would be created
or updated, then a summary like:

```
Datamodel sync summary:
  categories_created: 0  (would: 1)
  ...
```

## 6. The real run (when you're ready)

Drop `--dry-run`:

```bash
er-smart-sync datamodel \
  --config sync.yaml \
  --from-file path/to/datamodel.xml \
  --ca-identifier FOASF
```

The summary at the end shows what changed. Re-running the same command should
report all event types and choices as `unchanged` — the tool is idempotent.

## Next

→ [Workflows: Push a data model](../workflows/push-datamodel.md) — full
procedure with diagrams.
