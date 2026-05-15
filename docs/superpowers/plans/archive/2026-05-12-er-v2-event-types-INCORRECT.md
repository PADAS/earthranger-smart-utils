# EarthRanger v2 event types — Implementation Plan

> **⚠ ARCHIVED — INCORRECT. Do not execute this plan.**
>
> This plan implemented `archive/2026-05-12-er-v2-event-types-design-INCORRECT.md`,
> which prescribes a SMART → v2 schema mapping that ER's real v2 meta-schema
> (`das/das/activity/schemas/eventtype_meta_schemas.py`) rejects. The plan was
> executed and merged onto `feature/er-v2-event-types`; the resulting v2 builder
> produces output that fails every POST against a v2 tenant with
> `400 Invalid JSON Schema`. See the archived design doc for the per-field deltas.
>
> The v2 default in this repo has been reverted to v1 pending a redo. New spec is at
> `docs/superpowers/specs/2026-05-12-er-v2-event-types-design.md`. This file is
> preserved for archaeology only.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Teach `er-smart-sync` to create EarthRanger v2 event types (default) while keeping v1 reachable via a flag, and emit the correct v2 schema shape (JSON Schema 2020-12 + UI envelope) for every SMART attribute type.

**Architecture:** Two parallel builders selected by config. `smart_to_er.py` (v1) untouched; new `smart_to_er_v2.py` emits v2-shape `ERV2EventType` records. The synchronizer reads `event_type_version` from `EarthRangerConfig` and (a) picks the builder, (b) passes `version=` through to every `erclient` event-type call, (c) compares schemas appropriately for the version. Categories are version-less so unchanged.

**Tech Stack:** Python 3.10+, Pydantic v1, Click, `earthranger-client` (already supports `version=` on event-type methods), pytest, ruff, ty.

**Spec:** `docs/superpowers/specs/2026-05-12-er-v2-event-types-design.md`

---

## File Structure

| File | Action | Responsibility |
|---|---|---|
| `src/er_smart_sync/config.py` | modify | Add `event_type_version: Literal["v1","v2"] = "v2"` to `EarthRangerConfig` with alias-tolerant validator. |
| `src/er_smart_sync/smart_to_er_v2.py` | create | `ERV2EventType` model + `build_event_types_v2()` producing v2-shape records. |
| `src/er_smart_sync/synchronizer.py` | modify | Read `event_type_version` on init; branch builder; pass `version=` through to erclient; v2-aware schema diff; version-gate duplicate-key fallback. |
| `src/er_smart_sync/cli.py` | modify | `--event-type-version` flag on `datamodel` and `inspect-datamodel`; v2-aware print path; `config-template` line. |
| `tests/conftest.py` | modify | Pin existing `er_config` fixture to `event_type_version="v1"` so legacy tests stay v1; add `er_config_v2` fixture. |
| `tests/test_config.py` | modify | Default value, alias acceptance, invalid value rejection. |
| `tests/test_smart_to_er_v2.py` | create | Type mapping, inactive handling, configurable-model overlay, snapshot. |
| `tests/test_synchronizer.py` | modify | v2 wiring tests: builder selection, version kwarg, schema diff, duplicate-key log-and-skip. |
| `tests/test_cli.py` | modify | Flag parsing, config-template content. |
| `USAGE.md` | modify | New "Event type version" subsection. |

`defaults.DryRunERClient` is *not* modified: its `__getattr__` already records `**kwargs` so the new `version=` arg flows through automatically. Verified at `src/er_smart_sync/defaults.py:106-124`.

---

## Conventions for every task

- TDD: write the failing test, run it red, implement, run it green, commit.
- Pydantic **v1** (this project pins `<2.0`). Use `Field(alias=...)`, `parse_obj_as`, `.dict()`, `.json()`. No v2 patterns.
- After every code change, run the full suite once to catch regressions:
  ```
  .venv/bin/pytest -q
  ```
- Commit messages: conventional-commits style (`feat:`, `test:`, `refactor:`, `docs:`), one task per commit.
- Branch is already `feature/er-v2-event-types`. Stay on it. Do **not** merge to `main` from inside the plan — that's a separate human step.

---

## Task 1: Add `event_type_version` to `EarthRangerConfig`

**Files:**
- Modify: `src/er_smart_sync/config.py:42-54`
- Modify: `tests/test_config.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_config.py`:

```python
def test_er_config_event_type_version_defaults_to_v2():
    cfg = EarthRangerConfig(id="i", endpoint="https://x/api/v1.0")
    assert cfg.event_type_version == "v2"


def test_er_config_event_type_version_accepts_v1():
    cfg = EarthRangerConfig(
        id="i", endpoint="https://x/api/v1.0", event_type_version="v1"
    )
    assert cfg.event_type_version == "v1"


def test_er_config_event_type_version_accepts_dotted_aliases():
    cfg = EarthRangerConfig(
        id="i", endpoint="https://x/api/v1.0", event_type_version="v2.0"
    )
    assert cfg.event_type_version == "v2"

    cfg = EarthRangerConfig(
        id="i", endpoint="https://x/api/v1.0", event_type_version="V1"
    )
    assert cfg.event_type_version == "v1"


def test_er_config_event_type_version_rejects_unknown():
    import pytest
    with pytest.raises(Exception):
        EarthRangerConfig(
            id="i", endpoint="https://x/api/v1.0", event_type_version="v3"
        )
```

The existing `tests/test_config.py` already imports `EarthRangerConfig`; reuse that import.

- [ ] **Step 2: Run tests, verify red**

