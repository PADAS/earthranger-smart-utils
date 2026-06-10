"""Copy an EarthRanger event type from one ER site to another.

ER → ER, not SMART → ER: takes two ERClients (source + destination) and
copies a single event type (by ``value``), bringing along the v2 choice
option-sets its schema references via ``$ref``. Attaches the copy to a
target category that must already exist on the destination.

Lives outside ERSmartSynchronizer because that class is built around one
ER + one SMART config; ER → ER needs two ER clients and no SMART client.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from dataclasses import field as _dc_field

from smartconnect.er_sync_utils import EREventType

from .choices import (
    ChoiceOption,
    ChoiceSet,
    ChoicesStats,
    _fetch_existing,
    upsert_choices,
)
from .smart_to_er_v2 import ERV2EventType
from .synchronizer import _retry

logger = logging.getLogger(__name__)

# Pulls the choice field out of a v2 $ref URL like
# ".../choices.json?field=et123_species" or "...?field=x&extra=1".
_CHOICE_REF_FIELD_RE = re.compile(r"[?&]field=([^&\"']+)")


class EventTypeNotFound(Exception):
    """Raised when the source has no event type with the requested value."""


class TargetCategoryMissing(Exception):
    """Raised when the target category does not exist on the destination."""


@dataclass
class CopyEventTypeStats:
    """Summary of one copy_event_type run."""

    event_type_action: str = ""  # "created" | "updated"
    choice_fields_copied: int = 0
    choices: ChoicesStats = _dc_field(default_factory=ChoicesStats)


def extract_choice_fields(schema: dict) -> list[str]:
    """Return the choice ``field`` names referenced by ``$ref`` URLs in a v2 schema.

    Walks the whole schema dict for ``$ref`` strings and extracts the
    ``field=`` query parameter from each. Deduplicates while preserving
    first-seen order. Non-choice ``$ref``s (e.g. ``#/definitions/...``) are
    ignored because they have no ``field=`` parameter.
    """
    found: list[str] = []

    def walk(node):
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "$ref" and isinstance(value, str):
                    match = _CHOICE_REF_FIELD_RE.search(value)
                    if match:
                        found.append(match.group(1))
                else:
                    walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(schema)

    seen: set[str] = set()
    result: list[str] = []
    for f in found:
        if f not in seen:
            seen.add(f)
            result.append(f)
    return result


def _source_choice_sets(*, source_client, fields: list[str]) -> list[ChoiceSet]:
    """Read each choice field from the source and build ChoiceSets.

    Reuses ``choices._fetch_existing`` (pagination + 400-as-empty handling).
    Records are ordered by their source ``ordernum`` so upsert_choices
    reassigns equivalent positions on the destination. Fields with no source
    records are skipped with a warning (the dest field will render empty).
    """
    sets: list[ChoiceSet] = []
    for field_name in fields:
        records = _fetch_existing(er_client=source_client, field=field_name)
        if not records:
            logger.warning(
                "Source has no choice records for field %r; the destination "
                "event type may render an empty dropdown for it.",
                field_name,
            )
            continue
        records = sorted(records, key=lambda r: r.get("ordernum") or 0)
        options = tuple(
            ChoiceOption(
                value=r["value"],
                display=r.get("display") or r["value"],
                is_active=bool(r.get("is_active", True)),
            )
            for r in records
        )
        sets.append(ChoiceSet(field=field_name, options=options))
    return sets


def _as_dict(raw) -> dict:
    """Normalize an ER schema blob to a dict.

    ER returns v2 schemas JSON-stringified on GET; mirror the synchronizer's
    handling. Returns {} for unparseable / unexpected shapes.
    """
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            logger.warning("Source schema is a non-JSON string; treating as empty.")
            return {}
    if isinstance(raw, dict):
        return raw
    return {}


def copy_event_type(
    *,
    source_client,
    dest_client,
    value: str,
    target_category: str,
    version: str = "v2",
    copy_choices: bool = True,
) -> CopyEventTypeStats:
    """Copy one event type (by ``value``) from source ER to destination ER.

    For v2, also copies the choice option-sets the schema references onto the
    destination (before writing the event type, so its $refs resolve). The
    target category must already exist on the destination. If an event type
    with the same value already exists on the destination it is patched;
    otherwise it is created.

    Raises:
        EventTypeNotFound: the source has no event type with ``value``.
        TargetCategoryMissing: ``target_category`` is absent on the destination.
    """
    # 1. Fetch the source event type.
    source_types = source_client.get_event_types(
        include_inactive=True, include_schema=True, version=version
    )
    src = next((t for t in source_types if t.get("value") == value), None)
    if src is None:
        raise EventTypeNotFound(
            f"No event type with value {value!r} found on the source "
            f"(version={version})."
        )

    # 2. Verify the target category exists on the destination.
    dest_categories = dest_client.get_event_categories()
    if not any(c.get("value") == target_category for c in dest_categories):
        available = sorted(c.get("value") for c in dest_categories if c.get("value"))
        raise TargetCategoryMissing(
            f"Target category {target_category!r} does not exist on the "
            f"destination. Available categories: {available}"
        )

    stats = CopyEventTypeStats()
    schema_dict = _as_dict(src.get("schema"))

    # 3. Copy referenced choices (v2 only — v1 embeds enums inline).
    if copy_choices and version == "v2":
        fields = extract_choice_fields(schema_dict)
        if fields:
            choice_sets = _source_choice_sets(
                source_client=source_client, fields=fields
            )
            stats.choice_fields_copied = len(choice_sets)
            stats.choices = upsert_choices(
                er_client=dest_client, choice_sets=choice_sets
            )

    # 4. Reconstruct and write the event type with the overridden category.
    dest_types = dest_client.get_event_types(
        include_inactive=True, include_schema=True, version=version
    )
    existing_dest = next((t for t in dest_types if t.get("value") == value), None)

    if version == "v2":
        event_type = ERV2EventType(
            value=src["value"],
            display=src["display"],
            category=target_category,
            is_active=bool(src.get("is_active", True)),
            readonly=bool(src.get("readonly", False)),
            schema=schema_dict,
        )
    else:
        # EREventType.allow_population_by_field_name is False, so pass the
        # alias "schema" (a JSON string) rather than event_schema.
        event_type = EREventType(
            value=src["value"],
            display=src["display"],
            category=target_category,
            is_active=bool(src.get("is_active", True)),
            **{"schema": src.get("schema")},
        )

    if existing_dest is not None:
        event_type.id = existing_dest.get("id")
        _retry(
            dest_client.patch_event_type,
            event_type=event_type.dict(by_alias=True, exclude_none=True),
            version=version,
        )
        stats.event_type_action = "updated"
    else:
        _retry(
            dest_client.post_event_type,
            event_type=event_type.dict(by_alias=True, exclude_none=True),
            version=version,
        )
        stats.event_type_action = "created"

    return stats
