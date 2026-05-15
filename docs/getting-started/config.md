# Configuration

Most subcommands accept either a `--config <file>.yaml` flag or individual CLI
flags. The YAML form is recommended for repeatable runs.

## Generating a template

```bash
er-smart-sync config-template > sync.yaml
```

The output includes every field with inline comments. Edit it in your editor.

## YAML structure

```yaml
smart:
  endpoint: https://smart.example.org/server
  login: smart-user
  password: smart-secret
  version: "7.5.7"
  use_language_code: en
  ca_uuids:
    - 00000000-0000-0000-0000-000000000000
  configurable_models_lists: {}
  provider_key: smart_connect

earthranger:
  id: my-tenant
  endpoint: https://your-tenant.pamdas.org/api/v1.0
  token: ""
  login: ""
  password: ""
  client_id: das_web_client
  event_type_version: v2          # v1 or v2; default v2
  choices_base_url: /api/v2.0/schemas
```

## Required vs optional

### `smart:` section

| Field | Required? | Notes |
|---|---|---|
| `endpoint` | when using `--smart-api` | Full SMART Connect server URL |
| `login` / `password` | when using `--smart-api` | SMART credentials |
| `version` | optional (defaults to `"7.0"`) | SMART Connect server version |
| `use_language_code` | optional (defaults to `en`) | Language for resolving display names |
| `ca_uuids` | required for `datamodel`/`events`/`patrols` from API | List of CA UUIDs to sync |
| `configurable_models_lists` | optional | Per-CA configurable-model overlay metadata |
| `provider_key` | optional (defaults to `smart_connect`) | Routes messages downstream |

### `earthranger:` section

| Field | Required? | Notes |
|---|---|---|
| `id` | required | Any string — used as a state-store key |
| `endpoint` | required | EarthRanger API root (typically `.../api/v1.0`) |
| `token` | one of token / login+password | Preferred for service accounts |
| `login` + `password` | one of token / login+password | Falls back to OAuth |
| `client_id` | optional (default `das_web_client`) | OAuth client for password auth |
| `event_type_version` | optional (default `v2`) | Which ER event-type API to use; see [Event-type version](../concepts/event-type-version.md) |
| `choices_base_url` | optional (default `/api/v2.0/schemas`) | Prefix for v2 `$ref` URLs |

## Environment variables

`--network-timeout` (the process-wide socket-timeout ceiling) can be set via the
`ER_SMART_SYNC_NETWORK_TIMEOUT` environment variable. Useful for CI/CD where
the flag can't easily be passed.

## CLI flags override the config file

Any CLI flag overrides the equivalent YAML field. So you can keep credentials
in `sync.yaml` and pass `--event-type-version v1` on the command line to
override for a specific run.

## Multiple environments

A common pattern: one YAML per environment.

```bash
er-smart-sync datamodel --config configs/staging.yaml ...
er-smart-sync datamodel --config configs/production.yaml ...
```

Keep credentials out of version control — `.env`-style files or your team's
secret manager work fine.

## Next

→ [First run](first-run.md) if you haven't done one yet
→ [Workflows](../workflows/push-datamodel.md) for full procedures
