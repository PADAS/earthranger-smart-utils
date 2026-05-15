"""SMART → EarthRanger data-model conversion.

This module owns the mapping from a SMART Connect data model (and an optional
configurable data model overlay) to a list of ``EREventType`` records ready
to POST/PATCH against the EarthRanger API.

It replaces ``smartconnect.er_sync_utils.build_earthranger_event_types``, which
had several gaps:

* No mapping for ``TIME`` or ``ATTACHMENT`` attributes (KeyError at runtime).
* Multi-value ``LIST`` / ``MLIST`` attributes were forced down to a single
  ``string`` field (the multi-value branch was hard-disabled with ``if
  is_multiple and False``).
* ``DATE`` was emitted as a plain ``string`` with no JSON-Schema ``format``.
* Inactive category attributes were silently dropped from the schema.

We keep using ``EREventType`` / ``EventSchema`` / ``SchemaWrapper`` from
``smartconnect.er_sync_utils`` as the wire format so we stay drop-in compatible
with the rest of the codebase (notably ``_event_type_needs_update``).
"""

from __future__ import annotations

import json
import logging
from typing import Any

from pydantic import parse_obj_as
from smartconnect.er_sync_utils import EREventType, EventSchema, SchemaWrapper
from smartconnect.models import Attribute, Category, CategoryAttribute

logger = logging.getLogger(__name__)


# Type → JSON-Schema mapping. Each entry describes the schema fragment to
# emit for an attribute of that SMART type (excluding enums, which the caller
# layers on for LIST/MLIST/TREE).
#
# We intentionally distinguish (LIST single, LIST multi, MLIST, TREE) at the
# call site rather than via this table — they all have the same scalar JSON
# type but different "is this an array of enums vs a string enum" shape.
SMART_TYPE_TO_JSON: dict[str, dict[str, Any]] = {
    "TEXT": {"type": "string"},
    "NUMERIC": {"type": "number"},
    "BOOLEAN": {"type": "boolean"},
    "DATE": {"type": "string", "format": "date"},
    "TIME": {"type": "string", "format": "time"},
    "DATETIME": {"type": "string", "format": "date-time"},
    "ATTACHMENT": {"type": "string", "format": "uri"},
    # Enum-bearing types are handled specially in _attribute_property.
    "LIST": {"type": "string"},
    "MLIST": {"type": "array"},
    "TREE": {"type": "string"},
}


def build_event_types(
    *,
    dm: dict,
    cm: dict | None = None,
    ca_uuid: str,
    ca_identifier: str,
) -> list[EREventType]:
    """Build EREventType records for a SMART CA (or configurable model overlay).

    ``dm`` is the base data model as returned by ``DataModel.export_as_dict()``.
    ``cm`` is an optional configurable-model overlay as returned by
    ``ConfigurableDataModel.export_as_dict()`` — when supplied, its categories
    define the set of event types to emit and its attribute config gates which
    options are available per attribute.

    ``ca_uuid`` is the SMART conservation-area UUID. We prefix it onto event
    type ``value`` strings so that two CAs with the same category path don't
    collide on the EarthRanger side.

    ``ca_identifier`` is the bracketed short code (e.g. ``"SONM"``) extracted
    from the CA label; unused here directly but accepted for parity with the
    legacy signature.
    """
    del ca_identifier  # reserved for future use; parity with legacy signature

    source = cm if cm else dm
    cats = parse_obj_as(list[Category], source.get("categories") or [])
    cat_paths = [cat.path for cat in cats]
    attributes = parse_obj_as(list[Attribute], dm.get("attributes") or [])
    attribute_configs = cm.get("attributes") if cm else None

    event_types: list[EREventType] = []

    for cat in cats:
        try:
            event_type = _build_one_event_type(
                cat=cat,
                cats=cats,
                cat_paths=cat_paths,
                attributes=attributes,
                attribute_configs=attribute_configs,
                ca_uuid=ca_uuid,
                cm=cm,
            )
        except Exception:
            logger.exception(
                "Failed to build ER event type from SMART category %s",
                cat.path,
            )
            raise
        if event_type is not None:
            event_types.append(event_type)

    return event_types


def _build_one_event_type(
    *,
    cat: Category,
    cats: list[Category],
    cat_paths: list[str],
    attributes: list[Attribute],
    attribute_configs: list | None,
    ca_uuid: str,
    cm: dict | None,
) -> EREventType | None:
    """Build a single EREventType for ``cat``, or return None if it should be skipped."""

    # Only leaf categories become event types. A leaf has no descendants.
    is_leaf = _is_leaf_node(cat_paths, cat.path)
    # When a configurable model is provided every entry it contains is an
    # explicit choice to export; honor it. Otherwise the legacy rule applies:
    # active and leaf.
    is_active = bool(cm) or (cat.is_active and is_leaf)

    path_components = cat.hkeyPath.split(".") if cm else cat.path.split(".")
    value_suffix = "_".join(path_components)
    if cm:
        value = f"{ca_uuid}_{cm['cm_uuid']}_{value_suffix}"
    else:
        value = f"{ca_uuid}_{value_suffix}"
    # ER normalizes event type values to lowercase on write. We do the same
    # on the client so post-and-then-read-back comparisons stay consistent.
    value = value.lower()

    event_type = EREventType(value=value, display=cat.display, is_active=is_active)

    if not is_active:
        # Active=False categories still get registered so ER can show them
        # as deactivated; no schema needed.
        return event_type

    leaf_attributes = list(cat.attributes)
    if not cm:
        leaf_attributes.extend(_get_inherited_attributes(cats, path_components))

    if not leaf_attributes:
        logger.warning(
            "Skipping event type, no leaf attributes detected",
            extra=dict(value=value, display=cat.display),
        )
        return None

    schema = _build_schema(
        attributes=attributes,
        leaf_attributes=leaf_attributes,
        is_multiple=cat.is_multiple,
        attribute_configs=attribute_configs,
    )
    if not schema.properties:
        logger.warning(
            "Skipping event type, no schema properties detected",
            extra=dict(value=value, display=cat.display),
        )
        return None

    event_type.event_schema = json.dumps(
        SchemaWrapper(schema=schema).dict(by_alias=True), indent=2
    )
    return event_type


