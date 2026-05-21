"""Tests for er_smart_sync.choices."""

from __future__ import annotations

import dataclasses
from unittest.mock import MagicMock

import pytest

from er_smart_sync.choices import (
    derive_choice_field,
    event_type_value_for,
    sanitize_choice_value,
)

# ── sanitize_choice_value ──────────────────────────────────────


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("lion", "lion"),
        ("africa.kenya.nairobi", "africa_kenya_nairobi"),
        ("Côte d'Ivoire", "c_te_d_ivoire"),
        ("  trim me  ", "trim_me"),
        ("!@#$%", "_"),  # all non-word → fallback
        ("", "_"),
        ("123abc", "123abc"),
    ],
)
def test_sanitize_choice_value(raw, expected):
    assert sanitize_choice_value(raw) == expected


# ── derive_choice_field ────────────────────────────────────────


def test_derive_choice_field_deterministic():
    a = derive_choice_field("foasf_wildlife", "species")
    b = derive_choice_field("foasf_wildlife", "species")
    assert a == b


def test_derive_choice_field_event_type_scoped():
    a = derive_choice_field("foasf_wildlife", "species")
    b = derive_choice_field("foasf_incidents", "species")
    assert a != b


def test_derive_choice_field_matches_word_pattern():
    import re

    field = derive_choice_field("ca-uuid_path_with_dashes", "attr-key.with.dots")
    assert re.match(r"^\w+$", field), f"field {field!r} contains non-word chars"


def test_derive_choice_field_under_40_chars():
    field = derive_choice_field("x" * 200, "y" * 200)
    assert len(field) <= 40


def test_derive_choice_field_prefix_format():
    field = derive_choice_field("foasf_wildlife", "species")
    assert field.startswith("et")
    # 'et' + 8 hex + '_' + sanitized key
    assert field[2:10].isalnum()  # 8 hex chars
    assert field[10] == "_"
    assert field[11:] == "species"


# ── event_type_value_for ───────────────────────────────────────


def test_event_type_value_for_no_cm():
    # Matches the current v2 builder's "{ca_uuid}_{path_underscored}" lowercased.
    value = event_type_value_for(
        category_path="Incidents.Wildlife",
        ca_uuid="CA-Caps",
        cm=None,
    )
    assert value == "ca-caps_incidents_wildlife"


def test_event_type_value_for_with_cm():
    value = event_type_value_for(
        category_path="Incidents.Wildlife",
        ca_uuid="ca-1",
        cm={"cm_uuid": "CM-Caps", "categories": [], "attributes": []},
    )
    assert value == "ca-1_cm-caps_incidents_wildlife"


def test_event_type_value_for_with_cm_uses_hkey_path():
    """When cm is present, the v2 builder uses cat.hkeyPath; helper must too."""
    # We accept either category_path or hkey_path explicitly via the same arg.
    value = event_type_value_for(
        category_path="some.hkey.path",
        ca_uuid="ca-1",
        cm={"cm_uuid": "cm-1", "categories": [], "attributes": []},
    )
    assert value == "ca-1_cm-1_some_hkey_path"


# ── _shorten_value / _shorten_display ─────────────────────────


def test_shorten_value_short_input_unchanged():
    from er_smart_sync.choices import _shorten_value

    assert _shorten_value("lion") == "lion"
    assert _shorten_value("a" * 100) == "a" * 100  # exactly at limit


def test_shorten_value_long_input_hashed_to_exactly_100():
    from er_smart_sync.choices import _shorten_value

    raw = "africa_kenya_nairobi_westlands_neighborhood_" * 5  # ~220 chars
    out = _shorten_value(raw)
    assert len(out) == 100
    assert out.startswith(raw[:91])
    # 91 readable chars + "_" + 8 hex
    assert out[91] == "_"
    assert all(c in "0123456789abcdef" for c in out[92:])


def test_shorten_value_deterministic():
    from er_smart_sync.choices import _shorten_value

    raw = "x" * 200
    assert _shorten_value(raw) == _shorten_value(raw)


