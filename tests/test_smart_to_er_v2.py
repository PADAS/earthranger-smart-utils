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
        event_schema={"json": {}, "ui": {}},  # ty: ignore[unknown-argument]
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
        ("NUMERIC", {"type": "number"}, {"type": "NUMERIC"}),
        ("BOOLEAN", {"type": "boolean"}, {"type": "BOOLEAN"}),
        ("DATE", {"type": "string", "format": "date"}, {"type": "DATE_TIME"}),
        ("TIME", {"type": "string", "format": "time"}, {"type": "DATE_TIME"}),
        ("DATETIME", {"type": "string", "format": "date-time"}, {"type": "DATE_TIME"}),
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

    result = build_event_types_v2(dm=dm, cm=None, ca_uuid=CA_UUID, ca_identifier=CA_ID)

    assert len(result) == 1
    et = result[0]
    schema = et.event_schema
    assert schema is not None

    json_props = schema["json"]["properties"]["field1"]
    for k, v in expected_json.items():
        assert json_props[k] == v
    assert json_props["title"] == "Field One"
    # New: every property has description and deprecated.
    assert json_props["description"] == ""
    assert json_props["deprecated"] is False  # active attribute

    ui_field = schema["ui"]["fields"]["field1"]
    for k, v in expected_ui.items():
        assert ui_field[k] == v
    # UI fields no longer carry inputType for DATE_TIME types.
    if expected_ui["type"] == "DATE_TIME":
        assert "inputType" not in ui_field
    assert ui_field["parent"] == "section-1"

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
    result = build_event_types_v2(dm=dm, cm=None, ca_uuid=CA_UUID, ca_identifier=CA_ID)
    # Mutate the returned list — must not affect the module constant.
    assert result[0].event_schema is not None
    result[0].event_schema["ui"]["fields"]["f"]["allowableFileTypes"].append("EVIL")
    assert SCALAR_UI["ATTACHMENT"]["allowableFileTypes"] == original