Run: `.venv/bin/pytest tests/test_config.py -v -k event_type_version`
Expected: 4 failures (`event_type_version` doesn't exist yet).

- [ ] **Step 3: Implement**

In `src/er_smart_sync/config.py`, add a validator and field to `EarthRangerConfig`:

```python
from typing import Literal

import pydantic


class EarthRangerConfig(pydantic.BaseModel):
    """EarthRanger server connection configuration."""

    id: str
    endpoint: str
    login: str = ""
    password: str = ""
    token: str = ""
    client_id: str = "das_web_client"
    event_type_version: Literal["v1", "v2"] = "v2"

    @pydantic.validator("event_type_version", pre=True)
    def _normalize_event_type_version(cls, v):
        if not isinstance(v, str):
            return v
        normalized = v.strip().lower()
        return {"v1.0": "v1", "v2.0": "v2"}.get(normalized, normalized)
```

- [ ] **Step 4: Run tests, verify green**

```
.venv/bin/pytest tests/test_config.py -v -k event_type_version
.venv/bin/pytest -q
```
Expected: 4 pass; full suite still passes (existing tests don't touch this field, so default of `v2` is harmless — they patch `build_event_types` and we haven't wired anything to read the new field yet).

- [ ] **Step 5: Commit**

```
git add src/er_smart_sync/config.py tests/test_config.py
git commit -m "feat: add event_type_version to EarthRangerConfig (default v2)"
```

---

## Task 2: Pin existing tests to v1 via conftest fixture

**Background:** Once the synchronizer starts honoring `event_type_version`, existing tests (which patch `build_event_types` and assert v1 wire shape) would suddenly route through the v2 path. We pin them to v1 in the shared `er_config` fixture and add an `er_config_v2` fixture for new tests.

**Files:**
- Modify: `tests/conftest.py:36-41`

- [ ] **Step 1: Update conftest**

Replace the `er_config` fixture body and add `er_config_v2` immediately after:

```python
@pytest.fixture
def er_config():
    return EarthRangerConfig(
        id="test-integration-id",
        endpoint="https://test.pamdas.org/api/v1.0",
        token="test-token",
        event_type_version="v1",
    )


@pytest.fixture
def er_config_v2():
    return EarthRangerConfig(
        id="test-integration-id-v2",
        endpoint="https://test.pamdas.org/api/v1.0",
        token="test-token",
        event_type_version="v2",
    )


@pytest.fixture
def sync_config_v2(smart_config, er_config_v2):
    from er_smart_sync.config import SyncConfig
    return SyncConfig(smart=smart_config, earthranger=er_config_v2)
```

- [ ] **Step 2: Run full suite**

Run: `.venv/bin/pytest -q`
Expected: all existing tests pass unchanged (still v1).

- [ ] **Step 3: Commit**

```
git add tests/conftest.py
git commit -m "test: pin er_config fixture to v1 and add er_config_v2 fixture"
```

---

## Task 3: Create `smart_to_er_v2.py` with `ERV2EventType` model and empty-input builder

**Files:**
- Create: `src/er_smart_sync/smart_to_er_v2.py`
- Create: `tests/test_smart_to_er_v2.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_smart_to_er_v2.py`:

```python
"""Tests for er_smart_sync.smart_to_er_v2."""

from __future__ import annotations

import pytest

from er_smart_sync.smart_to_er_v2 import ERV2EventType, build_event_types_v2

CA_UUID = "ca-1234"
CA_ID = "FOASF"


def _attr(key: str, type_: str, *, display: str | None = None, options: list | None = None):
    return {
        "key": key,
        "type": type_,
        "isrequired": False,
        "display": display or key,
        "options": options,
    }


def _option(key: str, display: str | None = None):
    return {"key": key, "display": display or key, "isActive": True}


def _category(path: str, *, display: str | None = None, attributes: list | None = None,
              is_active: bool = True, is_multiple: bool = False, hkey_path: str | None = None):
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
```

- [ ] **Step 2: Run tests, verify red**

Run: `.venv/bin/pytest tests/test_smart_to_er_v2.py -v`
Expected: ModuleNotFoundError on `er_smart_sync.smart_to_er_v2`.

- [ ] **Step 3: Implement**

Create `src/er_smart_sync/smart_to_er_v2.py`:

```python
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
```

- [ ] **Step 4: Run tests, verify green**

Run: `.venv/bin/pytest tests/test_smart_to_er_v2.py -v`
Expected: 3 tests pass.

- [ ] **Step 5: Commit**

```
git add src/er_smart_sync/smart_to_er_v2.py tests/test_smart_to_er_v2.py
git commit -m "feat: scaffold smart_to_er_v2 with ERV2EventType and empty builder"
```

---

## Task 4: Scalar type mappings (TEXT, NUMERIC, BOOLEAN, DATE, TIME, DATETIME, ATTACHMENT)

Per the spec mapping table, each scalar type produces a `json.properties[key]` entry and a `ui.fields[key]` entry, plus a `ui.sections.section-1` entry that lists the field. The full envelope wraps both blocks.

**Files:**
- Modify: `src/er_smart_sync/smart_to_er_v2.py`
- Modify: `tests/test_smart_to_er_v2.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_smart_to_er_v2.py`:

```python
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

    ui_field = schema["ui"]["fields"]["field1"]
    for k, v in expected_ui.items():
        assert ui_field[k] == v

    # Field is listed in the default section
    section = schema["ui"]["sections"]["section-1"]
    assert {"name": "field1", "type": "field"} in section["leftColumn"]
    assert schema["ui"]["order"] == ["section-1"]


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
```

- [ ] **Step 2: Run tests, verify red**

Run: `.venv/bin/pytest tests/test_smart_to_er_v2.py -v -k "scalar or envelope or event_type_value"`
Expected: failures (builder still returns empty list).

- [ ] **Step 3: Implement**

Replace the body of `build_event_types_v2` and add helpers in `src/er_smart_sync/smart_to_er_v2.py`:

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
    del ca_identifier

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
```

- [ ] **Step 4: Run tests, verify green**

Run: `.venv/bin/pytest tests/test_smart_to_er_v2.py -v`
Expected: all 12 tests pass.

- [ ] **Step 5: Commit**

```
git add src/er_smart_sync/smart_to_er_v2.py tests/test_smart_to_er_v2.py
git commit -m "feat(v2): map scalar SMART types to v2 json+ui blocks"
```

---

## Task 5: Choice type mappings (LIST single, LIST multi, MLIST, TREE)

Inline `enum` in `json.properties[key]`; `CHOICE_LIST` with `DROPDOWN`/`CHECKBOX` + `choices` map in `ui.fields[key]`. TREE leaves are flattened (mirrors the v1 `_leaf_options` rule).

**Files:**
- Modify: `src/er_smart_sync/smart_to_er_v2.py`
- Modify: `tests/test_smart_to_er_v2.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_smart_to_er_v2.py`:

```python
# ── Choice/enum types ────────────────────────────────────────────


def test_list_single_value_emits_enum_and_dropdown():
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
        dm=dm, cm=None, ca_uuid=CA_UUID, ca_identifier=CA_ID
    )[0].event_schema

    json_prop = schema["json"]["properties"]["color"]
    assert json_prop["type"] == "string"
    assert json_prop["enum"] == ["red", "blue"]

    ui_field = schema["ui"]["fields"]["color"]
    assert ui_field["type"] == "CHOICE_LIST"
    assert ui_field["inputType"] == "DROPDOWN"
    assert ui_field["choices"] == {"red": "Red", "blue": "Blue"}


def test_list_multi_value_emits_array_enum_and_checkbox():
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
        dm=dm, cm=None, ca_uuid=CA_UUID, ca_identifier=CA_ID
    )[0].event_schema

    json_prop = schema["json"]["properties"]["tags"]
    assert json_prop["type"] == "array"
    assert json_prop["items"] == {"type": "string", "enum": ["a", "b"]}

    ui_field = schema["ui"]["fields"]["tags"]
    assert ui_field["inputType"] == "CHECKBOX"


