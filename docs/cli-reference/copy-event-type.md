# `copy-event-type`

Copy one event type from a source EarthRanger site to a destination site.
For v2 event types, the choice option-sets the schema references are copied
too. The target event category must already exist on the destination.

```bash
er-smart-sync copy-event-type \
  --source-endpoint source-site.pamdas.org --source-token SOURCE-TOKEN \
  --dest-endpoint   dest-site.pamdas.org   --dest-token   DEST-TOKEN \
  --event-type-value      jkperu_incidents_caza_furtiva \
  --target-event-category monitoring \
  --version v2
```

## Options

| Option | Required | What it does |
|---|---|---|
| `--source-endpoint` | yes | Source ER site — bare domain, scheme + host, or full `/api/v1.0` root |
| `--source-token` / `--source-username` + `--source-password` | one of | Source auth (token preferred) |
| `--dest-endpoint` | yes | Destination ER site — same accepted forms |
| `--dest-token` / `--dest-username` + `--dest-password` | one of | Destination auth |
| `--event-type-value` | yes | `value` of the event type to copy from the source |
| `--target-event-category` | yes | `value` of the destination event category to attach the copy to (must already exist) |
| `--version v1\|v2` | no | Event-type API version used on both sites. Default `v2` |

## Behavior

- **v2**: reads the source event type's schema, extracts every
  `choices.json?field=...` `$ref`, copies those Choice records to the
  destination, then creates the event type attached to
  `--target-event-category`.
- **v1**: copies the event type with its inline schema as-is.
- Honors the global `--dry-run` flag — reads from the source, logs the
  writes it would send to the destination.

## Failure modes

| Error | Meaning |
|---|---|
| `EventTypeNotFound` | No event type with `--event-type-value` on the source |
| `TargetCategoryMissing` | `--target-event-category` doesn't exist on the destination — create it there first |
