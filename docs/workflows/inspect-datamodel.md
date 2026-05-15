# Inspect a data model

`inspect-datamodel` prints exactly what `datamodel` would create or update in
EarthRanger — without making any writes. Use it as a sanity check before
running a real sync, or to diff what's in SMART against what's in ER.

## When to use it

- Before running `datamodel` for the first time against a new CA.
- After a SMART data model change, to confirm only the expected event types
  will be affected.
- To learn what a configurable-model overlay does without committing it.
- To preview the v2 schema and `$ref` URLs before they hit ER's meta-schema.

## File-based or API-based?

=== "File-based"

    ```bash
    er-smart-sync inspect-datamodel \
      --from-file ~/datamodel.xml \
      --ca-identifier JKPERU
    ```

    `--ca-identifier` is required, just like with `datamodel`.

=== "API-based"

    ```bash
    er-smart-sync inspect-datamodel \
      --config sync.yaml \
      --smart-ca-uuid 0a1b2c3d-4e5f-6789-abcd-ef0123456789
    ```

    The identifier is extracted from the CA label.

## Reading v2 output

For v2 (the default), the output groups event types by category and shows
each event type's fields with their UI types and `$ref` URLs (for choice
fields):

```
CA: JKPERU
Event types: 18
  active:   18
  inactive: 0

- jkperu_incidents_caza_furtiva
    display: Caza furtiva
    fields:
      signo_de_caza: array (ui=CHOICE_LIST/DROPDOWN, enum=...)
      acciontomada: array (ui=CHOICE_LIST/DROPDOWN, enum=...)
      ...

Choice sets: 60
- field: et5e6b96f4_signo_de_caza
    options (4):
      - rastro: Rastro
      - trampa: Trampa
      - ...
```

For each choice-bearing field, you'll see the derived `Choice.field` name
(the `et<hash>_<attr>` part). This is what `datamodel` will POST as
EarthRanger `Choice` records.

## Reading v1 output

If you pass `--event-type-version v1`, the output is closer to v1's inline-
enum format:

```
CA: JKPERU
Event types: 18
  active:   18
  inactive: 0

- jkperu_incidents_caza_furtiva
    display: Caza furtiva
    fields:
      signo_de_caza: array (enum=['rastro', 'trampa', 'red', 'cazador'])
      ...
```

No separate Choice section — v1 embeds the options directly in the schema.

## Tips

- `inspect-datamodel` honors your config's `choices_base_url` so the
  previewed `$ref` URLs match what a real sync would emit.
- Configurable models are supported: `--cm-from-file path/to/cm.xml`.
- Output goes to stdout, so you can pipe it: `... | grep -A5 mineria`.

## Related

- [Push a data model](push-datamodel.md) — the real sync
- [`inspect-datamodel` CLI reference](../cli-reference/inspect-datamodel.md)
- [Event-type version](../concepts/event-type-version.md)