def test_mlist_emits_array_enum_and_checkbox():
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
        dm=dm, cm=None, ca_uuid=CA_UUID, ca_identifier=CA_ID
    )[0].event_schema

    json_prop = schema["json"]["properties"]["species"]
    assert json_prop["type"] == "array"
    assert json_prop["items"]["enum"] == ["lion", "zebra"]
    assert schema["ui"]["fields"]["species"]["inputType"] == "CHECKBOX"


def test_tree_flattens_to_leaf_options():
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

    json_prop = schema["json"]["properties"]["region"]
    assert json_prop["type"] == "string"
    # Only leaves: africa.kenya.nairobi and africa.tanzania
    assert set(json_prop["enum"]) == {"africa.kenya.nairobi", "africa.tanzania"}
    assert schema["ui"]["fields"]["region"]["inputType"] == "DROPDOWN"
```

- [ ] **Step 2: Run tests, verify red**

Run: `.venv/bin/pytest tests/test_smart_to_er_v2.py -v -k "list_ or mlist_ or tree_"`
Expected: failures — choice-typed attributes currently skipped.

- [ ] **Step 3: Implement**

In `src/er_smart_sync/smart_to_er_v2.py`, replace the inner-loop body of `_build_field_blocks` so it handles choice types in addition to scalars:

```python
def _build_field_blocks(
    *,
    attributes: list[Attribute],
    leaf_attributes: list[CategoryAttribute],
    is_multiple: bool,
    attribute_configs: list | None,
) -> tuple[dict, dict, list[str]]:
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
        )
        if json_prop is None:
            continue

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
) -> tuple[dict | None, dict | None]:
    """Return (json_property, ui_field) or (None, None) to skip."""
    if smart_type in SCALAR_JSON and not options:
        return (
            {**SCALAR_JSON[smart_type], "title": display},
            dict(SCALAR_UI[smart_type]),
        )

    if not options:
        logger.warning("Unknown SMART type %r; emitting string", smart_type)
        return (
            {"type": "string", "title": display},
            {"type": "TEXT", "inputType": "SHORT_TEXT"},
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
    return kept


def _leaf_options(options: list) -> list:
    """Filter to leaf options only (for TREE-shaped option sets)."""
    keys = [o.key for o in options]
    return [o for o in options if _is_leaf_node(keys, o.key)]
```

- [ ] **Step 4: Run tests, verify green**

Run: `.venv/bin/pytest tests/test_smart_to_er_v2.py -v`
Expected: all tests pass (including the 4 new choice tests + previously-green scalar tests).

- [ ] **Step 5: Commit**

```
git add src/er_smart_sync/smart_to_er_v2.py tests/test_smart_to_er_v2.py
git commit -m "feat(v2): map LIST/MLIST/TREE to inline enums + CHOICE_LIST ui"
```

---

## Task 6: Inactive attributes — mark `deprecated: true`, keep in section

Per design decision: deprecated SMART attributes stay visible in the form, just marked `deprecated: true` in `json.properties`.

**Files:**
- Modify: `src/er_smart_sync/smart_to_er_v2.py`
- Modify: `tests/test_smart_to_er_v2.py`

- [ ] **Step 1: Write failing test**

Append to `tests/test_smart_to_er_v2.py`:

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

    assert schema["json"]["properties"]["retired_attr"]["deprecated"] is True
    assert "deprecated" not in schema["json"]["properties"]["active_attr"]

    # Both fields still listed in the form section
    leftCol = schema["ui"]["sections"]["section-1"]["leftColumn"]
    names = [item["name"] for item in leftCol]
    assert "active_attr" in names
    assert "retired_attr" in names
```

- [ ] **Step 2: Run, verify red**

Run: `.venv/bin/pytest tests/test_smart_to_er_v2.py -v -k inactive`
Expected: fail — `deprecated` key not emitted.

- [ ] **Step 3: Implement**

In `src/er_smart_sync/smart_to_er_v2.py`, thread `is_active` through `_build_field_blocks` and apply it to the json property:

```python
def _build_field_blocks(
    *,
    attributes: list[Attribute],
    leaf_attributes: list[CategoryAttribute],
    is_multiple: bool,
    attribute_configs: list | None,
) -> tuple[dict, dict, list[str]]:
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

        json_prop, ui_field = _build_property_pair(
            smart_type=smart_type,
            display=attribute.display,
            options=options,
            is_multiple=is_multiple,
        )
        if json_prop is None:
            continue

        if not cat_attr.is_active:
            json_prop["deprecated"] = True

        properties[key] = json_prop
        ui_fields[key] = ui_field
        order.append(key)

    return properties, ui_fields, order
```

- [ ] **Step 4: Run, verify green**

Run: `.venv/bin/pytest tests/test_smart_to_er_v2.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```
git add src/er_smart_sync/smart_to_er_v2.py tests/test_smart_to_er_v2.py
git commit -m "feat(v2): mark inactive SMART attributes as deprecated, keep in form"
```

---

## Task 7: Configurable-model overlay applies option filter

Existing `_filter_options_by_config` is already wired (Task 5). This task just adds an end-to-end test confirming that a CM overlay narrows the enum set on a LIST attribute.

**Files:**
- Modify: `tests/test_smart_to_er_v2.py`

- [ ] **Step 1: Write failing test**

Append to `tests/test_smart_to_er_v2.py`:

```python
def test_configurable_model_filters_options():
    dm = {
        "categories": [_category("c", attributes=[_cat_attr("color")])],
        "attributes": [
            _attr(
                "color", "LIST", display="Color",
                options=[_option("red"), _option("blue"), _option("green")],
            )
        ],
    }
    cm = {
        "cm_uuid": "cm-9999",
        "categories": [_category("c", attributes=[_cat_attr("color")])],
        "attributes": [
            {
                "key": "color",
                "options": [
                    {"key": "red", "isActive": True},
                    {"key": "blue", "isActive": False},
                    {"key": "green", "isActive": True},
                ],
            }
        ],
    }

    schema = build_event_types_v2(
        dm=dm, cm=cm, ca_uuid=CA_UUID, ca_identifier=CA_ID
    )[0].event_schema

    assert schema["json"]["properties"]["color"]["enum"] == ["red", "green"]
    assert schema["ui"]["fields"]["color"]["choices"] == {
        "red": "red", "green": "green"
    }


def test_configurable_model_event_type_value_includes_cm_uuid():
    dm = {
        "categories": [_category("Wildlife", attributes=[_cat_attr("a")])],
        "attributes": [_attr("a", "TEXT")],
    }
    cm = {
        "cm_uuid": "abcd-cm",
        "categories": [_category("Wildlife", attributes=[_cat_attr("a")], hkey_path="Wildlife")],
        "attributes": [],
    }
    result = build_event_types_v2(
        dm=dm, cm=cm, ca_uuid="ca-1", ca_identifier=CA_ID
    )
    assert result[0].value == "ca-1_abcd-cm_wildlife"
```

- [ ] **Step 2: Run, verify**

Run: `.venv/bin/pytest tests/test_smart_to_er_v2.py -v -k configurable`
Expected: both pass already (Task 5 wired the filter and Task 4 wired CM-aware value composition).

If they pass on first run, that's fine — we still keep them as regression coverage. No implementation step needed.

- [ ] **Step 3: Commit**

```
git add tests/test_smart_to_er_v2.py
git commit -m "test(v2): configurable-model overlay filters options and namespaces values"
```

---

## Task 8: Set `category` on every built event type from the synchronizer

The v1 path mutates `event_type.category` in `create_or_update_er_event_types` after the category is created/fetched. The v2 model treats `category` as a slug string (same value the v1 code already assigns). No builder change needed — just a sanity test confirming `ERV2EventType` accepts and round-trips the assignment.

**Files:**
- Modify: `tests/test_smart_to_er_v2.py`

- [ ] **Step 1: Write test**

Append to `tests/test_smart_to_er_v2.py`:

```python
def test_event_type_category_is_settable_post_build():
    dm = {
        "categories": [_category("c", attributes=[_cat_attr("a")])],
        "attributes": [_attr("a", "TEXT")],
    }
    et = build_event_types_v2(
        dm=dm, cm=None, ca_uuid=CA_UUID, ca_identifier=CA_ID
    )[0]
    et.category = "foasf"
    payload = et.dict(by_alias=True, exclude_none=True)
    assert payload["category"] == "foasf"
```

- [ ] **Step 2: Run, verify**

Run: `.venv/bin/pytest tests/test_smart_to_er_v2.py::test_event_type_category_is_settable_post_build -v`
Expected: passes (model already supports it).

- [ ] **Step 3: Commit**

```
git add tests/test_smart_to_er_v2.py
git commit -m "test(v2): category slug can be assigned post-build like v1"
```

---

## Task 9: Synchronizer reads `event_type_version` at init

**Files:**
- Modify: `src/er_smart_sync/synchronizer.py:88-120`
- Modify: `tests/test_synchronizer.py`

- [ ] **Step 1: Write failing test**

Append to `tests/test_synchronizer.py`:

```python
class TestEventTypeVersionWiring:
    def test_synchronizer_reads_event_type_version_from_config(
        self, sync_config, mock_er_client
    ):
        # sync_config uses er_config which is pinned to v1.
        sync = ERSmartSynchronizer(
            config=sync_config, er_client=mock_er_client, smart_client=MagicMock()
        )
        assert sync._event_type_version == "v1"

    def test_synchronizer_v2_from_config(
        self, sync_config_v2, mock_er_client
    ):
        sync = ERSmartSynchronizer(
            config=sync_config_v2, er_client=mock_er_client, smart_client=MagicMock()
        )
        assert sync._event_type_version == "v2"
```

- [ ] **Step 2: Run, verify red**

Run: `.venv/bin/pytest tests/test_synchronizer.py -v -k event_type_version`
Expected: AttributeError (`_event_type_version` missing).

- [ ] **Step 3: Implement**

In `src/er_smart_sync/synchronizer.py`, after `self.sync_mode = "both"` (around line 111), add:

```python
        self._event_type_version: str = config.earthranger.event_type_version
```

- [ ] **Step 4: Run, verify green**

Run: `.venv/bin/pytest tests/test_synchronizer.py -v -k event_type_version`
Expected: both pass.

- [ ] **Step 5: Commit**

```
git add src/er_smart_sync/synchronizer.py tests/test_synchronizer.py
git commit -m "feat(sync): read event_type_version from config at init"
```

---

## Task 10: Branch builder selection on version in `push_smart_ca_datamodel_to_earthranger`

**Files:**
- Modify: `src/er_smart_sync/synchronizer.py:233-248`
- Modify: `tests/test_synchronizer.py`

- [ ] **Step 1: Write failing test**

Append to `tests/test_synchronizer.py`:

```python
    def test_push_smart_ca_uses_v2_builder_when_configured(
        self, sync_config_v2, mock_er_client
    ):
        mock_er_client.get_event_categories.return_value = []
        mock_er_client.get_event_types.return_value = []

        dm = MagicMock()
        dm.export_as_dict.return_value = {"categories": []}

        with patch(
            "er_smart_sync.synchronizer.build_event_types_v2",
            return_value=[],
        ) as v2_builder, patch(
            "er_smart_sync.synchronizer.build_event_types",
            return_value=[],
        ) as v1_builder:
            sync = ERSmartSynchronizer(
                config=sync_config_v2,
                er_client=mock_er_client,
                smart_client=MagicMock(),
            )
            sync.push_smart_ca_datamodel_to_earthranger(
                dm=dm, smart_ca_uuid="uuid", ca_label="[TEST]"
            )

        v2_builder.assert_called_once()
        v1_builder.assert_not_called()

    def test_push_smart_ca_uses_v1_builder_when_configured(
        self, sync_config, mock_er_client
    ):
        mock_er_client.get_event_categories.return_value = []
        mock_er_client.get_event_types.return_value = []

        dm = MagicMock()
        dm.export_as_dict.return_value = {"categories": []}

        with patch(
            "er_smart_sync.synchronizer.build_event_types",
            return_value=[],
        ) as v1_builder, patch(
            "er_smart_sync.synchronizer.build_event_types_v2",
            return_value=[],
        ) as v2_builder:
            sync = ERSmartSynchronizer(
                config=sync_config,
                er_client=mock_er_client,
                smart_client=MagicMock(),
            )
            sync.push_smart_ca_datamodel_to_earthranger(
                dm=dm, smart_ca_uuid="uuid", ca_label="[TEST]"
            )

        v1_builder.assert_called_once()
        v2_builder.assert_not_called()
```

- [ ] **Step 2: Run, verify red**

Run: `.venv/bin/pytest tests/test_synchronizer.py -v -k uses_v2_builder`
Expected: ImportError or AttributeError — `build_event_types_v2` not imported in synchronizer.py.

- [ ] **Step 3: Implement**

In `src/er_smart_sync/synchronizer.py`:

1. Add import near the existing `from .smart_to_er import build_event_types`:
   ```python
   from .smart_to_er import build_event_types
   from .smart_to_er_v2 import build_event_types_v2
   ```

2. In `push_smart_ca_datamodel_to_earthranger`, replace the `event_types = build_event_types(...)` block with:
   ```python
       ca_identifier = self.get_identifier_from_ca_label(ca_label)
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

- [ ] **Step 4: Run, verify green**

Run:
```
.venv/bin/pytest tests/test_synchronizer.py -v -k "uses_v1_builder or uses_v2_builder"
.venv/bin/pytest -q
```
Expected: both new tests pass, full suite green.

- [ ] **Step 5: Commit**

```
git add src/er_smart_sync/synchronizer.py tests/test_synchronizer.py
git commit -m "feat(sync): select v1/v2 builder based on event_type_version"
```

---

## Task 11: Pass `version=` to erclient event-type methods

Four call sites: `synchronize_datamodel`'s pre-fetch (`get_event_types`), `create_or_update_er_event_types`'s inner fetch (`get_event_types`), `_find_existing_event_type` (`get_event_types`), and `_create_event_type` + `_update_event_type` (`post_event_type` / `patch_event_type`).

**Files:**
- Modify: `src/er_smart_sync/synchronizer.py:165-167, 444-447, 477-479, 500-503, 521-526`
- Modify: `tests/test_synchronizer.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_synchronizer.py`:

```python
    def test_v2_get_event_types_passes_version_kwarg(
        self, sync_config_v2, mock_er_client
    ):
        mock_er_client.get_event_categories.return_value = []
        mock_er_client.get_event_types.return_value = []

        sync = ERSmartSynchronizer(
            config=sync_config_v2,
            er_client=mock_er_client,
            smart_client=MagicMock(),
        )
        sync.config.smart.ca_uuids = []  # no CAs to iterate, snapshot still runs
        sync.synchronize_datamodel()

        get_calls = mock_er_client.get_event_types.call_args_list
        # snapshot at top of synchronize_datamodel must pass version="v2"
        assert any(c.kwargs.get("version") == "v2" for c in get_calls)

    def test_v2_post_event_type_passes_version_kwarg(
        self, sync_config_v2, mock_er_client
    ):
        mock_er_client.get_event_categories.return_value = []
        mock_er_client.get_event_types.return_value = []

        from er_smart_sync.smart_to_er_v2 import ERV2EventType

        et = ERV2EventType(value="v", display="V", category=None)
        with patch(
            "er_smart_sync.synchronizer.build_event_types_v2",
            return_value=[et],
        ):
            dm = MagicMock()
            dm.export_as_dict.return_value = {"categories": []}
            sync = ERSmartSynchronizer(
                config=sync_config_v2,
                er_client=mock_er_client,
                smart_client=MagicMock(),
            )
            sync.push_smart_ca_datamodel_to_earthranger(
                dm=dm, smart_ca_uuid="uuid", ca_label="[TEST]"
            )

        assert mock_er_client.post_event_type.called
        post_kwargs = mock_er_client.post_event_type.call_args.kwargs
        assert post_kwargs.get("version") == "v2"
        # Payload should have schema as a dict (or absent for an empty model)
        et_payload = post_kwargs["event_type"]
        if "schema" in et_payload:
            assert isinstance(et_payload["schema"], dict)
```

- [ ] **Step 2: Run, verify red**

Run: `.venv/bin/pytest tests/test_synchronizer.py -v -k "passes_version_kwarg"`
Expected: both fail — current code doesn't pass `version=`.

- [ ] **Step 3: Implement**

Five edits in `src/er_smart_sync/synchronizer.py`:

A) `synchronize_datamodel`, replace lines 165-167:
```python
        self._er_event_types_cache = self.er_client.get_event_types(
            include_inactive=True,
            include_schema=True,
            version=self._event_type_version,
        )
```

