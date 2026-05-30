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

from .choices import (
    _variant_disambiguator,
    derive_choice_field,
    event_type_value_for,
    sanitize_choice_value,
)

logger = logging.getLogger(__name__)


def _group_by_hkey(cats: list[Category]) -> dict[str, list[Category]]:
    """Group categories by hkeyPath (falls back to path). Preserves first-seen
    order of both keys and members so builder output is deterministic."""
    groups: dict[str, list[Category]] = {}
    for cat in cats:
        key = cat.hkeyPath or cat.path or ""
        groups.setdefault(key, []).append(cat)
    return groups



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
        "allowableFileTypes": ["audio", "document", "image", "video"],
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
    cm_variant_mode: str = "split",
) -> list[ERV2EventType]:
    """Build ERV2EventType records for a SMART CA (optionally with a CM overlay)."""
    del ca_identifier  # reserved; parity with v1 signature

    source = cm if cm else dm
    cats = parse_obj_as(list[Category], source.get("categories") or [])
    cat_paths = [cat.path for cat in cats]
    attributes = parse_obj_as(list[Attribute], dm.get("attributes") or [])
    attribute_configs = cm.get("attributes") if cm else None

    common = dict(
        cats=cats,
        cat_paths=cat_paths,
        attributes=attributes,
        attribute_configs=attribute_configs,
        ca_uuid=ca_uuid,
        cm=cm,
        choices_base_url=choices_base_url,
    )

    event_types: list[ERV2EventType] = []
    for _hkey, group in _group_by_hkey(cats).items():
        if len(group) == 1:
            et = _build_one(cat=group[0], **common)
            if et is not None:
                event_types.append(et)
        elif cm_variant_mode == "consolidate":
            et = _build_consolidated(group=group, **common)
            if et is not None:
                event_types.append(et)
        else:  # split
            for cat in group:
                et = _build_one(cat=cat, value_disambiguator=_variant_disambiguator(cat), **common)
                if et is not None:
                    event_types.append(et)
    return event_types


DISCRIMINATOR_SECTION_ID = "section-1"


