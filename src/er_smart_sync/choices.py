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
import logging
import re
from dataclasses import dataclass

from pydantic import parse_obj_as
from smartconnect.models import Attribute, Category, CategoryAttribute

logger = logging.getLogger(__name__)


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


@dataclass(frozen=True)
class ChoiceOption:
    """A single option in a choice set, with its activity flag."""

    value: str
    display: str
    is_active: bool = True


@dataclass(frozen=True)
class ChoiceSet:
    """The plan for one ER ``Choice.field`` worth of records."""

    field: str
    options: tuple[ChoiceOption, ...]


@dataclass
class ChoicesStats:
    """Per-run counters for the choices upsert phase."""

    created: int = 0
    updated: int = 0
    unchanged: int = 0
    deactivated: int = 0
    errored: int = 0


# Attribute types that bear choice options. Other types (TEXT, NUMERIC, etc.)
# never produce ChoiceSets.
_CHOICE_TYPES = {"LIST", "MLIST", "TREE"}


def build_choice_sets(
    *,
    dm: dict,
    cm: dict | None = None,
    ca_uuid: str,
) -> list[ChoiceSet]:
    """Walk a SMART data model and emit one ChoiceSet per (event_type, choice attr).

    Mirrors the structure of ``smart_to_er_v2.build_event_types_v2`` so that
    field names line up byte-for-byte. Does not produce event types; only
    the choices plan.
    """
    source = cm if cm else dm
    cats = parse_obj_as(list[Category], source.get("categories") or [])
    cat_paths = [cat.path for cat in cats]
    attributes = parse_obj_as(list[Attribute], dm.get("attributes") or [])

    result: list[ChoiceSet] = []
    for cat in cats:
        # Only leaf-or-CM-driven categories emit event types; only those need choices.
        is_leaf = _is_leaf_node(cat_paths, cat.path)
        is_active = bool(cm) or (cat.is_active and is_leaf)
        if not is_active:
            continue

        # Compute the same event_type_value the v2 builder will use.
        path_for_value = cat.hkeyPath if cm else cat.path
        et_value = event_type_value_for(
            category_path=path_for_value, ca_uuid=ca_uuid, cm=cm,
        )

        # Collect attributes from this category plus inherited (non-CM only).
        path_components = path_for_value.split(".")
        leaf_attrs = list(cat.attributes)
        if not cm:
            leaf_attrs.extend(_inherited_attributes(cats, path_components))

        for cat_attr in leaf_attrs:
            attribute = next(
                (a for a in attributes if a.key == cat_attr.key), None,
            )
            if attribute is None or attribute.type not in _CHOICE_TYPES:
                continue
            options = list(attribute.options or [])
            if not options:
                continue
            choice_options = tuple(
                ChoiceOption(
                    value=sanitize_choice_value(o.key),
                    display=o.display,
                    is_active=True,
                )
                for o in options
            )
            result.append(
                ChoiceSet(
                    field=derive_choice_field(et_value, cat_attr.key),
                    options=choice_options,
                )
            )

    return result


def _is_leaf_node(node_paths: list[str], cur_node: str) -> bool:
    prefix = f"{cur_node}."
    return not any(p.startswith(prefix) for p in node_paths)


def _inherited_attributes(
    cats: list[Category], path_components: list[str]
) -> list[CategoryAttribute]:
    inherited: list[CategoryAttribute] = []
    parent_path = ""
    for component in path_components[:-1]:
        parent_path = component if not parent_path else f"{parent_path}.{component}"
        parent_cat = next((c for c in cats if c.path == parent_path), None)
        if parent_cat:
            inherited.extend(parent_cat.attributes)
    return inherited
