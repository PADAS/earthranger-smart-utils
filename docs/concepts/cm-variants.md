# CM variant groups

A SMART Configurable Model (CM) can define multiple `<node>` elements that share
the same `categoryHkey` — each one is a distinct UI flow over a single underlying
data-model category, often with different per-variant attributes. These are called
**variant groups**.

Example: `Botswana_Guardians.xml` defines ten "Carcass" variants ("Large Predator
Carcass", "Small Predator Carcass", …) that all map to `animals.carcass` in the
base data model.

## Automatic detection

No enumeration of variant groups is required. `build_event_types_v2` groups CM
categories by `hkeyPath`. Groups of size 1 are processed as normal event types —
nothing changes. Groups of size > 1 trigger the configured mode.

## `--cm-variant-mode split` (default)

One ER event type is created per CM node. The slug format is:

```
{ca_uuid}_{cm_uuid}_{hkey}_{sanitize(display)}_{8hex(sha256(node_id))}
```

Each variant carries its own full attribute schema. Conservancy staff see all
variants as distinct report types in EarthRanger — for example, "Large Predator
Carcass" and "Small Predator Carcass" appear as separate entries in the ER event
picker.

The `sha256(node_id)` suffix guarantees slug uniqueness even if two variants
share a sanitized display string.

## `--cm-variant-mode consolidate`

One ER event type is created per variant group. Its schema uses ER's v2
conditional sections feature:

- **`section-1`** — always visible; holds a single `variant` discriminator field
  (a CHOICE_LIST dropdown).
- **`section-{N}` (N ≥ 2)** — one conditional section per variant, each carrying
  that variant's attributes. An `IS_EXACTLY` condition on the discriminator drives
  section visibility so only the selected variant's fields appear.

Variant attribute property keys are namespaced per section
(`section_2_age`, `section_3_age`, …) so two variants that share an attribute key
in the CM don't silently overwrite each other.

The discriminator dropdown is backed by a `ChoiceSet` emitted via
`build_choice_sets` — see [Choices](choices.md) for how those records are upserted.
The field name is derived as `derive_choice_field(value, "variant")`.

## v2 only

Both modes require v2 event types. v1 event types are Jinja-template based and
have no conditional section support. Tenants still on v1 keep the existing
downstream deduplication mitigation — unchanged behaviour, no new variant handling.

See [Event-type version](event-type-version.md) for how to check or set the
version for a tenant.

!!! warning "Rename-orphaning"
    Both modes derive part of each variant's identity from the sanitized
    `display` string. Renaming a variant in the CM changes that identity and
    **orphans events already stored under the old value** — EarthRanger will not
    update historical event records to the new slug.

    The `sha256(node_id)` component in split slugs guarantees uniqueness, but
    does not prevent rename-orphaning because the human-readable display is also
    baked into the slug. If you must rename a variant, plan for a data migration
    on the EarthRanger side.

## When to use which

| Mode | Best for |
|---|---|
| `split` (default) | Each variant is conceptually a distinct report type and conservancy staff should see separate entries in ER |
| `consolidate` | Variants are facets of one underlying observation — e.g. a single "Carcass" entry where the user picks the predator type after the fact |

## Related

- [Concept: Choices](choices.md) — discriminator ChoiceSet upsert behaviour
- [Concept: Event-type version](event-type-version.md) — v2 vs v1 differences
- [`datamodel` CLI reference](../cli-reference/datamodel.md)
