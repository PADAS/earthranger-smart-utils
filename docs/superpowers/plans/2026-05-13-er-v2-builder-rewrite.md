# ER v2 builder rewrite — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rewrite `smart_to_er_v2.py` so the v2 schema it emits passes ER's actual v2 meta-schema validation (no more inline `enum`, proper `unevaluatedProperties` envelope, full ui block with `headers`/`rightColumn`/`parent`, `deprecated`+`description` on every property, `$ref` URLs for choice attributes).

**Architecture:** Replace property emission and envelope shapes per the parent v2 spec. The choices module (already in place) supplies `derive_choice_field` for `$ref` URLs. The synchronizer's two-pass orchestration is already wired; this plan only changes the shape of what the builder produces, plus passes `choices_base_url` from config through to the builder.

**Tech Stack:** Python 3.10+, Pydantic v1, pytest, ruff, ty.

**Parent spec:** `docs/superpowers/specs/2026-05-12-er-v2-event-types-design.md`
**Reference example:** `/Users/chrisdo/padas/das/das/activity/tests/schemas_v2/test_event_export.py:25-277` (`CARCASS_V2_EVENTTYPE_SCHEMA`)
**Choices module landed in:** prior plan `2026-05-13-er-v2-choices-population.md`

---

## File Structure

| Path | Action | Responsibility |
|---|---|---|
| `src/er_smart_sync/smart_to_er_v2.py` | rewrite body | Emit v2 meta-schema-valid `ERV2EventType`s. Constants `SCALAR_JSON`/`SCALAR_UI` updated per parent spec. `_build_property_pair` rewritten for `$ref`. `_build_one` rewritten for `unevaluatedProperties`, full `ui` envelope, inactive-skip. New `choices_base_url` parameter on `build_event_types_v2`. |
| `src/er_smart_sync/synchronizer.py` | minor wiring | Pass `self.config.earthranger.choices_base_url` to `build_event_types_v2`. |
| `tests/test_smart_to_er_v2.py` | rewrite assertions | Update existing test assertions to new shapes; add snapshot test. |
| `tests/test_synchronizer.py` | small update | Update `test_v2_post_event_type_passes_version_kwarg` if it asserts schema shape. |

No new files. The whole rewrite stays inside the existing v2 builder module.

---

## Conventions for every task

- TDD: rewrite the failing test → run red → update implementation → run green → commit.
- The existing `tests/test_smart_to_er_v2.py` is full of assertions on the *old* shape. This plan replaces them incrementally per type. After each task, the suite must be fully green.
- Pydantic v1 only.
- After every code change run the full suite: `.venv/bin/pytest -q`. v1 path (default) must stay green throughout.
- Branch already in use: `feature/er-v2-event-types`. Commit there.
- Commit messages use conventional-commits style (`feat:`, `refactor:`, `test:`), one task per commit.
- The v2 `event_type_version` config default stays `"v1"` (Phase 0 already flipped it). This plan does not touch the default; Phase 4 in the parent spec handles that after manual smoke verification.

---

## Task 1: Plumb `choices_base_url` through `build_event_types_v2`

**Files:**
- Modify: `src/er_smart_sync/smart_to_er_v2.py`
- Modify: `src/er_smart_sync/synchronizer.py`
- Modify: `tests/test_smart_to_er_v2.py`
- Modify: `tests/test_synchronizer.py`

- [ ] **Step 1: Write failing test**

Append to `tests/test_smart_to_er_v2.py`:

```python
def test_build_event_types_v2_accepts_choices_base_url():
    """The builder takes choices_base_url to construct $ref URLs."""
    dm = {
        "categories": [_category("c", attributes=[_cat_attr("a")])],
        "attributes": [_attr("a", "TEXT")],
    }
    # Should not raise on the kwarg.
    result = build_event_types_v2(
        dm=dm, cm=None, ca_uuid=CA_UUID, ca_identifier=CA_ID,
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
        dm=dm, cm=None, ca_uuid=CA_UUID, ca_identifier=CA_ID,
    )
    assert len(result) == 1
```

- [ ] **Step 2: Run, verify red**

Run: `.venv/bin/pytest tests/test_smart_to_er_v2.py -v -k choices_base_url`
Expected: TypeError — `build_event_types_v2` doesn't accept that kwarg yet.

- [ ] **Step 3: Implement signature change**

In `src/er_smart_sync/smart_to_er_v2.py`, update the `build_event_types_v2` signature. The current declaration is:

```python
def build_event_types_v2(
    *,
    dm: dict,
    cm: dict | None = None,
    ca_uuid: str,
    ca_identifier: str,
) -> list[ERV2EventType]:
```