def _build_consolidated(
    *,
    group: list[Category],
    cats: list[Category],
    cat_paths: list[str],
    attributes: list[Attribute],
    attribute_configs: list | None,
    ca_uuid: str,
    cm: dict | None,
    choices_base_url: str = "/api/v2.0/schemas",
) -> ERV2EventType | None:
    """Build a single consolidated event type for a group of CM variant categories.

    Emits one event type whose schema has:
    - section-1: the always-visible discriminator field (a CHOICE_LIST that
      lets the user pick which variant applies).
    - section-{i} (i >= 2): one conditional section per variant, each carrying
      that variant's attributes and an IS_EXACTLY condition on the discriminator
      that hides it when its variant is not selected.

    Variant attribute keys are namespaced as ``{section_id}_{attr_key}``
    (with hyphens replaced by underscores so the result satisfies JSON Schema's
    ``^\\w+$``-flavored property-name rules).  This avoids silent overwrite when
    two variants share an attribute key (a common SMART pattern).
    """
    rep = group[0]
    hkey = rep.hkeyPath or rep.path or ""
    # Use event_type_value_for so the consolidated value matches the
    # discriminator ChoiceSet's value exactly (Task 10) — same field name.
    value = event_type_value_for(category_path=hkey, ca_uuid=ca_uuid, cm=cm)
    display = hkey.split(".")[-1].replace("_", " ").title()

    discriminator = derive_choice_field(value, "variant")

    properties: dict = {}
    ui_fields: dict = {}
    sections: dict = {}
    order: list[str] = [DISCRIMINATOR_SECTION_ID]

    # Discriminator: a single-select CHOICE_LIST. _build_choice_property_pair
    # derives the field name internally as derive_choice_field(value, "variant")
    # — identical to `discriminator` above — and sets parent="section-1", which
    # is exactly where the discriminator lives. Its options resolve at query
    # time from the ChoiceSet emitted in Task 10.
    disc_prop, disc_ui = _build_choice_property_pair(
        smart_type="LIST",
        display="Variant",
        is_multiple=False,
        attr_key="variant",
        choices_base_url=choices_base_url,
        event_type_value=value,
    )
    properties[discriminator] = disc_prop

    variant_section_ids: list[str] = []
    for i, cat in enumerate(group, start=2):
        section_id = f"section-{i}"
        # Namespace prefix: replace hyphens so the result is \w+-safe.
        ns_prefix = section_id.replace("-", "_")
        variant_section_ids.append(section_id)
        order.append(section_id)
        leaf_attributes = list(cat.attributes or [])
        props, fields, field_order = _build_field_blocks(
            attributes=attributes,
            leaf_attributes=leaf_attributes,
            is_multiple=bool(cat.is_multiple),
            attribute_configs=attribute_configs,
            choices_base_url=choices_base_url,
            event_type_value=value,
        )
        # Namespace every variant attribute key to avoid collisions when two
        # variants share the same attribute.  Also re-parent ui fields to the
        # variant's own section (away from the default "section-1") so rjsf
        # hides them correctly via the IS_EXACTLY condition.
        namespaced_field_order: list[str] = []
        for orig_key in field_order:
            ns_key = f"{ns_prefix}_{orig_key}"
            properties[ns_key] = props[orig_key]
            ui_fields[ns_key] = fields[orig_key]
            ui_fields[ns_key]["parent"] = section_id
            namespaced_field_order.append(ns_key)

        sections[section_id] = {
            "label": cat.display,
            "columns": 1,
            "isActive": True,
            "leftColumn": [{"name": k, "type": "field"} for k in namespaced_field_order],
            "rightColumn": [],
            "conditions": [{
                "field": discriminator,
                "id": f"condition-{i}",
                "operator": "IS_EXACTLY",
                "value": sanitize_choice_value(cat.display),
            }],
        }

    # Discriminator UI field: always-visible section-1; depends on variant sections.
    disc_ui["conditionalDependents"] = variant_section_ids
    ui_fields[discriminator] = disc_ui
    sections[DISCRIMINATOR_SECTION_ID] = {
        "label": display,
        "columns": 1,
        "isActive": True,
        "leftColumn": [{"name": discriminator, "type": "field"}],
        "rightColumn": [],
        "conditions": [],
    }

    if len(properties) <= 1:
        # Only the discriminator was added — no variant produced any fields.
        return None

    et = ERV2EventType(value=value, display=display, is_active=True)
    et.event_schema = {
        "json": {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "unevaluatedProperties": False,
            "properties": properties,
            "required": [discriminator],
        },
        "ui": {
            "fields": ui_fields,
            "headers": {},
            "order": order,
            "sections": sections,
        },
    }
    return et


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
    value_disambiguator: str | None = None,
) -> ERV2EventType | None:
    is_leaf = _is_leaf_node(cat_paths, cat.path)
    is_active = bool(cm) or (cat.is_active and is_leaf)

    if not is_active:
        # v2 has no equivalent of v1's "inactive event type" record — there
        # is no schema-less POST shape that passes the meta-schema. Skip.
        return None

    hkey = cat.hkeyPath or cat.path or ""
    path_components = hkey.split(".") if cm else (cat.path or "").split(".")
    value_suffix = "_".join(path_components)
    if cm:
        value = f"{ca_uuid}_{cm['cm_uuid']}_{value_suffix}"
    else:
        value = f"{ca_uuid}_{value_suffix}"
    if value_disambiguator:
        value = f"{value}_{value_disambiguator}"
    value = value.lower()

    # Pass event_type_value down so choice properties can derive their
    # Choice.field $ref URL.
    et_value = value

    et = ERV2EventType(value=value, display=cat.display, is_active=True)

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
        event_type_value=et_value,
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
    event_type_value: str = "",
) -> tuple[dict, dict, list[str]]:
    """Return (json.properties, ui.fields, field_order_for_section).

    Note: ``attribute_configs`` is accepted but no longer consulted here.
    The choices module owns CM-overlay handling (filtering, deactivation,
    TREE flattening); the v2 builder emits CHOICE_LIST schemas keyed on
    the SMART type alone and references the choices via ``$ref``.
    """
    del attribute_configs  # CM overlay is the choices module's concern
    properties: dict[str, dict] = {}
    ui_fields: dict[str, dict] = {}
    order: list[str] = []

    for cat_attr in leaf_attributes:
        key = cat_attr.key
        attribute = next((a for a in attributes if a.key == key), None)
        if attribute is None:
            logger.warning("Attribute %s not found in dm.attributes", key)
            continue

        json_prop, ui_field = _build_property_pair(
            smart_type=attribute.type,
            display=attribute.display,
            is_multiple=is_multiple,
            attr_key=cat_attr.key,
            choices_base_url=choices_base_url,
            event_type_value=event_type_value,
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
    is_multiple: bool,
    attr_key: str = "",
    choices_base_url: str = "/api/v2.0/schemas",
    event_type_value: str = "",
) -> tuple[dict | None, dict | None]:
    """Return (json_property, ui_field) or (None, None) to skip.

    Discriminates on ``smart_type`` alone, not on options content. Choice-
    bearing SMART types (LIST/MLIST/TREE) always emit a ``CHOICE_LIST`` with
    a ``$ref`` URL — even if the CM has deactivated all options. The
    referenced Choice records (upserted by the choices module) include the
    deactivated entries with ``is_active=False``, so ER renders an empty
    dropdown until the CM re-activates them. Falling back to a plain TEXT
    field would change the field's wire type and break tenants that have
    historical events stored under the choice schema.
    """
    if smart_type in {"LIST", "MLIST", "TREE"}:
        return _build_choice_property_pair(
            smart_type=smart_type,
            display=display,
            is_multiple=is_multiple,
            attr_key=attr_key,
            choices_base_url=choices_base_url,
            event_type_value=event_type_value,
        )

    if smart_type in SCALAR_JSON:
        return (
            {**copy.deepcopy(SCALAR_JSON[smart_type]), "title": display},
            copy.deepcopy(SCALAR_UI[smart_type]),
        )

    logger.warning("Unknown SMART type %r; emitting string", smart_type)
    return (
        {
            "type": "string",
            "title": display,
            "description": "",
            "deprecated": False,
        },
        {"type": "TEXT", "inputType": "SHORT_TEXT", "parent": "section-1"},
    )


