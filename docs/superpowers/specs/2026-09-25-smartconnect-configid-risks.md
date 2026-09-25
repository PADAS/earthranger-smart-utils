# Risk assessment: exposing CM attributeConfig identity in smartconnect-client

**Date:** 2026-09-25
**Status:** Risks accepted — proceed when scheduled
**Context:** Follow-up to shared choice lists (PR #16)

## Background

Shared choice lists (PR #16) scope `Choice.field` names per (CA, CM) and emit
one list per SMART attribute key. That is lossy for one SMART feature: a
Configurable Model can curate the *same* attribute differently per node —
multiple `<attributeConfig>` elements for one `attributeKey`, each node
referencing its curation via `configId`. Observed in a real CM
(Botswana Guardians): `nameofconservancy` has four configs actually in use
(6 areas / 4 areas / pinned to one area ×2); `otherspecies` has two
(7 of 15 options active vs 9 of 15).

The smartconnect-client parser (`ConfigurableModel.generate_attributes`)
flattens all configs to `{key, options}` and drops both the config `id` and
the node-level `configId` linkage, so er-smart-sync — before and after
choice sharing — collapses every key to its **first** config. Lifting this
requires smartconnect-client to expose the config identity, then extending
er-smart-sync's field derivation to it.

## Risks

### 1. Cached CM dicts (sharpest compatibility edge)

Both `SmartClient` and `AsyncSmartClient` round-trip the parsed CM through a
cache: `export_as_dict()` → JSON → cache, rehydrated later via
`import_from_dict()` (smartconnect/__init__.py:235–249,
async_client.py:271–285). After an upgrade, cached blobs written by the old
version hydrate **without** the new `config_id` keys.

- New code must treat the fields as optional, falling back to today's
  first-config-wins behavior, or the first post-deploy runs fail (or
  silently misbehave) until the cache expires.
- The reverse direction (new blobs, old reader) is safe — extra keys are
  ignored.

### 2. `cm["attributes"]` shape is a de-facto public API

Duplicate entries per key already occur (one per attributeConfig);
er-smart-sync's `_options_config_for` takes the first. The safe change is
**strictly additive**: add `config_id` to each attributes entry and to each
node attribute dict, change nothing else. Precedent: smartconnect-client
v1.11.2 "expose CM node id on Category" (PR #37) used exactly this pattern.
Restructuring (keying by config id, nesting options under nodes) would break
er-smart-sync's parsing and any consumer we cannot see.

### 3. `get_list_options` works partly by accident

It iterates `attribute.children` — untangle's built-in list of **all** child
elements, including `<name>` — and relies on downstream `if not key:
continue` to discard the resulting `{key: None}` junk. It also does not
recurse into nested `<treeNode>` hierarchies (21 present in the Botswana
CM). Any change to this function needs characterization tests against a real
CM XML first; even without changes, TREE attributeConfigs may already be
handled incompletely.

### 4. Version coordination across consumers

- er-smart-sync pins `smartconnect-client>=1.11.2,<2.0`, so a minor release
  (e.g. 1.12.0) flows in on the next resolve — er-smart-sync must tolerate
  both old and new dict shapes when it lands.
- gundi-smart-dispatcher pins the v1.7.0 wheel and never touches
  `ConfigurableModel`; das-smartconnect-provider shows no CM parsing.
  Unaffected.
- Consumers not checked out locally are unknown — another reason to stay
  strictly additive.
- Adding `Optional[str] config_id = None` to the shared Pydantic v1
  `CategoryAttribute` model is safe (shared with the v1 sync path; optional
  extra fields are ignored on both sides).

### 5. Field-name stability (the biggest risk, and it lives in er-smart-sync)

If the shared field derivation later hashes `config_id`, field names become
hostage to SMART's `attributeConfig id` UUIDs. **It is unverified whether
SMART keeps those ids stable** across CM edits, re-publishes, or
export/import in SMART desktop. If SMART regenerates them, every CM edit
mints new field names → recurring orphaned choice lists and schema
re-pointing (vs. the one-time `et*_` → `dm*_` migration).

**Must verify before deriving from config ids:** export the same CM twice;
edit an unrelated node and re-export; compare `attributeConfig id` values.

**Hedge:** keep the attr-key-based shared field for the `isDefault="true"`
config and give only non-default configs distinct fields — bounds any churn
to attributes an admin actually curated per-node.

### 6. Release logistics

smartconnect-client ships as wheels on GitHub releases: two repos, two PRs,
ordered rollout (library release first, then er-smart-sync pin bump +
derivation change), each with its own review cycle.

## Decision

Risks accepted (2026-09-25). The primary operator of er-smart-sync is the
maintainer and can manage the upgrade cases directly — in particular the
stale-cache window (Risk 1) and the one-time field migration if per-config
derivation lands (Risk 5). Order of work when picked up:

1. Verify `attributeConfig id` stability in SMART (Risk 5 checklist).
2. smartconnect-client: additive `config_id` exposure + characterization
   tests for `get_list_options` against a real CM XML; minor version bump.
3. er-smart-sync: pin bump; extend field derivation to config identity
   (default-config hedge unless step 1 proves ids stable).