B) `_create_event_type`, replace lines 444-447:
```python
            _retry(
                self.er_client.post_event_type,
                event_type=event_type.dict(by_alias=True, exclude_none=True),
                version=self._event_type_version,
            )
```

C) `_find_existing_event_type`, replace lines 477-479:
```python
            fresh = self.er_client.get_event_types(
                include_inactive=True,
                include_schema=True,
                version=self._event_type_version,
            )
```

D) `_update_event_type`, replace lines 500-503:
```python
            _retry(
                self.er_client.patch_event_type,
                event_type=event_type.dict(by_alias=True, exclude_none=True),
                version=self._event_type_version,
            )
```

E) `create_or_update_er_event_types`, replace lines 523-526:
```python
            existing_event_types = self.er_client.get_event_types(
                include_inactive=True,
                include_schema=True,
                version=self._event_type_version,
            )
```

- [ ] **Step 4: Run, verify green**

Run:
```
.venv/bin/pytest tests/test_synchronizer.py -v -k passes_version_kwarg
.venv/bin/pytest -q
```
Expected: both new tests pass; full suite still green (v1 tests get `version="v1"` and `mock_er_client` accepts kwargs without complaint).

- [ ] **Step 5: Commit**

```
git add src/er_smart_sync/synchronizer.py tests/test_synchronizer.py
git commit -m "feat(sync): pass event_type_version to all erclient event-type calls"
```

