# The CA identifier

Every conservation area (CA) in EarthRanger is identified by a short code
derived from the SMART CA's label. This page explains the convention, how
the code is extracted, and what to do when a CA doesn't follow the
convention.

## The bracketed convention

SMART Connect CA labels follow this format:

```
Human-readable name [SHORTCODE]
```

Examples:

- `Foasf Reserve [FOASF]`
- `Jungle Keepers Peru [JKPERU]`
- `Ujung Kulon National Park [UKNP]`

The `[SHORTCODE]` is what becomes the EarthRanger event-category
identifier. It's typically 4–6 uppercase letters, but our validator accepts
2–30 characters of letters, digits, hyphens, and underscores.

![SMART CA with bracketed identifier](../images/smart-ca-label-bracketed.png)
*SMART Connect → Conservation Areas list. The bracketed `[FOASF]` is what flows through to EarthRanger.*

!!! note "Screenshot placeholder"
    Replace with a screenshot of SMART Connect's Conservation Areas list
    view, showing a CA whose name follows the bracketed convention. See
    `docs/images/README.md`.

## How extraction works

```mermaid
flowchart LR
    A["'Foasf Reserve [FOASF]'"] --> B[regex: r'\[(.*?)\]']
    B --> C["matches: ['FOASF']"]
    C --> D["take last match: 'FOASF'"]
    D --> E["lowercase + slugify"]
    E --> F["'foasf'"]
    F --> G[ER event-category identifier]
```

When there are multiple brackets, the **last** match wins. So
`[A] and [B]` → `B`. This is intentional — it lets you prefix labels with
arbitrary tags (`[ARCHIVED] Foasf Reserve [FOASF]`) without breaking the
identifier extraction.

## When the CA has no brackets

If a SMART CA label has no bracketed code, `er-smart-sync` can't extract an
identifier and will fail with an actionable error:

```
ValueError: Could not extract a CA identifier from SMART label
'Conservation Area Without Brackets' (ca_uuid=some-ca-uuid). The label
must contain a bracketed short code, e.g. 'Foasf Reserve [FOASF]'. Fix
the label in SMART Connect, or use --from-file with an explicit
--ca-identifier.
```

Two fixes:

1. **Edit the label in SMART Connect** to add the bracketed code. Preferred
   if you have admin rights to SMART. The label is human-visible and the
   convention is well-established across PADAS CAs.
2. **Fall back to file-based with explicit `--ca-identifier`.** Export the
   data model XML and pass the identifier directly:

   ```bash
   er-smart-sync datamodel \
     --from-file ~/datamodel.xml \
     --ca-identifier FOASF
   ```

## What the identifier flows into

The extracted identifier is used in three places:

1. **ER event-category `value`** (the slug) — `calculate_event_category_value(identifier)` produces e.g. `"foasf"` (lowercased, punctuation stripped).
2. **ER event-category `display`** — when there's a configurable model, the display is `f"{identifier} {cm_name}"`. With no CM, it's just `identifier`.
3. **Subject naming during patrol sync** — ranger subjects get suffixed with `f" ({identifier})"` so they're disambiguated across CAs.

The event-type `value` does **not** depend on the identifier — it's keyed by
CA UUID instead. So even if your CA label is malformed, event-type values
stay stable across CAs.

## Validation rules

The `--ca-identifier` CLI flag is validated client-side:

- **Length:** 2–30 characters.
- **Allowed characters:** `A-Z`, `a-z`, `0-9`, `_`, `-`.
- **Rejected:** spaces, dots, accents, anything outside the set above.

Examples:

| Input | Result |
|---|---|
| `FOASF` | ✅ accepted |
| `smart-import` | ✅ accepted |
| `FOO_BAR_99` | ✅ accepted |
| `f` | ❌ too short |
| `has spaces` | ❌ contains a space |
| `Côte` | ❌ contains a non-ASCII character |

## Related

- [Workflow: Push a data model](../workflows/push-datamodel.md)
- [Configuration](../getting-started/config.md)
