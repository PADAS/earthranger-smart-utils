# Screenshot slots

This document is an internal index of the screenshot placeholders embedded in
the documentation. Replace the listed `.png` files with real screenshots when
you're capturing them — the docs will pick them up automatically.

## Capture conventions

- **PNG format**, sized to roughly 1200×800 (Material renders responsively).
- **Crop tightly** to the relevant UI region. Don't include browser chrome
  unless it's contextually important.
- **Redact** any tenant-specific names/tokens that aren't part of the canonical
  example (`FOASF`, `JKPERU`, etc.).
- **Light theme** for consistency unless the screenshot is specifically about
  dark-mode behavior.

## Slots to fill

| Filename | Where it's used | What to capture |
|---|---|---|
| `er-event-category-list.png` | `workflows/push-datamodel.md` | EarthRanger admin → Event Categories list, with a freshly-pushed category at the top. |
| `er-event-type-with-choices.png` | `workflows/populate-choices.md` | EarthRanger admin → Event Types → one event type's detail view, showing a CHOICE_LIST field with a populated dropdown. |
| `smart-ca-label-bracketed.png` | `concepts/ca-identifier.md` | SMART Connect → Conservation Areas list, showing a CA whose name follows the `Foasf Reserve [FOASF]` convention. |
| `er-event-type-form-v2.png` | `concepts/event-type-version.md` | EarthRanger admin → Event Type detail view, showing the v2 form (`unevaluatedProperties`, ui sections). |

When you replace a placeholder, delete its row from this table.
