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


# ── _source_choice_sets ────────────────────────────────────────


def test_source_choice_sets_orders_and_maps(monkeypatch):
    from unittest.mock import MagicMock

    records_by_field = {
        "et1_species": [
            {"value": "lion", "display": "Lion", "is_active": True, "ordernum": 1},
            {"value": "zebra", "display": "Zebra", "is_active": False, "ordernum": 0},
        ],
    }

    def fake_fetch(*, er_client, field):
        return records_by_field.get(field, [])

    monkeypatch.setattr(er_to_er, "_fetch_existing", fake_fetch)

    sets = er_to_er._source_choice_sets(
        source_client=MagicMock(), fields=["et1_species"]
    )
    assert len(sets) == 1
    cs = sets[0]
    assert cs.field == "et1_species"
    # sorted by ordernum: zebra (0) then lion (1)
    assert [o.value for o in cs.options] == ["zebra", "lion"]
    assert cs.options[0].is_active is False
    assert cs.options[1].display == "Lion"


def test_source_choice_sets_skips_empty_fields(monkeypatch):
    from unittest.mock import MagicMock

    monkeypatch.setattr(er_to_er, "_fetch_existing", lambda *, er_client, field: [])
    sets = er_to_er._source_choice_sets(source_client=MagicMock(), fields=["missing"])
    assert sets == []
