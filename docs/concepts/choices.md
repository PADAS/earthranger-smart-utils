# ER Choice records

EarthRanger's `Choice` records hold the option values for dropdown fields
(LIST, MLIST, TREE in SMART; CHOICE_LIST in ER). In v2 event types, these
records live separately from the event-type schema and are referenced via
`$ref` URLs.

This page explains how `er-smart-sync` upserts those records, how the field
names are derived, and what to do when things go wrong.

## What a Choice record looks like

In EarthRanger's database (and the `/api/v1.0/choices/` REST endpoint),
each option is a row:

```json
{
  "id": "<uuid>",
  "model": "activity.event",
  "field": "et5e6b96f4_sector",
  "value": "jk001",
  "display": "Sector JK001",
  "ordernum": 0,
  "is_active": true
}
```

- **`model`** — what kind of object this choice belongs to. We always set
  `"activity.event"`.
- **`field`** — the dropdown identifier. All options for one dropdown share
  the same `field` value. This is what event-type schemas reference via
  `$ref`.
- **`value`** — the underlying SMART option key, sanitized to `^\w+$`.
- **`display`** — the human-visible label.
- **`ordernum`** — preserves the SMART option order.
- **`is_active`** — soft-delete flag. Inactive choices stay in the DB so
  historical events can still resolve.

ER enforces a unique constraint on `(tenant, model, field, value)` —
duplicate options for the same dropdown collide.

## How `field` names are derived

For every event type with a choice attribute, we derive a stable `field`
name:

```
et{8-hex}_{attr_key}
```

The 8 hex chars are `sha256(event_type_value)[:8]`. The attribute key is
sanitized to `^\w+$`. The total is capped at 40 characters (ER's column
limit).

Example: for event type `jkperu_incidents_caza_furtiva` and attribute
`signo_de_caza`, the field name is `et5e6b96f4_signo_de_caza`.

The hash makes the name unique across all event types in a tenant, without
needing a registry. The same SMART attribute referenced from two different
event types gets two different field names — they're isolated by design.

## How `value` is derived

