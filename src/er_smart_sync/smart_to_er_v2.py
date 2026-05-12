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


SCALAR_JSON: dict[str, dict[str, Any]] = {
    "TEXT": {"type": "string"},
    "NUMERIC": {"type": "number"},
    "BOOLEAN": {"type": "boolean"},
    "DATE": {"type": "string", "format": "date"},
    "TIME": {"type": "string", "format": "time"},
    "DATETIME": {"type": "string", "format": "date-time"},
    "ATTACHMENT": {"type": "string", "format": "uri"},
}

SCALAR_UI: dict[str, dict[str, Any]] = {
    "TEXT": {"type": "TEXT", "inputType": "SHORT_TEXT"},
    "NUMERIC": {"type": "NUMBER"},
    "BOOLEAN": {"type": "BOOLEAN"},
    "DATE": {"type": "TEXT", "inputType": "DATE"},
    "TIME": {"type": "TEXT", "inputType": "TIME"},
    "DATETIME": {"type": "TEXT", "inputType": "DATETIME"},
    "ATTACHMENT": {
        "type": "ATTACHMENT",
        "allowableFileTypes": ["image", "document", "video", "audio"],
    },
}


def build_event_types_v2(
    *,
    dm: dict,
    cm: dict | None = None,
    ca_uuid: str,
    ca_identifier: str,
) -> list[ERV2EventType]:
    """Build ERV2EventType records for a SMART CA (optionally with a CM overlay)."""
    del ca_identifier  # reserved; parity with v1 signature

    source = cm if cm else dm
    cats = parse_obj_as(list[Category], source.get("categories") or [])
    cat_paths = [cat.path for cat in cats]
    attributes = parse_obj_as(list[Attribute], dm.get("attributes") or [])
    attribute_configs = cm.get("attributes") if cm else None

    event_types: list[ERV2EventType] = []
    for cat in cats:
        et = _build_one(
            cat=cat,
            cats=cats,
            cat_paths=cat_paths,
            attributes=attributes,
            attribute_configs=attribute_configs,
            ca_uuid=ca_uuid,
            cm=cm,
        )
        if et is not None:
            event_types.append(et)
    return event_types


def _build_one(
    *,
    cat: Category,
    cats: list[Category],
    cat_paths: list[str],
    attributes: list[Attribute],
    attribute_configs: list | None,
    ca_uuid: str,
    cm: dict | None,
) -> ERV2EventType | None:
    is_leaf = _is_leaf_node(cat_paths, cat.path)
    is_active = bool(cm) or (cat.is_active and is_leaf)

    path_components = cat.hkeyPath.split(".") if cm else cat.path.split(".")
    value_suffix = "_".join(path_components)
    if cm:
        value = f'{ca_uuid}_{cm["cm_uuid"]}_{value_suffix}'
    else:
        value = f"{ca_uuid}_{value_suffix}"
    value = value.lower()

    et = ERV2EventType(value=value, display=cat.display, is_active=is_active)
    if not is_active:
        return et

    leaf_attributes = list(cat.attributes)
    if not cm:
        leaf_attributes.extend(_get_inherited_attributes(cats, path_components))
    if not leaf_attributes:
        logger.warning(
            "Skipping v2 event type, no leaf attributes",
            extra=dict(value=value, display=cat.display),
        )
        return None

    properties, ui_fields, field_order = _build_field_blocks(
        attributes=attributes,
        leaf_attributes=leaf_attributes,
        is_multiple=cat.is_multiple,
        attribute_configs=attribute_configs,
    )
    if not properties:
        logger.warning(
            "Skipping v2 event type, no schema properties",
            extra=dict(value=value, display=cat.display),
        )
        return None

    et.event_schema = {
        "json": {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "additionalProperties": False,
            "properties": properties,
            "required": [],
        },
        "ui": {
            "fields": ui_fields,
            "sections": {
                "section-1": {
                    "label": "Details",
                    "columns": 1,
                    "isActive": True,
                    "leftColumn": [
                        {"name": k, "type": "field"} for k in field_order
                    ],
                }
            },
            "order": ["section-1"],
        },
    }
    return et


def _build_field_blocks(
    *,
    attributes: list[Attribute],
    leaf_attributes: list[CategoryAttribute],
    is_multiple: bool,
    attribute_configs: list | None,
) -> tuple[dict, dict, list[str]]:
    """Return (json.properties, ui.fields, field_order_for_section)."""
    properties: dict[str, dict] = {}
    ui_fields: dict[str, dict] = {}
    order: list[str] = []

    for cat_attr in leaf_attributes:
        key = cat_attr.key
        attribute = next((a for a in attributes if a.key == key), None)
        if attribute is None:
            logger.warning("Attribute %s not found in dm.attributes", key)
            continue

        smart_type = attribute.type
        if smart_type not in SCALAR_JSON:
            # Choice and tree types arrive in a later task; skip for now.
            continue

        json_prop = dict(SCALAR_JSON[smart_type])
        json_prop["title"] = attribute.display
        ui_field = dict(SCALAR_UI[smart_type])

        properties[key] = json_prop
        ui_fields[key] = ui_field
        order.append(key)

    return properties, ui_fields, order


def _is_leaf_node(node_paths: list[str], cur_node: str) -> bool:
    prefix = f"{cur_node}."
    return not any(p.startswith(prefix) for p in node_paths)


def _get_inherited_attributes(
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