def test_shorten_value_distinct_inputs_with_shared_prefix_differ():
    from er_smart_sync.choices import _shorten_value

    shared_prefix = "a" * 91
    out_a = _shorten_value(shared_prefix + "_aaaa_bbbb_cccc")
    out_b = _shorten_value(shared_prefix + "_xxxx_yyyy_zzzz")
    assert out_a != out_b
    # Same readable prefix; hash tails diverge.
    assert out_a[:91] == out_b[:91]
    assert out_a[92:] != out_b[92:]


def test_shorten_display_short_input_unchanged():
    from er_smart_sync.choices import _shorten_display

    assert _shorten_display("Lion") == "Lion"
    assert _shorten_display("x" * 100) == "x" * 100


def test_shorten_display_dotted_takes_last_segment():
    """SMART's TREE-with-no-<names> edge case: display falls back to the
    dotted key. The leaf name lives at the end — keep that."""
    from er_smart_sync.choices import _shorten_display

    raw = "africa.kenya.nairobi.westlands.specific_neighborhood_with_a_very_long_descriptive_identifier"
    raw = raw + "_padding_to_exceed_100_chars_total"  # ensure > 100
    assert len(raw) > 100
    out = _shorten_display(raw)
    assert out == raw.rsplit(".", 1)[-1]


def test_shorten_display_word_boundary_truncation():
    from er_smart_sync.choices import _shorten_display

    raw = "African Lion " + ("Panthera leo " * 20)  # whitespace, no dots
    assert len(raw) > 100
    out = _shorten_display(raw)
    assert len(out) <= 100
    assert out.endswith("…")
    # Cut on a word boundary, not mid-word.
    assert not out[:-1].endswith(" ")
    # The body before "…" must be a prefix of the original.
    assert raw.startswith(out[:-1])


def test_shorten_display_no_whitespace_hard_cut():
    from er_smart_sync.choices import _shorten_display

    raw = "x" * 200  # no whitespace, no dots
    out = _shorten_display(raw)
    assert len(out) == 100
    assert out == "x" * 99 + "…"


def test_shorten_display_overlong_leaf_truncates_leaf_not_path():
    """When the dotted path's *leaf* segment is itself > 100 chars, the
    truncation must operate on the leaf — not fall back to truncating from
    the start of the full path, which would lose the leaf identifier and
    return parent components."""
    from er_smart_sync.choices import _shorten_display

    parent = "africa.kenya.nairobi"
    leaf = "very_long_leaf_identifier_" + "x" * 100  # > 100 chars
    raw = f"{parent}.{leaf}"
    out = _shorten_display(raw)
    assert len(out) <= 100
    # Result must come from the leaf, not from "africa.kenya…" or similar.
    assert out.startswith("very_long_leaf_identifier")
    assert "africa" not in out
    assert "nairobi" not in out


def test_shorten_value_constants_align_to_100():
    """Guard the prefix-length derivation: any future change to
    _CHOICE_FIELD_MAX or _VALUE_HASH_LEN must keep the total at the cap."""
    from er_smart_sync.choices import (
        _CHOICE_FIELD_MAX,
        _VALUE_HASH_LEN,
        _VALUE_PREFIX_LEN,
    )

    assert _VALUE_PREFIX_LEN + 1 + _VALUE_HASH_LEN == _CHOICE_FIELD_MAX


# ── dataclasses ────────────────────────────────────────────────


def test_choice_option_frozen():
    from er_smart_sync.choices import ChoiceOption

    opt = ChoiceOption(value="lion", display="Lion", is_active=True)
    with pytest.raises(dataclasses.FrozenInstanceError):
        opt.value = "tiger"


