"""SMART → EarthRanger Choice records.

Owns the choices layer required by ER v2 event types:

- Pure helpers: ``sanitize_choice_value``, ``derive_choice_field``,
  ``event_type_value_for``.
- Plan-record dataclasses: ``ChoiceOption``, ``ChoiceSet``, ``ChoicesStats``.
- DM walker: ``build_choice_sets``.
- Upsert algorithm: ``upsert_choices``.

See ``docs/superpowers/specs/2026-05-13-er-v2-choices-population-design.md``
for full design rationale.
"""

from __future__ import annotations

import hashlib
import re


def sanitize_choice_value(option_key: str) -> str:
    """Map a SMART option key to a ``^\\w+$`` string.

    SMART keys may contain ``.`` (TREE leaf paths), accents, apostrophes,
    spaces. The Choice DB column requires letters/digits/underscores only.

    This rule is **load-bearing**: changing it later requires backfilling
    historical event records that store the resolved value string.
    """
    sanitized = re.sub(r"[^A-Za-z0-9]+", "_", option_key).strip("_").lower()
    return sanitized or "_"


def derive_choice_field(event_type_value: str, attr_key: str) -> str:
    """Derive a stable Choice.field name.

    Returns ``et{8hex}_{sanitized_attr_key}``. Total length ≤ 40 chars
    (truncated if needed; collisions vanishingly rare since SMART attr keys
    are well under 28 chars in practice).
    """
    digest = hashlib.sha256(event_type_value.encode("utf-8")).hexdigest()[:8]
    sanitized = sanitize_choice_value(attr_key)
    field = f"et{digest}_{sanitized}"
    if len(field) > 40:
        field = field[:40]
    return field


def event_type_value_for(
    *,
    category_path: str,
    ca_uuid: str,
    cm: dict | None,
) -> str:
    """Compute the event-type ``value`` string.

    Mirrors the scheme used by ``smart_to_er_v2._build_one``:

    - Without CM: ``{ca_uuid}_{path_underscored}`` lowercased.
    - With CM:    ``{ca_uuid}_{cm_uuid}_{path_underscored}`` lowercased.

    Caller passes ``category_path`` already resolved (``cat.path`` when no CM,
    ``cat.hkeyPath`` when CM is present — same rule the existing builder
    uses).
    """
    path_underscored = category_path.replace(".", "_")
    if cm:
        value = f"{ca_uuid}_{cm['cm_uuid']}_{path_underscored}"
    else:
        value = f"{ca_uuid}_{path_underscored}"
    return value.lower()
