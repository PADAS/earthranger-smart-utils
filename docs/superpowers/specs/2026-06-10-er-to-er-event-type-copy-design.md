# ER → ER event-type copy — design

## Problem

`er-smart-sync` syncs SMART data models into EarthRanger. There is no way to
copy an existing EarthRanger **event type** from one ER site to another. Doing
it by hand means re-creating the event type and every choice option-set its v2
schema references — error-prone and slow.

We want a CLI command that copies a single event type (by `value`) from a
source ER site to a destination ER site, attaching it to a target event
category that already exists on the destination, and bringing along the choice
records its schema references.

## Decisions (settled during brainstorming)

- **Version:** default to **v2**; a `--version v1|v2` flag overrides. Source and
  destination use the same version (no cross-version conversion).
- **Choices:** for v2, also copy the choice option-sets the source schema
  references via `$ref`, upserting them onto the destination. v1 schemas embed
  enums inline, so the choices leg is skipped for v1.
- **Target category:** must already exist on the destination. If it is missing,
  fail with an actionable error (do not auto-create).
- **Conflict:** if an event type with the same `value` already exists on the
  destination, **patch** it to match the source (idempotent / re-runnable).
- **Scope:** one event type per invocation. No wildcard or whole-category copy.
- **Auth:** each side accepts either a token or a username/password pair.

## Approach

A standalone module, `src/er_smart_sync/er_to_er.py`, exposing a
`copy_event_type()` function that takes **two** `ERClient`s (source and
destination). This is approach A from brainstorming: `ERSmartSynchronizer` is
built around one ER + one SMART config, so ER→ER work — which needs two ER
clients and no SMART client — lives in its own module rather than being forced
into the synchronizer. The choices leg reuses the existing
`choices.upsert_choices()`.

A new CLI subcommand `copy-event-type` builds the two clients from
`--source-*` / `--dest-*` options and calls the function.

## CLI

```
er-smart-sync [--dry-run] [-v] copy-event-type \
  --source-endpoint URL  (--source-token TOK | --source-username U --source-password P) \
  --event-type-value VALUE \
  --dest-endpoint URL    (--dest-token TOK | --dest-username U --dest-password P) \
  --target-event-category CATEGORY_VALUE \
  [--version v1|v2]   # default v2
```

- Each side requires an endpoint plus either a token or both username and
  password. Mirror the existing `_validate_config` auth rule; raise
  `click.UsageError` when neither is supplied.
- `--version` defaults to v2 (consistent with the rest of the CLI).
- Honors the existing global `--dry-run` (wrap the **destination** client in
  `DryRunERClient`; the source client is read-only) and `-v` debug logging.

## Module: `er_to_er.py`

```python
@dataclass
class CopyEventTypeStats:
    event_type_action: str          # "created" | "updated"
    choices: ChoicesStats           # reused from choices.py
    choice_fields_copied: int

def copy_event_type(
    *,
    source_client: ERClient,
    dest_client: ERClient,
    value: str,
    target_category: str,
    version: str = "v2",
    copy_choices: bool = True,
) -> CopyEventTypeStats: ...
```

### Steps

1. **Fetch source event type.**
   `source_client.get_event_types(include_inactive=True, include_schema=True,
   version=version)`; select the record whose `value == value`. If none,
   raise `EventTypeNotFound` (→ CLI surfaces a clear error naming the value and
   source endpoint).

2. **Verify the target category exists on the destination.**
   `dest_client.get_event_categories()`; if no category has
   `value == target_category`, raise `TargetCategoryMissing` listing the
   available category values. (Per decision: no auto-create.)

3. **Copy referenced choices (v2 only).**
   - Parse the source schema. ER returns v2 schemas as a JSON-stringified blob
     on GET, so normalize: `json.loads` if `str`, else use the dict (mirror the
     synchronizer's existing normalization at `synchronizer.py:593`).
   - Walk the schema for every `$ref` string and extract the choice field via
     `field=([^&"]+)` — base-url-agnostic, so it works regardless of the
     source's `choices_base_url`.
   - For each unique field, fetch source records:
     `source_client._get("choices", params={"model": "activity.event",
     "field": field, "include_inactive": True, "page_size": 200})`, following
     pagination the same way `choices._fetch_existing` does, and tolerate the
     "is not one of the available choices" 400 as an empty result.
   - Build a `ChoiceSet(field=field, options=(ChoiceOption(value, display,
     is_active), ...))` per field, preserving source `ordernum` order.
   - `upsert_choices(er_client=dest_client, choice_sets=...)`. Returns
     `ChoicesStats`.
   - Skipped entirely for v1.

4. **Reconstruct and write the event type.**
   - Build an `ERV2EventType` (v2) or `EREventType` (v1) from the source dict,
     override `category = target_category`, and clear `id`.
   - Look for an existing dest event type with the same `value`
     (`dest_client.get_event_types(..., version=version)`).
     - Exists → set `id`, `dest_client.patch_event_type(...)` → action
       `"updated"`.
     - Absent → `dest_client.post_event_type(...)` → action `"created"`.
   - Wrap writes with the synchronizer's `_retry` helper for transient
     failures.

5. **Return `CopyEventTypeStats`.** The CLI prints a summary in the style of
   the other subcommands (event-type action + choices created/updated/
   unchanged/errored + choice fields copied).

### Ordering

Choices are upserted **before** the event type is written, so the destination
event type's `$ref`s resolve immediately after the copy completes.

## Error handling

| Condition | Behavior |
|-----------|----------|
| Source event type not found | `EventTypeNotFound` → `click.ClickException` naming value + source endpoint |
| Target category missing on dest | `TargetCategoryMissing` → `click.ClickException` listing available categories |
| Missing/invalid auth per side | `click.UsageError` |
| Per-choice HTTP errors | Counted in `ChoicesStats.errored` (tolerant, non-fatal), surfaced in summary |
| Event-type POST/PATCH failure | Raised after `_retry` exhausts; CLI reports failure |

A nonzero `choices.errored` makes the command exit nonzero (matching the
`choices` subcommand's behavior).

## Testing

`tests/test_er_to_er.py`, two mocked `ERClient`s (mirrors the existing
mocked-client test style):

- **v2 happy path** — event type + referenced choices copied; correct
  category override; choices upserted on dest before event-type write.
- **v1 happy path** — event type copied; choices leg skipped.
- **`$ref` field extraction** — multiple `$ref`s, deduped fields, mixed schema
  shapes (string blob vs dict).
- **Source value not found** — raises `EventTypeNotFound`.
- **Target category missing** — raises `TargetCategoryMissing`.
- **Value already exists on dest** — patches instead of posting.
- **Choices pagination + 400-as-empty** — source choice fetch handles both.
- **Dry-run** — destination writes routed through `DryRunERClient`, none
  executed.

## Out of scope (YAGNI)

- Copying more than one event type per run (wildcard / whole-category).
- Cross-version conversion (v1 source → v2 dest or vice versa).
- Copying anything beyond the event type and its referenced choices (e.g.
  the category itself, icons, or unrelated schema modules).
