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
                "allowableFileTypes": ["audio", "document", "image", "video"],
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


def test_cm_deactivates_all_options_still_emits_choice_list():
    """When a CM marks every option of a LIST attribute as inactive,
    the v2 builder must still emit a CHOICE_LIST referencing the
    choices via $ref. Falling back to plain TEXT would change the
    field's wire type and break tenants that have historical events
    stored under the choice schema. Inactive options remain in the
    choices module's upsert (with is_active=False)."""
    from er_smart_sync.choices import derive_choice_field, event_type_value_for

    dm = {
        "categories": [_category("c", attributes=[_cat_attr("color")])],
        "attributes": [
            _attr(
                "color",
                "LIST",
                display="Color",
                options=[
                    _option("red"),
                    _option("blue"),
                ],
            )
        ],
    }
    cm = {
        "cm_uuid": "cm-1",
        "categories": [_category("c", attributes=[_cat_attr("color")])],
        "attributes": [
            {
                "key": "color",
                "options": [
                    {"key": "red", "isActive": False},
                    {"key": "blue", "isActive": False},
                ],
            }
        ],
    }
    schema = build_event_types_v2(dm=dm, cm=cm, ca_uuid=CA_UUID, ca_identifier=CA_ID)[
        0
    ].event_schema

    et_value = event_type_value_for(category_path="c", ca_uuid=CA_UUID, cm=cm)
    expected_field = derive_choice_field(et_value, "color")

    json_prop = schema["json"]["properties"]["color"]
    # Still CHOICE_LIST with anyOf $ref — NOT a plain string/TEXT fallback.
    assert json_prop["type"] == "string"
    assert json_prop["anyOf"] == [
        {"$ref": f"/api/v2.0/schemas/choices.json?field={expected_field}"}
    ]
    ui_field = schema["ui"]["fields"]["color"]
    assert ui_field["type"] == "CHOICE_LIST"
    assert ui_field["inputType"] == "DROPDOWN"
    assert ui_field["choices"]["existingChoiceList"] == [expected_field]


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
                    _cat_attr("region"),
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
            _attr("region", "TREE", display="Region",
                  options=[
                      _option("africa"),
                      _option("africa.kenya"),
                      _option("africa.kenya.nairobi"),
                      _option("africa.tanzania"),
                  ]),
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
    # All 9 fields appear in leftColumn
    leftcol_names = [item["name"] for item in section["leftColumn"]]
    for k in ("title", "count", "confirmed", "when_date", "photo",
              "species", "tags", "region", "legacy_field"):
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

    # TREE attribute: flattens to leaves and uses anyOf $ref like LIST single
    expected_region_field = derive_choice_field(et_value, "region")
    assert schema["json"]["properties"]["region"]["type"] == "string"
    assert schema["json"]["properties"]["region"]["anyOf"] == [
        {"$ref": f"/api/v2.0/schemas/choices.json?field={expected_region_field}"}
    ]
    assert schema["ui"]["fields"]["region"]["type"] == "CHOICE_LIST"

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


def test_inactive_choice_attribute_marked_deprecated():
    """Inactive LIST attributes get deprecated:True (the choice path's hardcoded
    False baseline is correctly overwritten by _build_field_blocks)."""
    dm = {
        "categories": [_category("c", attributes=[
            _cat_attr("species", is_active=False),
        ])],
        "attributes": [
            _attr(
                "species", "LIST", display="Species",
                options=[_option("lion", "Lion")],
            ),
        ],
    }
    schema = build_event_types_v2(
        dm=dm, cm=None, ca_uuid=CA_UUID, ca_identifier=CA_ID,
    )[0].event_schema

    json_prop = schema["json"]["properties"]["species"]
    assert json_prop["deprecated"] is True
    # The anyOf $ref is still present (deprecated attr stays in the schema).
    assert "anyOf" in json_prop


# ── Variant-group detection ───────────────────────────────────────