def test_choice_set_hashable():
    """ChoiceSet is frozen + options is a tuple, so it should hash."""
    from er_smart_sync.choices import ChoiceOption, ChoiceSet

    cs = ChoiceSet(
        field="etabcdef12_species",
        options=(
            ChoiceOption(value="lion", display="Lion", is_active=True),
            ChoiceOption(value="zebra", display="Zebra", is_active=True),
        ),
    )
    assert hash(cs)  # doesn't raise
    assert cs.field == "etabcdef12_species"
    assert len(cs.options) == 2


def test_choices_stats_default():
    from er_smart_sync.choices import ChoicesStats

    stats = ChoicesStats()
    assert stats.created == 0
    assert stats.updated == 0
    assert stats.unchanged == 0
    assert stats.deactivated == 0
    assert stats.errored == 0


def test_choices_stats_mutable():
    from er_smart_sync.choices import ChoicesStats

    stats = ChoicesStats()
    stats.created += 1
    stats.errored += 2
    assert stats.created == 1
    assert stats.errored == 2


# ── build_choice_sets ──────────────────────────────────────────


CA_UUID = "ca-1234"


def _attr(key, type_, *, display=None, options=None):
    return {
        "key": key,
        "type": type_,
        "isrequired": False,
        "display": display or key,
        "options": options,
    }


def _option(key, display=None, is_active=True):
    return {"key": key, "display": display or key, "isActive": is_active}


def _category(
    path,
    *,
    display=None,
    attributes=None,
    is_active=True,
    is_multiple=False,
    hkey_path=None,
):
    return {
        "path": path,
        "hkeyPath": hkey_path or path,
        "display": display or path,
        "is_multiple": is_multiple,
        "is_active": is_active,
        "attributes": attributes or [],
    }


def _cat_attr(key, *, is_active=True):
    return {"key": key, "is_active": is_active}


def test_build_choice_sets_empty_dm():
    from er_smart_sync.choices import build_choice_sets

    result = build_choice_sets(
        dm={"categories": [], "attributes": []},
        cm=None,
        ca_uuid=CA_UUID,
    )
    assert result == []


def test_build_choice_sets_scalar_only_dm_yields_nothing():
    from er_smart_sync.choices import build_choice_sets

    dm = {
        "categories": [_category("c", attributes=[_cat_attr("title")])],
        "attributes": [_attr("title", "TEXT")],
    }
    result = build_choice_sets(dm=dm, cm=None, ca_uuid=CA_UUID)
    assert result == []


def test_build_choice_sets_single_list_attribute():
    from er_smart_sync.choices import (
        ChoiceOption,
        build_choice_sets,
        derive_choice_field,
        event_type_value_for,
    )

    dm = {
        "categories": [_category("wildlife", attributes=[_cat_attr("species")])],
        "attributes": [
            _attr(
                "species",
                "LIST",
                display="Species",
                options=[
                    _option("lion", "Lion"),
                    _option("zebra", "Zebra"),
                ],
            ),
        ],
    }
    result = build_choice_sets(dm=dm, cm=None, ca_uuid=CA_UUID)

    assert len(result) == 1
    cs = result[0]
    expected_etvalue = event_type_value_for(
        category_path="wildlife",
        ca_uuid=CA_UUID,
        cm=None,
    )
    expected_field = derive_choice_field(expected_etvalue, "species")
    assert cs.field == expected_field
    assert cs.options == (
        ChoiceOption(value="lion", display="Lion", is_active=True),
        ChoiceOption(value="zebra", display="Zebra", is_active=True),
    )


def test_build_choice_sets_mlist_attribute():
    from er_smart_sync.choices import build_choice_sets

    dm = {
        "categories": [_category("incidents", attributes=[_cat_attr("tags")])],
        "attributes": [
            _attr("tags", "MLIST", options=[_option("a"), _option("b")]),
        ],
    }
    result = build_choice_sets(dm=dm, cm=None, ca_uuid=CA_UUID)
    assert len(result) == 1
    assert tuple(o.value for o in result[0].options) == ("a", "b")


