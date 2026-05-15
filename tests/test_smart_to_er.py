"""Tests for er_smart_sync.smart_to_er.

We avoid loading real SMART XML — the conversion takes plain dicts (the same
shape that ``DataModel.export_as_dict()`` produces) so the tests can describe
inputs inline.
"""

from __future__ import annotations

import json

import pytest

from er_smart_sync.smart_to_er import build_event_types

CA_UUID = "ca-1234"


def _attr(
    key: str,
    type_: str,
    *,
    display: str | None = None,
    options: list | None = None,
):
    return {
        "key": key,
        "type": type_,
        "isrequired": False,
        "display": display or key,
        "options": options,
    }


def _option(key: str, display: str | None = None):
    return {"key": key, "display": display or key, "isActive": True}


def _category(
    path: str,
    *,
    display: str | None = None,
    attributes: list | None = None,
    is_active: bool = True,
    is_multiple: bool = False,
    hkey_path: str | None = None,
):
    return {
        "path": path,
        "hkeyPath": hkey_path or path,
        "display": display or path,
        "is_multiple": is_multiple,
        "is_active": is_active,
        "attributes": attributes or [],
    }


def _cat_attr(key: str, *, is_active: bool = True):
    return {"key": key, "is_active": is_active}


def _schema(event_type):
    return json.loads(event_type.event_schema)["schema"]


# ── Type mappings ───────────────────────────────────────────────


@pytest.mark.parametrize(
    "smart_type,expected_type,expected_format",
    [
        ("TEXT", "string", None),
        ("NUMERIC", "number", None),
        ("BOOLEAN", "boolean", None),
        ("DATE", "string", "date"),
        ("TIME", "string", "time"),
        ("DATETIME", "string", "date-time"),
        ("ATTACHMENT", "string", "uri"),
    ],
)
def test_simple_type_mapping(smart_type, expected_type, expected_format):
    dm = {
        "categories": [
            _category("animals", attributes=[_cat_attr("the_field")]),
        ],
        "attributes": [_attr("the_field", smart_type)],
    }
    out = build_event_types(dm=dm, ca_uuid=CA_UUID, ca_identifier="X")
    assert len(out) == 1
    prop = _schema(out[0])["properties"]["the_field"]
    assert prop["type"] == expected_type
    if expected_format:
        assert prop["format"] == expected_format
    else:
        assert "format" not in prop


def test_list_single_select_emits_string_enum():
    dm = {
        "categories": [
            _category("incidents", attributes=[_cat_attr("species")]),
        ],
        "attributes": [
            _attr(
                "species",
                "LIST",
                options=[_option("lion"), _option("tiger")],
            )
        ],
    }
    out = build_event_types(dm=dm, ca_uuid=CA_UUID, ca_identifier="X")
    prop = _schema(out[0])["properties"]["species"]
    assert prop["type"] == "string"
    assert prop["enum"] == ["lion", "tiger"]
    assert prop["enumNames"] == {"lion": "lion", "tiger": "tiger"}


def test_mlist_emits_array_of_enum():
    dm = {
        "categories": [
            _category("incidents", attributes=[_cat_attr("tags")]),
        ],
        "attributes": [
            _attr(
                "tags",
                "MLIST",
                options=[_option("urgent"), _option("review")],
            )
        ],
    }
    out = build_event_types(dm=dm, ca_uuid=CA_UUID, ca_identifier="X")
    prop = _schema(out[0])["properties"]["tags"]
    assert prop["type"] == "array"
    assert prop["items"]["type"] == "string"
    assert prop["items"]["enum"] == ["urgent", "review"]
    assert prop["items"]["enumNames"] == {
        "urgent": "urgent",
        "review": "review",
    }


def test_list_multi_on_multiple_category_emits_array_of_enum():
    dm = {
        "categories": [
            _category(
                "observations",
                attributes=[_cat_attr("species")],
                is_multiple=True,
            ),
        ],
        "attributes": [
            _attr(
                "species",
                "LIST",
                options=[_option("lion"), _option("tiger")],
            )
        ],
    }
    out = build_event_types(dm=dm, ca_uuid=CA_UUID, ca_identifier="X")
    prop = _schema(out[0])["properties"]["species"]
    assert prop["type"] == "array"
    assert prop["items"]["enum"] == ["lion", "tiger"]


def test_tree_emits_leaf_options_only():
    dm = {
        "categories": [
            _category("species", attributes=[_cat_attr("taxonomy")]),
        ],
        "attributes": [
            _attr(
                "taxonomy",
                "TREE",
                options=[
                    _option("felidae"),
                    _option("felidae.lion"),
                    _option("felidae.tiger"),
                ],
            )
        ],
    }
    out = build_event_types(dm=dm, ca_uuid=CA_UUID, ca_identifier="X")
    prop = _schema(out[0])["properties"]["taxonomy"]
    assert prop["type"] == "string"
    # Only leaves — felidae itself has descendants and is filtered out.
    assert sorted(prop["enum"]) == ["felidae.lion", "felidae.tiger"]


def test_unknown_attribute_type_emits_string_with_warning(caplog):
    dm = {
        "categories": [
            _category("c", attributes=[_cat_attr("weird")]),
        ],
        "attributes": [_attr("weird", "POLYGON")],
    }
    out = build_event_types(dm=dm, ca_uuid=CA_UUID, ca_identifier="X")
    prop = _schema(out[0])["properties"]["weird"]
    assert prop["type"] == "string"


# ── Category semantics ──────────────────────────────────────────


def test_leaf_with_no_attributes_is_skipped():
    dm = {
        "categories": [_category("empty")],
        "attributes": [],
    }
    out = build_event_types(dm=dm, ca_uuid=CA_UUID, ca_identifier="X")
    assert out == []


