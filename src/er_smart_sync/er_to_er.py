"""Copy an EarthRanger event type from one ER site to another.

ER → ER, not SMART → ER: takes two ERClients (source + destination) and
copies a single event type (by ``value``), bringing along the v2 choice
option-sets its schema references via ``$ref``. Attaches the copy to a
target category that must already exist on the destination.

Lives outside ERSmartSynchronizer because that class is built around one
ER + one SMART config; ER → ER needs two ER clients and no SMART client.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from dataclasses import field as _dc_field

from .choices import ChoicesStats

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


def copy_event_type(*args, **kwargs):
    raise NotImplementedError  # implemented in Task 3
