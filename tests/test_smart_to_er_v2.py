"""Tests for er_smart_sync.smart_to_er_v2."""

from __future__ import annotations

import pytest

from er_smart_sync.smart_to_er_v2 import ERV2EventType, build_event_types_v2

CA_UUID = "ca-1234"
CA_ID = "FOASF"


def _attr(
    key: str, type_: str, *, display: str | None = None, options: list | None = None
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


# ── ERV2EventType model ───────────────────────────────────────────


def test_er_v2_event_type_serializes_schema_as_dict():
    et = ERV2EventType(
        value="v",
        display="V",
        category="cat",
        event_schema={"json": {}, "ui": {}},
    )
    payload = et.dict(by_alias=True, exclude_none=True)
    assert payload["schema"] == {"json": {}, "ui": {}}
    assert "event_schema" not in payload


def test_er_v2_event_type_minimal():
    et = ERV2EventType(value="v", display="V", category="cat")
    payload = et.dict(by_alias=True, exclude_none=True)
    assert payload["value"] == "v"
    assert payload["display"] == "V"
    assert payload["category"] == "cat"
    assert payload["is_active"] is True
    assert payload["readonly"] is False


# ── build_event_types_v2 — empty input ────────────────────────────


def test_empty_data_model_yields_no_event_types():
    result = build_event_types_v2(
        dm={"categories": [], "attributes": []},
        cm=None,
        ca_uuid=CA_UUID,
        ca_identifier=CA_ID,
    )
    assert result == []


# ── Scalar type mapping ─────────────────────────────────────────


@pytest.mark.parametrize(
    "smart_type,expected_json,expected_ui",
    [
        ("TEXT", {"type": "string"}, {"type": "TEXT", "inputType": "SHORT_TEXT"}),
        ("NUMERIC", {"type": "number"}, {"type": "NUMBER"}),
        ("BOOLEAN", {"type": "boolean"}, {"type": "BOOLEAN"}),
        ("DATE", {"type": "string", "format": "date"},
         {"type": "TEXT", "inputType": "DATE"}),
        ("TIME", {"type": "string", "format": "time"},
         {"type": "TEXT", "inputType": "TIME"}),
        ("DATETIME", {"type": "string", "format": "date-time"},
         {"type": "TEXT", "inputType": "DATETIME"}),
        (
            "ATTACHMENT",
            {"type": "string", "format": "uri"},
            {
                "type": "ATTACHMENT",
                "allowableFileTypes": ["image", "document", "video", "audio"],
            },
        ),
    ],
)
def test_scalar_attribute_mapping(smart_type, expected_json, expected_ui):
    dm = {
        "categories": [
            _category("incidents", attributes=[_cat_attr("field1")]),
        ],
        "attributes": [_attr("field1", smart_type, display="Field One")],
    }

    result = build_event_types_v2(
        dm=dm, cm=None, ca_uuid=CA_UUID, ca_identifier=CA_ID
    )

    assert len(result) == 1
    et = result[0]
    schema = et.event_schema
    assert schema is not None

    json_props = schema["json"]["properties"]["field1"]
    for k, v in expected_json.items():
        assert json_props[k] == v
    assert json_props["title"] == "Field One"

    ui_field = schema["ui"]["fields"]["field1"]
    for k, v in expected_ui.items():
        assert ui_field[k] == v

    # Field is listed in the default section
    section = schema["ui"]["sections"]["section-1"]
    assert {"name": "field1", "type": "field"} in section["leftColumn"]
    assert schema["ui"]["order"] == ["section-1"]


def test_attachment_allowable_file_types_is_not_shared_across_calls():
    """Verify SCALAR_UI constant isn't mutated when ATTACHMENT properties are built."""
    from er_smart_sync.smart_to_er_v2 import SCALAR_UI

    original = list(SCALAR_UI["ATTACHMENT"]["allowableFileTypes"])

    dm = {
        "categories": [_category("c", attributes=[_cat_attr("f")])],
        "attributes": [_attr("f", "ATTACHMENT")],
    }
    result = build_event_types_v2(
        dm=dm, cm=None, ca_uuid=CA_UUID, ca_identifier=CA_ID
    )
    # Mutate the returned list — must not affect the module constant.
    result[0].event_schema["ui"]["fields"]["f"]["allowableFileTypes"].append(
        "EVIL"
    )
    assert SCALAR_UI["ATTACHMENT"]["allowableFileTypes"] == original


def test_envelope_top_level_keys():
    dm = {
        "categories": [_category("incidents", attributes=[_cat_attr("a")])],
        "attributes": [_attr("a", "TEXT")],
    }
    result = build_event_types_v2(
        dm=dm, cm=None, ca_uuid=CA_UUID, ca_identifier=CA_ID
    )
    schema = result[0].event_schema
    assert schema["json"]["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["json"]["type"] == "object"
    assert schema["json"]["additionalProperties"] is False
    assert schema["json"]["required"] == []


def test_event_type_value_is_ca_scoped_and_lowercased():
    dm = {
        "categories": [_category("Incidents.Wildlife", attributes=[_cat_attr("a")])],
        "attributes": [_attr("a", "TEXT")],
    }
    result = build_event_types_v2(
        dm=dm, cm=None, ca_uuid="CA-WITH-CAPS", ca_identifier=CA_ID
    )
    # only leaf categories emit; here the only category IS a leaf
    assert len(result) == 1
    assert result[0].value == "ca-with-caps_incidents_wildlife"
    assert result[0].display == "Incidents.Wildlife"
