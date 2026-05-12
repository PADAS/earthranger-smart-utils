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