def test_group_by_hkey_singletons_and_groups():
    from smartconnect.models import Category
    from er_smart_sync.smart_to_er_v2 import _group_by_hkey

    cats = [
        Category(path="a", hkeyPath="x", display="A", id="1"),
        Category(path="b", hkeyPath="y", display="B1", id="2"),
        Category(path="c", hkeyPath="y", display="B2", id="3"),
    ]
    groups = _group_by_hkey(cats)
    assert list(groups.keys()) == ["x", "y"]          # insertion order preserved
    assert len(groups["x"]) == 1
    assert [c.id for c in groups["y"]] == ["2", "3"]   # member order preserved


def test_variant_disambiguator_is_stable_and_readable():
    from smartconnect.models import Category
    from er_smart_sync.smart_to_er_v2 import _variant_disambiguator

    cat = Category(path="c", hkeyPath="animals.carcass", display="Large Predator Carcass", id="node-1")
    out = _variant_disambiguator(cat)
    assert out.startswith("large_predator_carcass_")
    assert _variant_disambiguator(cat) == out               # deterministic
    # 8-hex node-id suffix
    assert len(out.rsplit("_", 1)[-1]) == 8


def test_variant_disambiguator_missing_id_falls_back(caplog):
    from smartconnect.models import Category
    from er_smart_sync.smart_to_er_v2 import _variant_disambiguator

    cat = Category(path="c", hkeyPath="animals.carcass", display="Large Predator Carcass", id=None)
    with caplog.at_level("WARNING"):
        out = _variant_disambiguator(cat)
    assert out == "large_predator_carcass"
    assert any("no id" in r.message.lower() for r in caplog.records)


def test_build_one_appends_value_disambiguator():
    from smartconnect.models import Attribute, Category
    from er_smart_sync.smart_to_er_v2 import _build_one

    cat = Category(path="carcass", hkeyPath="animals.carcass", display="Large Predator Carcass",
                   id="n1", attributes=[{"key": "age"}])
    attrs = [Attribute(key="age", type="NUMERIC", display="Age")]
    et = _build_one(
        cat=cat, cats=[cat], cat_paths=["carcass"], attributes=attrs,
        attribute_configs=None, ca_uuid="ca1", cm={"cm_uuid": "cm1"},
        value_disambiguator="large_predator_carcass_a1b2c3d4",
    )
    assert et is not None
    assert et.value == "ca1_cm1_animals_carcass_large_predator_carcass_a1b2c3d4"


# ── build_event_types_v2 split / grouping ─────────────────────────


def test_build_event_types_v2_split_emits_one_per_variant():
    from er_smart_sync.smart_to_er_v2 import build_event_types_v2

    cm = {
        "cm_uuid": "cm1",
        "categories": [
            {"path": "carcass.lp", "hkeyPath": "animals.carcass", "display": "Large Predator Carcass",
             "id": "n1", "attributes": [{"key": "age"}]},
            {"path": "carcass.sp", "hkeyPath": "animals.carcass", "display": "Small Predator Carcass",
             "id": "n2", "attributes": [{"key": "age"}]},
        ],
        "attributes": [],
    }
    dm = {"attributes": [{"key": "age", "type": "NUMERIC", "display": "Age"}]}
    ets = build_event_types_v2(dm=dm, cm=cm, ca_uuid="ca1", ca_identifier="CA", cm_variant_mode="split")
    values = sorted(e.value for e in ets)
    assert len(values) == 2
    assert all(v.startswith("ca1_cm1_animals_carcass_") for v in values)
    assert values[0] != values[1]


def test_build_event_types_v2_singleton_unchanged():
    from er_smart_sync.smart_to_er_v2 import build_event_types_v2
    cm = {
        "cm_uuid": "cm1",
        "categories": [
            {"path": "incident", "hkeyPath": "incidents.report", "display": "Report",
             "id": "n9", "attributes": [{"key": "age"}]},
        ],
        "attributes": [],
    }
    dm = {"attributes": [{"key": "age", "type": "NUMERIC", "display": "Age"}]}
    ets = build_event_types_v2(dm=dm, cm=cm, ca_uuid="ca1", ca_identifier="CA", cm_variant_mode="split")
    assert len(ets) == 1
    assert ets[0].value == "ca1_cm1_incidents_report"   # no disambiguator for singletons


# ── Consolidate mode ──────────────────────────────────────────────