Change to:

```python
def build_event_types_v2(
    *,
    dm: dict,
    cm: dict | None = None,
    ca_uuid: str,
    ca_identifier: str,
    choices_base_url: str = "/api/v2.0/schemas",
) -> list[ERV2EventType]:
```

Thread `choices_base_url` through `_build_one` (add same kwarg with same default) and `_build_field_blocks` and `_build_property_pair` (add same kwarg, no default at internal call sites). These helpers don't yet use the parameter; that comes in Task 5. For now, just thread it through.

The call from `build_event_types_v2` → `_build_one`:

```python
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
```

The call from `_build_one` → `_build_field_blocks`:

```python
    properties, ui_fields, field_order = _build_field_blocks(
        attributes=attributes,
        leaf_attributes=leaf_attributes,
        is_multiple=cat.is_multiple,
        attribute_configs=attribute_configs,
        choices_base_url=choices_base_url,
    )
```

The call from `_build_field_blocks` → `_build_property_pair`:

```python
        json_prop, ui_field = _build_property_pair(
            smart_type=smart_type,
            display=attribute.display,
            options=options,
            is_multiple=is_multiple,
            choices_base_url=choices_base_url,
        )
```

Add the parameter to all three helper signatures (`_build_one`, `_build_field_blocks`, `_build_property_pair`). For `_build_one` and `_build_field_blocks` and `_build_property_pair`, just accept the parameter and pass it through — no use yet.

- [ ] **Step 4: Update synchronizer wiring**

In `src/er_smart_sync/synchronizer.py`, find the v2 builder call (in `push_smart_ca_datamodel_to_earthranger`). It currently looks like:

```python
        builder = (
            build_event_types_v2
            if self._event_type_version == "v2"
            else build_event_types
        )
        event_types = builder(
            dm=dm_dict,
            cm=cdm_dict,
            ca_uuid=smart_ca_uuid,
            ca_identifier=ca_identifier,
        )
```

Replace with branched call (we can't pass `choices_base_url` to the v1 builder):

```python
        if self._event_type_version == "v2":
            event_types = build_event_types_v2(
                dm=dm_dict,
                cm=cdm_dict,
                ca_uuid=smart_ca_uuid,
                ca_identifier=ca_identifier,
                choices_base_url=self.config.earthranger.choices_base_url,
            )
        else:
            event_types = build_event_types(
                dm=dm_dict,
                cm=cdm_dict,
                ca_uuid=smart_ca_uuid,
                ca_identifier=ca_identifier,
            )
```

- [ ] **Step 5: Run, verify green**

```
.venv/bin/pytest tests/test_smart_to_er_v2.py -v
.venv/bin/pytest -q
.venv/bin/ruff check src tests
```
Expected: 2 new tests pass; full suite green.

- [ ] **Step 6: Commit**

```
git add src/er_smart_sync/smart_to_er_v2.py src/er_smart_sync/synchronizer.py tests/test_smart_to_er_v2.py
git commit -m "feat(v2): plumb choices_base_url through build_event_types_v2"
```

---

## Task 2: `unevaluatedProperties` instead of `additionalProperties` in json envelope

Per parent spec (line 137-149), ER's v2 meta-schema requires `unevaluatedProperties: False`, not `additionalProperties: False`. They mean different things in JSON Schema 2020-12.

**Files:**
- Modify: `src/er_smart_sync/smart_to_er_v2.py`
- Modify: `tests/test_smart_to_er_v2.py`

- [ ] **Step 1: Rewrite failing test**

Find `test_envelope_top_level_keys` in `tests/test_smart_to_er_v2.py`. It currently asserts `additionalProperties is False`. Replace the entire test with:

```python
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
    assert schema["json"]["unevaluatedProperties"] is False
    assert "additionalProperties" not in schema["json"]
    assert schema["json"]["required"] == []
```

- [ ] **Step 2: Run, verify red**

Run: `.venv/bin/pytest tests/test_smart_to_er_v2.py::test_envelope_top_level_keys -v`
Expected: KeyError on `unevaluatedProperties` (still using `additionalProperties`).

- [ ] **Step 3: Implement**

In `src/er_smart_sync/smart_to_er_v2.py`, find `_build_one`'s assignment of `et.event_schema`. Currently:

```python
    et.event_schema = {
        "json": {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "additionalProperties": False,
            "properties": properties,
            "required": [],
        },
        ...
```

Replace `"additionalProperties": False` with `"unevaluatedProperties": False`. Keep everything else as-is for now.

- [ ] **Step 4: Run, verify green**

```
.venv/bin/pytest tests/test_smart_to_er_v2.py -v
.venv/bin/pytest -q
```

- [ ] **Step 5: Commit**

```
git add src/er_smart_sync/smart_to_er_v2.py tests/test_smart_to_er_v2.py
git commit -m "fix(v2): use unevaluatedProperties instead of additionalProperties"
```

---

## Task 3: Full ui envelope — `headers`, `rightColumn`, `parent`

Parent spec (line 151-170) requires:
- `ui.headers: {}` at top level
- `ui.sections.section-1.rightColumn: []`
- Every `ui.fields[k]` has `parent: "section-1"`

**Files:**
- Modify: `src/er_smart_sync/smart_to_er_v2.py`
- Modify: `tests/test_smart_to_er_v2.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_smart_to_er_v2.py`:

```python
def test_ui_envelope_has_required_keys():
    """ui must have fields, headers, order, sections per the v2 meta-schema."""
    dm = {
        "categories": [_category("c", attributes=[_cat_attr("a")])],
        "attributes": [_attr("a", "TEXT")],
    }
    schema = build_event_types_v2(
        dm=dm, cm=None, ca_uuid=CA_UUID, ca_identifier=CA_ID
    )[0].event_schema

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
    schema = build_event_types_v2(
        dm=dm, cm=None, ca_uuid=CA_UUID, ca_identifier=CA_ID
    )[0].event_schema

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
    schema = build_event_types_v2(
        dm=dm, cm=None, ca_uuid=CA_UUID, ca_identifier=CA_ID
    )[0].event_schema

    for field_name, field_block in schema["ui"]["fields"].items():
        assert field_block.get("parent") == "section-1", (
            f"ui.fields[{field_name}] missing parent='section-1'"
        )
```

- [ ] **Step 2: Run, verify red**

Run: `.venv/bin/pytest tests/test_smart_to_er_v2.py -v -k "ui_envelope or ui_section or ui_field_has_parent"`
Expected: failures — `headers` missing, `rightColumn` missing, `parent` missing.

- [ ] **Step 3: Implement**

A) In `src/er_smart_sync/smart_to_er_v2.py`'s `_build_one`, find the `"ui": {...}` block. Currently:

```python
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
```

Replace with:

```python
        "ui": {
            "fields": ui_fields,
            "headers": {},
            "order": ["section-1"],
            "sections": {
                "section-1": {
                    "label": "Details",
                    "columns": 1,
                    "isActive": True,
                    "leftColumn": [
                        {"name": k, "type": "field"} for k in field_order
                    ],
                    "rightColumn": [],
                }
            },
        },
```

B) Update `SCALAR_UI` to include `parent: "section-1"` on every entry. Find the existing `SCALAR_UI` dict and update each value:

```python
SCALAR_UI: dict[str, dict[str, Any]] = {
    "TEXT": {"type": "TEXT", "inputType": "SHORT_TEXT", "parent": "section-1"},
    "NUMERIC": {"type": "NUMBER", "parent": "section-1"},
    "BOOLEAN": {"type": "BOOLEAN", "parent": "section-1"},
    "DATE": {"type": "TEXT", "inputType": "DATE", "parent": "section-1"},
    "TIME": {"type": "TEXT", "inputType": "TIME", "parent": "section-1"},
    "DATETIME": {"type": "TEXT", "inputType": "DATETIME", "parent": "section-1"},
    "ATTACHMENT": {
        "type": "ATTACHMENT",
        "allowableFileTypes": ["image", "document", "video", "audio"],
        "parent": "section-1",
    },
}
```

(We're temporarily leaving NUMERIC's ui type as `"NUMBER"` and DATE/TIME/DATETIME with the old shape — Task 4 fixes those.)

C) The choice path in `_build_property_pair` returns inline `CHOICE_LIST` ui blocks. Update both branches (the `is_array` and the `else`) to include `"parent": "section-1"`. Find the section in `_build_property_pair` that currently looks like:

```python
    if is_array:
        json_prop = {...}
        ui_field = {
            "type": "CHOICE_LIST",
            "inputType": "CHECKBOX",
            "choices": choices,
        }
    else:
        json_prop = {...}
        ui_field = {
            "type": "CHOICE_LIST",
            "inputType": "DROPDOWN",
            "choices": choices,
        }
```

Add `"parent": "section-1"` to both `ui_field` dicts. (Task 5 rewrites these whole branches; for now we just add `parent` so test_ui_field_has_parent passes for choice-type attributes.)