---

## Task 12: `_event_type_needs_update` handles v2 dict schemas

For v2 the schema attribute on `ERV2EventType` is a `dict`, and `existing_er_event_type["schema"]` returned by erclient v2 is also a dict (not stringified). The current code does `json.loads(event_type.event_schema)` — wrong for v2.

**Files:**
- Modify: `src/er_smart_sync/synchronizer.py:408-425`
- Modify: `tests/test_synchronizer.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_synchronizer.py`:

```python
    def test_event_type_needs_update_v2_dict_equal(
        self, sync_config_v2, mock_er_client
    ):
        from er_smart_sync.smart_to_er_v2 import ERV2EventType

        sync = ERSmartSynchronizer(
            config=sync_config_v2,
            er_client=mock_er_client,
            smart_client=MagicMock(),
        )
        et = ERV2EventType(
            value="v", display="V", category="c",
            event_schema={"json": {"a": 1}, "ui": {}},
        )
        existing = {
            "value": "v", "display": "V", "is_active": True,
            "schema": {"json": {"a": 1}, "ui": {}},
        }
        assert sync._event_type_needs_update(et, existing) is False

    def test_event_type_needs_update_v2_dict_different(
        self, sync_config_v2, mock_er_client
    ):
        from er_smart_sync.smart_to_er_v2 import ERV2EventType

        sync = ERSmartSynchronizer(
            config=sync_config_v2,
            er_client=mock_er_client,
            smart_client=MagicMock(),
        )
        et = ERV2EventType(
            value="v", display="V", category="c",
            event_schema={"json": {"a": 2}, "ui": {}},
        )
        existing = {
            "value": "v", "display": "V", "is_active": True,
            "schema": {"json": {"a": 1}, "ui": {}},
        }
        assert sync._event_type_needs_update(et, existing) is True
```

- [ ] **Step 2: Run, verify red**

Run: `.venv/bin/pytest tests/test_synchronizer.py -v -k needs_update_v2`
Expected: TypeError — `json.loads` of a dict.

- [ ] **Step 3: Implement**

In `src/er_smart_sync/synchronizer.py`, replace `_event_type_needs_update` (lines 408-425) with:

```python
    def _event_type_needs_update(
        self, event_type, existing_er_event_type: dict
    ) -> bool:
        if (
            event_type.is_active != existing_er_event_type.get("is_active")
            or event_type.display != existing_er_event_type.get("display")
        ):
            return True

        if event_type.is_active and event_type.event_schema:
            if self._event_type_version == "v2":
                new_schema = event_type.event_schema
                existing_schema = existing_er_event_type.get("schema") or {}
                if not isinstance(existing_schema, dict):
                    # Defensive: shouldn't happen on v2 endpoint but guard anyway.
                    existing_schema = {}
                if new_schema != existing_schema:
                    return True
            else:
                new_schema = json.loads(event_type.event_schema).get("schema")
                existing_schema = json.loads(
                    existing_er_event_type.get("schema", "{}")
                ).get("schema")
                if not er_event_type_schemas_equal(new_schema, existing_schema):
                    return True

        return False
```

- [ ] **Step 4: Run, verify green**

Run:
```
.venv/bin/pytest tests/test_synchronizer.py -v -k needs_update
.venv/bin/pytest -q
```
Expected: new tests pass; existing v1 schema diff tests still green.

- [ ] **Step 5: Commit**

```
git add src/er_smart_sync/synchronizer.py tests/test_synchronizer.py
git commit -m "feat(sync): compare v2 event-type schemas as dicts"
```

---

## Task 13: Version-gate the duplicate-key fallback (v2 = log-and-skip)

On v2 we do **not** auto-patch a duplicate-value event type — the colliding record is likely a leftover v1 entry, and patching it cross-version is destructive. Log a clear error and let the stats counter record it as errored.

**Files:**
- Modify: `src/er_smart_sync/synchronizer.py:427-468`
- Modify: `tests/test_synchronizer.py`

- [ ] **Step 1: Write failing test**

Append to `tests/test_synchronizer.py`:

```python
    def test_v2_duplicate_key_logs_and_skips_no_patch(
        self, sync_config_v2, mock_er_client, caplog
    ):
        from er_smart_sync.smart_to_er_v2 import ERV2EventType

        mock_er_client.get_event_categories.return_value = []
        mock_er_client.get_event_types.return_value = []
        mock_er_client.post_event_type.side_effect = Exception(
            "duplicate key value violates unique constraint"
        )

        et = ERV2EventType(value="v", display="V", category=None)
        with patch(
            "er_smart_sync.synchronizer.build_event_types_v2",
            return_value=[et],
        ):
            dm = MagicMock()
            dm.export_as_dict.return_value = {"categories": []}
            sync = ERSmartSynchronizer(
                config=sync_config_v2,
                er_client=mock_er_client,
                smart_client=MagicMock(),
            )
            with caplog.at_level("WARNING"):
                sync.push_smart_ca_datamodel_to_earthranger(
                    dm=dm, smart_ca_uuid="uuid", ca_label="[TEST]"
                )

        # Post attempted; patch NOT attempted (no auto-recover on v2)
        assert mock_er_client.post_event_type.called
        assert not mock_er_client.patch_event_type.called
        assert any(
            "exists in v1" in r.message or "duplicate" in r.message.lower()
            for r in caplog.records
        )
```

- [ ] **Step 2: Run, verify red**

Run: `.venv/bin/pytest tests/test_synchronizer.py -v -k duplicate_key_logs_and_skips`
Expected: failure — current code falls back to patch on any duplicate-key.

- [ ] **Step 3: Implement**

In `src/er_smart_sync/synchronizer.py`, replace the `except Exception as e:` block in `_create_event_type` (lines ~449-468):

```python
        except Exception as e:
            if "duplicate key" in str(e) or "already exists" in str(e):
                if self._event_type_version == "v2":
                    logger.warning(
                        "Event type value %r already exists in this tenant "
                        "(possibly under v1). Skipping; run ER's "
                        "POST /api/v2.0/activity/eventtypes/migrate/ to "
                        "convert legacy v1 records before retrying.",
                        event_type.value,
                        extra=dict(value=event_type.value),
                    )
                    return False
                logger.warning(
                    "post_event_type hit existing record; patching instead",
                    extra=dict(value=event_type.value),
                )
                existing = self._find_existing_event_type(event_type.value)
                if existing is not None:
                    self._update_event_type(event_type, existing)
                    return False
            logger.exception(
                "Error occurred during er_client.post_event_type",
                extra=dict(
                    event_type=event_type.dict(
                        by_alias=True, exclude_none=True
                    ),
                    error=str(e),
                ),
            )
            return False
```

- [ ] **Step 4: Run, verify green**

Run:
```
.venv/bin/pytest tests/test_synchronizer.py -v -k duplicate_key
.venv/bin/pytest -q
```
Expected: new v2 test passes, existing v1 duplicate-key test (around line 245-273) still passes.

- [ ] **Step 5: Commit**

```
git add src/er_smart_sync/synchronizer.py tests/test_synchronizer.py
git commit -m "feat(sync): on v2 duplicate-key, log-and-skip instead of auto-patching"
```

---

## Task 14: `--event-type-version` flag on `datamodel` subcommand

**Files:**
- Modify: `src/er_smart_sync/cli.py:189-226`
- Modify: `tests/test_cli.py`

- [ ] **Step 1: Write failing test**

Append to `tests/test_cli.py`. (If `test_cli.py` already has a CliRunner fixture, reuse the existing pattern; the snippet below uses `click.testing.CliRunner` standalone if not.)