def test_build_choice_sets_cm_filters_active_options():
    from er_smart_sync.choices import build_choice_sets

    dm = {
        "categories": [_category("c", attributes=[_cat_attr("color")])],
        "attributes": [
            _attr(
                "color",
                "LIST",
                options=[
                    _option("red"),
                    _option("blue"),
                    _option("green"),
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
                    {"key": "red", "isActive": True},
                    {"key": "blue", "isActive": False},
                    {"key": "green", "isActive": True},
                ],
            }
        ],
    }
    result = build_choice_sets(dm=dm, cm=cm, ca_uuid=CA_UUID)

    assert len(result) == 1
    values = [(o.value, o.is_active) for o in result[0].options]
    assert values == [
        ("red", True),
        ("blue", False),  # CM deactivated → still emitted, is_active=False
        ("green", True),
    ]


def test_build_choice_sets_cm_omits_option_entirely():
    """An option present in the DM but missing from CM is dropped from the plan."""
    from er_smart_sync.choices import build_choice_sets

    dm = {
        "categories": [_category("c", attributes=[_cat_attr("color")])],
        "attributes": [
            _attr(
                "color",
                "LIST",
                options=[_option("red"), _option("blue"), _option("legacy")],
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
                    {"key": "red", "isActive": True},
                    {"key": "blue", "isActive": True},
                ],
            }
        ],
    }
    result = build_choice_sets(dm=dm, cm=cm, ca_uuid=CA_UUID)
    assert [o.value for o in result[0].options] == ["red", "blue"]


def test_build_choice_sets_tree_flattens_to_leaves():
    from er_smart_sync.choices import build_choice_sets

    dm = {
        "categories": [_category("c", attributes=[_cat_attr("region")])],
        "attributes": [
            _attr(
                "region",
                "TREE",
                options=[
                    _option("africa"),
                    _option("africa.kenya"),
                    _option("africa.kenya.nairobi"),
                    _option("africa.tanzania"),
                ],
            )
        ],
    }
    result = build_choice_sets(dm=dm, cm=None, ca_uuid=CA_UUID)
    values = {o.value for o in result[0].options}
    # Only leaves: africa.kenya.nairobi → africa_kenya_nairobi,
    # africa.tanzania → africa_tanzania
    assert values == {"africa_kenya_nairobi", "africa_tanzania"}


def test_build_choice_sets_deep_tree_key_truncated_to_100():
    """A deep TREE leaf whose sanitized key exceeds 100 chars yields a
    ChoiceOption.value of exactly 100 chars, stable across builds."""
    from er_smart_sync.choices import build_choice_sets

    # 8 components × ~22 chars + dots → sanitized form is ~180 chars.
    deep_key = ".".join([f"level_{i}_deep_label_part" for i in range(8)])
    assert len(deep_key) > 100

    dm = {
        "categories": [_category("c", attributes=[_cat_attr("region")])],
        "attributes": [
            _attr("region", "TREE", options=[_option(deep_key, display="Leaf")]),
        ],
    }
    sets_a = build_choice_sets(dm=dm, cm=None, ca_uuid=CA_UUID)
    sets_b = build_choice_sets(dm=dm, cm=None, ca_uuid=CA_UUID)
    assert len(sets_a) == 1 and len(sets_a[0].options) == 1
    value = sets_a[0].options[0].value
    assert len(value) == 100
    assert value == sets_b[0].options[0].value  # deterministic across builds


def test_build_choice_sets_skips_inactive_categories_without_cm():
    from er_smart_sync.choices import build_choice_sets

    dm = {
        "categories": [
            _category("c", attributes=[_cat_attr("color")], is_active=False),
        ],
        "attributes": [
            _attr("color", "LIST", options=[_option("red")]),
        ],
    }
    result = build_choice_sets(dm=dm, cm=None, ca_uuid=CA_UUID)
    assert result == []


