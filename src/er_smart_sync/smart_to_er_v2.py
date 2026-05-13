"""SMART → EarthRanger v2 data-model conversion.

Emits ER v2-shape event types (JSON Schema 2020-12 envelope with ``json`` and
``ui`` sections, category as slug string, top-level ``readonly`` flag).

Parallel to ``smart_to_er.py`` which owns v1 conversion. Builder selection
happens in ``synchronizer.ERSmartSynchronizer`` based on
``EarthRangerConfig.event_type_version``.
"""

from __future__ import annotations

import copy
import logging
from collections.abc import Sequence
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
    "TEXT": {"type": "string", "description": ""},
    "NUMERIC": {"type": "number", "description": ""},
    "BOOLEAN": {"type": "boolean", "description": ""},
    "DATE": {"type": "string", "format": "date", "description": ""},
    "TIME": {"type": "string", "format": "time", "description": ""},
    "DATETIME": {"type": "string", "format": "date-time", "description": ""},
    "ATTACHMENT": {"type": "string", "format": "uri", "description": ""},
}

SCALAR_UI: dict[str, dict[str, Any]] = {
    "TEXT": {"type": "TEXT", "inputType": "SHORT_TEXT", "parent": "section-1"},
    "NUMERIC": {"type": "NUMERIC", "parent": "section-1"},
    "BOOLEAN": {"type": "BOOLEAN", "parent": "section-1"},
    "DATE": {"type": "DATE_TIME", "parent": "section-1"},
    "TIME": {"type": "DATE_TIME", "parent": "section-1"},
    "DATETIME": {"type": "DATE_TIME", "parent": "section-1"},
    "ATTACHMENT": {
        "type": "ATTACHMENT",
        "allowableFileTypes": ["image", "document", "video", "audio"],
        "parent": "section-1",
    },
}


def build_event_types_v2(
    *,
    dm: dict,
    cm: dict | None = None,
    ca_uuid: str,
    ca_identifier: str,
    choices_base_url: str = "/api/v2.0/schemas",
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
            choices_base_url=choices_base_url,
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
    choices_base_url: str = "/api/v2.0/schemas",
) -> ERV2EventType | None:
    is_leaf = _is_leaf_node(cat_paths, cat.path)
    is_active = bool(cm) or (cat.is_active and is_leaf)

    hkey = cat.hkeyPath or cat.path or ""
    path_components = hkey.split(".") if cm else (cat.path or "").split(".")
    value_suffix = "_".join(path_components)
    if cm:
        value = f"{ca_uuid}_{cm['cm_uuid']}_{value_suffix}"
    else:
        value = f"{ca_uuid}_{value_suffix}"
    value = value.lower()

    et = ERV2EventType(value=value, display=cat.display, is_active=bool(is_active))
    if not is_active:
        return et

    leaf_attributes = list(cat.attributes or [])
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
        is_multiple=bool(cat.is_multiple),
        attribute_configs=attribute_configs,
        choices_base_url=choices_base_url,
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
            "unevaluatedProperties": False,
            "properties": properties,
            "required": [],
        },
        "ui": {
            "fields": ui_fields,
            "headers": {},
            "order": ["section-1"],
            "sections": {
                "section-1": {
                    "label": "Details",
                    "columns": 1,
                    "isActive": True,
                    "leftColumn": [{"name": k, "type": "field"} for k in field_order],
                    "rightColumn": [],
                }
            },
        },
    }
    return et


def _build_field_blocks(
    *,
    attributes: list[Attribute],
    leaf_attributes: list[CategoryAttribute],
    is_multiple: bool,
    attribute_configs: list | None,
    choices_base_url: str = "/api/v2.0/schemas",
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
        options = list(attribute.options or [])
        options_cfg = _options_config_for(attribute_configs, key)

        if options:
            if options_cfg is not None:
                options = _filter_options_by_config(options, options_cfg)
            elif smart_type == "TREE":
                options = _leaf_options(options)
            # else: keep all options as-is for LIST/MLIST

        json_prop, ui_field = _build_property_pair(
            smart_type=smart_type,
            display=attribute.display,
            options=options,
            is_multiple=is_multiple,
            choices_base_url=choices_base_url,
        )
        if json_prop is None or ui_field is None:
            continue

        json_prop["deprecated"] = not cat_attr.is_active

        properties[key] = json_prop
        ui_fields[key] = ui_field
        order.append(key)

    return properties, ui_fields, order


def _build_property_pair(
    *,
    smart_type: str,
    display: str,
    options: list,
    is_multiple: bool,
    choices_base_url: str = "/api/v2.0/schemas",
) -> tuple[dict | None, dict | None]:
    """Return (json_property, ui_field) or (None, None) to skip."""
    if smart_type in SCALAR_JSON and not options:
        return (
            {**copy.deepcopy(SCALAR_JSON[smart_type]), "title": display},
            copy.deepcopy(SCALAR_UI[smart_type]),
        )

    if not options:
        if smart_type in {"LIST", "MLIST", "TREE"}:
            logger.warning(
                "All options filtered out for %r choice; emitting string",
                smart_type,
            )
        else:
            logger.warning("Unknown SMART type %r; emitting string", smart_type)
        return (
            {"type": "string", "title": display},
            {"type": "TEXT", "inputType": "SHORT_TEXT", "parent": "section-1"},
        )

    keys = [o.key for o in options]
    choices = {o.key: o.display for o in options}
    is_array = smart_type == "MLIST" or (smart_type == "LIST" and is_multiple)

    if is_array:
        json_prop = {
            "type": "array",
            "title": display,
            "items": {"type": "string", "enum": keys},
        }
        ui_field = {
            "type": "CHOICE_LIST",
            "inputType": "CHECKBOX",
            "choices": choices,
            "parent": "section-1",
        }
    else:
        json_prop = {
            "type": "string",
            "title": display,
            "enum": keys,
        }
        ui_field = {
            "type": "CHOICE_LIST",
            "inputType": "DROPDOWN",
            "choices": choices,
            "parent": "section-1",
        }
    return json_prop, ui_field


def _options_config_for(attribute_configs: list | None, key: str) -> list | None:
    if not attribute_configs:
        return None
    cfg = next((c for c in attribute_configs if c.get("key") == key), None)
    return cfg.get("options") if cfg else None


def _filter_options_by_config(options: list, options_config: list) -> list:
    """Keep options the configurable-model overlay marks active, preserving CM order."""
    kept = []
    for opt_cfg in options_config:
        key = opt_cfg.get("key")
        if not key or not opt_cfg.get("isActive"):
            continue
        match = next((o for o in options if o.key == key), None)
        if match:
            kept.append(match)
        else:
            logger.warning("No option found for config key %s", key)
    return kept


def _leaf_options(options: list) -> list:
    """Filter to leaf options only (for TREE-shaped option sets)."""
    keys = [o.key for o in options]
    return [o for o in options if _is_leaf_node(keys, o.key)]


def _is_leaf_node(node_paths: list[str], cur_node: str) -> bool:
    prefix = f"{cur_node}."
    return not any(p.startswith(prefix) for p in node_paths)


def _get_inherited_attributes(
    cats: list[Category], path_components: Sequence[str]
) -> list[CategoryAttribute]:
    inherited: list[CategoryAttribute] = []
    parent_path = ""
    for component in path_components[:-1]:
        parent_path = component if not parent_path else f"{parent_path}.{component}"
        parent_cat = next((c for c in cats if c.path == parent_path), None)
        if parent_cat:
            inherited.extend(parent_cat.attributes or [])
    return inherited
