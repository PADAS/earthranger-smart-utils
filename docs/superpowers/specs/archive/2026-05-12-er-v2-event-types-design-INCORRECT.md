# Design: EarthRanger v2 event types

> **⚠ ARCHIVED — INCORRECT. Do not implement from this document.**
>
> This spec was approved but never grounded in ER's actual v2 meta-schema
> (`das/das/activity/schemas/eventtype_meta_schemas.py`). Smoke-testing
> against a v2 tenant (line ~224, "Open questions / risks") was deferred
> and never happened. The SMART → v2 schema mapping table at lines 107–118
> is wrong on every row — every property shape this spec prescribes is
> rejected by ER's `main_event_type_schema` validator with
> `400 Invalid JSON Schema`.
>
> Specific deltas vs. the real meta-schema, for the record:
> - Every field-type subschema requires `deprecated: <bool>` (this spec omits it).
> - Single CHOICE_LIST must use `anyOf: [{"$ref": ...}, ...]`; the spec's inline `enum` is rejected.
> - Multi CHOICE_LIST must use `items: {type:"string", anyOf:[...]}` plus `uniqueItems: true`; the spec's `items: {type:"string", enum:[...]}` is rejected.
> - Top-level `json` envelope requires `unevaluatedProperties: false`, not `additionalProperties: false`.
> - `ui` requires a `headers` key (even if empty); the spec omits it.
> - Sections require `rightColumn`; the spec only emits `leftColumn`.
> - UI field `type` enums are different: ER uses `NUMERIC` (not `NUMBER`) and `DATE_TIME` (not `TEXT`+`inputType:DATE`).
> - All UI field schemas require `parent`; the spec omits it.
> - v2 has no `is_active=False` concept (hard-delete via DELETE), so inactive event types should be skipped entirely, not POSTed with no schema.
>
> A new spec, grounded in the actual meta-schema, lives at
> `docs/superpowers/specs/2026-05-12-er-v2-event-types-design.md` (the
> file replacing this one — note the rename to the archive directory).
> The choices-API follow-up that was deferred is now a hard prerequisite,
> not optional: there is no inline-enum escape hatch in v2.
>
> Original status/author/date below preserved for history.

**Status:** Approved (design phase) — **INVALIDATED 2026-05-12**
**Author:** Claude (with @chrisdo)
**Date:** 2026-05-12

## Goal

Teach `er-smart-sync` to create EarthRanger **v2** event types (in addition to the v1 shape it produces today) when pushing a SMART data model. Default new sync runs to v2; keep v1 reachable via flag.

## Background

The EarthRanger backend (`das` repo) ships two coexisting event-type APIs. v2 is production-ready, has its own URL prefix, a different wire format, and a richer schema spec. The two share one DB table and one tenant-wide unique constraint on `value` (across both versions). `earthranger-client` (the HTTP client this repo uses) already supports v2 via a `version=` kwarg on every event-type method — no client-library work is required.

### v1 → v2 wire-format deltas

| Aspect | v1 | v2 |
|---|---|---|
| Endpoint | `/api/v1.0/activity/events/eventtypes/` | `/api/v2.0/activity/eventtypes/` |
| `schema` | stringified JSON | JSON **dict** |
| Schema spec | draft-04 + Jinja2 templates | **JSON Schema 2020-12**, with two top-level keys: `json` + `ui` |
| `category` | UUID FK | category **value** (slug string) |
| PATCH identifier | `id` (UUID) | `value` (slug) |
| `readonly` | nested inside schema | top-level field |
| Extra fields | — | `ordernum` |
| Choice enums | Jinja2 template variables | `$ref` to choices.json **or** inline `enum` |
| Field UI types | n/a | `TEXT`, `NUMBER`, `BOOLEAN`, `CHOICE_LIST`, `ATTACHMENT`, `LOCATION`, `COLLECTION` |
| `is_active=False` | soft-delete via DELETE | hard-delete (409 if in use) |

Categories are version-less (same `activity/events/categories/` endpoint), so no work there.

## Non-goals

The following are deliberately out of scope and either deferred to follow-up specs or left to a future revision:

- **Populating ER's tenant-managed Choices via the choices API**, then emitting `$ref` URLs from event schemas. Tracked separately. For this spec we inline `enum` directly in each property.
- **Server-side `POST /api/v2.0/activity/eventtypes/migrate/`** orchestration to bulk-convert previously-pushed v1 event types into v2. Useful but separate.
- **Auto-detection** of the ER tenant's v2 readiness. User picks via flag/config.
- **Multi-column or multi-section UI layouts.** We emit a single default section listing all fields in schema order. Users can re-layout in ER's Form Designer post-import.
- **`ordernum` population.** Left unset; ER falls back to ordering by `value`.

## Approach

Two parallel builders, version selected at the boundary. Keep `build_event_types()` (v1) untouched; add `build_event_types_v2()` that emits the v2 schema shape. Thread a `version` parameter through the synchronizer to `erclient`. CLI flag `--event-type-version v1|v2` (default `v2`). Config key `er.event_type_version` (default `v2`).

Considered alternatives:

- *Single intermediate model + version-specific serializers.* Rejected: v1's `schema: str` and v2's `{json, ui}` are structurally different enough that the intermediate model would skew toward one or the other, costing more code, not less.
- *Drop v1 entirely.* Rejected: not what was asked for; we want a soft migration.

## Architecture

All changes confined to existing modules + one new builder file + one new test file. Public CLI surface gains one flag.

### Module-level changes

**`src/er_smart_sync/config.py`** (Pydantic v1)
- Add to `EarthRangerConfig`: `event_type_version: Literal["v1", "v2"] = "v2"`.
- Validator: lowercase the value, accept aliases `"v1.0"` → `"v1"` and `"v2.0"` → `"v2"`.

**`src/er_smart_sync/cli.py`**
- Global `--event-type-version {v1,v2}` flag on `datamodel` and `inspect-datamodel`. When provided, overrides config.
- `config-template` YAML: include the `event_type_version: v2` line, commented with `# v1 or v2; default v2`.
- USAGE.md: new "Event type version" subsection in the `datamodel` reference.

**`src/er_smart_sync/smart_to_er.py`** — unchanged. Still owns v1.

**`src/er_smart_sync/smart_to_er_v2.py` (new)**
- `build_event_types_v2(*, dm, cm=None, ca_uuid, ca_identifier) -> list[ERV2EventType]`. Same signature as `build_event_types()`.
- New Pydantic v1 model `ERV2EventType` lives in this module:
  ```python
  class ERV2EventType(BaseModel):
      value: str
      display: str
      category: str           # category value (slug), not UUID
      is_active: bool = True
      readonly: bool = False
      schema_: dict | None = Field(None, alias="schema")  # dict, not string

      class Config:
          allow_population_by_field_name = True
  ```
- The legacy `EREventType` from `smartconnect.er_sync_utils` stays in use for v1.

**`src/er_smart_sync/synchronizer.py`**
- `_event_type_version`: read from `EarthRangerConfig`, stash on `self`.
- All five erclient event-type calls get `version=self._event_type_version` passed through:
  - `er_client.get_event_types(...)`
  - `er_client.get_event_type(...)` (used by `_find_existing_event_type`)
  - `er_client.post_event_type(...)`
  - `er_client.patch_event_type(...)`
- `synchronize_datamodel()` branches once on version to pick the right builder.
- `_event_type_needs_update()`: handle both shapes:
  - v1: compare stringified schemas via `er_event_type_schemas_equal` (current behavior).
  - v2: compare schema dicts directly (`==`, after sorting keys recursively — or `json.dumps(sort_keys=True)` on both sides).
- `_create_event_type` / `_update_event_type`:
  - For v2: payload uses `category: <value-slug>`, `schema: <dict>`, `readonly: <bool>`.
  - The `dict(by_alias=True, exclude_none=True)` call on `ERV2EventType` produces the right shape because of the `schema` alias.

**`src/er_smart_sync/defaults.py`**
- `DryRunERClient.{post,patch}_event_type`: accept and record `version=` kwarg in `self.calls`.

### SMART → v2 schema mapping