def test_build_choice_sets_two_categories_distinct_fields():
    """Same attribute in two leaf categories → two distinct field hashes."""
    from er_smart_sync.choices import build_choice_sets

    dm = {
        "categories": [
            _category("incidents", attributes=[_cat_attr("species")]),
            _category("wildlife", attributes=[_cat_attr("species")]),
        ],
        "attributes": [
            _attr("species", "LIST", options=[_option("lion"), _option("zebra")]),
        ],
    }
    result = build_choice_sets(dm=dm, cm=None, ca_uuid=CA_UUID)
    assert len(result) == 2
    assert result[0].field != result[1].field


# ── upsert_choices ─────────────────────────────────────────────


def _mock_er_client_for_choices(
    existing_results=None,
    post_response=None,
    patch_response=None,
):
    """MagicMock that mimics ERClient._get/_post/_patch for the choices endpoint."""
    client = MagicMock()
    client._get.return_value = (
        existing_results
        if existing_results is not None
        else {"count": 0, "next": None, "results": []}
    )
    client._post.return_value = post_response or {"id": "new-uuid"}
    client._patch.return_value = patch_response or {}
    return client


def test_upsert_choices_creates_new_options():
    from er_smart_sync.choices import (
        ChoiceOption,
        ChoiceSet,
        upsert_choices,
    )

    client = _mock_er_client_for_choices(
        existing_results={"count": 0, "next": None, "results": []},
    )
    choice_sets = [
        ChoiceSet(
            field="etabcdef12_species",
            options=(
                ChoiceOption(value="lion", display="Lion", is_active=True),
                ChoiceOption(value="zebra", display="Zebra", is_active=True),
            ),
        )
    ]

    stats = upsert_choices(er_client=client, choice_sets=choice_sets)

    assert stats.created == 2
    assert stats.updated == 0
    assert stats.unchanged == 0
    assert stats.deactivated == 0
    assert stats.errored == 0
    # Two POSTs, in option order
    posts = client._post.call_args_list
    assert len(posts) == 2
    assert posts[0].kwargs["payload"]["field"] == "etabcdef12_species"
    assert posts[0].kwargs["payload"]["value"] == "lion"
    assert posts[0].kwargs["payload"]["display"] == "Lion"
    assert posts[0].kwargs["payload"]["ordernum"] == 0
    assert posts[0].kwargs["payload"]["is_active"] is True
    assert posts[0].kwargs["payload"]["model"] == "activity.event"


def test_upsert_choices_fetches_existing_with_correct_params():
    from er_smart_sync.choices import (
        ChoiceOption,
        ChoiceSet,
        upsert_choices,
    )

    client = _mock_er_client_for_choices()
    upsert_choices(
        er_client=client,
        choice_sets=[
            ChoiceSet(
                field="etxxx_color",
                options=(ChoiceOption(value="red", display="Red", is_active=True),),
            )
        ],
    )

    assert client._get.called
    get_call = client._get.call_args
    # Path should be 'choices', params should include field and include_inactive
    path_arg = get_call.args[0] if get_call.args else get_call.kwargs.get("path")
    assert "choices" in path_arg
    params = get_call.kwargs.get("params", {})
    assert params.get("field") == "etxxx_color"
    assert params.get("include_inactive") is True
    assert params.get("model") == "activity.event"


def test_upsert_choices_unchanged_no_writes():
    from er_smart_sync.choices import ChoiceOption, ChoiceSet, upsert_choices

    client = _mock_er_client_for_choices(
        existing_results={
            "count": 1,
            "next": None,
            "results": [
                {
                    "id": "uuid-1",
                    "model": "activity.event",
                    "field": "etxxx_color",
                    "value": "red",
                    "display": "Red",
                    "ordernum": 0,
                    "is_active": True,
                }
            ],
        },
    )
    stats = upsert_choices(
        er_client=client,
        choice_sets=[
            ChoiceSet(
                field="etxxx_color",
                options=(ChoiceOption(value="red", display="Red", is_active=True),),
            )
        ],
    )
    assert stats.unchanged == 1
    assert stats.created == 0
    assert stats.updated == 0
    client._post.assert_not_called()
    client._patch.assert_not_called()


