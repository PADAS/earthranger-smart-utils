"""Tests for er_smart_sync.er_to_er."""

from __future__ import annotations

from er_smart_sync import er_to_er  # noqa: F401
from er_smart_sync.er_to_er import (
    CopyEventTypeStats,  # noqa: F401
    EventTypeNotFound,  # noqa: F401
    TargetCategoryMissing,  # noqa: F401
    copy_event_type,  # noqa: F401
    extract_choice_fields,
)

# ── extract_choice_fields ──────────────────────────────────────


def test_extract_choice_fields_finds_refs():
    schema = {
        "json": {
            "properties": {
                "species": {
                    "anyOf": [
                        {"$ref": "/api/v2.0/schemas/choices.json?field=et123_species"}
                    ]
                },
                "note": {"type": "string"},
            }
        }
    }
    assert extract_choice_fields(schema) == ["et123_species"]


def test_extract_choice_fields_dedupes_preserving_order():
    ref = "/x/choices.json?field=fa"
    schema = {
        "a": {"$ref": ref},
        "b": {"items": {"$ref": "/x/choices.json?field=fb"}},
        "c": {"$ref": ref},
    }
    assert extract_choice_fields(schema) == ["fa", "fb"]


def test_extract_choice_fields_ignores_non_choice_refs():
    schema = {"x": {"$ref": "#/definitions/Foo"}}
    assert extract_choice_fields(schema) == []


def test_extract_choice_fields_handles_field_with_trailing_params():
    schema = {"x": {"$ref": "/c/choices.json?field=et9_kind&extra=1"}}
    assert extract_choice_fields(schema) == ["et9_kind"]