def test_build_consolidated_emits_discriminator_and_conditional_sections():
    from er_smart_sync.smart_to_er_v2 import build_event_types_v2

    cm = {
        "cm_uuid": "cm1",
        "categories": [
            {"path": "carcass.lp", "hkeyPath": "animals.carcass", "display": "Large Predator Carcass",
             "id": "n1", "attributes": [{"key": "age"}]},
            {"path": "carcass.sp", "hkeyPath": "animals.carcass", "display": "Small Predator Carcass",
             "id": "n2", "attributes": [{"key": "lc"}]},
        ],
        "attributes": [],
    }
    dm = {"attributes": [
        {"key": "age", "type": "NUMERIC", "display": "Age"},
        {"key": "lc", "type": "NUMERIC", "display": "Large Carnivore"},
    ]}
    ets = build_event_types_v2(dm=dm, cm=cm, ca_uuid="ca1", ca_identifier="CA", cm_variant_mode="consolidate")
    assert len(ets) == 1
    et = ets[0]
    assert et.value == "ca1_cm1_animals_carcass"
    assert et.display == "Carcass"
    schema = et.event_schema
    ui = schema["ui"]
    # one section per variant + the discriminator section
    assert len(ui["sections"]) == 3
    assert ui["order"][0] == "section-1"
    # discriminator field present and required
    disc = next(k for k in schema["json"]["properties"] if k.endswith("_variant"))
    assert disc in schema["json"]["required"]
    # each variant section carries an IS_EXACTLY condition on the discriminator
    variant_sections = [s for sid, s in ui["sections"].items() if sid != "section-1"]
    for s in variant_sections:
        cond = s["conditions"][0]
        assert cond["operator"] == "IS_EXACTLY"
        assert cond["field"] == disc
        assert cond["id"].startswith("condition-")
    # discriminator field lists the variant sections as conditionalDependents
    dep = ui["fields"][disc]["conditionalDependents"]
    assert set(dep) == {sid for sid in ui["sections"] if sid != "section-1"}
    # variant attribute fields are namespaced (e.g. section_2_age) and
    # re-parented to their own section (not the default section-1), so
    # conditional visibility actually hides them.
    field_keys = [k for k in ui["fields"] if k.endswith("_age") or k.endswith("_lc")]
    assert len(field_keys) == 2, f"expected 2 namespaced variant fields, got {field_keys}"
    for fk in field_keys:
        assert ui["fields"][fk]["parent"] != "section-1", (
            f"expected {fk} re-parented away from section-1"
        )
        # Each namespaced key must also appear in the JSON schema properties.
        assert fk in schema["json"]["properties"], (
            f"namespaced key {fk!r} missing from json.properties"
        )
    parents = {ui["fields"][fk]["parent"] for fk in field_keys}
    assert len(parents) == 2, f"expected 2 distinct parent sections, got {parents}"


def test_build_consolidated_handles_shared_attribute_across_variants():
    """When two variants share an attribute key, the consolidate builder must
    namespace per variant to avoid property-key collision."""
    from er_smart_sync.smart_to_er_v2 import build_event_types_v2

    cm = {
        "cm_uuid": "cm1",
        "categories": [
            {"path": "carcass.lp", "hkeyPath": "animals.carcass", "display": "Large Predator Carcass",
             "id": "n1", "attributes": [{"key": "age"}]},
            {"path": "carcass.sp", "hkeyPath": "animals.carcass", "display": "Small Predator Carcass",
             "id": "n2", "attributes": [{"key": "age"}]},  # SAME attribute key
        ],
        "attributes": [],
    }
    dm = {"attributes": [{"key": "age", "type": "NUMERIC", "display": "Age"}]}
    ets = build_event_types_v2(dm=dm, cm=cm, ca_uuid="ca1", ca_identifier="CA",
                               cm_variant_mode="consolidate")
    assert len(ets) == 1
    props = ets[0].event_schema["json"]["properties"]
    fields = ets[0].event_schema["ui"]["fields"]
    # Both variants' "age" fields must coexist as distinct properties.
    age_keys = [k for k in props if k.endswith("_age")]
    assert len(age_keys) == 2, f"expected 2 namespaced age fields, got {age_keys}"
    # And they live in different sections.
    parents = {fields[k]["parent"] for k in age_keys}
    assert len(parents) == 2, f"expected 2 distinct parents, got {parents}"
