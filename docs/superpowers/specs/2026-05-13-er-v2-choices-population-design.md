# Design: ER v2 choices population

**Status:** Draft
**Author:** Claude (with @chrisdo)
**Date:** 2026-05-13
**Parent spec:** `2026-05-12-er-v2-event-types-design.md` (calls this work out as a Phase-1 prerequisite)

## Goal

Before `er-smart-sync` can POST v2 event types whose schemas reference choice values via `$ref` (the only shape ER's v2 meta-schema accepts for dropdowns), every option set must exist as `Choice` records in EarthRanger. This spec covers building, upserting, and orchestrating those records.

## Why this isn't a follow-up

ER's v2 meta-schema (`das/das/activity/schemas/eventtype_meta_schemas.py:96-180`) accepts choices **only** via `anyOf: [{"$ref": "<uri>"}]`. There is no inline-`enum` escape hatch. SMART data models are choice-heavy (Sector, species, equipment, etc.). Without this layer in place, virtually no SMART event type will POST successfully under v2. The previous v2 implementation tried inline `enum` and was rejected on the first dropdown attribute (`Sector`, MLIST, 36 options) against `gundi-dev.staging.pamdas.org`.

## Background: ER's Choices API

From reading `das/das/choices/`:

- **Endpoint:** `GET/POST /api/v1.0/choices/`, `GET/PUT/PATCH/DELETE /api/v1.0/choices/{uuid}/`.
- **Filters:** `?model=<dotted>&field=<csv>&include_inactive=<bool>`. Default queryset is `is_active=True`; pass `include_inactive=true` to see soft-deleted rows.
- **Choice record fields** (`das/das/choices/models.py:105-141`):
  - `model` — string FK to a content type. Default `"activity.event"`.
  - `field` — `CharField(max_length=40)`; must match `^\w+$` (letters/digits/underscores).
  - `value` — `CharField(max_length=100)`; must match `^\w+$`.
  - `display` — `CharField(max_length=100)`; free text.
  - `ordernum` — `SmallIntegerField`, optional; orders options in dropdowns.
  - `icon` — optional.
  - `is_active` — boolean, soft-delete flag.
  - `sub_choice_of` — M2M for TREE hierarchies. **Not exposed on the REST serializer**; ignored for now.
- **Unique constraint:** `(tenant, model, field, value)`. Same `field` value across many records (one per option) is the normal shape.
- **DELETE behavior:** soft-deletes (`is_active=False`, `delete_on=now`); record stays in DB.
- **Upsert semantics:** no native upsert. Use GET to find by `(model, field, value)`, then PATCH; POST handles a race by returning 409.
- **The `$ref` URL** referenced by event-type schemas is `/api/v2.0/schemas/choices.json?field=<field>`, which proxies to `ChoicesView` filtered by `field=` (NOT by `model=`). See `das/das/schemas/views.py:79-91`. The reference example uses no `model` filter; same-`field`-different-`model` would collide, so we pin `model="activity.event"`.

## Scope

In scope:

- Building a list of `ChoiceSet` records from a SMART data model (+ optional configurable-model overlay), one per (event_type, attribute) pair that bears options.
- A stable `Choice.field` naming scheme that fits the 40-char limit.
- A value-sanitization rule that maps SMART option keys → `^\w+$` strings.
- An upsert algorithm that handles create, no-op, update, soft-deactivate, and re-activate.
- A new CLI subcommand `choices` plus inline auto-run from `datamodel` (v2 path only), with a `--skip-choices` escape hatch.
- Stats/observability for the choices phase.
- Tests covering field derivation, value sanitization, the upsert decision matrix, end-to-end orchestration.

Out of scope (deferred):

- TREE-hierarchy preservation via `sub_choice_of`. Flatten to leaves, matching v1 parity.
- Cross-CA / cross-tenant choice deduplication. Field names are event-type-scoped; same option set appearing in many event types becomes many `Choice` rows. Acceptable given typical SMART CA sizes.
- Bulk POST. ER has no bulk endpoint; we POST one row at a time. CAs have at most low-thousands of options.
- Icons. SMART has no icon concept that maps cleanly.
- `sub_choice_of` for parent-child filtered dropdowns. Future work.

## Authoritative naming and sanitization

### `Choice.field` derivation

```python
import hashlib
import re


def derive_choice_field(event_type_value: str, attr_key: str) -> str:
    """Derive a stable Choice.field name.

    Tenant-unique iff event_type_value is unique (which it is, since the
    synchronizer scopes event_type_value by ca_uuid and, when applicable,
    cm_uuid).
    """
    digest = hashlib.sha256(event_type_value.encode("utf-8")).hexdigest()[:8]
    sanitized = _sanitize_field_segment(attr_key)
    field = f"et{digest}_{sanitized}"
    if len(field) > 40:
        # 28-char budget for the attribute key. SMART keys observed in the wild
        # are well under that; truncating risks collision but is preferable
        # to silently failing the 40-char DB column.
        field = field[:40]
    return field


def _sanitize_field_segment(s: str) -> str:
    """Strip to ^\w+$ (letters, digits, underscores), lowercase."""
    sanitized = re.sub(r"[^A-Za-z0-9]+", "_", s).strip("_").lower()
    return sanitized or "x"  # fallback if the input is all non-word chars
```

Properties:

- 12-char fixed prefix (`et` + 8 hex + `_`), 28 chars for the (sanitized) attr_key.
- Stable across re-syncs because `event_type_value` is stable.
- Event-type-scoped: same SMART attribute in 5 event types → 5 choice sets. Verified acceptable in question round.
- CM-aware: `event_type_value` already encodes `cm_uuid` when present, so different CMs naturally get different choice sets.
- Collision-proof at scale: 8 hex chars = 4 billion buckets; truncation only triggers if attr_key > 28 chars, in which case we accept the (vanishingly small) collision risk.

### `Choice.value` sanitization

```python
def sanitize_choice_value(option_key: str) -> str:
    """Map a SMART option key to a ^\w+$ string."""
    sanitized = re.sub(r"[^A-Za-z0-9]+", "_", option_key).strip("_").lower()
    return sanitized or "_"
```

Notes:

- TREE leaf keys (e.g. `africa.kenya.nairobi`) become `africa_kenya_nairobi`. Same flattening v1 does.
- Apostrophes, accents, spaces all collapse to underscores.
- **This rule is load-bearing.** Historical event records store the resolved value string, not a foreign key. Changing this rule later requires backfilling stored event values. **Lock it down on first ship.**
- The unchanged `option.display` carries the original (human-readable, possibly accented) label.

## Data flow

```
SMART DM + optional CM
        │
        ▼
build_event_types_v2(dm, cm, ca_uuid)
        │
        ├──► list[ERV2EventType]   (each property's $ref uses derive_choice_field)
        └──► list[ChoiceSet]
              │
              ▼
       upsert_choices(er_client, choice_sets)
              │
              ▼
   ┌──────────────────────────────┐
   │ choices_errored > 0 for CA?  │
   ├──── yes ──► skip event-type POSTs for this CA, log abort
   └──── no  ──► proceed to event-type POSTs (existing code path)
```

### `ChoiceSet` plan record

```python
from dataclasses import dataclass


@dataclass(frozen=True)
class ChoiceOption:
    value: str          # sanitize_choice_value(option.key)
    display: str        # option.display, untouched
    is_active: bool     # CM isActive flag, or True if no CM


@dataclass(frozen=True)
class ChoiceSet:
    field: str                       # derive_choice_field(event_type_value, attr_key)
    options: tuple[ChoiceOption, ...]  # order matches SMART option order → ordernum
```

Same `field` may legitimately appear in two `ChoiceSet` records if the v2 builder emits it twice (e.g. the carcass reference uses `carcassrep_species` at the top level AND nested inside `animal_groups.items.properties`). The upserter deduplicates by `field`, asserting the options are identical between duplicates (raise if not — that's a bug in the builder).

## Upsert algorithm

Per `ChoiceSet`:

1. **Fetch existing:** `GET /api/v1.0/choices/?model=activity.event&field={field}&include_inactive=true&page_size=200`. Page through if needed (SMART option sets are typically < 100).
2. **Index existing by `value`** → `{value: choice_record}`.
3. **For each planned option (ordered, index → `ordernum`):**
   - **Not in existing:** `POST /api/v1.0/choices/` with `{model, field, value, display, ordernum, is_active}`. On 409 (race), GET-by-value and proceed to PATCH branch.
   - **In existing, fully matched** (display equal, ordernum equal, is_active equal): no-op, increment `choices_unchanged`.
   - **In existing, drifted**: `PATCH /api/v1.0/choices/{id}/` with only changed fields. Increment `choices_updated`.
   - **In existing, active=False but planned active=True**: `PATCH is_active=True`. Increment `choices_updated`.
   - **In existing, active=True but planned active=False**: `PATCH is_active=False`. Increment `choices_deactivated`.
4. **Orphan handling**: for each existing record whose `value` is not in the planned options AND `is_active=True`: `PATCH is_active=False`. Increment `choices_deactivated`. (Soft-deactivate, don't hard-delete — historical events may still reference the value.)
5. **Errors**: each Choice operation is independent. Failure increments `choices_errored` and logs (single line, `value` and `field` in the extra dict). The remaining options in the same `ChoiceSet` still attempt; subsequent `ChoiceSet`s in the run still attempt.

After all `ChoiceSet`s for a CA are processed: if `choices_errored > 0` for that CA, **skip the event-type POST phase for that CA** and log a clear abort message naming the affected fields. This is the abort-strict failure semantics decided in question round.

Retries use the existing `_retry()` helper (exponential backoff, network/5xx only).

## Module layout

| Path | Action | Responsibility |
|---|---|---|
| `src/er_smart_sync/choices.py` | create | `derive_choice_field`, `sanitize_choice_value`, `ChoiceOption`, `ChoiceSet`, `upsert_choices`, `ChoicesStats` dataclass. |
| `src/er_smart_sync/smart_to_er_v2.py` | (rewrite per parent spec) | Imports `derive_choice_field` and `sanitize_choice_value`. Returns `tuple[list[ERV2EventType], list[ChoiceSet]]`. |
| `src/er_smart_sync/synchronizer.py` | modify | New two-pass orchestration in `push_smart_ca_datamodel_to_earthranger` on v2: call `build_event_types_v2`, upsert choices, gate event-type POSTs on choices success. Extend `datamodel_stats` with five new counters. New `_event_type_version`-aware path; v1 path unchanged. |
| `src/er_smart_sync/config.py` | modify | Add `EarthRangerConfig.choices_base_url: str = "/api/v2.0/schemas"` for the `$ref` URL prefix. |
| `src/er_smart_sync/cli.py` | modify | New `choices` subcommand. New `--skip-choices` flag on `datamodel`. v2 path in `inspect-datamodel` previews choice sets. |
| `tests/test_choices.py` | create | Field derivation, value sanitization, upsert decision matrix (mocked `er_client`). |
| `tests/test_synchronizer.py` | modify | Two-pass orchestration; abort on choice failure; `--skip-choices` honored. |
| `tests/test_cli.py` | modify | `choices` subcommand wiring; `--skip-choices` flag. |

The v2 body rewrite is the **parent spec's** job (`2026-05-12-er-v2-event-types-design.md`). This spec is the choices layer; the two land in sequence: choices first (this spec), then v2 builder rewrite (parent spec, Phase 2).

## CLI surface

### New: `er-smart-sync choices`

```
er-smart-sync choices --config sync.yaml
er-smart-sync choices --from-file dm.xml [--cm-from-file cm.xml] [--cm-uuid <uuid>] \
    --er-endpoint <url> --er-token <t> [--dry-run]
```

Same flag surface as `datamodel`: accepts either a config or individual `--smart-*`/`--er-*` flags, plus the file-based `--from-file` variants. Behavior:

- Loads the SMART data model.
- Builds `ChoiceSet`s (calling the same `build_event_types_v2` to compute the field names — even though we discard the event-types output here, we use the builder's wiring for consistency; alternative is a lighter-weight `build_choice_sets` helper if memory/time becomes a concern, which it won't).
- Runs `upsert_choices`.
- Prints a `ChoicesStats` summary.
- Exits non-zero if any choice errored (mirrors how `datamodel --dry-run` errors out on critical issues today).

### Modified: `er-smart-sync datamodel`

New flag: `--skip-choices` (default false). When `--event-type-version v2` (or config equivalent) and `--skip-choices` is not set, the synchronizer runs the choices phase before event types as described in the data-flow diagram. When `--skip-choices` is set, the choices phase is skipped (warning logged: "choices not upserted; event-type POSTs may produce broken dropdowns if choice sets are missing").

When `--event-type-version v1`, `--skip-choices` is a no-op (silent — choices aren't a v1 concept).

### Modified: `er-smart-sync inspect-datamodel`

When `--event-type-version v2`, also print the `ChoiceSet`s that would be upserted (field name, option count, option keys+displays). Section appears after the event-type listing.

## Configuration

Add to `EarthRangerConfig`:

```python
choices_base_url: str = "/api/v2.0/schemas"
```

This is the prefix for the `$ref` URL: `{choices_base_url}/choices.json?field={field}`. Default matches ER's standard. Settable for tenants that mount the API at a non-standard path.

YAML template line:

```yaml
  # URL prefix for v2 choice $refs. Default matches ER's standard layout.
  choices_base_url: /api/v2.0/schemas
```

## Stats

Extend `ERSmartSynchronizer.datamodel_stats` with:

- `choices_created`
- `choices_updated`
- `choices_unchanged`
- `choices_deactivated`
- `choices_errored`

The existing summary line (`Datamodel sync summary: ...`) auto-includes these because it iterates the dict.

## Testing

### `tests/test_choices.py` (new)

- `derive_choice_field`:
  - Deterministic (same inputs → same output).
  - Different `event_type_value` → different hash → different `field`.
  - 40-char ceiling honored even for ridiculous attr_keys.
  - Output matches `^\w+$`.
- `sanitize_choice_value`:
  - `africa.kenya.nairobi` → `africa_kenya_nairobi`.
  - Accented chars → underscored.
  - All-non-word input → `_` (fallback).
- `upsert_choices` decision matrix, with `MagicMock` `er_client`:
  - New option → POST.
  - Unchanged → no GET-or-write side effects.
  - Display drift → PATCH.
  - Ordernum drift → PATCH.
  - is_active drift (T→F and F→T) → PATCH.
  - Orphan (in ER, not in plan) → PATCH is_active=False.
  - 409 on POST → falls back to GET+PATCH on the racing record.
  - 500 on POST → retried per `_retry`, eventually increments `choices_errored`.
  - Duplicate `ChoiceSet.field` across the input list with identical options → deduplicated; with different options → raises `ValueError` (builder bug guard).

### `tests/test_synchronizer.py` (extend `TestEventTypeVersionWiring`)

- v2 path: `push_smart_ca_datamodel_to_earthranger` calls `upsert_choices` BEFORE `er_client.post_event_type`.
- Choice error aborts event-type POSTs: when `upsert_choices` reports `choices_errored=1`, no `post_event_type` calls happen for that CA; abort log line emitted; `datamodel_stats["event_types_errored"]` reflects the skip.
- v1 path: no `upsert_choices` call, no abort logic exercised. Backward compat.
- `--skip-choices` honored: when set, choices phase skipped, event-type POSTs still happen.

### `tests/test_cli.py` (extend)

- `choices` subcommand wires through to `upsert_choices` and prints stats.
- `choices` subcommand with `--dry-run` doesn't call POST/PATCH (uses `DryRunERClient`).
- `--skip-choices` flag on `datamodel` propagates correctly.
- `inspect-datamodel --event-type-version v2` includes a choices section in stdout.

### End-to-end smoke

Documented runbook (in this spec or a sibling doc) against `gundi-dev.staging.pamdas.org`:

1. `er-smart-sync choices --from-file <real fixture> --er-endpoint <staging>` → verify choice records exist in ER admin UI.
2. `er-smart-sync datamodel --from-file <same> --event-type-version v2` (with the parent-spec rewrite applied) → verify event types POST cleanly, dropdowns render in ER UI.

## Rollout

- **Phase 1a (this spec, implementation):** ship the choices layer behind the existing v2 flag (which is currently default-v1 per the parent spec's Phase 0 revert). v2 path now runs choices automatically.
- **Phase 1b:** validate manually against `gundi-dev.staging.pamdas.org`. No automated end-to-end; the staging tenant is the integration test.
- **Phase 2 (parent spec):** rewrite the v2 builder body to produce meta-schema-valid output. With choices already populated, the rewritten builder's `$ref` URLs resolve correctly.
- **Phase 4 (parent spec):** flip default back to v2.

## Open questions / future work

1. **Cross-event-type choice deduplication.** Today, two event types referencing the same SMART attribute create two `Choice` sets. If choice volume becomes a concern, we can move to CA-scoped field names. The hash scheme makes this transparent to migrate later (re-sync + cleanup old field-name records, no data loss — see "consequences of changing later" discussion in the brainstorming round).
2. **TREE hierarchy via `sub_choice_of`.** Out of scope this round. Flat-leaves matches v1 behavior.
3. **Icons.** SMART has no equivalent. Could be future work if a UX need surfaces.
4. **Bulk upsert.** ER has no bulk endpoint. If a CA grows to >10k options and the per-row POST round-trip is painful, propose a `bulk_create` extension to ER's `ChoicesView`. Not blocking.
5. **Choice field-name migration tool.** A future `er-smart-sync choices --rename --from-pattern --to-pattern` could help if we change the derivation scheme. Out of scope here; defer until we actually want to migrate.

## Acceptance criteria

- A SMART data-model fixture covering at least one LIST, one MLIST, one TREE attribute syncs cleanly via `er-smart-sync choices --from-file fixture.xml`, producing the expected `Choice` records in a mocked-or-real ER endpoint.
- A subsequent v2 `datamodel` push (once the parent-spec rewrite lands) produces event types whose `$ref` URLs resolve to the newly-populated choice sets and pass ER's meta-schema validation.
- All unit tests pass; ruff/format/ty clean.
- No regression in v1 behavior (the existing 122-test suite still green).
