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

        attribute_configs = cm.get("attributes") if cm else None

        for cat_attr in leaf_attrs:
            attribute = next(
                (a for a in attributes if a.key == cat_attr.key), None,
            )
            if attribute is None or attribute.type not in _CHOICE_TYPES:
                continue
            options = list(attribute.options or [])
            if not options:
                continue

            options_cfg = _options_config_for(attribute_configs, cat_attr.key)
            if options_cfg is not None:
                choice_options = _options_from_cm_config(options, options_cfg)
            else:
                if attribute.type == "TREE":
                    options = _leaf_options(options)
                choice_options = tuple(
                    ChoiceOption(
                        value=sanitize_choice_value(o.key),
                        display=o.display,
                        is_active=True,
                    )
                    for o in options
                )

            if not choice_options:
                continue

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


def _options_config_for(
    attribute_configs: list | None, key: str
) -> list | None:
    if not attribute_configs:
        return None
    cfg = next((c for c in attribute_configs if c.get("key") == key), None)
    return cfg.get("options") if cfg else None


def _options_from_cm_config(
    options: list, options_config: list
) -> tuple[ChoiceOption, ...]:
    """Build ChoiceOptions in CM order. Options the CM marks isActive=False
    appear with is_active=False; options the CM omits entirely are dropped."""
    by_key = {o.key: o for o in options}
    result: list[ChoiceOption] = []
    for opt_cfg in options_config:
        key = opt_cfg.get("key")
        if not key:
            continue
        original = by_key.get(key)
        if not original:
            logger.warning("CM references unknown option key %s", key)
            continue
        result.append(
            ChoiceOption(
                value=sanitize_choice_value(original.key),
                display=original.display,
                is_active=bool(opt_cfg.get("isActive")),
            )
        )
    return tuple(result)


def _leaf_options(options: list) -> list:
    """For TREE option sets: keep only leaves (no children)."""
    keys = [o.key for o in options]
    return [o for o in options if _is_leaf_node(keys, o.key)]


# ER's Choice DB model is shared across content types; for event types we
# always POST with model="activity.event" (the serializer default).
_CHOICE_MODEL = "activity.event"

# Path prefix for the choices REST endpoint. Versionless in ER.
_CHOICES_PATH = "choices"


def upsert_choices(
    *,
    er_client,
    choice_sets: list[ChoiceSet],
) -> ChoicesStats:
    """Upsert each ChoiceSet against ER's Choices API.

    Returns a ChoicesStats counter dataclass; failures are logged and
    counted but do not raise. Per-set processing is independent; an error
    in one ChoiceSet does not block subsequent sets.
    """
    stats = ChoicesStats()
    seen_fields: dict[str, ChoiceSet] = {}

    for cs in choice_sets:
        # Deduplicate: same field, identical options is fine; same field,
        # different options is a builder bug.
        if cs.field in seen_fields:
            if seen_fields[cs.field].options != cs.options:
                raise ValueError(
                    f"ChoiceSet field {cs.field!r} appears twice with "
                    f"different options; this is a builder bug."
                )
            continue
        seen_fields[cs.field] = cs

        try:
            _upsert_one_set(er_client=er_client, cs=cs, stats=stats)
        except Exception:
            logger.exception(
                "Failed to upsert ChoiceSet",
                extra=dict(field=cs.field),
            )
            stats.errored += len(cs.options)

    return stats


def _upsert_one_set(*, er_client, cs: ChoiceSet, stats: ChoicesStats) -> None:
    existing = _fetch_existing(er_client=er_client, field=cs.field)
    existing_by_value: dict[str, dict] = {r["value"]: r for r in existing}

    for ordernum, planned in enumerate(cs.options):
        existing_record = existing_by_value.get(planned.value)
        if existing_record is None:
            _create_choice(
                er_client=er_client,
                cs_field=cs.field,
                option=planned,
                ordernum=ordernum,
                stats=stats,
            )
        else:
            _maybe_patch_choice(
                er_client=er_client,
                existing=existing_record,
                planned=planned,
                ordernum=ordernum,
                stats=stats,
            )


def _fetch_existing(*, er_client, field: str) -> list[dict]:
    """List all existing Choice records for (model=activity.event, field=...).
    Follows pagination via the DRF `next` URL."""
    results: list[dict] = []
    page = er_client._get(
        path=_CHOICES_PATH,
        params={
            "model": _CHOICE_MODEL,
            "field": field,
            "include_inactive": True,
            "page_size": 200,
        },
    )
    while True:
        if isinstance(page, dict) and "results" in page:
            results.extend(page["results"])
            next_url = page.get("next")
            if not next_url:
                break
            page = er_client._get(path=next_url)
        elif isinstance(page, list):
            results.extend(page)
            break
        else:
            break
    return results


def _create_choice(
    *,
    er_client,
    cs_field: str,
    option: ChoiceOption,
    ordernum: int,
    stats: ChoicesStats,
) -> None:
    payload = {
        "model": _CHOICE_MODEL,
        "field": cs_field,
        "value": option.value,
        "display": option.display,
        "ordernum": ordernum,
        "is_active": option.is_active,
    }
    try:
        er_client._post(path=_CHOICES_PATH, payload=payload)
        stats.created += 1
    except Exception as e:
        logger.exception(
            "Failed to POST choice",
            extra=dict(field=cs_field, value=option.value, error=str(e)),
        )
        stats.errored += 1


def _maybe_patch_choice(
    *,
    er_client,
    existing: dict,
    planned: ChoiceOption,
    ordernum: int,
    stats: ChoicesStats,
) -> None:
    changes: dict = {}
    if existing.get("display") != planned.display:
        changes["display"] = planned.display
    if existing.get("ordernum") != ordernum:
        changes["ordernum"] = ordernum
    if existing.get("is_active") != planned.is_active:
        changes["is_active"] = planned.is_active

    if not changes:
        stats.unchanged += 1
        return

    is_deactivation = (
        "is_active" in changes
        and existing.get("is_active") is True
        and planned.is_active is False
    )

    path = f"{_CHOICES_PATH}/{existing['id']}"
    try:
        er_client._patch(path=path, payload=changes)
    except Exception as e:
        logger.exception(
            "Failed to PATCH choice",
            extra=dict(id=existing.get("id"), error=str(e)),
        )
        stats.errored += 1
        return

    if is_deactivation:
        stats.deactivated += 1
    else:
        stats.updated += 1
