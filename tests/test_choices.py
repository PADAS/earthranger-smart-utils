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
