"""SMART → EarthRanger v2 data-model conversion.

Emits ER v2-shape event types (JSON Schema 2020-12 envelope with ``json`` and
``ui`` sections, category as slug string, top-level ``readonly`` flag).

Parallel to ``smart_to_er.py`` which owns v1 conversion. Builder selection
happens in ``synchronizer.ERSmartSynchronizer`` based on
``EarthRangerConfig.event_type_version``.
"""

from __future__ import annotations

import logging
from typing import Any

import pydantic
from pydantic import BaseModel, Field, parse_obj_as
from smartconnect.models import Attribute, Category, CategoryAttribute

logger = logging.getLogger(__name__)


class ERV2EventType(BaseModel):
    """Wire model for an ER v2 event-type POST/PATCH payload.

    Mirrors fields documented in the v2 spec (`docs/superpowers/specs/...`):
    category is a slug, schema is a dict (not stringified), readonly is a
    top-level field.
    """

    id: pydantic.UUID4 | None = None
    value: str
    display: str
    category: str | None = None
    is_active: bool = True
    readonly: bool = False
    event_schema: dict | None = Field(None, alias="schema")

    class Config:
        allow_population_by_field_name = True


def build_event_types_v2(
    *,
    dm: dict,
    cm: dict | None = None,
    ca_uuid: str,
    ca_identifier: str,
) -> list[ERV2EventType]:
    """Build ERV2EventType records for a SMART CA (optionally with a configurable-model overlay)."""
    del ca_identifier  # reserved; parity with v1 signature

    source = cm if cm else dm
    cats = parse_obj_as(list[Category], source.get("categories") or [])
    if not cats:
        return []

    # Subsequent tasks fill this in — for now return early.
    return []