A choice's `value` is what ER stores on every event record that selects
this option. It must be stable across syncs (changing it orphans historical
events) and match `^\w+$` (ER's column constraint).

The mapping happens in two stages:

1. **Sanitize.** Take the SMART option `key` and replace any run of
   non-alphanumeric characters with a single `_`, strip leading/trailing
   `_`, lowercase. Empty results fall back to `"_"`.

    | SMART option `key`     | Sanitized value         |
    |------------------------|-------------------------|
    | `lion`                 | `lion`                  |
    | `Côte d'Ivoire`        | `c_te_d_ivoire`         |
    | `africa.kenya.nairobi` | `africa_kenya_nairobi`  |
    | `  trim me  `          | `trim_me`               |

2. **Cap at 100 chars.** ER's column is `varchar(100)`. If the sanitized
   value exceeds that, replace the tail with a deterministic hash suffix
   over the **sanitized** string (not the raw SMART key):

    ```python
    sanitized[:91] + "_" + hashlib.sha256(sanitized.encode("utf-8")).hexdigest()[:8]
    ```

    Total length is exactly 100. The 91-char readable prefix aids
    debugging; the 8-hex tail (~4 billion buckets) makes collisions
    vanishingly rare. Same input always produces the same output, so
    re-runs find the same record.

    The shortening only fires for deeply-nested SMART **TREE** leaves —
    LIST/MLIST option keys are typically short. A log line at DEBUG level
    is emitted for each shortened value (re-run with `-v` to see them); a
    deep TREE can produce many shortenings per sync, so the default
    output stays quiet.

!!! warning "Historical events keep the old value"
    If a previously-synced choice had a long unhashed value (from a
    version of the tool that pre-dates the cap), the new sync will use
    the hashed form going forward. Events stored under the old value
    keep referencing it — they're not rewritten. This is bounded
    (only affects choices that exceeded 100 chars *and* had existing
    event data).

## How `display` is derived

The `display` is the human-visible label and is **passed through directly
from SMART** — no sanitization. SMART's API resolves it to the configured
language (`--smart-language`), so the same attribute can have different
displays per sync if you change languages.

ER caps `display` at `varchar(100)` too. Long displays are shortened with
a structural strategy that aims to preserve meaning:

1. **Common case (≤ 100 chars):** keep unchanged.
2. **Dotted fallback case:** SMART's data-model parser falls back to using
   the dotted TREE path as the display when a leaf node has no `<names>`
   element (see `smartconnect.models.generate_tree_children`). When this
   happens and the path exceeds 100 chars, we keep only the last dotted
   segment (the leaf's own identifier), since that's the meaningful part.
   If the leaf segment is itself > 100 chars, the next-tier truncation
   (word-boundary / hard-cut) operates on the leaf, not on the full path
   — keeping the focus on the leaf identifier.

    Input (135 chars):

    ```
    africa.kenya.nairobi.westlands.specific_neighborhood_with_a_very_long_descriptive_identifier_padding_to_exceed_one_hundred
    ```

    Output:

    ```
    specific_neighborhood_with_a_very_long_descriptive_identifier_padding_to_exceed_one_hundred
    ```

3. **Long natural-language label:** word-boundary truncate at the last
   whitespace before char 99, append `…`.

    Input (130 chars):

    ```
    African Lion - Panthera leo - one of the four big cats found across sub-Saharan Africa including Kenya and Tanzania
    ```

    Output (≤ 100 chars):

    ```
    African Lion - Panthera leo - one of the four big cats found across sub-Saharan Africa…
    ```

4. **Pathological no-whitespace string:** hard-cut at 99 + `…`.

Each shortening logs at DEBUG level naming the strategy used (re-run with
`-v` to see them).

## CM overlay rules

If you sync with a Configurable Model (`--cm-from-file` or
`--smart-cm-uuid`), the CM acts as an overlay on the base data model:

| CM action on an option | Effect on the resulting Choice |
|---|---|
| Lists it with `isActive=true`  | `is_active=true`, included in the plan |
| Lists it with `isActive=false` | `is_active=false`, still included |
| Omits it entirely              | Dropped from the plan; if it exists in ER, the upsert phase soft-deactivates it (`is_active=false`) |
| Lists it but changes its key   | Still keyed by the *original* SMART key — the CM cannot rename options, only filter and reorder |

CM order wins. The `ordernum` field on each Choice record reflects the
CM's listing order, not the base DM's.

## The `$ref` URL

In a v2 event-type schema, choice fields look like:

```json
"signo_de_caza": {
  "type": "array",
  "uniqueItems": true,
  "items": {
    "type": "string",
    "anyOf": [
      {"$ref": "/api/v2.0/schemas/choices.json?field=et5e6b96f4_signo_de_caza"}
    ]
  }
}
```

EarthRanger's `choices.json` endpoint resolves this `$ref` to the active
Choice records with that `field` name, returning their `value` and
`display` for the dropdown.

If the referenced Choice records don't exist yet (because the choices phase
hasn't run, or was skipped), the dropdown renders empty.

## Upsert decision matrix

When `er-smart-sync choices` runs, for each option in each Choice set, it
decides one of these outcomes by comparing the SMART data against what ER
already has:

| ER state | SMART state | Outcome | Counter |
|---|---|---|---|
| Doesn't exist | Active | POST a new record | `created` |
| Exists, active, matches | Active, same display/ordernum | No-op | `unchanged` |
| Exists, active, drifted | Active, different display/ordernum | PATCH the diff | `updated` |
| Exists, inactive | Active | PATCH `is_active=True` (re-activate) | `updated` |
| Exists, active | Inactive (CM removed it) | PATCH `is_active=False` (soft-delete) | `deactivated` |
| Exists, but not in plan | (absent) | PATCH `is_active=False` (orphan) | `deactivated` |
| (any) | (any) | Network/server error | `errored` |

After processing all Choice sets, you see the summary:

```
Choices done: created=929 updated=1 unchanged=0 deactivated=0 errored=0
```

## Why the choices phase exists at all

v1 EarthRanger embedded dropdown options directly in the event-type schema
(`enum: ["jk001", "jk002", ...]`). v2 doesn't allow this — its meta-schema
rejects inline `enum` in choice fields, requiring `anyOf: [{$ref: ...}]`
instead.

So `er-smart-sync` had to add a layer that pre-creates the referenced
records. Without it, every v2 event-type POST would fail with
`Invalid JSON Schema: ... is not valid under any of the given schemas`.

## TREE handling

SMART TREE attributes have parent/child option hierarchies (e.g. `africa →
africa.kenya → africa.kenya.nairobi`). `er-smart-sync` **flattens to leaves**
— only the deepest options become Choice records.

Two practical consequences for TREE attributes:

- The flattened leaf's SMART `key` is the full dotted path
  (`africa.kenya.nairobi`), which becomes the basis for the choice
  [`value`](#how-value-is-derived). Deep trees can push the sanitized
  value past 100 chars, triggering the hash-suffix shortening.
- A leaf node with no `<names>` element falls back to using the dotted
  path itself as its `display`, which is what the
  [`display` shortening](#how-display-is-derived) handles via the
  last-segment rule.

Matching v1 behavior. ER's `Choice` model supports `sub_choice_of` for
hierarchical relations, but using it would change downstream behavior. Out
of scope for now.

## Related

- [Workflow: Populate choices](../workflows/populate-choices.md)
- [Concept: Event-type version](event-type-version.md)
- [`choices` CLI reference](../cli-reference/choices.md)
