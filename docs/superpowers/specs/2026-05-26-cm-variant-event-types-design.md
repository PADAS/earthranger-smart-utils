# CM-variant event types: split vs consolidate

## Context

SMART Configurable Models (CMs) can define multiple UI "variants" over a
single underlying data-model category. In the Botswana_Guardians CM, the
category `animals.carcass` has 10 distinct CM nodes — "Large Predator
Carcass", "Small Predator Carcass", etc. — each a separate data-collection
flow with **its own attribute set**, but all sharing the same
`categoryHkey` (`animals.carcass.`).

The v2 event-type builder derives an event type's `value` slug from
`hkeyPath`, so all 10 variants collapse to one slug. ER's unique
constraint on `value` means only one survives; the rest are dropped. PR #9
shipped a downstream dedup mitigation in `create_or_update_er_event_types`
(matched-existing-display picker) that keeps re-runs idempotent but is
lossy — 9 of the 10 variants never reach EarthRanger.

This is the root-cause fix for that mitigation. It gives operators a
choice, per sync, between two faithful representations of variant groups
instead of silently dropping variants.

**Scope decision (agreed during brainstorming):** v2 only. ER's
conditional-section feature — which consolidate mode depends on — exists
only in the v2 meta-schema (`das/das/activity/schemas/eventtype_meta_schemas.py`).
v1 event types are Jinja-template-based with no schema validation and no
conditional support. v1 tenants keep the current downstream-dedup
behavior, untouched.

## Modes

A new global option `--cm-variant-mode {split,consolidate}` (CLI flag +
`cm_variant_mode` config field), **default `split`**.

### split (default)

One ER event type per CM node. Each variant keeps its full attribute
schema. Conservancy staff see all variants as distinct report types
(e.g. 10 "Carcass" types).

- `value` = base + per-variant disambiguator:
  `{ca_uuid}_{cm_uuid}_{hkeyPath}_{sanitize(display)}_{sha256(node_id)[:8]}`
- `display` = `cat.display` (already distinct per variant).
- No attribute merging — the whole point is fidelity to each CM node.

### consolidate

One ER event type per variant group, with the variant surfaced as a
required single-select discriminator field, and each variant's attributes
shown conditionally based on the selection.

- `value` = base `{ca_uuid}_{cm_uuid}_{hkeyPath}` (no disambiguator).
- `display` = title-cased `hkeyPath` leaf (e.g. `carcass` → "Carcass").
  Variant parent displays are unreliable (Botswana had "Carcass" and
  "Carcass " with a trailing space); the path leaf is stable.
  *Alternative considered:* longest-common-prefix of variant displays.
- Discriminator field: name `derive_choice_field(value, "variant")`,
  options = one per variant (`value=sanitize(display)`, `display=display`).
- Sections: `section-1` holds the discriminator (always visible); one
  section per variant holds that variant's full attribute set.
- Conditions: each variant section carries
  `{field: <discriminator>, id: "condition-N", operator: "IS_EXACTLY",
  value: <variant option value>}`; the discriminator field carries
  `conditionalDependents: [<variant section ids>]`.

## Variant-group detection

Automatic, in `build_event_types_v2`: group parsed `cats` by `hkeyPath`.

- Group size 1 → normal event type, current code path, zero change.
- Group size > 1 → variant group; apply the configured mode.

No enumeration of "which categories are variant groups" — the shared
`hkeyPath` *is* the signal. When a CM no longer has duplicate-path nodes,
the special handling disappears with no config change.

## Components & changes

### smartconnect-client (`smartconnect/models.py`)

Expose the CM node `id` so the v2 builder can derive stable split slugs.

1. `Category` model: add `id: Optional[str] = None`. Optional because
   DM-path categories don't carry a node id.
2. `ConfigurableDataModel.generate_node_paths` (both yield branches): add
   `'id': subcat['id']`, guarded so a malformed node falls back to `None`.

Ships as a smartconnect-client patch release (e.g. 1.11.2). er-smart-sync
then bumps its floor to `smartconnect-client>=1.11.2`. **Release ordering:
smartconnect-client first, then er-smart-sync.**

### er-smart-sync — split (`smart_to_er_v2.py`)

- `build_event_types_v2`: group by `hkeyPath`; for variant groups in split
  mode, call `_build_one(..., value_disambiguator=...)` per member.
- `_build_one`: accept `value_disambiguator`; append `_{disambiguator}` to
  `value` before lowercasing.
- Disambiguator: `f"{sanitize_choice_value(cat.display)}_{sha256(cat.id)[:8]}"`.
  Fallback to display-only + WARNING when `cat.id` is None.

### er-smart-sync — consolidate (`smart_to_er_v2.py` + `choices.py`)

- `build_event_types_v2`: for variant groups in consolidate mode, build a
  single event type from the group — synthesize the discriminator field,
  emit one section per variant, wire conditions + `conditionalDependents`.
- `choices.py` `build_choice_sets`: gain variant-group + mode awareness so
  it emits the discriminator's ChoiceSet (its options are Choice records).

### Event-type value length

The base CM value already approaches 100 chars for deep Botswana paths;
split's disambiguator pushes past it. **Verify ER's `EventType.value`
max_length in `das/activity/models.py`.** If capped, promote the
`_shorten_value` hash-suffix helper from `choices.py` to a shared util and
apply it to event-type values too (same deterministic scheme → idempotent
re-runs). If `EventType.value` is generously sized, no shortening needed.

## Stability / orphaning caveat

Both modes derive their per-variant identity (split slug; consolidate
discriminator option value) from the sanitized variant **display**.
Renaming a variant in the CM changes that identity, orphaning events
stored under the old value. The `sha256(node_id)` component in split slugs
guarantees uniqueness but does **not** prevent rename-orphaning, since the
readable display is also part of the slug. This tradeoff was chosen
(readability + uniqueness) over rename-stability during brainstorming.

## Testing

- **Grouping:** singleton categories unchanged; >1 sharing `hkeyPath`
  detected as a group. Unit test in `tests/test_smart_to_er_v2.py`.
- **split:** a 3-variant group yields 3 event types with distinct,
  deterministic slugs; each retains its own attributes; re-run produces
  identical slugs. Fallback-on-missing-id path covered.
- **consolidate:** a 3-variant group yields 1 event type with a
  discriminator field, 3 conditional sections, correct `IS_EXACTLY`
  conditions and `conditionalDependents`; the emitted schema validates
  against ER's v2 meta-schema (use the meta-schema or a known-good
  fixture); discriminator ChoiceSet emitted by `build_choice_sets`.
- **smartconnect-client:** `generate_node_paths` populates `id`;
  `Category` accepts and defaults it. Test in that repo's suite.
- **End-to-end:** dry-run both modes against Botswana_Guardians; confirm
  split yields N carcass types and consolidate yields 1 + selector.
  Real-run idempotence check (second run: 0 updated).

## Out of scope

- v1 event types (no conditional support; keep downstream dedup).
- Common-attribute extraction into an always-visible section in
  consolidate mode (v1 repeats shared attributes per variant section).
- Migrating events stored under pre-fix dropped/merged variant values.
- Per-CA mode override (global flag only; per-CA is a possible future
  extension).
