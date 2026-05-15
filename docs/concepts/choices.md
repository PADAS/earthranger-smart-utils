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

Matching v1 behavior. ER's `Choice` model supports `sub_choice_of` for
hierarchical relations, but using it would change downstream behavior. Out
of scope for now.

## Related

- [Workflow: Populate choices](../workflows/populate-choices.md)
- [Concept: Event-type version](event-type-version.md)
- [`choices` CLI reference](../cli-reference/choices.md)