For each SMART attribute we emit (a) a property in the v2 `json.properties` block and (b) a field entry in the v2 `ui.fields` block.

| SMART type | v2 `json.properties[key]` | v2 `ui.fields[key]` |
|---|---|---|
| TEXT | `{type: "string", title: <display>}` | `{type: "TEXT", inputType: "SHORT_TEXT"}` |
| NUMERIC | `{type: "number", title: <display>}` | `{type: "NUMBER"}` |
| BOOLEAN | `{type: "boolean", title: <display>}` | `{type: "BOOLEAN"}` |
| DATE | `{type: "string", format: "date", title: <display>}` | `{type: "TEXT", inputType: "DATE"}` |
| TIME | `{type: "string", format: "time", title: <display>}` | `{type: "TEXT", inputType: "TIME"}` |
| DATETIME | `{type: "string", format: "date-time", title: <display>}` | `{type: "TEXT", inputType: "DATETIME"}` |
| ATTACHMENT | `{type: "string", format: "uri", title: <display>}` | `{type: "ATTACHMENT", allowableFileTypes: ["image","document","video","audio"]}` |
| LIST (single) | `{type: "string", enum: [...keys], title: <display>}` | `{type: "CHOICE_LIST", inputType: "DROPDOWN", choices: {<key>: <display>, ...}}` |
| LIST (multi) / MLIST | `{type: "array", items: {type: "string", enum: [...keys]}, title: <display>}` | `{type: "CHOICE_LIST", inputType: "CHECKBOX", choices: {...}}` |
| TREE | leaf-flatten: `{type: "string", enum: [...leaf_keys], title: <display>}` | `{type: "CHOICE_LIST", inputType: "DROPDOWN", choices: {...}}` |

Notes on the mapping:

- `enumNames` (used in v1 for display labels) is not v2-canonical. v2's `ui.fields[key].choices` carries the key→display mapping; the `json.properties[key].enum` carries the keys only.
- Inactive attributes (`cat_attr.is_active == False`) get `deprecated: true` in `json.properties` **and remain present in `ui.sections`** so the form still renders them. JSON Schema 2020-12's `deprecated` flag is the standards-compliant marker; the ER UI may or may not visually distinguish deprecated fields, but the metadata is preserved either way.
- The TREE leaf-flatten rule mirrors what `smart_to_er.py` already does for v1 (`_leaf_options`).

### v2 schema envelope

The full schema dict posted to ER looks like:

```json
{
  "json": {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "additionalProperties": false,
    "properties": { /* per the table above */ },
    "required": []
  },
  "ui": {
    "fields": { /* per the table above */ },
    "sections": {
      "section-1": {
        "label": "Details",
        "columns": 1,
        "isActive": true,
        "leftColumn": [
          {"name": "<key1>", "type": "field"},
          {"name": "<key2>", "type": "field"}
        ]
      }
    },
    "order": ["section-1"]
  }
}
```

`required: []` for now — SMART doesn't surface per-attribute required-ness for event types in a way we can reliably honor across CAs. Can be tightened later.

### Category posting (unchanged for both v1 and v2)