```python
from click.testing import CliRunner

from er_smart_sync.cli import main


def test_datamodel_event_type_version_v1_flag_overrides_config_default(tmp_path, monkeypatch):
    """--event-type-version v1 should produce a synchronizer with _event_type_version == 'v1'."""
    captured = {}

    def fake_make_sync(config, ctx=None):
        from er_smart_sync.synchronizer import ERSmartSynchronizer
        sync = ERSmartSynchronizer.__new__(ERSmartSynchronizer)
        sync._event_type_version = config.earthranger.event_type_version
        sync.sync_mode = "both"
        sync.datamodel_stats = {
            "categories_created": 0, "categories_existing": 0,
            "event_types_created": 0, "event_types_updated": 0,
            "event_types_unchanged": 0, "event_types_skipped_by_mode": 0,
            "event_types_errored": 0,
        }
        # Stub out the network calls the command would make
        sync.push_smart_ca_datamodel_to_earthranger = lambda **kwargs: None
        sync.synchronize_datamodel = lambda: None
        captured["sync"] = sync
        return sync

    monkeypatch.setattr("er_smart_sync.cli._make_synchronizer", fake_make_sync)

    # Use a dummy XML file (file-based path)
    dm_file = tmp_path / "dm.xml"
    dm_file.write_text("<datamodel/>")

    # Patch the SmartClient.load_datamodel so we don't actually parse XML
    from unittest.mock import MagicMock
    monkeypatch.setattr(
        "smartconnect.SmartClient.load_datamodel",
        lambda self, filename: MagicMock(),
    )

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "datamodel",
            "--from-file", str(dm_file),
            "--er-endpoint", "https://x/api/v1.0",
            "--er-token", "t",
            "--er-id", "i",
            "--event-type-version", "v1",
        ],
    )
    assert result.exit_code == 0, result.output
    assert captured["sync"]._event_type_version == "v1"


def test_datamodel_event_type_version_defaults_to_v2(tmp_path, monkeypatch):
    """No --event-type-version flag → uses config default which is v2."""
    captured = {}

    def fake_make_sync(config, ctx=None):
        from er_smart_sync.synchronizer import ERSmartSynchronizer
        sync = ERSmartSynchronizer.__new__(ERSmartSynchronizer)
        sync._event_type_version = config.earthranger.event_type_version
        sync.sync_mode = "both"
        sync.datamodel_stats = {
            "categories_created": 0, "categories_existing": 0,
            "event_types_created": 0, "event_types_updated": 0,
            "event_types_unchanged": 0, "event_types_skipped_by_mode": 0,
            "event_types_errored": 0,
        }
        sync.push_smart_ca_datamodel_to_earthranger = lambda **kwargs: None
        sync.synchronize_datamodel = lambda: None
        captured["sync"] = sync
        return sync

    monkeypatch.setattr("er_smart_sync.cli._make_synchronizer", fake_make_sync)

    dm_file = tmp_path / "dm.xml"
    dm_file.write_text("<datamodel/>")

    from unittest.mock import MagicMock
    monkeypatch.setattr(
        "smartconnect.SmartClient.load_datamodel",
        lambda self, filename: MagicMock(),
    )

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "datamodel",
            "--from-file", str(dm_file),
            "--er-endpoint", "https://x/api/v1.0",
            "--er-token", "t",
            "--er-id", "i",
        ],
    )
    assert result.exit_code == 0, result.output
    assert captured["sync"]._event_type_version == "v2"
```

- [ ] **Step 2: Run, verify red**

Run: `.venv/bin/pytest tests/test_cli.py -v -k "event_type_version"`
Expected: `--event-type-version` is an unknown flag → exit code != 0.

- [ ] **Step 3: Implement**

In `src/er_smart_sync/cli.py`:

A) Add a new Click option to the `datamodel` command (right after `--mode`, around line 204):

```python
@click.option(
    "--event-type-version",
    type=click.Choice(["v1", "v2"]),
    default=None,
    help="EarthRanger event-type API version. Overrides the value in --config or the default (v2).",
)
```

B) Add `event_type_version` to the `datamodel` function parameter list (around line 225) and propagate it into the config **before** `_make_synchronizer`:

```python
def datamodel(
    ctx,
    config_file,
    ...
    mode,
    event_type_version,
):
    """Sync SMART data models to EarthRanger as event categories/types."""
    config = _build_config(
        ...
    )
    if event_type_version:
        config.earthranger.event_type_version = event_type_version

    ...
```

(Pydantic v1 models with mutable assignment work by default; `EarthRangerConfig` does not set `allow_mutation=False`.)

- [ ] **Step 4: Run, verify green**

Run:
```
.venv/bin/pytest tests/test_cli.py -v -k event_type_version
.venv/bin/pytest -q
```
Expected: both new tests pass; everything else green.

- [ ] **Step 5: Commit**

```
git add src/er_smart_sync/cli.py tests/test_cli.py
git commit -m "feat(cli): --event-type-version flag on datamodel subcommand"
```

---

## Task 15: `--event-type-version` flag on `inspect-datamodel`

`inspect-datamodel` calls `build_event_types` directly today; v2 must call `build_event_types_v2`, and the printer must handle both shapes.

**Files:**
- Modify: `src/er_smart_sync/cli.py:662-816`
- Modify: `tests/test_cli.py`

- [ ] **Step 1: Write failing test**

Append to `tests/test_cli.py`:

```python
def test_inspect_datamodel_v2_prints_field_types(tmp_path, monkeypatch):
    from unittest.mock import MagicMock

    dm_mock = MagicMock()
    dm_mock.export_as_dict.return_value = {
        "categories": [{
            "path": "incidents",
            "hkeyPath": "incidents",
            "display": "Incidents",
            "is_multiple": False,
            "is_active": True,
            "attributes": [{"key": "color", "is_active": True}],
        }],
        "attributes": [{
            "key": "color",
            "type": "LIST",
            "isrequired": False,
            "display": "Color",
            "options": [
                {"key": "red", "display": "Red", "isActive": True},
                {"key": "blue", "display": "Blue", "isActive": True},
            ],
        }],
    }
    monkeypatch.setattr(
        "smartconnect.SmartClient.load_datamodel",
        lambda self, filename: dm_mock,
    )

    dm_file = tmp_path / "dm.xml"
    dm_file.write_text("<datamodel/>")

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "inspect-datamodel",
            "--from-file", str(dm_file),
            "--ca-label", "Foasf [FOASF]",
            "--event-type-version", "v2",
        ],
    )
    assert result.exit_code == 0, result.output
    # v2 printer should mention CHOICE_LIST or DROPDOWN somewhere
    assert "CHOICE_LIST" in result.output or "DROPDOWN" in result.output
    assert "color" in result.output
```

- [ ] **Step 2: Run, verify red**

Run: `.venv/bin/pytest tests/test_cli.py -v -k inspect_datamodel_v2`
Expected: `--event-type-version` not a known flag.

- [ ] **Step 3: Implement**

In `src/er_smart_sync/cli.py`:

A) Add Click option to `inspect-datamodel` (after the `--ca-label` option around line 689):

```python
@click.option(
    "--event-type-version",
    type=click.Choice(["v1", "v2"]),
    default="v2",
    help="Which event-type schema shape to print. Default: v2.",
)
```

B) Add `event_type_version` to the `inspect_datamodel_cmd` signature and use it to pick the builder, and pick the right printer:

```python
def inspect_datamodel_cmd(
    config_file,
    ...
    ca_label,
    event_type_version,
):
    ...
    if event_type_version == "v2":
        from .smart_to_er_v2 import build_event_types_v2
        event_types = build_event_types_v2(
            dm=dm.export_as_dict(),
            cm=cm.export_as_dict() if cm else None,
            ca_uuid=ca_uuid,
            ca_identifier=ca_identifier,
        )
        _print_event_type_summary_v2(event_types, ca_label=ca_label)
    else:
        from .smart_to_er import build_event_types
        event_types = build_event_types(
            dm=dm.export_as_dict(),
            cm=cm.export_as_dict() if cm else None,
            ca_uuid=ca_uuid,
            ca_identifier=ca_identifier,
        )
        _print_event_type_summary(event_types, ca_label=ca_label)
```

C) Add the v2 printer below the existing `_print_event_type_summary`:

```python
def _print_event_type_summary_v2(event_types, *, ca_label: str) -> None:
    click.echo(f"CA: {ca_label}")
    click.echo(f"Event types: {len(event_types)}")
    active = [et for et in event_types if et.is_active]
    inactive = [et for et in event_types if not et.is_active]
    click.echo(f"  active:   {len(active)}")
    click.echo(f"  inactive: {len(inactive)}")
    click.echo("")

    for et in event_types:
        active_marker = "" if et.is_active else " [inactive]"
        click.echo(f"- {et.value}{active_marker}")
        click.echo(f"    display: {et.display}")
        if not et.event_schema:
            continue
        properties = et.event_schema.get("json", {}).get("properties", {})
        ui_fields = et.event_schema.get("ui", {}).get("fields", {})
        if not properties:
            continue
        click.echo("    fields:")
        for key, prop in properties.items():
            type_part = prop.get("type", "?")
            if "format" in prop:
                type_part = f"{type_part}/{prop['format']}"
            ui = ui_fields.get(key, {})
            extras = []
            ui_type = ui.get("type")
            if ui_type:
                input_type = ui.get("inputType")
                extras.append(
                    f"ui={ui_type}/{input_type}" if input_type else f"ui={ui_type}"
                )
            enum = prop.get("enum")
            items = prop.get("items", {})
            if not enum and isinstance(items, dict):
                enum = items.get("enum")
            if enum:
                extras.append(f"enum={enum}")
            if prop.get("deprecated"):
                extras.append("deprecated")
            extras_str = f" ({', '.join(extras)})" if extras else ""
            click.echo(f"      {key}: {type_part}{extras_str}")
```

- [ ] **Step 4: Run, verify green**

Run:
```
.venv/bin/pytest tests/test_cli.py -v -k inspect_datamodel_v2
.venv/bin/pytest -q
```

- [ ] **Step 5: Commit**

```
git add src/er_smart_sync/cli.py tests/test_cli.py
git commit -m "feat(cli): inspect-datamodel honors --event-type-version (v2 printer)"
```

---

## Task 16: `config-template` mentions `event_type_version`

**Files:**
- Modify: `src/er_smart_sync/cli.py:445-503` (`_CONFIG_YAML_TEMPLATE`)
- Modify: `tests/test_cli.py`

- [ ] **Step 1: Write failing test**

Append to `tests/test_cli.py`:

```python
def test_config_template_mentions_event_type_version():
    runner = CliRunner()
    result = runner.invoke(main, ["config-template"])
    assert result.exit_code == 0, result.output
    assert "event_type_version" in result.output
    assert "v2" in result.output
```

- [ ] **Step 2: Run, verify red**

Run: `.venv/bin/pytest tests/test_cli.py -v -k config_template_mentions`
Expected: failure — string not present.

- [ ] **Step 3: Implement**

In `src/er_smart_sync/cli.py`, in `_CONFIG_YAML_TEMPLATE`, append inside the `earthranger:` block (right after the `client_id` line, before the closing `"""`):

```yaml
  # EarthRanger event-type API version: "v1" or "v2". Default: v2.
  # v2 is the current EarthRanger event-type shape (JSON Schema 2020-12 +
  # UI envelope). v1 is the legacy shape and is still supported for tenants
  # that haven't enabled v2.
  event_type_version: v2
```

- [ ] **Step 4: Run, verify green**

Run:
```
.venv/bin/pytest tests/test_cli.py -v -k config_template_mentions
.venv/bin/pytest -q
```

- [ ] **Step 5: Commit**

```
git add src/er_smart_sync/cli.py tests/test_cli.py
git commit -m "docs(cli): document event_type_version in config-template"
```

---

## Task 17: USAGE.md — "Event type version" section

**Files:**
- Modify: `USAGE.md`

- [ ] **Step 1: Add docs**

Find the section in `USAGE.md` documenting the `datamodel` subcommand. Add a new subsection immediately after the existing flag table:

```markdown
### Event type version

EarthRanger supports two event-type API versions: v1 (legacy) and v2 (current).
By default `er-smart-sync` creates **v2 event types**, which use the JSON Schema
2020-12 envelope (`json` + `ui` blocks) and a richer set of field types
(`TEXT`, `NUMBER`, `BOOLEAN`, `CHOICE_LIST`, `ATTACHMENT`, etc.).

Override with `--event-type-version v1` on the `datamodel` or `inspect-datamodel`
commands, or with `event_type_version: v1` under `earthranger:` in your config.

EarthRanger enforces a tenant-wide unique constraint on event-type `value`
across **both** versions. If a previous run created v1 event types and you
re-run with v2, you'll get duplicate-key conflicts. `er-smart-sync` logs and
skips these — to convert existing v1 records to v2, run EarthRanger's
server-side migrate endpoint:

```
POST /api/v2.0/activity/eventtypes/migrate/
```

(Tooling around that endpoint is tracked as a follow-up; you can call it
directly via `curl` or the ER admin UI today.)
```

- [ ] **Step 2: Sanity-check the doc**

Run: `git diff USAGE.md` and read the addition top-to-bottom.

- [ ] **Step 3: Commit**

```
git add USAGE.md
git commit -m "docs: document v1/v2 event-type selection and migrate workflow"
```

---

## Task 18: Final integration smoke check

Make sure the full suite is green, the format is clean, the type checker is happy, and the CLI smoke-runs.

- [ ] **Step 1: Run full test suite**

Run: `.venv/bin/pytest -q`
Expected: all tests pass.

- [ ] **Step 2: Lint and format**

Run:
```
.venv/bin/ruff check src tests
.venv/bin/ruff format --check src tests
```
Expected: clean. If ruff format complains, run `.venv/bin/ruff format src tests` and commit the result.

- [ ] **Step 3: Type check**

Run: `.venv/bin/ty check`
Expected: clean.

- [ ] **Step 4: CLI smoke**

```
.venv/bin/er-smart-sync --help
.venv/bin/er-smart-sync config-template | grep event_type_version
.venv/bin/er-smart-sync datamodel --help | grep event-type-version
.venv/bin/er-smart-sync inspect-datamodel --help | grep event-type-version
```
Expected: each grep matches.

- [ ] **Step 5: Commit any cleanup**

If any lint/format/type fixes were needed:
```
git add -p   # stage targeted hunks
git commit -m "chore: lint/format pass after v2 event-type work"
```
Otherwise this step is a no-op.

---

## Out of scope (do not implement here)

These are spec-acknowledged follow-ups; do **not** add tasks for them in this plan.

- Populating ER's tenant-managed Choices via the choices API and emitting `$ref` URLs.
- `migrate-to-v2` CLI subcommand wrapping `POST /api/v2.0/activity/eventtypes/migrate/`.
- Auto-detecting ER tenant v2 readiness.
- Multi-column or multi-section UI layouts.
- `ordernum` ordering.

If you discover during implementation that one of these is actually a blocker, **stop** and surface it — don't grow the plan unilaterally.