def test_envelope_top_level_keys():
    dm = {
        "categories": [_category("incidents", attributes=[_cat_attr("a")])],
        "attributes": [_attr("a", "TEXT")],
    }
    result = build_event_types_v2(dm=dm, cm=None, ca_uuid=CA_UUID, ca_identifier=CA_ID)
    schema = result[0].event_schema
    assert schema is not None
    assert schema["json"]["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["json"]["type"] == "object"
    assert schema["json"]["unevaluatedProperties"] is False
    assert "additionalProperties" not in schema["json"]
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


# ── Choice/enum types ────────────────────────────────────────────


def test_list_single_value_emits_anyof_ref_and_choice_list():
    from er_smart_sync.choices import derive_choice_field, event_type_value_for

    dm = {
        "categories": [_category("c", attributes=[_cat_attr("color")])],
        "attributes": [
            _attr(
                "color",
                "LIST",
                display="Color",
                options=[_option("red", "Red"), _option("blue", "Blue")],
            )
        ],
    }
    schema = build_event_types_v2(
        dm=dm,
        cm=None,
        ca_uuid=CA_UUID,
        ca_identifier=CA_ID,
        choices_base_url="/api/v2.0/schemas",
    )[0].event_schema

    et_value = event_type_value_for(category_path="c", ca_uuid=CA_UUID, cm=None)
    expected_field = derive_choice_field(et_value, "color")

    json_prop = schema["json"]["properties"]["color"]
    assert json_prop["type"] == "string"
    assert json_prop["title"] == "Color"
    assert json_prop["description"] == ""
    assert json_prop["deprecated"] is False
    assert json_prop["anyOf"] == [
        {"$ref": f"/api/v2.0/schemas/choices.json?field={expected_field}"}
    ]
    # Inline enum must not be present.
    assert "enum" not in json_prop

    ui_field = schema["ui"]["fields"]["color"]
    assert ui_field["type"] == "CHOICE_LIST"
    assert ui_field["inputType"] == "DROPDOWN"
    assert ui_field["placeholder"] == ""
    assert ui_field["parent"] == "section-1"
    assert ui_field["choices"] == {
        "type": "EXISTING_CHOICE_LIST",
        "existingChoiceList": [expected_field],
        "eventTypeCategories": [],
        "featureCategories": [],
        "myDataType": "",
        "subjectGroups": [],
        "subjectSubtypes": [],
    }


def test_list_multi_value_emits_array_anyof_ref():
    from er_smart_sync.choices import derive_choice_field, event_type_value_for

    dm = {
        "categories": [
            _category("c", is_multiple=True, attributes=[_cat_attr("tags")]),
        ],
        "attributes": [
            _attr(
                "tags",
                "LIST",
                display="Tags",
                options=[_option("a"), _option("b")],
            )
        ],
    }
    schema = build_event_types_v2(
        dm=dm,
        cm=None,
        ca_uuid=CA_UUID,
        ca_identifier=CA_ID,
    )[0].event_schema

    et_value = event_type_value_for(category_path="c", ca_uuid=CA_UUID, cm=None)
    expected_field = derive_choice_field(et_value, "tags")

    json_prop = schema["json"]["properties"]["tags"]
    assert json_prop["type"] == "array"
    assert json_prop["uniqueItems"] is True
    assert json_prop["deprecated"] is False
    assert json_prop["description"] == ""
    assert json_prop["title"] == "Tags"
    assert json_prop["items"] == {
        "type": "string",
        "anyOf": [{"$ref": f"/api/v2.0/schemas/choices.json?field={expected_field}"}],
    }
    # Inline enum must not be present.
    assert "enum" not in json_prop
    assert "enum" not in json_prop["items"]

    ui_field = schema["ui"]["fields"]["tags"]
    assert ui_field["type"] == "CHOICE_LIST"
    assert ui_field["inputType"] == "DROPDOWN"
    assert ui_field["parent"] == "section-1"


def test_mlist_emits_array_anyof_ref():
    from er_smart_sync.choices import derive_choice_field, event_type_value_for

    dm = {
        "categories": [_category("c", attributes=[_cat_attr("species")])],
        "attributes": [
            _attr(
                "species",
                "MLIST",
                display="Species",
                options=[_option("lion"), _option("zebra")],
            )
        ],
    }
    schema = build_event_types_v2(
        dm=dm,
        cm=None,
        ca_uuid=CA_UUID,
        ca_identifier=CA_ID,
    )[0].event_schema

    et_value = event_type_value_for(category_path="c", ca_uuid=CA_UUID, cm=None)
    expected_field = derive_choice_field(et_value, "species")

    json_prop = schema["json"]["properties"]["species"]
    assert json_prop["type"] == "array"
    assert json_prop["uniqueItems"] is True
    assert json_prop["items"]["anyOf"] == [
        {"$ref": f"/api/v2.0/schemas/choices.json?field={expected_field}"}
    ]
    assert schema["ui"]["fields"]["species"]["type"] == "CHOICE_LIST"


def test_tree_flattens_to_leaf_options():
    """TREE flattening still happens at the builder; choices module emits the leaves."""
    from er_smart_sync.choices import derive_choice_field, event_type_value_for

    dm = {
        "categories": [_category("c", attributes=[_cat_attr("region")])],
        "attributes": [
            _attr(
                "region",
                "TREE",
                display="Region",
                options=[
                    _option("africa"),
                    _option("africa.kenya"),
                    _option("africa.kenya.nairobi"),
                    _option("africa.tanzania"),
                ],
            )
        ],
    }
    schema = build_event_types_v2(dm=dm, cm=None, ca_uuid=CA_UUID, ca_identifier=CA_ID)[
        0
    ].event_schema

    et_value = event_type_value_for(category_path="c", ca_uuid=CA_UUID, cm=None)
    expected_field = derive_choice_field(et_value, "region")

    json_prop = schema["json"]["properties"]["region"]
    assert json_prop["anyOf"] == [
        {"$ref": f"/api/v2.0/schemas/choices.json?field={expected_field}"}
    ]
    # TREE acts like LIST-single for v2 schema: emits anyOf, not array.
    assert json_prop["type"] == "string"
    assert schema["ui"]["fields"]["region"]["type"] == "CHOICE_LIST"
    assert schema["ui"]["fields"]["region"]["inputType"] == "DROPDOWN"


def test_inactive_attribute_marked_deprecated_and_kept_in_section():
    dm = {
        "categories": [
            _category(
                "c",
                attributes=[
                    _cat_attr("active_attr", is_active=True),
                    _cat_attr("retired_attr", is_active=False),
                ],
            )
        ],
        "attributes": [
            _attr("active_attr", "TEXT", display="Active"),
            _attr("retired_attr", "TEXT", display="Retired"),
        ],
    }
    schema = build_event_types_v2(dm=dm, cm=None, ca_uuid=CA_UUID, ca_identifier=CA_ID)[
        0
    ].event_schema

    # Both have `deprecated`; only the inactive one is True.
    assert schema["json"]["properties"]["retired_attr"]["deprecated"] is True
    assert schema["json"]["properties"]["active_attr"]["deprecated"] is False

    # Both fields still listed in the form section
    leftCol = schema["ui"]["sections"]["section-1"]["leftColumn"]
    names = [item["name"] for item in leftCol]
    assert "active_attr" in names
    assert "retired_attr" in names


# ── Configurable-model overlay ────────────────────────────────────


def test_configurable_model_event_type_value_includes_cm_uuid():
    dm = {
        "categories": [_category("Wildlife", attributes=[_cat_attr("a")])],
        "attributes": [_attr("a", "TEXT")],
    }
    cm = {
        "cm_uuid": "abcd-cm",
        "categories": [
            _category("Wildlife", attributes=[_cat_attr("a")], hkey_path="Wildlife")
        ],
        "attributes": [],
    }
    result = build_event_types_v2(dm=dm, cm=cm, ca_uuid="ca-1", ca_identifier=CA_ID)
    assert result[0].value == "ca-1_abcd-cm_wildlife"


def test_event_type_category_is_settable_post_build():
    dm = {
        "categories": [_category("c", attributes=[_cat_attr("a")])],
        "attributes": [_attr("a", "TEXT")],
    }
    et = build_event_types_v2(dm=dm, cm=None, ca_uuid=CA_UUID, ca_identifier=CA_ID)[0]
    et.category = "foasf"
    payload = et.dict(by_alias=True, exclude_none=True)
    assert payload["category"] == "foasf"


def test_build_event_types_v2_accepts_choices_base_url():
    """The builder takes choices_base_url to construct $ref URLs."""
    dm = {
        "categories": [_category("c", attributes=[_cat_attr("a")])],
        "attributes": [_attr("a", "TEXT")],
    }
    # Should not raise on the kwarg.
    result = build_event_types_v2(
        dm=dm,
        cm=None,
        ca_uuid=CA_UUID,
        ca_identifier=CA_ID,
        choices_base_url="/custom/v2/schemas",
    )
    assert len(result) == 1


def test_build_event_types_v2_choices_base_url_defaults():
    """Default choices_base_url is /api/v2.0/schemas (parameter is optional)."""
    dm = {
        "categories": [_category("c", attributes=[_cat_attr("a")])],
        "attributes": [_attr("a", "TEXT")],
    }
    # Calling without choices_base_url must still succeed.
    result = build_event_types_v2(
        dm=dm,
        cm=None,
        ca_uuid=CA_UUID,
        ca_identifier=CA_ID,
    )
    assert len(result) == 1


def test_ui_envelope_has_required_keys():
    """ui must have fields, headers, order, sections per the v2 meta-schema."""
    dm = {
        "categories": [_category("c", attributes=[_cat_attr("a")])],
        "attributes": [_attr("a", "TEXT")],
    }
    schema = build_event_types_v2(dm=dm, cm=None, ca_uuid=CA_UUID, ca_identifier=CA_ID)[
        0
    ].event_schema

    ui = schema["ui"]
    assert "fields" in ui
    assert ui["headers"] == {}
    assert ui["order"] == ["section-1"]
    assert "sections" in ui


def test_ui_section_has_required_keys():
    """section-1 must have columns, isActive, leftColumn, rightColumn."""
    dm = {
        "categories": [_category("c", attributes=[_cat_attr("a")])],
        "attributes": [_attr("a", "TEXT")],
    }
    schema = build_event_types_v2(dm=dm, cm=None, ca_uuid=CA_UUID, ca_identifier=CA_ID)[
        0
    ].event_schema

    section = schema["ui"]["sections"]["section-1"]
    assert section["columns"] == 1
    assert section["isActive"] is True
    assert isinstance(section["leftColumn"], list)
    assert section["rightColumn"] == []


def test_ui_field_has_parent():
    """Every ui field block must have parent='section-1'."""
    dm = {
        "categories": [_category("c", attributes=[_cat_attr("a"), _cat_attr("b")])],
        "attributes": [
            _attr("a", "TEXT"),
            _attr("b", "NUMERIC"),
        ],
    }
    schema = build_event_types_v2(dm=dm, cm=None, ca_uuid=CA_UUID, ca_identifier=CA_ID)[
        0
    ].event_schema

    for field_name, field_block in schema["ui"]["fields"].items():
        assert field_block.get("parent") == "section-1", (
            f"ui.fields[{field_name}] missing parent='section-1'"
        )


def test_build_event_types_v2_skips_inactive_non_cm_categories():
    """Inactive leaf categories without a CM produce no event type at all."""
    dm = {
        "categories": [
            _category("c", attributes=[_cat_attr("a")], is_active=False),
        ],
        "attributes": [_attr("a", "TEXT")],
    }
    result = build_event_types_v2(
        dm=dm,
        cm=None,
        ca_uuid=CA_UUID,
        ca_identifier=CA_ID,
    )
    assert result == []


def test_snapshot_full_mix_of_types():
    """Single event type with every supported SMART attribute type."""
    from er_smart_sync.choices import derive_choice_field, event_type_value_for

    dm = {
        "categories": [
            _category(
                "incidents",
                attributes=[
                    _cat_attr("title"),
                    _cat_attr("count"),
                    _cat_attr("confirmed"),
                    _cat_attr("when_date"),
                    _cat_attr("photo"),
                    _cat_attr("species"),
                    _cat_attr("tags"),
                    _cat_attr("legacy_field", is_active=False),
                ],
            ),
        ],
        "attributes": [
            _attr("title", "TEXT", display="Title"),
            _attr("count", "NUMERIC", display="Count"),
            _attr("confirmed", "BOOLEAN", display="Confirmed"),
            _attr("when_date", "DATE", display="When"),
            _attr("photo", "ATTACHMENT", display="Photo"),
            _attr(
                "species",
                "LIST",
                display="Species",
                options=[_option("lion", "Lion"), _option("zebra", "Zebra")],
            ),
            _attr(
                "tags", "MLIST", display="Tags", options=[_option("a"), _option("b")]
            ),
            _attr("legacy_field", "TEXT", display="Legacy"),
        ],
    }
    result = build_event_types_v2(
        dm=dm,
        cm=None,
        ca_uuid="ca-snap",
        ca_identifier="SNAP",
        choices_base_url="/api/v2.0/schemas",
    )

    assert len(result) == 1
    schema = result[0].event_schema

    # Top-level json envelope
    assert schema["json"]["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["json"]["type"] == "object"
    assert schema["json"]["unevaluatedProperties"] is False
    assert schema["json"]["required"] == []

    # Top-level ui envelope
    assert schema["ui"]["headers"] == {}
    assert schema["ui"]["order"] == ["section-1"]
    section = schema["ui"]["sections"]["section-1"]
    assert section["columns"] == 1
    assert section["isActive"] is True
    assert section["rightColumn"] == []
    # All 8 fields appear in leftColumn
    leftcol_names = [item["name"] for item in section["leftColumn"]]
    for k in (
        "title",
        "count",
        "confirmed",
        "when_date",
        "photo",
        "species",
        "tags",
        "legacy_field",
    ):
        assert k in leftcol_names

    # Spot-check every property carries description + deprecated.
    for key, prop in schema["json"]["properties"].items():
        assert "description" in prop, f"{key} missing description"
        assert "deprecated" in prop, f"{key} missing deprecated"

    # Specific shape checks
    assert schema["json"]["properties"]["title"]["type"] == "string"
    assert schema["json"]["properties"]["count"]["type"] == "number"
    assert schema["json"]["properties"]["confirmed"]["type"] == "boolean"
    assert schema["json"]["properties"]["when_date"]["format"] == "date"
    assert schema["json"]["properties"]["photo"]["format"] == "uri"
    assert schema["json"]["properties"]["legacy_field"]["deprecated"] is True

    # Choice attribute uses anyOf $ref
    et_value = event_type_value_for(
        category_path="incidents",
        ca_uuid="ca-snap",
        cm=None,
    )
    expected_species_field = derive_choice_field(et_value, "species")
    expected_tags_field = derive_choice_field(et_value, "tags")
    assert schema["json"]["properties"]["species"]["anyOf"] == [
        {"$ref": f"/api/v2.0/schemas/choices.json?field={expected_species_field}"}
    ]
    assert schema["json"]["properties"]["tags"]["type"] == "array"
    assert schema["json"]["properties"]["tags"]["uniqueItems"] is True
    assert schema["json"]["properties"]["tags"]["items"]["anyOf"] == [
        {"$ref": f"/api/v2.0/schemas/choices.json?field={expected_tags_field}"}
    ]

    # All ui fields have parent
    for key, ui_block in schema["ui"]["fields"].items():
        assert ui_block.get("parent") == "section-1", f"ui.fields[{key}] missing parent"

    # Scalar ui types
    assert schema["ui"]["fields"]["title"]["type"] == "TEXT"
    assert schema["ui"]["fields"]["count"]["type"] == "NUMERIC"
    assert schema["ui"]["fields"]["confirmed"]["type"] == "BOOLEAN"
    assert schema["ui"]["fields"]["when_date"]["type"] == "DATE_TIME"
    assert schema["ui"]["fields"]["photo"]["type"] == "ATTACHMENT"
    assert schema["ui"]["fields"]["species"]["type"] == "CHOICE_LIST"
    assert schema["ui"]["fields"]["tags"]["type"] == "CHOICE_LIST"
