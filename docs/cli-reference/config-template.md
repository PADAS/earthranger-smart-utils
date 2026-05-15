# `config-template`

Print a fully-commented YAML config template to stdout.

```bash
er-smart-sync config-template [OPTIONS]
```

## Options

| Flag | Default | Description |
|---|---|---|
| `--output FILE` | — | Write to FILE instead of stdout. |

## Invocations

**Pipe to a file:**

```bash
er-smart-sync config-template > sync.yaml
```

**Equivalent with `--output`:**

```bash
er-smart-sync config-template --output sync.yaml
```

## What's in the template

Every field that `er-smart-sync` reads from a YAML config, with inline
comments explaining when each is needed and what the default value is. Edit
the file in your editor and pass it via `--config sync.yaml` to other
subcommands.

See [Configuration](../getting-started/config.md) for a field-by-field
explanation.

## See also

- [Getting started: Configuration](../getting-started/config.md)