def test_inactive_category_emits_event_type_without_schema():
    dm = {
        "categories": [
            _category("old", attributes=[_cat_attr("x")], is_active=False),
        ],
        "attributes": [_attr("x", "TEXT")],
    }
    out = build_event_types(dm=dm, ca_uuid=CA_UUID, ca_identifier="X")
    assert len(out) == 1
    assert out[0].is_active is False
    assert out[0].event_schema is None


def test_inactive_category_attribute_kept_as_readonly_in_schema():
    dm = {
        "categories": [
            _category(
                "c",
                attributes=[
                    _cat_attr("active_attr"),
                    _cat_attr("retired_attr", is_active=False),
                ],
            ),
        ],
        "attributes": [
            _attr("active_attr", "TEXT"),
            _attr("retired_attr", "TEXT"),
        ],
    }
    out = build_event_types(dm=dm, ca_uuid=CA_UUID, ca_identifier="X")
    props = _schema(out[0])["properties"]
    # Both present — but the retired one is marked readOnly so ER UIs can
    # still render historical events that recorded it.
    assert "retired_attr" in props
    assert props["retired_attr"].get("readOnly") is True
    assert props["active_attr"].get("readOnly") is not True


def test_only_leaf_categories_emit_active_event_types():
    # `incidents` is a parent of `incidents.poaching` — it should not emit
    # an active event type, but the leaf should.
    dm = {
        "categories": [
            _category("incidents", attributes=[_cat_attr("parent_field")]),
            _category(
                "incidents.poaching",
                attributes=[_cat_attr("leaf_field")],
                hkey_path="incidents.poaching",
            ),
        ],
        "attributes": [
            _attr("parent_field", "TEXT"),
            _attr("leaf_field", "TEXT"),
        ],
    }
    out = build_event_types(dm=dm, ca_uuid=CA_UUID, ca_identifier="X")
    by_value = {et.value: et for et in out}

    # Parent: inactive (no schema)
    parent = by_value[f"{CA_UUID}_incidents"]
    assert parent.is_active is False
    assert parent.event_schema is None

    # Leaf: active with inherited parent_field + own leaf_field
    leaf = by_value[f"{CA_UUID}_incidents_poaching"]
    assert leaf.is_active is True
    props = _schema(leaf)["properties"]
    assert set(props.keys()) == {"parent_field", "leaf_field"}


def test_value_prefixed_with_ca_uuid_and_lowercased():
    # The generated value is lowercased so it matches what ER stores after
    # its own normalization (ER lowercases event-type values on write).
    dm = {
        "categories": [_category("c", attributes=[_cat_attr("a")])],
        "attributes": [_attr("a", "TEXT")],
    }
    out = build_event_types(dm=dm, ca_uuid="THE-CA", ca_identifier="X")
    assert out[0].value == "the-ca_c"


# ── Configurable model overlay ──────────────────────────────────


def test_configurable_model_overrides_categories_and_filters_options():
    # Base data model defines an attribute with three options.
    dm = {
        "categories": [
            _category("incidents", attributes=[_cat_attr("level")]),
        ],
        "attributes": [
            _attr(
                "level",
                "LIST",
                options=[
                    _option("low"),
                    _option("medium"),
                    _option("high"),
                ],
            )
        ],
    }
    # Configurable model overlay: only export `incidents`, and restrict the
    # `level` attribute to {low, high}.
    cm = {
        "cm_uuid": "CM1",
        "categories": [
            _category(
                "incidents",
                attributes=[_cat_attr("level")],
                hkey_path="incidents",
            ),
        ],
        "attributes": [
            {
                "key": "level",
                "options": [
                    {"key": "low", "isActive": True},
                    {"key": "medium", "isActive": False},
                    {"key": "high", "isActive": True},
                ],
            }
        ],
    }
    out = build_event_types(dm=dm, cm=cm, ca_uuid=CA_UUID, ca_identifier="X")
    assert len(out) == 1
    assert out[0].value == f"{CA_UUID}_cm1_incidents"
    prop = _schema(out[0])["properties"]["level"]
    assert prop["enum"] == ["low", "high"]


def test_value_is_lowercased_for_mixed_case_paths():
    # Regression: SMART data models can name paths in mixed case
    # (e.g. "actividadesAntropicas.Mineria"). ER normalizes event-type
    # values to lowercase on write, so we must do the same locally.
    dm = {
        "categories": [
            _category(
                "actividadesAntropicas.Mineria",
                hkey_path="actividadesAntropicas.Mineria",
                attributes=[_cat_attr("note")],
            ),
        ],
        "attributes": [_attr("note", "TEXT")],
    }
    out = build_event_types(dm=dm, ca_uuid=CA_UUID, ca_identifier="X")
    assert out[0].value == f"{CA_UUID}_actividadesantropicas_mineria".lower()


def test_configurable_model_skips_options_without_isactive():
    dm = {
        "categories": [_category("c", attributes=[_cat_attr("k")])],
        "attributes": [_attr("k", "LIST", options=[_option("a"), _option("b")])],
    }
    cm = {
        "cm_uuid": "CM1",
        "categories": [_category("c", attributes=[_cat_attr("k")], hkey_path="c")],
        "attributes": [
            {
                "key": "k",
                "options": [
                    {"key": "a", "isActive": False},
                    {"key": "b", "isActive": True},
                ],
            }
        ],
    }
    out = build_event_types(dm=dm, cm=cm, ca_uuid=CA_UUID, ca_identifier="X")
    prop = _schema(out[0])["properties"]["k"]
    assert prop["enum"] == ["b"]
