"""Tests for er_smart_sync.er_to_er."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from er_smart_sync import er_to_er  # noqa: F401
from er_smart_sync.choices import ChoiceSet, ChoicesStats
from er_smart_sync.defaults import DryRunERClient
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
    monkeypatch.setattr(er_to_er, "_fetch_existing", lambda *, er_client, field: [])
    sets = er_to_er._source_choice_sets(source_client=MagicMock(), fields=["missing"])
    assert sets == []


# ── copy_event_type ────────────────────────────────────────────


def _src_v2_event_type(value="ca_lion", with_ref=True):
    ref = "/api/v2.0/schemas/choices.json?field=et1_species"
    props = {"species": {"anyOf": [{"$ref": ref}]}} if with_ref else {}
    schema = {"json": {"properties": props}, "ui": {}}
    return {
        "id": "11111111-1111-1111-1111-111111111111",
        "value": value,
        "display": "Lion",
        "category": "source_cat",
        "is_active": True,
        "readonly": False,
        "schema": json.dumps(schema),  # ER returns v2 schema as a JSON string
    }


def _dest_with_category(value="target_cat"):
    dest = MagicMock()
    dest.get_event_categories.return_value = [{"value": value, "display": "Target"}]
    dest.get_event_types.return_value = []  # nothing exists yet → create path
    return dest


def test_copy_event_type_v2_creates_and_copies_choices(monkeypatch):
    source = MagicMock()
    source.get_event_types.return_value = [_src_v2_event_type()]
    dest = _dest_with_category()

    captured = {}

    def fake_upsert(*, er_client, choice_sets):
        captured["client"] = er_client
        captured["sets"] = choice_sets
        return ChoicesStats(created=2)

    monkeypatch.setattr(er_to_er, "upsert_choices", fake_upsert)

    def fake_choice_sets(*, source_client, fields):
        return [ChoiceSet(field=f, options=()) for f in fields]

    monkeypatch.setattr(er_to_er, "_source_choice_sets", fake_choice_sets)

    stats = copy_event_type(
        source_client=source,
        dest_client=dest,
        value="ca_lion",
        target_category="target_cat",
        version="v2",
    )

    assert stats.event_type_action == "created"
    assert stats.choice_fields_copied == 1
    assert stats.choices.created == 2
    # choices upserted onto the DESTINATION
    assert captured["client"] is dest
    # event type posted with overridden category and dict schema
    dest.post_event_type.assert_called_once()
    posted = dest.post_event_type.call_args.kwargs["event_type"]
    assert posted["category"] == "target_cat"
    assert isinstance(posted["schema"], dict)
    assert dest.post_event_type.call_args.kwargs["version"] == "v2"


def test_copy_event_type_v1_skips_choices(monkeypatch):
    source = MagicMock()
    source.get_event_types.return_value = [
        {
            "id": "1",
            "value": "ca_lion",
            "display": "Lion",
            "category": "source_cat",
            "is_active": True,
            "schema": json.dumps({"schema": {"properties": {}}}),
        }
    ]
    dest = _dest_with_category()

    def fail_if_called(**kwargs):
        raise AssertionError("choices must not be copied for v1")

    monkeypatch.setattr(er_to_er, "_source_choice_sets", fail_if_called)

    stats = copy_event_type(
        source_client=source,
        dest_client=dest,
        value="ca_lion",
        target_category="target_cat",
        version="v1",
    )

    assert stats.event_type_action == "created"
    assert stats.choice_fields_copied == 0
    posted = dest.post_event_type.call_args.kwargs["event_type"]
    assert posted["category"] == "target_cat"
    # v1 schema stays a JSON string (EREventType.event_schema aliases "schema")
    assert isinstance(posted["schema"], str)


def test_copy_event_type_source_not_found():
    source = MagicMock()
    source.get_event_types.return_value = [_src_v2_event_type(value="other")]
    dest = _dest_with_category()
    with pytest.raises(EventTypeNotFound):
        copy_event_type(
            source_client=source,
            dest_client=dest,
            value="ca_lion",
            target_category="target_cat",
            version="v2",
        )


def test_copy_event_type_target_category_missing():
    source = MagicMock()
    source.get_event_types.return_value = [_src_v2_event_type()]
    dest = MagicMock()
    dest.get_event_categories.return_value = [{"value": "something_else"}]
    with pytest.raises(TargetCategoryMissing):
        copy_event_type(
            source_client=source,
            dest_client=dest,
            value="ca_lion",
            target_category="target_cat",
            version="v2",
        )


def test_copy_event_type_patches_when_value_exists(monkeypatch):
    source = MagicMock()
    source.get_event_types.return_value = [_src_v2_event_type()]
    dest = MagicMock()
    dest.get_event_categories.return_value = [{"value": "target_cat"}]
    dest.get_event_types.return_value = [
        {"id": "99999999-9999-9999-9999-999999999999", "value": "ca_lion"}
    ]
    monkeypatch.setattr(er_to_er, "upsert_choices", lambda **k: ChoicesStats())
    monkeypatch.setattr(
        er_to_er, "_source_choice_sets", lambda *, source_client, fields: []
    )

    stats = copy_event_type(
        source_client=source,
        dest_client=dest,
        value="ca_lion",
        target_category="target_cat",
        version="v2",
    )

    assert stats.event_type_action == "updated"
    dest.patch_event_type.assert_called_once()
    patched = dest.patch_event_type.call_args.kwargs["event_type"]
    assert patched["id"] == "99999999-9999-9999-9999-999999999999"
    assert patched["category"] == "target_cat"
    dest.post_event_type.assert_not_called()


def test_copy_event_type_dry_run_makes_no_dest_writes(monkeypatch):
    """DryRunERClient intercepts all dest writes; the inner mock stays clean."""
    inner = MagicMock()
    inner.get_event_categories.return_value = [{"value": "target_cat"}]
    inner.get_event_types.return_value = []  # create path
    dry = DryRunERClient(inner)

    source = MagicMock()
    source.get_event_types.return_value = [_src_v2_event_type()]

    def fake_upsert(*, er_client, choice_sets):
        # Perform a write via the dest client to prove dry-run intercepts it.
        er_client._post(path="choices", payload={"x": 1})
        return ChoicesStats(created=1)

    monkeypatch.setattr(er_to_er, "upsert_choices", fake_upsert)
    monkeypatch.setattr(
        er_to_er,
        "_source_choice_sets",
        lambda *, source_client, fields: [ChoiceSet(field="et1_species", options=())],
    )

    stats = copy_event_type(
        source_client=source,
        dest_client=dry,
        value="ca_lion",
        target_category="target_cat",
        version="v2",
    )

    assert stats.event_type_action == "created"

    # Inner client must have received NO write calls.
    inner.post_event_type.assert_not_called()
    inner.patch_event_type.assert_not_called()
    inner._post.assert_not_called()

    # DryRunERClient must have recorded both intercepted writes.
    intercepted_names = [name for (name, _args, _kwargs) in dry.calls]
    assert "post_event_type" in intercepted_names
    assert "_post" in intercepted_names


def test_source_choice_sets_follows_pagination():
    """_source_choice_sets follows the ``next`` pagination chain on the source."""
    page1 = {
        "results": [{"value": "a", "display": "A", "is_active": True, "ordernum": 0}],
        "next": "choices?page=2",
    }
    page2 = {
        "results": [{"value": "b", "display": "B", "is_active": True, "ordernum": 1}],
        "next": None,
    }

    source = MagicMock()
    source._get.side_effect = [page1, page2]

    sets = er_to_er._source_choice_sets(source_client=source, fields=["et1_species"])

    assert len(sets) == 1
    cs = sets[0]
    assert cs.field == "et1_species"
    assert [o.value for o in cs.options] == ["a", "b"]
    assert source._get.call_count == 2


# ── _normalize_v2_schema_for_post ──────────────────────────────


def test_normalize_v2_schema_adds_unevaluated_and_drops_additional():
    # This is the exact shape ER's GET returns: additionalProperties marker,
    # no unevaluatedProperties (which ER's POST meta-schema requires).
    schema = {
        "json": {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "additionalProperties": False,
            "properties": {"a": {"type": "string"}},
            "required": ["a"],
        },
        "ui": {"fields": {}},
    }
    out = er_to_er._normalize_v2_schema_for_post(schema)
    jb = out["json"]
    assert jb["unevaluatedProperties"] is False
    assert "additionalProperties" not in jb
    # Everything else is preserved untouched.
    assert jb["properties"] == {"a": {"type": "string"}}
    assert jb["required"] == ["a"]
    assert jb["type"] == "object"
    assert out["ui"] == {"fields": {}}


def test_normalize_v2_schema_mirrors_additional_true():
    schema = {"json": {"additionalProperties": True, "properties": {}}}
    jb = er_to_er._normalize_v2_schema_for_post(schema)["json"]
    assert jb["unevaluatedProperties"] is True
    assert "additionalProperties" not in jb


def test_normalize_v2_schema_preserves_existing_unevaluated():
    schema = {"json": {"unevaluatedProperties": False, "properties": {}}}
    jb = er_to_er._normalize_v2_schema_for_post(schema)["json"]
    assert jb["unevaluatedProperties"] is False


def test_normalize_v2_schema_no_json_block_is_noop():
    assert er_to_er._normalize_v2_schema_for_post({}) == {}
    assert er_to_er._normalize_v2_schema_for_post({"ui": {}}) == {"ui": {}}


def test_copy_event_type_v2_repairs_schema_for_post(monkeypatch):
    # Source returns ER's GET shape (additionalProperties, no unevaluatedProperties).
    src = _src_v2_event_type()
    schema = {
        "json": {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "species": {"anyOf": [{"$ref": "/c/choices.json?field=et1_species"}]}
            },
            "required": [],
        },
        "ui": {},
    }
    src["schema"] = json.dumps(schema)

    source = MagicMock()
    source.get_event_types.return_value = [src]
    dest = _dest_with_category()
    monkeypatch.setattr(er_to_er, "upsert_choices", lambda **k: ChoicesStats())
    monkeypatch.setattr(
        er_to_er, "_source_choice_sets", lambda *, source_client, fields: []
    )

    copy_event_type(
        source_client=source,
        dest_client=dest,
        value="ca_lion",
        target_category="target_cat",
        version="v2",
    )

    posted = dest.post_event_type.call_args.kwargs["event_type"]
    jb = posted["schema"]["json"]
    assert jb["unevaluatedProperties"] is False
    assert "additionalProperties" not in jb
    # The choice $ref in properties survives normalization untouched.
    assert "species" in jb["properties"]