def test_upsert_choices_patches_drifted_display():
    from er_smart_sync.choices import ChoiceOption, ChoiceSet, upsert_choices

    client = _mock_er_client_for_choices(
        existing_results={
            "count": 1,
            "next": None,
            "results": [
                {
                    "id": "uuid-1",
                    "model": "activity.event",
                    "field": "etxxx_color",
                    "value": "red",
                    "display": "OLD Red",
                    "ordernum": 0,
                    "is_active": True,
                }
            ],
        },
    )
    stats = upsert_choices(
        er_client=client,
        choice_sets=[
            ChoiceSet(
                field="etxxx_color",
                options=(ChoiceOption(value="red", display="Red", is_active=True),),
            )
        ],
    )
    assert stats.updated == 1
    assert stats.unchanged == 0
    patch_call = client._patch.call_args
    assert "choices/uuid-1" in patch_call.kwargs["path"]
    assert patch_call.kwargs["payload"]["display"] == "Red"


def test_upsert_choices_reactivates_inactive_record():
    from er_smart_sync.choices import ChoiceOption, ChoiceSet, upsert_choices

    client = _mock_er_client_for_choices(
        existing_results={
            "count": 1,
            "next": None,
            "results": [
                {
                    "id": "uuid-1",
                    "model": "activity.event",
                    "field": "etxxx_color",
                    "value": "red",
                    "display": "Red",
                    "ordernum": 0,
                    "is_active": False,
                }
            ],
        },
    )
    stats = upsert_choices(
        er_client=client,
        choice_sets=[
            ChoiceSet(
                field="etxxx_color",
                options=(ChoiceOption(value="red", display="Red", is_active=True),),
            )
        ],
    )
    assert stats.updated == 1
    assert client._patch.call_args.kwargs["payload"]["is_active"] is True


def test_upsert_choices_deactivates_when_planned_inactive():
    from er_smart_sync.choices import ChoiceOption, ChoiceSet, upsert_choices

    client = _mock_er_client_for_choices(
        existing_results={
            "count": 1,
            "next": None,
            "results": [
                {
                    "id": "uuid-1",
                    "model": "activity.event",
                    "field": "etxxx_color",
                    "value": "red",
                    "display": "Red",
                    "ordernum": 0,
                    "is_active": True,
                }
            ],
        },
    )
    stats = upsert_choices(
        er_client=client,
        choice_sets=[
            ChoiceSet(
                field="etxxx_color",
                options=(ChoiceOption(value="red", display="Red", is_active=False),),
            )
        ],
    )
    assert stats.deactivated == 1
    assert client._patch.call_args.kwargs["payload"]["is_active"] is False


def test_upsert_choices_deactivates_orphans():
    """Existing active records not in the plan get soft-deactivated."""
    from er_smart_sync.choices import ChoiceOption, ChoiceSet, upsert_choices

    client = _mock_er_client_for_choices(
        existing_results={
            "count": 2,
            "next": None,
            "results": [
                {
                    "id": "uuid-r",
                    "model": "activity.event",
                    "field": "etxxx_color",
                    "value": "red",
                    "display": "Red",
                    "ordernum": 0,
                    "is_active": True,
                },
                {
                    "id": "uuid-l",
                    "model": "activity.event",
                    "field": "etxxx_color",
                    "value": "legacy",
                    "display": "Legacy",
                    "ordernum": 1,
                    "is_active": True,
                },
            ],
        },
    )
    stats = upsert_choices(
        er_client=client,
        choice_sets=[
            ChoiceSet(
                field="etxxx_color",
                options=(ChoiceOption(value="red", display="Red", is_active=True),),
            )
        ],
    )
    # red is unchanged; legacy is orphaned → deactivated.
    assert stats.unchanged == 1
    assert stats.deactivated == 1
    patch_call = client._patch.call_args
    assert "choices/uuid-l" in patch_call.kwargs["path"]
    assert patch_call.kwargs["payload"] == {"is_active": False}


