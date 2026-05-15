# Populate choices

The `choices` subcommand upserts SMART option sets as EarthRanger `Choice`
records. **You don't usually run this directly** — `datamodel` runs it
inline before pushing event types. This page covers when and why you might
run it standalone.

## Why choices?

EarthRanger's v2 event-type schemas don't embed dropdown options inline.
Instead, every choice attribute references a separate `Choice` record set
via a `$ref` URL:

```json
"sector": {
  "type": "array",
  "items": {
    "type": "string",
    "anyOf": [
      {"$ref": "/api/v2.0/schemas/choices.json?field=et5e6b96f4_sector"}
    ]
  }
}
```

For that `$ref` to resolve, the `Choice` records with `field=et5e6b96f4_sector`
must exist in EarthRanger first. The `choices` subcommand creates them.

See [ER Choice records](../concepts/choices.md) for the full background on
how field names are derived and what gets stored.

## Inline vs standalone

=== "Inline (default for v2)"

    Run `datamodel --event-type-version v2 ...` and the choices phase runs
    automatically before event types are POSTed:

    ```bash
    er-smart-sync datamodel \
      --config sync.yaml \
      --from-file ~/datamodel.xml \
      --ca-identifier JKPERU
    ```

    The summary line shows both phases:

    ```
    choices_created: 929
    choices_updated: 0
    choices_unchanged: 0
    ...
    event_types_created: 18
    ```

=== "Standalone"

    Run `choices` alone, without touching event types:

    ```bash
    er-smart-sync choices \
      --config sync.yaml \
      --from-file ~/datamodel.xml \
      --cm-from-file ~/datamodel.cm.xml
    ```

    Useful for:

    - Pre-warming a new EarthRanger tenant before doing a `datamodel` push.
    - Re-syncing only the choices after a configurable-model change, without
      re-PATCHing every event type.
    - Diagnosing choice-specific issues in isolation.

## --skip-choices

If you've already run `choices` separately and want to push event types
without re-running the choices phase, use `--skip-choices`:

```bash
er-smart-sync datamodel \
  --config sync.yaml \
  --from-file ~/datamodel.xml \
  --ca-identifier JKPERU \
  --skip-choices
```

This is rare — the choices phase is fast on re-runs (only the GETs run, no
writes) — but useful in long-running scripts where you want to manage the
phases independently.

## Aborting on choice errors

If any Choice upsert errors out, the synchronizer **aborts the event-type
phase for that conservation area**. The reasoning: pushing event types whose
`$ref` URLs resolve to missing/incomplete Choice records produces broken
dropdowns in EarthRanger, which is worse than skipping the push and surfacing
the error.

You'll see a warning in the log:

```
WARNING: Aborting event-type push for CA <uuid>: <N> choice operations failed.
Investigate the choice errors above before re-running.
```

Fix the choice errors (typically network-related; see
[Troubleshooting](../troubleshooting.md)) and re-run. The choices phase is
idempotent — already-correct records are no-op.

## Reading the choices summary

```
Choices done: created=929 updated=1 unchanged=0 deactivated=0 errored=0
```

- **`created`** — new Choice record in EarthRanger.
- **`updated`** — Choice existed; we PATCHed `display` or `ordernum` to match
  current SMART state.
- **`unchanged`** — Choice matched SMART; no-op.
- **`deactivated`** — Choice existed and was active in ER, but the
  configurable-model now marks it inactive (or it was removed from the SMART
  data model). We soft-deleted it via PATCH.
- **`errored`** — POST/PATCH failed; check the log above for the specific
  field/value.

## After the upsert

In EarthRanger's admin UI:

![ER event type with populated choices](../images/er-event-type-with-choices.png)
*An event type's detail view shows each CHOICE_LIST attribute resolving to
its underlying Choice records via `$ref`.*

!!! note "Screenshot placeholder"
    Replace with a screenshot of an EarthRanger event-type detail view that
    has at least one populated CHOICE_LIST dropdown. See
    `docs/images/README.md`.

## Related

- [Push a data model](push-datamodel.md) — the parent workflow that runs
  choices inline
- [ER Choice records](../concepts/choices.md) — concepts and field-name
  derivation
- [`choices` CLI reference](../cli-reference/choices.md) — flag-by-flag