Also update the unknown-type fallback ui field in `_build_property_pair`:

```python
        return (
            {"type": "string", "title": display},
            {"type": "TEXT", "inputType": "SHORT_TEXT", "parent": "section-1"},
        )
```

- [ ] **Step 4: Run, verify green**

```
.venv/bin/pytest tests/test_smart_to_er_v2.py -v
.venv/bin/pytest -q
.venv/bin/ruff check src tests
```

All existing tests should still pass (we only added keys, didn't remove any), plus the 3 new ui tests.

- [ ] **Step 5: Commit**

```
git add src/er_smart_sync/smart_to_er_v2.py tests/test_smart_to_er_v2.py
git commit -m "feat(v2): emit headers, rightColumn, parent in ui envelope"
```

---

## Task 4: Per-property `deprecated` + `description` everywhere; UI type renames

Parent spec (lines 67-77, 134-135): every property requires `deprecated: <bool>` (True for inactive, False for active). Reference example always emits `description: ""`. UI type for NUMERIC is `"NUMERIC"` (not `"NUMBER"`); UI for DATE/TIME/DATETIME is `{type: "DATE_TIME", parent: "section-1"}` (no inputType).

**Files:**
- Modify: `src/er_smart_sync/smart_to_er_v2.py`
- Modify: `tests/test_smart_to_er_v2.py`

- [ ] **Step 1: Rewrite failing tests**

The existing scalar parametrize test (`test_scalar_attribute_mapping`) hardcodes `"NUMBER"`, `"DATE"`/`"TIME"`/`"DATETIME"` for ui types and asserts no `description`/`deprecated`. Replace the entire test plus add deprecated assertions:

```python
@pytest.mark.parametrize(
    "smart_type,expected_json,expected_ui",
    [
        ("TEXT", {"type": "string"}, {"type": "TEXT", "inputType": "SHORT_TEXT"}),
        ("NUMERIC", {"type": "number"}, {"type": "NUMERIC"}),
        ("BOOLEAN", {"type": "boolean"}, {"type": "BOOLEAN"}),
        ("DATE", {"type": "string", "format": "date"}, {"type": "DATE_TIME"}),
        ("TIME", {"type": "string", "format": "time"}, {"type": "DATE_TIME"}),
        ("DATETIME", {"type": "string", "format": "date-time"}, {"type": "DATE_TIME"}),
        ("ATTACHMENT", {"type": "string", "format": "uri"},
         {"type": "ATTACHMENT", "allowableFileTypes": ["image", "document", "video", "audio"]}),
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
```

Also update `test_inactive_attribute_marked_deprecated_and_kept_in_section` to assert ACTIVE attributes have `deprecated: False`:

```python
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
    schema = build_event_types_v2(
        dm=dm, cm=None, ca_uuid=CA_UUID, ca_identifier=CA_ID
    )[0].event_schema

    # Both have `deprecated`; only the inactive one is True.
    assert schema["json"]["properties"]["retired_attr"]["deprecated"] is True
    assert schema["json"]["properties"]["active_attr"]["deprecated"] is False

    # Both fields still listed in the form section
    leftCol = schema["ui"]["sections"]["section-1"]["leftColumn"]
    names = [item["name"] for item in leftCol]
    assert "active_attr" in names
    assert "retired_attr" in names
```

- [ ] **Step 2: Run, verify red**

Run: `.venv/bin/pytest tests/test_smart_to_er_v2.py -v -k "scalar_attribute_mapping or inactive_attribute"`
Expected: failures — NUMERIC ui still emits `"NUMBER"`; DATE/TIME/DATETIME still have `inputType`; no `description`/`deprecated` on active props.

- [ ] **Step 3: Implement**

A) In `src/er_smart_sync/smart_to_er_v2.py`, update `SCALAR_JSON` to include `description: ""`. Replace:

```python
SCALAR_JSON: dict[str, dict[str, Any]] = {
    "TEXT": {"type": "string"},
    "NUMERIC": {"type": "number"},
    "BOOLEAN": {"type": "boolean"},
    "DATE": {"type": "string", "format": "date"},
    "TIME": {"type": "string", "format": "time"},
    "DATETIME": {"type": "string", "format": "date-time"},
    "ATTACHMENT": {"type": "string", "format": "uri"},
}
```

with:

```python
SCALAR_JSON: dict[str, dict[str, Any]] = {
    "TEXT": {"type": "string", "description": ""},
    "NUMERIC": {"type": "number", "description": ""},
    "BOOLEAN": {"type": "boolean", "description": ""},
    "DATE": {"type": "string", "format": "date", "description": ""},
    "TIME": {"type": "string", "format": "time", "description": ""},
    "DATETIME": {"type": "string", "format": "date-time", "description": ""},
    "ATTACHMENT": {"type": "string", "format": "uri", "description": ""},
}
```

B) Update `SCALAR_UI` for NUMERIC and DATE/TIME/DATETIME:

```python
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
```

C) In `_build_field_blocks`, currently the deprecated marker is conditional:

```python
        if not cat_attr.is_active:
            json_prop["deprecated"] = True

        properties[key] = json_prop
```

Replace with unconditional assignment so active attributes get `deprecated: False`:

```python
        json_prop["deprecated"] = not cat_attr.is_active

        properties[key] = json_prop
```

- [ ] **Step 4: Run, verify green**

```
.venv/bin/pytest tests/test_smart_to_er_v2.py -v
.venv/bin/pytest -q
.venv/bin/ruff check src tests
```

- [ ] **Step 5: Commit**

```
git add src/er_smart_sync/smart_to_er_v2.py tests/test_smart_to_er_v2.py
git commit -m "feat(v2): emit deprecated+description on every property; rename NUMERIC/DATE_TIME ui"
```

---

## Task 5: LIST single / TREE — `anyOf $ref` instead of inline `enum`

Per parent spec (lines 83-108): single-CHOICE_LIST emits `anyOf: [{$ref: "<base>/choices.json?field=<field>"}]` and a CHOICE_LIST ui block with full `choices` sub-block.

**Files:**
- Modify: `src/er_smart_sync/smart_to_er_v2.py`
- Modify: `tests/test_smart_to_er_v2.py`

- [ ] **Step 1: Rewrite failing tests**

Replace `test_list_single_value_emits_enum_and_dropdown` with the new shape:

```python
def test_list_single_value_emits_anyof_ref_and_choice_list():
    from er_smart_sync.choices import derive_choice_field, event_type_value_for

    dm = {
        "categories": [_category("c", attributes=[_cat_attr("color")])],
        "attributes": [
            _attr(
                "color", "LIST", display="Color",
                options=[_option("red", "Red"), _option("blue", "Blue")],
            )
        ],
    }
    schema = build_event_types_v2(
        dm=dm, cm=None, ca_uuid=CA_UUID, ca_identifier=CA_ID,
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


def test_tree_flattens_to_leaf_options():
    """TREE flattening still happens at the builder; choices module emits the leaves."""
    from er_smart_sync.choices import derive_choice_field, event_type_value_for

    dm = {
        "categories": [_category("c", attributes=[_cat_attr("region")])],
        "attributes": [
            _attr(
                "region", "TREE", display="Region",
                options=[
                    _option("africa"),
                    _option("africa.kenya"),
                    _option("africa.kenya.nairobi"),
                    _option("africa.tanzania"),
                ],
            )
        ],
    }
    schema = build_event_types_v2(
        dm=dm, cm=None, ca_uuid=CA_UUID, ca_identifier=CA_ID
    )[0].event_schema

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
```

- [ ] **Step 2: Run, verify red**

Run: `.venv/bin/pytest tests/test_smart_to_er_v2.py -v -k "list_single_value_emits_anyof or tree_flattens"`
Expected: failures — currently emits inline `enum`, not `anyOf $ref`.

- [ ] **Step 3: Implement**

In `src/er_smart_sync/smart_to_er_v2.py`:

A) Add an import at the top alongside existing imports:

```python
from .choices import derive_choice_field, event_type_value_for
```

B) `_build_one` needs to pass the computed `event_type_value` down so `_build_property_pair` can call `derive_choice_field(event_type_value, attr_key)`. Find the spot in `_build_one` where `value = value.lower()` and add right after it:

```python
    # Pass event_type_value down so choice properties can derive their
    # Choice.field $ref URL. Same string the choices module uses.
    et_value = value
```

(Or just reuse `value` directly; either works. Define `et_value = value` for clarity since `value` gets assigned to the ERV2EventType.)

Then pass `et_value` through `_build_field_blocks`. Update the call:

```python
    properties, ui_fields, field_order = _build_field_blocks(
        attributes=attributes,
        leaf_attributes=leaf_attributes,
        is_multiple=cat.is_multiple,
        attribute_configs=attribute_configs,
        choices_base_url=choices_base_url,
        event_type_value=et_value,
    )
```

Add `event_type_value` parameter to `_build_field_blocks`. Inside, pass it to `_build_property_pair`:

```python
        json_prop, ui_field = _build_property_pair(
            smart_type=smart_type,
            display=attribute.display,
            options=options,
            is_multiple=is_multiple,
            attr_key=cat_attr.key,
            choices_base_url=choices_base_url,
            event_type_value=event_type_value,
        )
```

Add `attr_key` and `event_type_value` parameters to `_build_property_pair`.

C) Rewrite the choice branch in `_build_property_pair`. Find the current `if options:` branch (everything after the scalar handling). The current logic builds inline-enum json + CHOICE_LIST ui. Replace the entire post-scalar block with:

```python
    if not options:
        if smart_type in {"LIST", "MLIST", "TREE"}:
            logger.warning(
                "All options filtered out for %r choice attribute; "
                "emitting plain string",
                smart_type,
            )
        else:
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
```

(`deprecated` is set to `False` here as a baseline; `_build_field_blocks` overwrites it from `cat_attr.is_active` on the next line, same as scalar path.)

Note: the spec mentions multi-CHOICE_LIST `inputType` can be either `"DROPDOWN"` or `"LIST"`. We use `"DROPDOWN"` for both single and multi for consistency with the carcass reference (which uses `DROPDOWN` for everything).

D) Make sure `attr_key` is passed to `_build_property_pair` from `_build_field_blocks`. The for-loop in `_build_field_blocks` looks like:

```python
    for cat_attr in leaf_attributes:
        key = cat_attr.key
        ...
        json_prop, ui_field = _build_property_pair(
            ...
            attr_key=key,
            ...
        )
```

- [ ] **Step 4: Cleanup obsolete CM test**

The existing `test_configurable_model_filters_options` asserts `schema["json"]["properties"]["color"]["enum"] == ["red", "green"]`. After this rewrite, the v2 builder no longer emits inline `enum` — option filtering happens in the choices module, not in the v2 schema. The behavior the test was guarding has moved to `tests/test_choices.py::test_build_choice_sets_cm_filters_active_options` (already covered as of the previous plan).

**Delete** `test_configurable_model_filters_options` from `tests/test_smart_to_er_v2.py`. Keep `test_configurable_model_event_type_value_includes_cm_uuid` — it only asserts the event-type value slug, which is still relevant.

- [ ] **Step 5: Run, verify green**

```
.venv/bin/pytest tests/test_smart_to_er_v2.py -v
.venv/bin/pytest -q
.venv/bin/ruff check src tests
```

Note: the existing `test_list_multi_value_emits_array_enum_and_checkbox` and `test_mlist_emits_array_enum_and_checkbox` will now fail because they assert the old `items.enum` shape. Task 6 fixes those.

If those tests fail (they will), mark them with `@pytest.mark.xfail(reason="Task 6 rewrites this assertion")` to keep the run green during this commit. Then remove the xfail and replace the tests in Task 6.

- [ ] **Step 6: Commit**

```
git add src/er_smart_sync/smart_to_er_v2.py tests/test_smart_to_er_v2.py
git commit -m "feat(v2): emit anyOf \$ref for single-choice and TREE attributes"
```

---

## Task 6: LIST multi / MLIST — `array` + `items.anyOf` + `uniqueItems`

Parent spec (lines 110-131): array shape with `items: {type: "string", anyOf: [{$ref}]}`, top-level `uniqueItems: True`, outer `deprecated` + `description` + `title`. Same CHOICE_LIST ui as single.

This is mostly already implemented in Task 5 (`is_array` branch). Task 6 just removes the xfail markers and verifies the multi/MLIST tests pass.

**Files:**
- Modify: `tests/test_smart_to_er_v2.py`

- [ ] **Step 1: Remove xfails and rewrite the multi/MLIST tests**

Replace `test_list_multi_value_emits_array_enum_and_checkbox` and `test_mlist_emits_array_enum_and_checkbox` with these (drop any `xfail` markers added in Task 5):

```python
def test_list_multi_value_emits_array_anyof_ref():
    from er_smart_sync.choices import derive_choice_field, event_type_value_for

    dm = {
        "categories": [
            _category("c", is_multiple=True, attributes=[_cat_attr("tags")]),
        ],
        "attributes": [
            _attr(
                "tags", "LIST", display="Tags",
                options=[_option("a"), _option("b")],
            )
        ],
    }
    schema = build_event_types_v2(
        dm=dm, cm=None, ca_uuid=CA_UUID, ca_identifier=CA_ID,
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
                "species", "MLIST", display="Species",
                options=[_option("lion"), _option("zebra")],
            )
        ],
    }
    schema = build_event_types_v2(
        dm=dm, cm=None, ca_uuid=CA_UUID, ca_identifier=CA_ID,
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
```