`synchronize_datamodel()` already posts categories via `post_event_category` / `patch_event_category`, which are version-less. No changes here. v2 event types reference the category by its `value` slug (same string we already use as the category's `value` field today).

### Identifying existing event types

`_find_existing_event_type(value)` already looks up by `value`. This works for **both** v1 and v2 — the value strings are unique tenant-wide across versions. Tighten one detail: when running in v2 mode the call to `get_event_types` will only return v2-version records, but the unique-value constraint spans both. So if a v1 record for the same `value` already exists, our `post_event_type(version="v2")` will hit a duplicate-key error.

Recovery rule: on duplicate-key during a v2 POST, surface a clear error message ("an event type with this value exists in v1; either choose a different category prefix or run the server-side migrate endpoint to convert it to v2"). Do **not** auto-patch the v1 record into v2 — that's a destructive cross-version edit.

### Logging

Per-event-type create/update log lines already print the display and value (`Creating ER event type %r (%s)`). Add the version: `Creating ER event type %r (%s) [v2]`. One-line addition.

## Data flow (v2 path)

1. User runs `er-smart-sync datamodel --config sync.yaml` (or `--event-type-version v2`).
2. CLI builds `SyncConfig`, sets `er.event_type_version = "v2"`.
3. `ERSmartSynchronizer.__init__` stashes `self._event_type_version = "v2"`.
4. `synchronize_datamodel()` fetches SMART data model + optional CM; calls `build_event_types_v2(...)`.
5. For each event type:
   - Look up existing by `value` via `er_client.get_event_types(version="v2")` (snapshot taken once at run start).
   - If missing → `post_event_type(version="v2")` with v2-shaped payload.
   - If present and `_event_type_needs_update(...)` → `patch_event_type(version="v2")`.
   - On POST duplicate-key error: log and skip (per "Identifying existing event types" above).
6. Stats incremented as today (`created` / `updated` / `unchanged` / `skipped` / `errored`).

## Testing

**New: `tests/test_smart_to_er_v2.py`**

- One unit test per SMART attribute type asserting the exact `json.properties[key]` + `ui.fields[key]` shape.
- Configurable-model overlay test: option filtering via `attribute_configs` still works in v2 output.
- Inactive-attribute test: `deprecated: true` present in `json.properties`, field **still** listed in `ui.sections.section-1.leftColumn`.
- TREE leaf-flatten test.
- Snapshot test: a fixture data model from `tests/fixtures/` → assert against a stored `expected_v2.json`.
- Category value test: assert `category` field on the output equals the same lower-cased CA-scoped identifier we use for v1.

**Extended: `tests/test_synchronizer.py`**

- Parametrize the existing `test_datamodel_*` happy-path tests over `(v1, v2)`. Assert:
  - `er_client.post_event_type` was called with the expected `version=` kwarg.
  - The payload's `schema` field is a `str` for v1 and a `dict` for v2.
  - The payload's `category` field is a UUID for v1 and a slug for v2.
- New `test_v2_duplicate_key_does_not_patch_v1`: simulate a duplicate-key error on POST when version=v2, assert no PATCH is issued and an error is logged.
- New `test_event_type_needs_update_v2`: two dicts with the same content but different key ordering → not flagged as a diff.

**Extended: `tests/test_cli.py`**

- `--event-type-version v1` and `--event-type-version v2` both wire through to `EarthRangerConfig.event_type_version`.
- Config YAML loads `event_type_version: v2`.
- `config-template` output contains the `event_type_version` line.

## Rollout

Single PR. v2 is the new default; users who want v1 set `--event-type-version v1` or `er.event_type_version: v1` in their YAML.

USAGE.md gets a "Choosing v1 vs v2 event types" subsection explaining:

- Why v2 (richer field types, structured UI, better long-term).
- When you might still want v1 (your ER tenant hasn't enabled v2 yet — unlikely but possible).
- That you cannot have the same `value` exist as both v1 and v2; if you've previously pushed v1 event types, run ER's server-side `POST /api/v2.0/activity/eventtypes/migrate/` to convert them (separate, follow-up tooling spec).

## Open questions / risks

- **Inline `enum` acceptance**: the v2 docs show `anyOf: [$ref]` for choices. JSON Schema 2020-12 *does* allow inline `enum` and the v2 validator is built on a standard validator, so this should work. We will verify with a smoke test against a v2 tenant during implementation. If it fails, the choices-API follow-up spec becomes a blocker.
- **`deprecated: true` rendering** in ER's form designer: we're emitting a valid JSON Schema flag but ER UIs may or may not honor it visually. Acceptable risk — the field stays present in both the schema and the form layout, so worst-case a deprecated field renders as a normal field with the flag preserved as metadata for downstream consumers.

## Related future work (not in this spec)

- **Choices population in ER**: post option sets to ER's choices API, then emit `$ref` URLs from event schemas instead of inline `enum`. Separate spec.
- **`migrate-to-v2` subcommand**: orchestrate `POST /api/v2.0/activity/eventtypes/migrate/` for previously-pushed v1 event types. Separate spec.
