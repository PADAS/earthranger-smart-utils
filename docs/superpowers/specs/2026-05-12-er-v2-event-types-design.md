# Design: EarthRanger v2 event types (rewrite)

**Status:** Draft — supersedes `archive/2026-05-12-er-v2-event-types-design-INCORRECT.md`
**Author:** Claude (with @chrisdo)
**Date:** 2026-05-12
**Replaces:** the previous spec of the same name (now in `archive/`), which prescribed a SMART → v2 mapping that ER's real meta-schema rejects at every property.

## Goal

Teach `er-smart-sync` to create EarthRanger **v2** event types that pass ER's actual v2 meta-schema validation, in addition to the v1 shape it produces today. v1 remains the default until v2 is verified end-to-end against a live tenant. After verification, v2 becomes the default.

## What went wrong the first time, and how this spec is grounded differently

The previous spec was written without reading `das/das/activity/schemas/eventtype_meta_schemas.py` (ER's authoritative v2 meta-schema) or any concrete v2 event-type example. It treated v2 as "JSON Schema 2020-12 with a `json`/`ui` envelope" and inferred property shapes from that abstraction. That gamble lost: ER's `main_event_type_schema` only accepts property shapes that match one of nine `$ref`'d field subschemas, none of which permits inline `enum`, all of which require a `deprecated` flag, and several of which require fields the previous spec omitted entirely (`unevaluatedProperties`, `headers`, `rightColumn`, `parent`).

**This spec is grounded in two sources of truth, not abstraction:**

1. **`das/das/activity/schemas/eventtype_meta_schemas.py`** — the validator ER actually runs (`Draft202012Validator(main_event_type_schema).validate(data)` at `das/das/activity/serializers/fields/json_schema.py:48`). Property-shape constraints in this spec come from there, citing line numbers.
2. **`das/das/activity/tests/schemas_v2/test_event_export.py:25-277`** (`CARCASS_V2_EVENTTYPE_SCHEMA`) — a known-passing v2 event-type from ER's own test suite. Used as the reference example for every shape.

Implementation must not deviate from these without re-reading them; the previous failure mode was reasoning by analogy. If a shape isn't documented in one of these two files, smoke-test it against a v2 tenant before merging.

## Background (corrected wire-format deltas, v1 → v2)

| Aspect | v1 | v2 |
|---|---|---|
| Endpoint | `/api/v1.0/activity/events/eventtypes/` | `/api/v2.0/activity/eventtypes/` |
| `schema` | stringified JSON | JSON **dict** with top-level `json` + `ui` |
| Schema spec | draft-04 + Jinja2 templates | **JSON Schema 2020-12**, validated against `main_event_type_schema` |
| `category` | UUID FK | category **value** (slug string) |
| PATCH identifier | `id` (UUID) | `value` (slug) |
| `readonly` | nested inside schema | top-level field |
| Choice values | inline Jinja template variables | **`anyOf: [{"$ref": ".../choices.json?field=<field_name>"}]`** — `$ref` only, no inline `enum` |
| Field UI types | n/a | `TEXT`, `NUMERIC`, `BOOLEAN`, `CHOICE_LIST`, `ATTACHMENT`, `DATE_TIME`, `LOCATION`, `COLLECTION` |
| `is_active=False` | soft-delete via DELETE | **no concept** — hard-delete via DELETE; do not POST inactive records |
| Per-property required keys | n/a | `deprecated` always required; `format`/`anyOf`/`items`/`uniqueItems` required for specific types |
| JSON envelope `additionalProperties: False` | n/a | replaced by `unevaluatedProperties: false` (different keyword) |
| UI block | n/a | requires `fields`, `headers`, `order`, `sections` (all four; `headers` may be `{}`) |
| Section block | n/a | requires `columns`, `isActive`, `leftColumn`, `rightColumn` (both columns required, even if empty) |
| Field UI block | n/a | every field UI block requires `parent` (= section id or parent field id) |

Categories are version-less (same `activity/events/categories/` endpoint), so no work there.

## Hard prerequisite: choices population

ER's v2 meta-schema (`eventtype_meta_schemas.py:96-152`, `:154-180`) accepts choices **only** as `anyOf: [{"$ref": "<uri-reference>"}, ...]`. There is no inline-enum escape hatch. SMART data models contain many choice attributes (LIST, MLIST, TREE — Sector, species, equipment lists), so without choices integration, most event types will fail.

The reference example uses `$ref` URIs of the form `/api/v2.0/schemas/choices.json?field=<field_name>`, pointing to ER's Choices API (`das/das/choices/urls.py`). Each SMART option set must be upserted as ER `Choice` records grouped by a stable `field` name before the event type that references them is POSTed.

**This makes choices-population a phase-1 prerequisite, not a follow-up.** The previous spec called it a "Related future work" follow-up; experience says it's blocking.

A separate sub-spec (`docs/superpowers/specs/<date>-er-v2-choices-population.md`, to be written) should cover:
- `field` name derivation from SMART attribute key + CA scoping (must be stable across re-syncs; unique within tenant)
- Upsert semantics: idempotent POST to `/choices/`, handle existing records, decide on inactive-option behavior (soft-delete? mark inactive in ER and keep referencing? Open question — see below)
- Ordering preservation (SMART option order → `ordernum`)
- TREE flattening (same rule as v1: emit leaves; parent/child relationships expressed via `sub_choice_of` if we want hierarchy, but flattening matches current v1 behavior)
- New `er-smart-sync` CLI surface (subcommand or inline step in `datamodel` flow)

Once that spec is written and the implementation lands, this spec's v2 builder can reference its output.

## Authoritative SMART → v2 mapping table

Every shape below is copied from `eventtype_meta_schemas.py` and cross-referenced against `CARCASS_V2_EVENTTYPE_SCHEMA` in `test_event_export.py`. **Required keys are non-negotiable**; missing any of them produces a 400.

### Scalar attributes

| SMART type | `json.properties[key]` | `ui.fields[key]` |
|---|---|---|
| TEXT | `{type:"string", title:<display>, description:"", deprecated:<bool>}` | `{type:"TEXT", inputType:"SHORT_TEXT", parent:"section-1"}` |
| NUMERIC | `{type:"number", title:<display>, description:"", deprecated:<bool>}` | `{type:"NUMERIC", parent:"section-1"}` |
| BOOLEAN | `{type:"boolean", title:<display>, description:"", deprecated:<bool>}` | `{type:"BOOLEAN", parent:"section-1"}` |
| DATE / TIME / DATETIME | `{type:"string", format:"date"\|"time"\|"date-time", title:<display>, description:"", deprecated:<bool>}` | `{type:"DATE_TIME", parent:"section-1"}` |
| ATTACHMENT | `{type:"string", format:"uri", title:<display>, deprecated:<bool>}` | `{type:"ATTACHMENT", allowableFileTypes:["audio","document","image","video"], parent:"section-1"}` |

Notes:
- `deprecated` is required by every field-JSON subschema (`eventtype_meta_schemas.py:43, 77, 297, 390, 422, 491`, etc.). Active SMART attributes get `deprecated: false`; inactive ones get `deprecated: true` and stay in the form (renders the value if pre-existing data exists; reduces destructive edits).
- TEXT and NUMERIC `description: ""` is present in the reference example but not strictly required by the meta-schema (`description: {"type":"string"}` is in the `properties` list but not in `required`). Emit it for consistency with the reference.

### Choice attributes (LIST / MLIST / TREE)

Both shapes require choices to exist in ER first. Let `field_name(ca_uuid, attribute_key)` be the stable identifier that survives re-syncs (derivation rule deferred to the choices spec — likely `{ca_identifier}_{attribute_key}` lowercased, sanitized).

**LIST (single) and TREE-leaves:**

```python
json_prop = {
    "type": "string",
    "title": display,
    "description": "",
    "deprecated": is_inactive,
    "anyOf": [{"$ref": f"{base_schemas_url}/choices.json?field={field_name}"}],
}
ui_field = {
    "type": "CHOICE_LIST",
    "inputType": "DROPDOWN",
    "placeholder": "",
    "choices": {
        "type": "EXISTING_CHOICE_LIST",
        "existingChoiceList": [field_name],
        "eventTypeCategories": [],
        "featureCategories": [],
        "myDataType": "",  # required-by-meta-schema? See open Q below.
        "subjectGroups": [],
        "subjectSubtypes": [],
    },
    "parent": "section-1",
}
```

**MLIST (multi-LIST):**

```python
json_prop = {
    "type": "array",
    "title": display,
    "description": "",
    "deprecated": is_inactive,
    "uniqueItems": True,
    "items": {
        "type": "string",
        "anyOf": [{"$ref": f"{base_schemas_url}/choices.json?field={field_name}"}],
    },
}
ui_field = {
    "type": "CHOICE_LIST",
    "inputType": "DROPDOWN",  # or "LIST" — meta-schema accepts both
    "placeholder": "",
    "choices": { … same shape as single … },
    "parent": "section-1",
}
```

Constraints from `eventtype_meta_schemas.py:135-180`:
- single CHOICE_LIST required keys: `anyOf`, `deprecated`, `title`, `type`
- multi CHOICE_LIST required keys: `deprecated`, `items`, `title`, `type`, `uniqueItems`; `uniqueItems` must be the constant `true`.

### Top-level `json` envelope

```python
{
    "$schema": "https://json-schema.org/draft/2020-12/schema",  # required, exact const
    "type": "object",                                            # required, exact const
    "unevaluatedProperties": False,                              # required, exact const False
    "properties": { … },
    "required": [],                                              # required key, may be empty list
}
```

(`eventtype_meta_schemas.py:1438-1540`. Note: `additionalProperties: false` is wrong; the meta-schema uses `unevaluatedProperties`.)

### Top-level `ui` envelope

```python
{
    "fields": { … per-field UI blocks … },
    "headers": {},          # required to be present; may be empty
    "order": ["section-1"], # required; lists section ids in render order
    "sections": {
        "section-1": {
            "columns": 1,
            "isActive": True,
            "label": "Details",  # optional
            "leftColumn": [{"name": k, "type": "field"} for k in field_order],
            "rightColumn": [],    # required; may be empty
        },
    },
}
```

(`eventtype_meta_schemas.py:1389-1432` for `ui`, `:1359-1384` for section.)

### Inactive event types

In v2, `is_active=False` is not a valid POST shape — there is no record without a schema. Per the wire-format-deltas table above and the previous spec's section on "v2 hard-delete":

- **Active SMART category, leaf or has CM context:** emit a v2 event type as above.
- **Inactive SMART category (no CM context):** **skip entirely**. Do not POST a schemaless record; do not POST `is_active=False`. The v1 builder soft-deletes; v2 has no analogue, so the right thing is to leave any pre-existing v2 record untouched.
- **Currently-existing v2 record whose corresponding SMART category went inactive:** out of scope for this spec; needs a separate "deletion / deprecation" decision (see open questions).

## Architecture

Module-level changes are scoped to the existing files. The previous spec's module split (new `smart_to_er_v2.py`, builder selection in `synchronizer.py`, `version=` plumbing through `erclient`) is the right shape and stays. **The current `smart_to_er_v2.py` is salvageable as scaffolding but every property-emission function needs rewriting.** Specifically:

- `_build_property_pair` ([smart_to_er_v2.py:223-276]) — rewrite all branches:
  - Add `deprecated: <bool>` and `description: ""` to every JSON property.
  - Replace single-CHOICE_LIST inline `enum` with `anyOf: [{$ref: ...}]`.
  - Replace multi-CHOICE_LIST `items: {enum:...}` with `items: {type:"string", anyOf:[{$ref:...}]}`; add `uniqueItems: True`; add outer `deprecated`.
  - Replace `{type: "NUMBER"}` UI with `{type: "NUMERIC"}`.
  - Replace `{type: "TEXT", inputType: "DATE"\|"TIME"\|"DATETIME"}` UI with `{type: "DATE_TIME"}`.
  - Add `parent: "section-1"` to every UI field.
  - For CHOICE_LIST UI, emit the full `choices` block referencing `existingChoiceList: [field_name]`.
- `_build_one` ([smart_to_er_v2.py:101-171]):
  - Top-level: replace `additionalProperties: False` with `unevaluatedProperties: False`.
  - `ui`: add `headers: {}` and `rightColumn: []` to the section.
  - Inactive case: change `if not is_active: return et` (returning a schemaless record) to `if not is_active: return None` (skip entirely on v2).
- `SCALAR_JSON` / `SCALAR_UI` constants ([smart_to_er_v2.py:45-66]) — regenerate per the table above. `SCALAR_UI` entries all need `parent`; the JSON entries all need `deprecated` and (for TEXT/NUMERIC) `description`.
- The `value`-slug pattern in `_build_one` ([smart_to_er_v2.py:113-121]) — verify it matches `FIELD_NAME_PATTERN = ^[a-zA-Z0-9_-]+$` for SMART hkey paths. Underscored slugs should pass; dotted paths (TREE leaves) only appear inside choice values, not as event-type `value`, so this is probably fine. **Sanity-check during implementation.**

The `version=` plumbing through `synchronizer.py` and `erclient` is already correct and does not need to change. The bug was entirely in the schema produced, not the transport.

The CLI flag and config-key behavior also doesn't need to change structurally — `--event-type-version v2` and `event_type_version: v2` already wire through. Only the default flip (back to v2) is reverted by this spec, gated on verification.

## Phased rollout

1. **Phase 0 (done):** revert v2-default to v1; mark v2 builder as experimental; archive the incorrect spec/plan.
2. **Phase 1 (blocking):** write and implement the choices-population sub-spec. Without it, no LIST/MLIST/TREE attribute can be synced under v2.
3. **Phase 2:** rewrite `smart_to_er_v2.py` per this spec. Re-run unit tests; update tests to match new shapes. The reference test in `das/das/activity/tests/schemas_v2/test_event_export.py:25-277` should serve as the golden snapshot for at least one fixture.
4. **Phase 3:** smoke-test against a real v2 tenant (`gundi-dev.staging.pamdas.org` is the one used in the failing run that motivated this rewrite). Confirm a CA with a mix of scalar and choice attributes syncs cleanly.
5. **Phase 4:** flip default back to `v2`, update USAGE.md and CLI help to remove "experimental" warnings.

Each phase produces a separately-shippable PR. Phase 1 is the longest and most likely to surface further open questions.

## Open questions

1. **`choices.myDataType` required?** The meta-schema (`eventtype_meta_schemas.py:194-203`) lists `myDataType` as an enum (including empty string) but the `required` list at `:209` is only `["type"]`. The reference example always includes `myDataType` (`""` or one of the enum values). Safe default: emit `myDataType: ""` everywhere for `EXISTING_CHOICE_LIST`. Confirm during Phase 3.
2. **`description` required?** Per the meta-schema it isn't, but every reference example emits it as `""`. Decision: emit `description: ""` everywhere for byte-for-byte parity with reference output.
3. **TREE attribute hierarchy.** SMART TREE option sets have parent/child relationships. v1 flattens to leaves. ER's `Choice` model supports `sub_choice_of` for hierarchy. Open: do we flatten (matches v1, simpler) or preserve hierarchy (richer, but new behavior)? Recommend flatten for parity in Phase 1, revisit in a later spec.
4. **Stale v2 records when SMART category goes inactive.** This spec skips inactive SMART categories on POST. What about existing v2 records whose source category later goes inactive? Options: (a) leave alone, (b) mark `is_active=False` if v2 supports it after all, (c) DELETE if unused. Defer to a separate decision; safe default is (a).
5. **Duplicate-value collisions with pre-existing v1 records.** Carried over from the previous spec, still real. The current behavior (log-and-skip on v2 POST conflict, recommend migrate endpoint) is correct. Keep it.
6. **Value-slug character set.** SMART hkey-paths split on `.` and rejoin with `_` for the `value`. Verify all resulting slugs match `FIELD_NAME_PATTERN`. If any SMART attribute keys contain characters outside `[a-zA-Z0-9_-]` (e.g. `.`, accents), sanitize during slugification.

## Testing

- **Snapshot test:** ship a SMART data-model fixture that covers every attribute type (TEXT, NUMERIC, BOOLEAN, DATE/TIME/DATETIME, ATTACHMENT, LIST, MLIST, TREE) and at least one inactive attribute. Snapshot the v2 builder's output and diff against a hand-written expected dict. The expected dict should be visually similar to `CARCASS_V2_EVENTTYPE_SCHEMA` from `test_event_export.py` (re-creating the same shape from a different input data model).
- **Meta-schema validation in test:** import `main_event_type_schema` from the local `das` clone (or vendor a copy with attribution) and validate every builder output against it as part of `tests/test_smart_to_er_v2.py`. Catches regressions of the kind that caused the previous spec to fail in production.
- **Inactive-category skip:** assert the builder returns no event type when `cat.is_active=False` and `cm is None`.
- **Choices integration test (Phase 1 deliverable):** end-to-end test that POSTs choices, then POSTs an event type that references them via `$ref`, against a fixture/mocked ER endpoint.
- **Smoke test (Phase 3 manual):** documented script against `gundi-dev.staging.pamdas.org`. Add the runbook to the docs alongside this spec.

## Rollout-related code already in place

The Phase 0 revert flipped:
- `src/er_smart_sync/config.py:56` default to `"v1"`
- `src/er_smart_sync/cli.py` help text on `--event-type-version` (datamodel + inspect-datamodel)
- `src/er_smart_sync/cli.py` `config-template` YAML default
- `src/er_smart_sync/synchronizer.py` startup warning when `v2` is selected
- `USAGE.md` "Event type version" section
- `tests/test_config.py`, `tests/test_cli.py` — renamed `*_defaults_to_v2` → `*_defaults_to_v1`

The current v2 builder (`smart_to_er_v2.py`), all its tests (`tests/test_smart_to_er_v2.py`, `tests/test_smart_to_er_v2_*.py`), and the related synchronizer wiring all still exist and pass their unit tests. They just produce output that doesn't pass ER's actual meta-schema. Phase 2 replaces the builder body; Phase 1 deliverables are net-new.