def test_upsert_choices_does_not_deactivate_already_inactive_orphans():
    """Already-inactive orphans are no-ops, not double-deactivated."""
    from er_smart_sync.choices import ChoiceOption, ChoiceSet, upsert_choices

    client = _mock_er_client_for_choices(
        existing_results={
            "count": 1,
            "next": None,
            "results": [
                {
                    "id": "uuid-l",
                    "model": "activity.event",
                    "field": "etxxx_color",
                    "value": "legacy",
                    "display": "Legacy",
                    "ordernum": 0,
                    "is_active": False,
                },
            ],
        },
    )
    stats = upsert_choices(
        er_client=client,
        choice_sets=[
            ChoiceSet(
                field="etxxx_color",
                options=(ChoiceOption(value="red", display="Red", is_active=True),),
            )
        ],
    )
    assert stats.deactivated == 0
    assert stats.created == 1
    client._patch.assert_not_called()


def test_upsert_choices_duplicate_field_identical_options_deduplicates():
    from er_smart_sync.choices import ChoiceOption, ChoiceSet, upsert_choices

    client = _mock_er_client_for_choices()
    options = (ChoiceOption(value="red", display="Red", is_active=True),)
    stats = upsert_choices(
        er_client=client,
        choice_sets=[
            ChoiceSet(field="etxxx_color", options=options),
            ChoiceSet(field="etxxx_color", options=options),
        ],
    )
    # Second occurrence skipped; only one POST.
    assert stats.created == 1
    assert client._post.call_count == 1


def test_upsert_choices_duplicate_field_different_options_raises():
    from er_smart_sync.choices import ChoiceOption, ChoiceSet, upsert_choices

    client = _mock_er_client_for_choices()
    with pytest.raises(ValueError, match="builder bug"):
        upsert_choices(
            er_client=client,
            choice_sets=[
                ChoiceSet(
                    field="etxxx_color",
                    options=(ChoiceOption(value="red", display="Red", is_active=True),),
                ),
                ChoiceSet(
                    field="etxxx_color",
                    options=(
                        ChoiceOption(value="blue", display="Blue", is_active=True),
                    ),
                ),
            ],
        )


def test_dry_run_er_client_intercepts_choices_writes():
    """DryRunERClient must catch _post and _patch calls used by upsert_choices."""
    from unittest.mock import MagicMock

    from er_smart_sync.choices import (
        ChoiceOption,
        ChoiceSet,
        upsert_choices,
    )
    from er_smart_sync.defaults import DryRunERClient

    inner = MagicMock()
    inner._get.return_value = {"count": 0, "next": None, "results": []}
    inner._post = MagicMock()  # Real ERClient would have this attr
    inner._patch = MagicMock()

    dry = DryRunERClient(inner)

    stats = upsert_choices(
        er_client=dry,
        choice_sets=[
            ChoiceSet(
                field="etxxx_color",
                options=(ChoiceOption(value="red", display="Red", is_active=True),),
            )
        ],
    )

    # Stats should still record the would-be operation.
    assert stats.created == 1
    # Real inner._post / _patch must NOT have been called.
    inner._post.assert_not_called()
    inner._patch.assert_not_called()
    # DryRun should have recorded the call in self.calls.
    assert any("_post" in c[0] for c in dry.calls)


