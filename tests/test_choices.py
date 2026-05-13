"""Tests for er_smart_sync.choices."""

from __future__ import annotations

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


# ── dataclasses ────────────────────────────────────────────────


def test_choice_option_frozen():
    from er_smart_sync.choices import ChoiceOption

    opt = ChoiceOption(value="lion", display="Lion", is_active=True)
    with pytest.raises(Exception):  # frozen dataclass
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


def _category(path, *, display=None, attributes=None,
              is_active=True, is_multiple=False, hkey_path=None):
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
                "species", "LIST", display="Species",
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
        category_path="wildlife", ca_uuid=CA_UUID, cm=None,
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
                "color", "LIST",
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
                "color", "LIST",
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
                "region", "TREE",
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
    # Only leaves: africa.kenya.nairobi → africa_kenya_nairobi, africa.tanzania → africa_tanzania
    assert values == {"africa_kenya_nairobi", "africa_tanzania"}


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
    """Same attribute referenced from two leaf categories → two distinct field hashes."""
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


from unittest.mock import MagicMock


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
        ChoicesStats,
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
                options=(
                    ChoiceOption(value="red", display="Red", is_active=True),
                ),
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
            "count": 1, "next": None,
            "results": [
                {
                    "id": "uuid-1", "model": "activity.event",
                    "field": "etxxx_color", "value": "red",
                    "display": "Red", "ordernum": 0, "is_active": True,
                }
            ],
        },
    )
    stats = upsert_choices(
        er_client=client,
        choice_sets=[
            ChoiceSet(
                field="etxxx_color",
                options=(
                    ChoiceOption(value="red", display="Red", is_active=True),
                ),
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
            "count": 1, "next": None,
            "results": [
                {
                    "id": "uuid-1", "model": "activity.event",
                    "field": "etxxx_color", "value": "red",
                    "display": "OLD Red", "ordernum": 0, "is_active": True,
                }
            ],
        },
    )
    stats = upsert_choices(
        er_client=client,
        choice_sets=[
            ChoiceSet(
                field="etxxx_color",
                options=(
                    ChoiceOption(value="red", display="Red", is_active=True),
                ),
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
            "count": 1, "next": None,
            "results": [
                {
                    "id": "uuid-1", "model": "activity.event",
                    "field": "etxxx_color", "value": "red",
                    "display": "Red", "ordernum": 0, "is_active": False,
                }
            ],
        },
    )
    stats = upsert_choices(
        er_client=client,
        choice_sets=[
            ChoiceSet(
                field="etxxx_color",
                options=(
                    ChoiceOption(value="red", display="Red", is_active=True),
                ),
            )
        ],
    )
    assert stats.updated == 1
    assert client._patch.call_args.kwargs["payload"]["is_active"] is True


def test_upsert_choices_deactivates_when_planned_inactive():
    from er_smart_sync.choices import ChoiceOption, ChoiceSet, upsert_choices

    client = _mock_er_client_for_choices(
        existing_results={
            "count": 1, "next": None,
            "results": [
                {
                    "id": "uuid-1", "model": "activity.event",
                    "field": "etxxx_color", "value": "red",
                    "display": "Red", "ordernum": 0, "is_active": True,
                }
            ],
        },
    )
    stats = upsert_choices(
        er_client=client,
        choice_sets=[
            ChoiceSet(
                field="etxxx_color",
                options=(
                    ChoiceOption(value="red", display="Red", is_active=False),
                ),
            )
        ],
    )
    assert stats.deactivated == 1
    assert client._patch.call_args.kwargs["payload"]["is_active"] is False