- [ ] **Step 2: Run, verify green**

```
.venv/bin/pytest tests/test_smart_to_er_v2.py -v
.venv/bin/pytest -q
```

Both new multi/MLIST tests should pass on the existing implementation from Task 5 (the `is_array` branch in `_build_property_pair`). No new implementation needed.

- [ ] **Step 3: Commit**

```
git add tests/test_smart_to_er_v2.py
git commit -m "test(v2): assert array+anyOf+uniqueItems for LIST multi and MLIST"
```

---

## Task 7: Inactive non-CM categories — skip entirely

Parent spec (lines 172-178): inactive SMART categories without a CM context produce no v2 event type at all (not an `is_active=False` stub).

**Files:**
- Modify: `src/er_smart_sync/smart_to_er_v2.py`
- Modify: `tests/test_smart_to_er_v2.py`

- [ ] **Step 1: Add failing test**

Find any existing test that exercises the inactive-leaf-no-CM path (`test_build_choice_sets_skips_inactive_categories_without_cm` is in `test_choices.py`; check `test_smart_to_er_v2.py` for similar). If no such test exists for the builder specifically, append:

```python
def test_build_event_types_v2_skips_inactive_non_cm_categories():
    """Inactive leaf categories without a CM produce no event type at all."""
    dm = {
        "categories": [
            _category("c", attributes=[_cat_attr("a")], is_active=False),
        ],
        "attributes": [_attr("a", "TEXT")],
    }
    result = build_event_types_v2(
        dm=dm, cm=None, ca_uuid=CA_UUID, ca_identifier=CA_ID,
    )
    assert result == []
```

- [ ] **Step 2: Run, verify red**

Run: `.venv/bin/pytest tests/test_smart_to_er_v2.py -v -k skips_inactive_non_cm`
Expected: failure — currently returns `[ERV2EventType(is_active=False)]` (a stub).

- [ ] **Step 3: Implement**

In `src/er_smart_sync/smart_to_er_v2.py`, find `_build_one`. The current logic near the top is:

```python
def _build_one(
    ...
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
        # Active=False categories still get registered so ER can show them
        # as deactivated; no schema needed.
        return et
```

Change the early-return to return None:

```python
def _build_one(
    ...
) -> ERV2EventType | None:
    is_leaf = _is_leaf_node(cat_paths, cat.path)
    is_active = bool(cm) or (cat.is_active and is_leaf)

    if not is_active:
        # v2 has no equivalent of v1's "inactive event type" record — there
        # is no schema-less POST shape that passes the meta-schema. Skip.
        return None

    path_components = cat.hkeyPath.split(".") if cm else cat.path.split(".")
    value_suffix = "_".join(path_components)
    if cm:
        value = f'{ca_uuid}_{cm["cm_uuid"]}_{value_suffix}'
    else:
        value = f"{ca_uuid}_{value_suffix}"
    value = value.lower()
    et_value = value

    et = ERV2EventType(value=value, display=cat.display, is_active=True)
```

(Note: `is_active=True` is the only state we emit now since `is_active=False` is the skip path. Also the `et_value = value` assignment was added in Task 5.)

- [ ] **Step 4: Run, verify green**

```
.venv/bin/pytest tests/test_smart_to_er_v2.py -v
.venv/bin/pytest -q
```

- [ ] **Step 5: Commit**

```
git add src/er_smart_sync/smart_to_er_v2.py tests/test_smart_to_er_v2.py
git commit -m "feat(v2): skip inactive non-CM categories entirely (no schemaless POST)"
```

---

## Task 8: Snapshot test covering the full mix

Parent spec (line 224): ship a SMART data-model fixture covering every attribute type + an inactive attribute, and snapshot-compare the v2 builder's output against an inline expected dict.

**Files:**
- Modify: `tests/test_smart_to_er_v2.py`

- [ ] **Step 1: Add the snapshot test**

Append to `tests/test_smart_to_er_v2.py`:

```python
def test_snapshot_full_mix_of_types():
    """Single event type with every supported SMART attribute type."""
    from er_smart_sync.choices import derive_choice_field, event_type_value_for

    dm = {
        "categories": [
            _category("incidents", attributes=[
                _cat_attr("title"),
                _cat_attr("count"),
                _cat_attr("confirmed"),
                _cat_attr("when_date"),
                _cat_attr("photo"),
                _cat_attr("species"),
                _cat_attr("tags"),
                _cat_attr("legacy_field", is_active=False),
            ]),
        ],
        "attributes": [
            _attr("title", "TEXT", display="Title"),
            _attr("count", "NUMERIC", display="Count"),
            _attr("confirmed", "BOOLEAN", display="Confirmed"),
            _attr("when_date", "DATE", display="When"),
            _attr("photo", "ATTACHMENT", display="Photo"),
            _attr("species", "LIST", display="Species",
                  options=[_option("lion", "Lion"), _option("zebra", "Zebra")]),
            _attr("tags", "MLIST", display="Tags",
                  options=[_option("a"), _option("b")]),
            _attr("legacy_field", "TEXT", display="Legacy"),
        ],
    }
    result = build_event_types_v2(
        dm=dm, cm=None, ca_uuid="ca-snap", ca_identifier="SNAP",
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
    for k in ("title", "count", "confirmed", "when_date", "photo",
              "species", "tags", "legacy_field"):
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
        category_path="incidents", ca_uuid="ca-snap", cm=None,
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
        assert ui_block.get("parent") == "section-1", (
            f"ui.fields[{key}] missing parent"
        )

    # Scalar ui types
    assert schema["ui"]["fields"]["title"]["type"] == "TEXT"
    assert schema["ui"]["fields"]["count"]["type"] == "NUMERIC"
    assert schema["ui"]["fields"]["confirmed"]["type"] == "BOOLEAN"
    assert schema["ui"]["fields"]["when_date"]["type"] == "DATE_TIME"
    assert schema["ui"]["fields"]["photo"]["type"] == "ATTACHMENT"
    assert schema["ui"]["fields"]["species"]["type"] == "CHOICE_LIST"
    assert schema["ui"]["fields"]["tags"]["type"] == "CHOICE_LIST"
```

- [ ] **Step 2: Run, verify green**

```
.venv/bin/pytest tests/test_smart_to_er_v2.py::test_snapshot_full_mix_of_types -v
.venv/bin/pytest -q
```

The test should pass on the existing implementation from Tasks 1-7. If anything fails, that's a real bug — fix it (which probably means a small implementation tweak) before committing.

- [ ] **Step 3: Commit**

```
git add tests/test_smart_to_er_v2.py
git commit -m "test(v2): snapshot test covering full mix of attribute types"
```

---

## Task 9: Final integration smoke

- [ ] **Step 1: Run full test suite**

```
.venv/bin/pytest -q
```
Expected: all tests pass (was 175 from prior plan; expect 175+ after this plan's additions).

- [ ] **Step 2: Lint and format**

```
.venv/bin/ruff check src tests
.venv/bin/ruff format --check src tests
```

If `ruff format --check` complains, run `.venv/bin/ruff format src tests` and check the diff. Only commit format changes to files this PLAN touched (smart_to_er_v2.py, synchronizer.py, test_smart_to_er_v2.py).

If `ruff check` flags new issues (compared to baseline), fix them. Pre-existing E501 etc. stays.

- [ ] **Step 3: Type check**

```
.venv/bin/ty check
```

Address only regressions introduced by this plan. Pre-existing diagnostics stay.

- [ ] **Step 4: CLI smoke**

```
.venv/bin/er-smart-sync --help
.venv/bin/er-smart-sync datamodel --help | grep event-type-version
.venv/bin/er-smart-sync inspect-datamodel --help | grep event-type-version
```

The smoke is light — Phase 4 (default-flip to v2) is in a later plan; we just want to make sure nothing broke.

- [ ] **Step 5: Commit any cleanup**

If lint/format/type fixes were needed:
```
git add <touched files>
git commit -m "chore: lint/format pass after v2 builder rewrite"
```

Otherwise this step is a no-op.

---

## Out of scope (do not implement here)

These are spec-acknowledged follow-ups; do **not** add tasks for them in this plan.

- **Meta-schema validation in tests** (parent spec testing section line 225). Requires vendoring or path-dependent import of the das repo's `main_event_type_schema`. Defer; the snapshot test in Task 8 is the regression guard for now.
- **Phase 3 smoke against `gundi-dev.staging.pamdas.org`.** Manual; not part of this plan.
- **Phase 4 flip default back to v2.** Separate plan, gated on Phase 3 success.
- **TREE `sub_choice_of` hierarchy preservation.** Flatten to leaves, matching v1 + Phase 1 parity.
- **Stale v2 record cleanup when SMART category goes inactive.** Open question in parent spec; punt.
- **CM-aware section layouts** (multi-column, multi-section). Out of scope.

If you discover during implementation that one of these is actually a blocker, **stop** and surface it — don't grow the plan unilaterally.