def test_upsert_choices_patches_drifted_ordernum():
    """Ordernum drift triggers a PATCH (preserves dropdown order in ER UI)."""
    from er_smart_sync.choices import ChoiceOption, ChoiceSet, upsert_choices

    client = _mock_er_client_for_choices(
        existing_results={
            "count": 2, "next": None,
            "results": [
                {
                    "id": "uuid-r", "model": "activity.event",
                    "field": "etxxx_color", "value": "red",
                    "display": "Red", "ordernum": 1,  # was second
                    "is_active": True,
                },
                {
                    "id": "uuid-b", "model": "activity.event",
                    "field": "etxxx_color", "value": "blue",
                    "display": "Blue", "ordernum": 0,  # was first
                    "is_active": True,
                },
            ],
        },
    )
    # Plan reverses the order: red first, blue second.
    stats = upsert_choices(
        er_client=client,
        choice_sets=[
            ChoiceSet(
                field="etxxx_color",
                options=(
                    ChoiceOption(value="red", display="Red", is_active=True),
                    ChoiceOption(value="blue", display="Blue", is_active=True),
                ),
            )
        ],
    )
    # Both records had their ordernum flipped → both PATCHed.
    assert stats.updated == 2
    # Two PATCH calls, each with ordernum in payload.
    assert client._patch.call_count == 2
    patch_payloads = [c.kwargs["payload"] for c in client._patch.call_args_list]
    assert any(p.get("ordernum") == 0 for p in patch_payloads)
    assert any(p.get("ordernum") == 1 for p in patch_payloads)


def test_upsert_choices_handles_missing_field_filter_400():
    """Fresh tenants have no Choice records for our derived field names yet.
    ER's AllValuesMultipleFilter returns 400 for those GETs. We must
    interpret that 400 as 'no existing records' and proceed to POST."""
    from erclient.er_errors import ERClientException

    from er_smart_sync.choices import (
        ChoiceOption,
        ChoiceSet,
        upsert_choices,
    )

    inner = MagicMock()
    inner._get.side_effect = ERClientException(
        "Failed to <bound method ...> to ER web service. "
        "400 from ER. Message: unknown reason "
        '{"field":["Select a valid choice. etxxx_color is not one of '
        'the available choices."]}'
    )
    inner._post.return_value = {"id": "new-uuid"}

    stats = upsert_choices(
        er_client=inner,
        choice_sets=[
            ChoiceSet(
                field="etxxx_color",
                options=(ChoiceOption(value="red", display="Red", is_active=True),),
            )
        ],
    )
    assert stats.created == 1
    assert stats.errored == 0
    inner._post.assert_called_once()


def test_upsert_choices_propagates_other_400_errors():
    """Only the specific AllValuesMultipleFilter 400 is treated as empty.
    Other 400s (auth issues, malformed payload, etc.) must propagate."""
    from erclient.er_errors import ERClientException

    from er_smart_sync.choices import (
        ChoiceOption,
        ChoiceSet,
        upsert_choices,
    )

    inner = MagicMock()
    inner._get.side_effect = ERClientException(
        "Failed... 400 ... unrelated authentication failure"
    )

    stats = upsert_choices(
        er_client=inner,
        choice_sets=[
            ChoiceSet(
                field="etxxx_color",
                options=(ChoiceOption(value="red", display="Red", is_active=True),),
            )
        ],
    )
    # The exception bubbles to upsert_choices' outer try/except, which
    # counts it as errored.
    assert stats.errored == 1
    inner._post.assert_not_called()


def test_upsert_choices_counts_one_error_per_failed_set_not_per_option():
    """When _upsert_one_set raises catastrophically (e.g. an unhandled
    error in _fetch_existing), the outer handler counts that as ONE
    failed set, not as len(cs.options) errors. Per-option HTTP errors
    have their own counting inside _create_choice / _maybe_patch_choice."""
    from erclient.er_errors import ERClientException

    from er_smart_sync.choices import (
        ChoiceOption,
        ChoiceSet,
        upsert_choices,
    )

    inner = MagicMock()
    inner._get.side_effect = ERClientException(
        "Failed... 500 ... internal server error"
    )

    stats = upsert_choices(
        er_client=inner,
        choice_sets=[
            ChoiceSet(
                field="etxxx_color",
                options=(
                    ChoiceOption(value="red", display="Red", is_active=True),
                    ChoiceOption(value="blue", display="Blue", is_active=True),
                    ChoiceOption(value="green", display="Green", is_active=True),
                    ChoiceOption(value="yellow", display="Yellow", is_active=True),
                ),
            )
        ],
    )
    # ONE failed set → errored == 1 (not 4).
    assert stats.errored == 1
    assert stats.created == 0