def _build_schema(
    *,
    attributes: list[Attribute],
    leaf_attributes: list[CategoryAttribute],
    is_multiple: bool,
    attribute_configs: list | None,
) -> EventSchema:
    """Build the JSON-Schema-shaped EventSchema for one event type."""
    properties: dict[str, dict] = {}
    schema_definition: list[str] = []

    for cat_attr in leaf_attributes:
        key = cat_attr.key
        attribute = next((a for a in attributes if a.key == key), None)
        if attribute is None:
            logger.warning("Attribute not found in data model", extra=dict(key=key))
            continue

        attribute_options_config = _options_config_for(attribute_configs, key)
        try:
            prop = _attribute_property(
                attribute=attribute,
                is_active=cat_attr.is_active,
                allow_multi=is_multiple,
                options_config=attribute_options_config,
            )
        except Exception:
            logger.exception("Error building schema property for attribute %s", key)
            continue

        if prop is None:
            continue

        schema_definition.append(key)
        properties[key] = prop

    return EventSchema(
        type="object",
        definition=schema_definition,
        properties=properties,
    )


def _attribute_property(
    *,
    attribute: Attribute,
    is_active: bool,
    allow_multi: bool,
    options_config: list | None,
) -> dict | None:
    """Build the JSON-Schema property dict for one attribute.

    Returns None if the attribute should be omitted entirely (e.g. unknown
    type with no safe fallback).
    """
    smart_type = attribute.type
    if smart_type not in SMART_TYPE_TO_JSON:
        logger.warning(
            "Unknown SMART attribute type %r for key %s; emitting string",
            smart_type,
            attribute.key,
        )
        prop: dict[str, Any] = {"type": "string", "title": attribute.display}
    else:
        prop = dict(SMART_TYPE_TO_JSON[smart_type])
        prop["title"] = attribute.display

    if not is_active:
        # Surface inactive attributes in the schema with readOnly so ER UIs
        # can render but not solicit them. Excluding entirely (as the legacy
        # code did) hides the attribute even on historical events.
        prop["readOnly"] = True

    options = list(attribute.options or [])
    if not options:
        return prop

    if options_config is not None:
        options = _filter_options_by_config(options, options_config)
    else:
        options = _leaf_options(options)

    if not options:
        return prop

    enum_keys = [o.key for o in options]
    enum_names = {o.key: o.display for o in options}

    if smart_type == "MLIST" or (smart_type == "LIST" and allow_multi):
        # Multi-value selection: array of enum strings.
        prop["type"] = "array"
        prop["items"] = {
            "type": "string",
            "enum": enum_keys,
            "enumNames": enum_names,
        }
    else:
        # Single selection (including TREE): scalar string with enum.
        prop["type"] = "string"
        prop["enum"] = enum_keys
        prop["enumNames"] = enum_names

    return prop


def _options_config_for(attribute_configs: list | None, key: str) -> list | None:
    if not attribute_configs:
        return None
    cfg = next((c for c in attribute_configs if c.get("key") == key), None)
    return cfg.get("options") if cfg else None


def _filter_options_by_config(options: list, options_config: list) -> list:
    """Keep options that the configurable-model overlay marks active."""
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
    """Filter to leaf options only (used for TREE-shaped option sets)."""
    keys = [o.key for o in options]
    return [o for o in options if _is_leaf_node(keys, o.key)]


def _is_leaf_node(node_paths: list[str], cur_node: str) -> bool:
    """True if no other entry in node_paths has cur_node as a strict prefix."""
    prefix = f"{cur_node}."
    return not any(p.startswith(prefix) for p in node_paths)


def _get_inherited_attributes(
    cats: list[Category], path_components: list[str]
) -> list[CategoryAttribute]:
    """Walk parent categories of a leaf and collect their attributes.

    SMART category attributes are inherited down the tree, so a leaf event's
    schema must include every attribute defined on its ancestors.
    """
    inherited: list[CategoryAttribute] = []
    parent_path = ""
    # Skip the last component — that's the leaf itself.
    for component in path_components[:-1]:
        parent_path = component if not parent_path else f"{parent_path}.{component}"
        parent_cat = next((c for c in cats if c.path == parent_path), None)
        if parent_cat:
            inherited.extend(parent_cat.attributes)
    return inherited