def _build_choice_property_pair(
    *,
    smart_type: str,
    display: str,
    is_multiple: bool,
    attr_key: str,
    choices_base_url: str,
    event_type_value: str,
) -> tuple[dict, dict]:
    """Emit the (json, ui) pair for a LIST/MLIST/TREE attribute as a
    CHOICE_LIST referencing the choices module's ``$ref`` URL."""
    field_name = derive_choice_field(event_type_value, attr_key)
    ref_url = f"{choices_base_url}/choices.json?field={field_name}"
    is_array = smart_type == "MLIST" or (smart_type == "LIST" and is_multiple)

    choices_block = {
        "type": "EXISTING_CHOICE_LIST",
        "existingChoiceList": [field_name],
        "eventTypeCategories": [],
        "featureCategories": [],
        "myDataType": "",
        "subjectGroups": [],
        "subjectSubtypes": [],
    }
    ui_field = {
        "type": "CHOICE_LIST",
        "inputType": "DROPDOWN",
        "placeholder": "",
        "choices": choices_block,
        "parent": "section-1",
    }

    if is_array:
        json_prop = {
            "type": "array",
            "title": display,
            "description": "",
            "deprecated": False,
            "uniqueItems": True,
            "items": {
                "type": "string",
                "anyOf": [{"$ref": ref_url}],
            },
        }
    else:
        json_prop = {
            "type": "string",
            "title": display,
            "description": "",
            "deprecated": False,
            "anyOf": [{"$ref": ref_url}],
        }
    return json_prop, ui_field


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
