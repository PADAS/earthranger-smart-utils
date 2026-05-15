# ER v2 choices population — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `er-smart-sync` build and upsert EarthRanger `Choice` records for every choice-bearing SMART attribute, so that v2 event types (rewritten in a follow-up plan) can reference them via `$ref` instead of failing the v2 meta-schema with inline `enum`.

**Architecture:** New `src/er_smart_sync/choices.py` module owns derivation/sanitization helpers, the `ChoiceOption` / `ChoiceSet` / `ChoicesStats` dataclasses, `build_choice_sets`, and `upsert_choices`. `synchronizer.py` runs a two-pass orchestration on v2: build choice sets → upsert → build event types → POST, with strict abort-on-choice-error per CA. New CLI subcommand `choices` runs only the upsert; `datamodel` does it inline when v2 is selected (escape hatch: `--skip-choices`).

**Tech Stack:** Python 3.10+, Pydantic v1, Click, `earthranger-client`, pytest, ruff, ty.

**Spec:** `docs/superpowers/specs/2026-05-13-er-v2-choices-population-design.md`

---

## File Structure

| Path | Action | Responsibility |
|---|---|---|
| `src/er_smart_sync/choices.py` | create | Pure helpers (`sanitize_choice_value`, `derive_choice_field`, `event_type_value_for`), dataclasses (`ChoiceOption`, `ChoiceSet`, `ChoicesStats`), DM walker (`build_choice_sets`), upsert algorithm (`upsert_choices`). |
| `src/er_smart_sync/config.py` | modify | Add `EarthRangerConfig.choices_base_url: str = "/api/v2.0/schemas"`. |
| `src/er_smart_sync/synchronizer.py` | modify | Extend `datamodel_stats` with five new counters; v2 path in `push_smart_ca_datamodel_to_earthranger` runs choices phase before event-type phase; abort event-type POSTs for a CA if any choice errored. |
| `src/er_smart_sync/cli.py` | modify | New `choices` subcommand. `--skip-choices` flag on `datamodel`. v2 `inspect-datamodel` prints choice sets. `config-template` includes `choices_base_url`. |
| `tests/test_choices.py` | create | Unit tests for the three pure helpers, the three dataclasses, `build_choice_sets`, and the `upsert_choices` decision matrix. |
| `tests/test_synchronizer.py` | modify | Two new tests inside `TestEventTypeVersionWiring`: v2 orchestrates choices-then-types; choices-errored aborts event-type POSTs. |
| `tests/test_cli.py` | modify | `choices` subcommand wiring; `--skip-choices` honored; `inspect-datamodel v2` includes a choices section. |
| `tests/test_config.py` | modify | `choices_base_url` defaults and override. |
| `USAGE.md` | modify | Document the new `choices` subcommand and the `--skip-choices` flag. |

---

## Conventions for every task

- TDD: write the failing test → run red → implement → run green → commit.
- Pydantic v1. The project pins `<2.0`.
- After every code change run the full suite: `.venv/bin/pytest -q`. The v1 path (default) must stay green throughout.
- The current `smart_to_er_v2.py` body is broken against ER's meta-schema. This plan **does not** rewrite it — that's a separate plan (the parent spec's Phase 2). For the synchronizer tests in this plan, mock `build_event_types_v2` to return `[]` or whatever shape the test needs.
- Branch already in use: `feature/er-v2-event-types`. Commit there. PR review/merge is a human step after the plan completes.
- Commit messages use conventional-commits style (`feat:`, `test:`, `refactor:`, `docs:`), one task per commit.

---

## Task 1: Three pure helpers (`sanitize_choice_value`, `derive_choice_field`, `event_type_value_for`)

**Files:**
- Create: `src/er_smart_sync/choices.py`
- Create: `tests/test_choices.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_choices.py`:

```python
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
```

- [ ] **Step 2: Run, verify red**

Run: `.venv/bin/pytest tests/test_choices.py -v`
Expected: ModuleNotFoundError on `er_smart_sync.choices`.

- [ ] **Step 3: Implement**

Create `src/er_smart_sync/choices.py`:

```python
"""SMART → EarthRanger Choice records.

Owns the choices layer required by ER v2 event types:

- Pure helpers: ``sanitize_choice_value``, ``derive_choice_field``,
  ``event_type_value_for``.
- Plan-record dataclasses: ``ChoiceOption``, ``ChoiceSet``, ``ChoicesStats``.
- DM walker: ``build_choice_sets``.
- Upsert algorithm: ``upsert_choices``.

See ``docs/superpowers/specs/2026-05-13-er-v2-choices-population-design.md``
for full design rationale.
"""

from __future__ import annotations

import hashlib
import re


def sanitize_choice_value(option_key: str) -> str:
    """Map a SMART option key to a ``^\\w+$`` string.

    SMART keys may contain ``.`` (TREE leaf paths), accents, apostrophes,
    spaces. The Choice DB column requires letters/digits/underscores only.

    This rule is **load-bearing**: changing it later requires backfilling
    historical event records that store the resolved value string.
    """
    sanitized = re.sub(r"[^A-Za-z0-9]+", "_", option_key).strip("_").lower()
    return sanitized or "_"


def derive_choice_field(event_type_value: str, attr_key: str) -> str:
    """Derive a stable Choice.field name.

    Returns ``et{8hex}_{sanitized_attr_key}``. Total length ≤ 40 chars
    (truncated if needed; collisions vanishingly rare since SMART attr keys
    are well under 28 chars in practice).
    """
    digest = hashlib.sha256(event_type_value.encode("utf-8")).hexdigest()[:8]
    sanitized = sanitize_choice_value(attr_key)
    field = f"et{digest}_{sanitized}"
    if len(field) > 40:
        field = field[:40]
    return field


def event_type_value_for(
    *,
    category_path: str,
    ca_uuid: str,
    cm: dict | None,
) -> str:
    """Compute the event-type ``value`` string.

    Mirrors the scheme used by ``smart_to_er_v2._build_one``:

    - Without CM: ``{ca_uuid}_{path_underscored}`` lowercased.
    - With CM:    ``{ca_uuid}_{cm_uuid}_{path_underscored}`` lowercased.

    Caller passes ``category_path`` already resolved (``cat.path`` when no CM,
    ``cat.hkeyPath`` when CM is present — same rule the existing builder
    uses).
    """
    path_underscored = category_path.replace(".", "_")
    if cm:
        value = f"{ca_uuid}_{cm['cm_uuid']}_{path_underscored}"
    else:
        value = f"{ca_uuid}_{path_underscored}"
    return value.lower()
```

- [ ] **Step 4: Run, verify green**

```
.venv/bin/pytest tests/test_choices.py -v
.venv/bin/pytest -q
.venv/bin/ruff check src/er_smart_sync/choices.py tests/test_choices.py
```
Expected: all tests pass, ruff clean.

- [ ] **Step 5: Commit**

```
git add src/er_smart_sync/choices.py tests/test_choices.py
git commit -m "feat(choices): add sanitize_choice_value, derive_choice_field, event_type_value_for"
```

---

## Task 2: `ChoiceOption`, `ChoiceSet`, `ChoicesStats` dataclasses

**Files:**
- Modify: `src/er_smart_sync/choices.py`
- Modify: `tests/test_choices.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_choices.py`:

```python
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
```

- [ ] **Step 2: Run, verify red**

Run: `.venv/bin/pytest tests/test_choices.py -v -k "choice_option or choice_set or choices_stats"`
Expected: ImportError or AttributeError.

- [ ] **Step 3: Implement**

Append to `src/er_smart_sync/choices.py`:

```python
from dataclasses import dataclass, field as dc_field


@dataclass(frozen=True)
class ChoiceOption:
    """A single option in a choice set, with its activity flag."""

    value: str
    display: str
    is_active: bool = True


@dataclass(frozen=True)
class ChoiceSet:
    """The plan for one ER ``Choice.field`` worth of records."""

    field: str
    options: tuple[ChoiceOption, ...]


@dataclass
class ChoicesStats:
    """Per-run counters for the choices upsert phase."""

    created: int = 0
    updated: int = 0
    unchanged: int = 0
    deactivated: int = 0
    errored: int = 0
```

Add the `dataclass` import alongside existing imports near the top of the file.

- [ ] **Step 4: Run, verify green**

```
.venv/bin/pytest tests/test_choices.py -v
.venv/bin/pytest -q
```

- [ ] **Step 5: Commit**

```
git add src/er_smart_sync/choices.py tests/test_choices.py
git commit -m "feat(choices): add ChoiceOption, ChoiceSet, ChoicesStats dataclasses"
```

---

## Task 3: `build_choice_sets` — basic shape (empty input, single LIST attribute)

**Files:**
- Modify: `src/er_smart_sync/choices.py`
- Modify: `tests/test_choices.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_choices.py`:

```python
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
```

- [ ] **Step 2: Run, verify red**

Run: `.venv/bin/pytest tests/test_choices.py -v -k build_choice_sets`
Expected: AttributeError — `build_choice_sets` not defined.

- [ ] **Step 3: Implement**

Append to `src/er_smart_sync/choices.py`:

```python
import logging
from typing import Any

from pydantic import parse_obj_as
from smartconnect.models import Attribute, Category, CategoryAttribute

logger = logging.getLogger(__name__)


# Attribute types that bear choice options. Other types (TEXT, NUMERIC, etc.)
# never produce ChoiceSets.
_CHOICE_TYPES = {"LIST", "MLIST", "TREE"}


def build_choice_sets(
    *,
    dm: dict,
    cm: dict | None = None,
    ca_uuid: str,
) -> list[ChoiceSet]:
    """Walk a SMART data model and emit one ChoiceSet per (event_type, choice attribute).

    Mirrors the structure of ``smart_to_er_v2.build_event_types_v2`` so that
    field names line up byte-for-byte. Does not produce event types; only
    the choices plan.
    """
    source = cm if cm else dm
    cats = parse_obj_as(list[Category], source.get("categories") or [])
    cat_paths = [cat.path for cat in cats]
    attributes = parse_obj_as(list[Attribute], dm.get("attributes") or [])

    result: list[ChoiceSet] = []
    for cat in cats:
        # Only leaf-or-CM-driven categories emit event types; only those need choices.
        is_leaf = _is_leaf_node(cat_paths, cat.path)
        is_active = bool(cm) or (cat.is_active and is_leaf)
        if not is_active:
            continue

        # Compute the same event_type_value the v2 builder will use.
        path_for_value = cat.hkeyPath if cm else cat.path
        et_value = event_type_value_for(
            category_path=path_for_value, ca_uuid=ca_uuid, cm=cm,
        )

        # Collect attributes from this category plus inherited (non-CM only).
        path_components = path_for_value.split(".")
        leaf_attrs = list(cat.attributes)
        if not cm:
            leaf_attrs.extend(_inherited_attributes(cats, path_components))

        for cat_attr in leaf_attrs:
            attribute = next(
                (a for a in attributes if a.key == cat_attr.key), None,
            )
            if attribute is None or attribute.type not in _CHOICE_TYPES:
                continue
            options = list(attribute.options or [])
            if not options:
                continue
            choice_options = tuple(
                ChoiceOption(
                    value=sanitize_choice_value(o.key),
                    display=o.display,
                    is_active=True,
                )
                for o in options
            )
            result.append(
                ChoiceSet(
                    field=derive_choice_field(et_value, cat_attr.key),
                    options=choice_options,
                )
            )

    return result


def _is_leaf_node(node_paths: list[str], cur_node: str) -> bool:
    prefix = f"{cur_node}."
    return not any(p.startswith(prefix) for p in node_paths)


def _inherited_attributes(
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

- [ ] **Step 4: Run, verify green**

```
.venv/bin/pytest tests/test_choices.py -v
.venv/bin/pytest -q
.venv/bin/ruff check src/er_smart_sync/choices.py
```

- [ ] **Step 5: Commit**

```
git add src/er_smart_sync/choices.py tests/test_choices.py
git commit -m "feat(choices): build_choice_sets for LIST and MLIST attributes"
```

---

## Task 4: `build_choice_sets` — CM overlay, TREE flattening, inactive categories

**Files:**
- Modify: `src/er_smart_sync/choices.py`
- Modify: `tests/test_choices.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_choices.py`:

```python
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
```

- [ ] **Step 2: Run, verify red**

Run: `.venv/bin/pytest tests/test_choices.py -v -k "cm_filters or cm_omits or tree or skips_inactive or two_categories"`
Expected: at least the CM-related and TREE tests fail; the skip-inactive test should already pass; two-categories should already pass.

- [ ] **Step 3: Implement**

In `src/er_smart_sync/choices.py`, augment `build_choice_sets` to consult the CM's attribute config when present and to flatten TREE attributes. Replace the inner `for cat_attr in leaf_attrs:` block with:

```python
        attribute_configs = cm.get("attributes") if cm else None

        for cat_attr in leaf_attrs:
            attribute = next(
                (a for a in attributes if a.key == cat_attr.key), None,
            )
            if attribute is None or attribute.type not in _CHOICE_TYPES:
                continue
            options = list(attribute.options or [])
            if not options:
                continue

            options_cfg = _options_config_for(attribute_configs, cat_attr.key)
            if options_cfg is not None:
                choice_options = _options_from_cm_config(options, options_cfg)
            else:
                if attribute.type == "TREE":
                    options = _leaf_options(options)
                choice_options = tuple(
                    ChoiceOption(
                        value=sanitize_choice_value(o.key),
                        display=o.display,
                        is_active=True,
                    )
                    for o in options
                )

            if not choice_options:
                continue

            result.append(
                ChoiceSet(
                    field=derive_choice_field(et_value, cat_attr.key),
                    options=choice_options,
                )
            )
```

Add these helpers near the other internals:

```python
def _options_config_for(
    attribute_configs: list | None, key: str
) -> list | None:
    if not attribute_configs:
        return None
    cfg = next((c for c in attribute_configs if c.get("key") == key), None)
    return cfg.get("options") if cfg else None


def _options_from_cm_config(
    options: list, options_config: list
) -> tuple[ChoiceOption, ...]:
    """Build ChoiceOptions in CM order. Options the CM marks isActive=False
    appear with is_active=False; options the CM omits entirely are dropped."""
    by_key = {o.key: o for o in options}
    result: list[ChoiceOption] = []
    for opt_cfg in options_config:
        key = opt_cfg.get("key")
        if not key:
            continue
        original = by_key.get(key)
        if not original:
            logger.warning("CM references unknown option key %s", key)
            continue
        result.append(
            ChoiceOption(
                value=sanitize_choice_value(original.key),
                display=original.display,
                is_active=bool(opt_cfg.get("isActive")),
            )
        )
    return tuple(result)


def _leaf_options(options: list) -> list:
    """For TREE option sets: keep only leaves (no children)."""
    keys = [o.key for o in options]
    return [o for o in options if _is_leaf_node(keys, o.key)]
```

- [ ] **Step 4: Run, verify green**

```
.venv/bin/pytest tests/test_choices.py -v
.venv/bin/pytest -q
.venv/bin/ruff check src/er_smart_sync/choices.py
```

- [ ] **Step 5: Commit**

```
git add src/er_smart_sync/choices.py tests/test_choices.py
git commit -m "feat(choices): build_choice_sets handles CM overlay, TREE flattening, inactive cats"
```

---

## Task 5: `upsert_choices` — fetch existing + create new

**Files:**
- Modify: `src/er_smart_sync/choices.py`
- Modify: `tests/test_choices.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_choices.py`:

```python
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
```

- [ ] **Step 2: Run, verify red**

Run: `.venv/bin/pytest tests/test_choices.py -v -k upsert_choices`
Expected: ImportError on `upsert_choices`.

- [ ] **Step 3: Implement**

Append to `src/er_smart_sync/choices.py`:

```python
# ER's Choice DB model is shared across content types; for event types we
# always POST with model="activity.event" (the serializer default).
_CHOICE_MODEL = "activity.event"

# Path prefix for the choices REST endpoint. Versionless in ER.
_CHOICES_PATH = "choices"


def upsert_choices(
    *,
    er_client,
    choice_sets: list[ChoiceSet],
) -> ChoicesStats:
    """Upsert each ChoiceSet against ER's Choices API.

    Returns a ChoicesStats counter dataclass; failures are logged and
    counted but do not raise. Per-set processing is independent; an error
    in one ChoiceSet does not block subsequent sets.
    """
    stats = ChoicesStats()
    seen_fields: dict[str, ChoiceSet] = {}

    for cs in choice_sets:
        # Deduplicate: same field, identical options is fine; same field,
        # different options is a builder bug.
        if cs.field in seen_fields:
            if seen_fields[cs.field].options != cs.options:
                raise ValueError(
                    f"ChoiceSet field {cs.field!r} appears twice with "
                    f"different options; this is a builder bug."
                )
            continue
        seen_fields[cs.field] = cs

        try:
            _upsert_one_set(er_client=er_client, cs=cs, stats=stats)
        except Exception:
            logger.exception(
                "Failed to upsert ChoiceSet",
                extra=dict(field=cs.field),
            )
            stats.errored += len(cs.options)

    return stats


def _upsert_one_set(*, er_client, cs: ChoiceSet, stats: ChoicesStats) -> None:
    existing = _fetch_existing(er_client=er_client, field=cs.field)
    existing_by_value: dict[str, dict] = {r["value"]: r for r in existing}

    for ordernum, planned in enumerate(cs.options):
        existing_record = existing_by_value.get(planned.value)
        if existing_record is None:
            _create_choice(
                er_client=er_client,
                cs_field=cs.field,
                option=planned,
                ordernum=ordernum,
                stats=stats,
            )
        else:
            # Update logic added in Task 6.
            stats.unchanged += 1


def _fetch_existing(*, er_client, field: str) -> list[dict]:
    """List all existing Choice records for (model=activity.event, field=...).
    Follows pagination via the DRF `next` URL."""
    results: list[dict] = []
    page = er_client._get(
        path=_CHOICES_PATH,
        params={
            "model": _CHOICE_MODEL,
            "field": field,
            "include_inactive": True,
            "page_size": 200,
        },
    )
    while True:
        if isinstance(page, dict) and "results" in page:
            results.extend(page["results"])
            next_url = page.get("next")
            if not next_url:
                break
            page = er_client._get(path=next_url)
        elif isinstance(page, list):
            results.extend(page)
            break
        else:
            break
    return results


def _create_choice(
    *,
    er_client,
    cs_field: str,
    option: ChoiceOption,
    ordernum: int,
    stats: ChoicesStats,
) -> None:
    payload = {
        "model": _CHOICE_MODEL,
        "field": cs_field,
        "value": option.value,
        "display": option.display,
        "ordernum": ordernum,
        "is_active": option.is_active,
    }
    try:
        er_client._post(path=_CHOICES_PATH, payload=payload)
        stats.created += 1
    except Exception as e:
        logger.exception(
            "Failed to POST choice",
            extra=dict(field=cs_field, value=option.value, error=str(e)),
        )
        stats.errored += 1
```

- [ ] **Step 4: Run, verify green**

```
.venv/bin/pytest tests/test_choices.py -v
.venv/bin/pytest -q
.venv/bin/ruff check src/er_smart_sync/choices.py
```

- [ ] **Step 5: Commit**

```
git add src/er_smart_sync/choices.py tests/test_choices.py
git commit -m "feat(choices): upsert_choices creates new Choice records"
```

---

## Task 6: `upsert_choices` — update, no-op, deactivate, reactivate

**Files:**
- Modify: `src/er_smart_sync/choices.py`
- Modify: `tests/test_choices.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_choices.py`:

```python
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
```

- [ ] **Step 2: Run, verify red**

Run: `.venv/bin/pytest tests/test_choices.py -v -k "unchanged_no_writes or drifted or reactivates or deactivates"`
Expected: 4 failures — `_upsert_one_set` currently treats every existing record as unchanged.

- [ ] **Step 3: Implement**

In `src/er_smart_sync/choices.py`, replace the body of the `else` branch inside `_upsert_one_set`'s for-loop. Replace this:

```python
        else:
            # Update logic added in Task 6.
            stats.unchanged += 1
```

with:

```python
        else:
            _maybe_patch_choice(
                er_client=er_client,
                existing=existing_record,
                planned=planned,
                ordernum=ordernum,
                stats=stats,
            )
```

Then append the helper:

```python
def _maybe_patch_choice(
    *,
    er_client,
    existing: dict,
    planned: ChoiceOption,
    ordernum: int,
    stats: ChoicesStats,
) -> None:
    changes: dict = {}
    if existing.get("display") != planned.display:
        changes["display"] = planned.display
    if existing.get("ordernum") != ordernum:
        changes["ordernum"] = ordernum
    if existing.get("is_active") != planned.is_active:
        changes["is_active"] = planned.is_active

    if not changes:
        stats.unchanged += 1
        return

    is_deactivation = (
        "is_active" in changes
        and existing.get("is_active") is True
        and planned.is_active is False
    )

    path = f"{_CHOICES_PATH}/{existing['id']}"
    try:
        er_client._patch(path=path, payload=changes)
    except Exception as e:
        logger.exception(
            "Failed to PATCH choice",
            extra=dict(id=existing.get("id"), error=str(e)),
        )
        stats.errored += 1
        return

    if is_deactivation:
        stats.deactivated += 1
    else:
        stats.updated += 1
```

- [ ] **Step 4: Run, verify green**

```
.venv/bin/pytest tests/test_choices.py -v
.venv/bin/pytest -q
.venv/bin/ruff check src/er_smart_sync/choices.py
```

- [ ] **Step 5: Commit**

```
git add src/er_smart_sync/choices.py tests/test_choices.py
git commit -m "feat(choices): upsert_choices handles update/no-op/(de)activate"
```

---

## Task 7: `upsert_choices` — orphan deactivation + duplicate-set dedup

**Files:**
- Modify: `src/er_smart_sync/choices.py`
- Modify: `tests/test_choices.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_choices.py`:

```python
def test_upsert_choices_deactivates_orphans():
    """Existing active records not in the plan get soft-deactivated."""
    from er_smart_sync.choices import ChoiceOption, ChoiceSet, upsert_choices

    client = _mock_er_client_for_choices(
        existing_results={
            "count": 2, "next": None,
            "results": [
                {
                    "id": "uuid-r", "model": "activity.event",
                    "field": "etxxx_color", "value": "red",
                    "display": "Red", "ordernum": 0, "is_active": True,
                },
                {
                    "id": "uuid-l", "model": "activity.event",
                    "field": "etxxx_color", "value": "legacy",
                    "display": "Legacy", "ordernum": 1, "is_active": True,
                },
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
            "count": 1, "next": None,
            "results": [
                {
                    "id": "uuid-l", "model": "activity.event",
                    "field": "etxxx_color", "value": "legacy",
                    "display": "Legacy", "ordernum": 0, "is_active": False,
                },
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
                    options=(
                        ChoiceOption(value="red", display="Red", is_active=True),
                    ),
                ),
                ChoiceSet(
                    field="etxxx_color",
                    options=(
                        ChoiceOption(value="blue", display="Blue", is_active=True),
                    ),
                ),
            ],
        )
```

- [ ] **Step 2: Run, verify red**

Run: `.venv/bin/pytest tests/test_choices.py -v -k "orphans or duplicate_field"`
Expected: 2 fail (orphan tests), the dedup-identical test passes (already coded in Task 5), the dedup-different test passes (also already coded in Task 5).

- [ ] **Step 3: Implement orphan handling**

In `src/er_smart_sync/choices.py`, replace the body of `_upsert_one_set` to track planned values and deactivate orphans after the main loop:

```python
def _upsert_one_set(*, er_client, cs: ChoiceSet, stats: ChoicesStats) -> None:
    existing = _fetch_existing(er_client=er_client, field=cs.field)
    existing_by_value: dict[str, dict] = {r["value"]: r for r in existing}
    planned_values: set[str] = set()

    for ordernum, planned in enumerate(cs.options):
        planned_values.add(planned.value)
        existing_record = existing_by_value.get(planned.value)
        if existing_record is None:
            _create_choice(
                er_client=er_client,
                cs_field=cs.field,
                option=planned,
                ordernum=ordernum,
                stats=stats,
            )
        else:
            _maybe_patch_choice(
                er_client=er_client,
                existing=existing_record,
                planned=planned,
                ordernum=ordernum,
                stats=stats,
            )

    # Orphan handling: active records not in the plan get soft-deactivated.
    for record in existing:
        if record["value"] in planned_values:
            continue
        if not record.get("is_active"):
            continue
        try:
            er_client._patch(
                path=f"{_CHOICES_PATH}/{record['id']}",
                payload={"is_active": False},
            )
            stats.deactivated += 1
        except Exception as e:
            logger.exception(
                "Failed to deactivate orphan choice",
                extra=dict(id=record.get("id"), error=str(e)),
            )
            stats.errored += 1
```

- [ ] **Step 4: Run, verify green**

```
.venv/bin/pytest tests/test_choices.py -v
.venv/bin/pytest -q
.venv/bin/ruff check src/er_smart_sync/choices.py
```

- [ ] **Step 5: Commit**

```
git add src/er_smart_sync/choices.py tests/test_choices.py
git commit -m "feat(choices): upsert_choices deactivates orphans, validates duplicate fields"
```

---

## Task 8: `EarthRangerConfig.choices_base_url`

**Files:**
- Modify: `src/er_smart_sync/config.py`
- Modify: `tests/test_config.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_config.py`:

```python
def test_er_config_choices_base_url_default():
    cfg = EarthRangerConfig(id="i", endpoint="https://x/api/v1.0")
    assert cfg.choices_base_url == "/api/v2.0/schemas"


def test_er_config_choices_base_url_override():
    cfg = EarthRangerConfig(
        id="i", endpoint="https://x/api/v1.0",
        choices_base_url="/custom/path",
    )
    assert cfg.choices_base_url == "/custom/path"
```

- [ ] **Step 2: Run, verify red**

Run: `.venv/bin/pytest tests/test_config.py -v -k choices_base_url`
Expected: AttributeError.

- [ ] **Step 3: Implement**

In `src/er_smart_sync/config.py`, add the field to `EarthRangerConfig` (immediately after `event_type_version`):

```python
    choices_base_url: str = "/api/v2.0/schemas"
```

- [ ] **Step 4: Run, verify green**

```
.venv/bin/pytest tests/test_config.py -v
.venv/bin/pytest -q
```

- [ ] **Step 5: Commit**

```
git add src/er_smart_sync/config.py tests/test_config.py
git commit -m "feat(config): add choices_base_url to EarthRangerConfig"
```

---

## Task 9: Synchronizer two-pass orchestration (v2: build choices → upsert → build types → POST)

**Files:**
- Modify: `src/er_smart_sync/synchronizer.py`
- Modify: `tests/test_synchronizer.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_synchronizer.py` (inside the existing `TestEventTypeVersionWiring` class):

```python
    def test_v2_runs_choices_phase_before_event_types(
        self, sync_config_v2, mock_er_client
    ):
        """build_choice_sets and upsert_choices called BEFORE post_event_type."""
        from unittest.mock import call

        mock_er_client.get_event_categories.return_value = []
        mock_er_client.get_event_types.return_value = []

        dm = MagicMock()
        dm.export_as_dict.return_value = {"categories": []}

        order = []

        def fake_build_choice_sets(**kwargs):
            order.append("build_choice_sets")
            return []

        def fake_upsert_choices(**kwargs):
            from er_smart_sync.choices import ChoicesStats
            order.append("upsert_choices")
            return ChoicesStats()

        def fake_build_event_types(**kwargs):
            order.append("build_event_types_v2")
            return []

        with patch(
            "er_smart_sync.synchronizer.build_choice_sets",
            side_effect=fake_build_choice_sets,
        ), patch(
            "er_smart_sync.synchronizer.upsert_choices",
            side_effect=fake_upsert_choices,
        ), patch(
            "er_smart_sync.synchronizer.build_event_types_v2",
            side_effect=fake_build_event_types,
        ):
            sync = ERSmartSynchronizer(
                config=sync_config_v2,
                er_client=mock_er_client,
                smart_client=MagicMock(),
            )
            sync.push_smart_ca_datamodel_to_earthranger(
                dm=dm, smart_ca_uuid="ca-1", ca_label="[TEST]"
            )

        assert order == [
            "build_choice_sets",
            "upsert_choices",
            "build_event_types_v2",
        ]

    def test_v1_path_does_not_call_choices(
        self, sync_config, mock_er_client
    ):
        """v1 path is untouched — no build_choice_sets, no upsert_choices."""
        mock_er_client.get_event_categories.return_value = []
        mock_er_client.get_event_types.return_value = []

        dm = MagicMock()
        dm.export_as_dict.return_value = {"categories": []}

        with patch(
            "er_smart_sync.synchronizer.build_choice_sets",
            return_value=[],
        ) as build_choices, patch(
            "er_smart_sync.synchronizer.upsert_choices",
        ) as upsert, patch(
            "er_smart_sync.synchronizer.build_event_types",
            return_value=[],
        ):
            sync = ERSmartSynchronizer(
                config=sync_config,
                er_client=mock_er_client,
                smart_client=MagicMock(),
            )
            sync.push_smart_ca_datamodel_to_earthranger(
                dm=dm, smart_ca_uuid="ca-1", ca_label="[TEST]"
            )

        build_choices.assert_not_called()
        upsert.assert_not_called()


    def test_v2_choice_stats_merge_into_datamodel_stats(
        self, sync_config_v2, mock_er_client
    ):
        """The five new counters are populated from upsert_choices' return value."""
        from er_smart_sync.choices import ChoicesStats

        mock_er_client.get_event_categories.return_value = []
        mock_er_client.get_event_types.return_value = []

        dm = MagicMock()
        dm.export_as_dict.return_value = {"categories": []}

        stats = ChoicesStats(created=3, updated=1, unchanged=5,
                             deactivated=2, errored=0)

        with patch(
            "er_smart_sync.synchronizer.build_choice_sets",
            return_value=[],
        ), patch(
            "er_smart_sync.synchronizer.upsert_choices",
            return_value=stats,
        ), patch(
            "er_smart_sync.synchronizer.build_event_types_v2",
            return_value=[],
        ):
            sync = ERSmartSynchronizer(
                config=sync_config_v2,
                er_client=mock_er_client,
                smart_client=MagicMock(),
            )
            sync.push_smart_ca_datamodel_to_earthranger(
                dm=dm, smart_ca_uuid="ca-1", ca_label="[TEST]"
            )

        assert sync.datamodel_stats["choices_created"] == 3
        assert sync.datamodel_stats["choices_updated"] == 1
        assert sync.datamodel_stats["choices_unchanged"] == 5
        assert sync.datamodel_stats["choices_deactivated"] == 2
        assert sync.datamodel_stats["choices_errored"] == 0
```

- [ ] **Step 2: Run, verify red**

Run: `.venv/bin/pytest tests/test_synchronizer.py -v -k "runs_choices_phase or v1_path_does_not_call_choices or choice_stats_merge"`
Expected: 3 failures (ImportError on `build_choice_sets`/`upsert_choices` in synchronizer module; missing stats keys).

- [ ] **Step 3: Implement**

In `src/er_smart_sync/synchronizer.py`:

A) Add imports near the existing `from .smart_to_er_v2 import ERV2EventType, build_event_types_v2`:

```python
from .choices import build_choice_sets, upsert_choices
```

B) In `ERSmartSynchronizer.__init__`, extend the `self.datamodel_stats` dict to include the five new counters. Find the existing dict literal and add:

```python
            "choices_created": 0,
            "choices_updated": 0,
            "choices_unchanged": 0,
            "choices_deactivated": 0,
            "choices_errored": 0,
```

C) In `push_smart_ca_datamodel_to_earthranger`, locate the `ca_identifier = self.get_identifier_from_ca_label(ca_label)` line followed by the builder-selection ternary. Insert the choices phase between `ca_identifier` and the builder selection. The full block becomes:

```python
        ca_identifier = self.get_identifier_from_ca_label(ca_label)

        if self._event_type_version == "v2":
            choice_sets = build_choice_sets(
                dm=dm_dict, cm=cdm_dict, ca_uuid=smart_ca_uuid,
            )
            choices_stats = upsert_choices(
                er_client=self.er_client, choice_sets=choice_sets,
            )
            self.datamodel_stats["choices_created"] += choices_stats.created
            self.datamodel_stats["choices_updated"] += choices_stats.updated
            self.datamodel_stats["choices_unchanged"] += choices_stats.unchanged
            self.datamodel_stats["choices_deactivated"] += choices_stats.deactivated
            self.datamodel_stats["choices_errored"] += choices_stats.errored

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

```
.venv/bin/pytest tests/test_synchronizer.py -v
.venv/bin/pytest -q
.venv/bin/ruff check src/er_smart_sync/synchronizer.py
```

- [ ] **Step 5: Commit**

```
git add src/er_smart_sync/synchronizer.py tests/test_synchronizer.py
git commit -m "feat(sync): v2 two-pass orchestration (choices then event types)"
```

---

## Task 10: Abort event-type POSTs when choices errored

**Files:**
- Modify: `src/er_smart_sync/synchronizer.py`
- Modify: `tests/test_synchronizer.py`

- [ ] **Step 1: Write failing test**

Append to `tests/test_synchronizer.py` (inside `TestEventTypeVersionWiring`):

```python
    def test_v2_abort_event_types_when_choices_errored(
        self, sync_config_v2, mock_er_client, caplog
    ):
        from er_smart_sync.choices import ChoicesStats

        mock_er_client.get_event_categories.return_value = []
        mock_er_client.get_event_types.return_value = []

        dm = MagicMock()
        dm.export_as_dict.return_value = {"categories": []}

        stats = ChoicesStats(errored=2)

        with patch(
            "er_smart_sync.synchronizer.build_choice_sets",
            return_value=[],
        ), patch(
            "er_smart_sync.synchronizer.upsert_choices",
            return_value=stats,
        ), patch(
            "er_smart_sync.synchronizer.build_event_types_v2",
        ) as build_types:
            sync = ERSmartSynchronizer(
                config=sync_config_v2,
                er_client=mock_er_client,
                smart_client=MagicMock(),
            )
            with caplog.at_level("WARNING"):
                sync.push_smart_ca_datamodel_to_earthranger(
                    dm=dm, smart_ca_uuid="ca-1", ca_label="[TEST]"
                )

        # build_event_types_v2 never called; no POSTs attempted.
        build_types.assert_not_called()
        mock_er_client.post_event_type.assert_not_called()
        # Clear warning log.
        assert any(
            "Aborting event-type push" in r.message
            for r in caplog.records
        )
```

- [ ] **Step 2: Run, verify red**

Run: `.venv/bin/pytest tests/test_synchronizer.py -v -k abort_event_types_when_choices_errored`
Expected: failure — build_event_types_v2 still called after errored choices.

- [ ] **Step 3: Implement**

In `src/er_smart_sync/synchronizer.py`, modify the v2 block from Task 9 to early-return on choices errors. Replace the choices phase block with:

```python
        if self._event_type_version == "v2":
            choice_sets = build_choice_sets(
                dm=dm_dict, cm=cdm_dict, ca_uuid=smart_ca_uuid,
            )
            choices_stats = upsert_choices(
                er_client=self.er_client, choice_sets=choice_sets,
            )
            self.datamodel_stats["choices_created"] += choices_stats.created
            self.datamodel_stats["choices_updated"] += choices_stats.updated
            self.datamodel_stats["choices_unchanged"] += choices_stats.unchanged
            self.datamodel_stats["choices_deactivated"] += choices_stats.deactivated
            self.datamodel_stats["choices_errored"] += choices_stats.errored
            if choices_stats.errored > 0:
                logger.warning(
                    "Aborting event-type push for CA %s: %d choice "
                    "operations failed. Investigate the choice errors above "
                    "before re-running.",
                    smart_ca_uuid,
                    choices_stats.errored,
                    extra=dict(
                        ca_uuid=smart_ca_uuid,
                        choices_errored=choices_stats.errored,
                    ),
                )
                return
```

(The `return` exits `push_smart_ca_datamodel_to_earthranger` for this CA; the synchronizer's outer loop over CAs continues normally.)

- [ ] **Step 4: Run, verify green**

```
.venv/bin/pytest tests/test_synchronizer.py -v
.venv/bin/pytest -q
.venv/bin/ruff check src/er_smart_sync/synchronizer.py
```

- [ ] **Step 5: Commit**

```
git add src/er_smart_sync/synchronizer.py tests/test_synchronizer.py
git commit -m "feat(sync): abort event-type push when choices phase errored"
```

---

## Task 11: CLI `choices` subcommand

**Files:**
- Modify: `src/er_smart_sync/cli.py`
- Modify: `tests/test_cli.py`

- [ ] **Step 1: Write failing test**

Append to `tests/test_cli.py`:

```python
def test_choices_subcommand_runs_upsert(tmp_path, monkeypatch):
    """`er-smart-sync choices --from-file` invokes build_choice_sets + upsert_choices."""
    from click.testing import CliRunner

    from er_smart_sync.cli import main

    captured = {}

    def fake_build_choice_sets(**kwargs):
        from er_smart_sync.choices import ChoiceOption, ChoiceSet
        captured["build_called"] = True
        return [
            ChoiceSet(
                field="etxxx_color",
                options=(ChoiceOption(value="red", display="Red", is_active=True),),
            )
        ]

    def fake_upsert_choices(*, er_client, choice_sets):
        from er_smart_sync.choices import ChoicesStats
        captured["upsert_called"] = True
        captured["choice_sets"] = choice_sets
        return ChoicesStats(created=1)

    monkeypatch.setattr(
        "er_smart_sync.cli.build_choice_sets", fake_build_choice_sets,
    )
    monkeypatch.setattr(
        "er_smart_sync.cli.upsert_choices", fake_upsert_choices,
    )
    monkeypatch.setattr(
        "smartconnect.SmartClient.load_datamodel",
        lambda self, filename: MagicMock(export_as_dict=lambda: {"categories": []}),
    )

    dm_file = tmp_path / "dm.xml"
    dm_file.write_text("<datamodel/>")

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "choices",
            "--from-file", str(dm_file),
            "--er-endpoint", "https://x/api/v1.0",
            "--er-token", "t",
            "--er-id", "i",
        ],
    )
    assert result.exit_code == 0, result.output
    assert captured.get("build_called") is True
    assert captured.get("upsert_called") is True
    assert "created=1" in result.output or "Created: 1" in result.output


def test_choices_subcommand_exits_nonzero_on_errors(tmp_path, monkeypatch):
    from click.testing import CliRunner

    from er_smart_sync.cli import main

    monkeypatch.setattr(
        "er_smart_sync.cli.build_choice_sets",
        lambda **kw: [],
    )

    def fake_upsert(**kw):
        from er_smart_sync.choices import ChoicesStats
        return ChoicesStats(errored=1)

    monkeypatch.setattr("er_smart_sync.cli.upsert_choices", fake_upsert)
    monkeypatch.setattr(
        "smartconnect.SmartClient.load_datamodel",
        lambda self, filename: MagicMock(export_as_dict=lambda: {"categories": []}),
    )

    dm_file = tmp_path / "dm.xml"
    dm_file.write_text("<datamodel/>")

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "choices",
            "--from-file", str(dm_file),
            "--er-endpoint", "https://x/api/v1.0",
            "--er-token", "t",
            "--er-id", "i",
        ],
    )
    assert result.exit_code != 0
```

- [ ] **Step 2: Run, verify red**

Run: `.venv/bin/pytest tests/test_cli.py -v -k choices_subcommand`
Expected: failure — `choices` subcommand doesn't exist.

- [ ] **Step 3: Implement**

In `src/er_smart_sync/cli.py`:

A) Add imports near the top:

```python
from .choices import build_choice_sets, upsert_choices
```

B) Add the subcommand. Place it after the existing `datamodel` subcommand definition. Reuse the same `smart_options`, `er_options`, and the file-based pattern from `datamodel`:

```python
@main.command()
@click.option("--config", "config_file", type=click.Path(exists=True), help="YAML config file")
@smart_options
@er_options
@click.option("--smart-ca-uuid", multiple=True, help="Conservation area UUID(s) to sync")
@click.option("--from-file", "datamodel_file", type=click.Path(exists=True), help="Load data model from local XML file instead of SMART API")
@click.option("--cm-from-file", "cm_file", type=click.Path(exists=True), help="Load configurable model from local XML file (used with --from-file)")
@click.option("--cm-uuid", "cm_uuid", default=None, help="Configurable-model UUID. Defaults to the zero UUID.")
@click.pass_context
def choices(
    ctx,
    config_file,
    smart_api,
    smart_username,
    smart_password,
    smart_version,
    smart_language,
    er_endpoint,
    er_token,
    er_username,
    er_password,
    er_id,
    smart_ca_uuid,
    datamodel_file,
    cm_file,
    cm_uuid,
):
    """Upsert SMART option sets as EarthRanger Choice records.

    Required before pushing v2 event types; v2 event-type schemas reference
    choices via $ref, and the referenced records must exist first.
    """
    config = _build_config(
        config_file=config_file,
        smart_api=smart_api,
        smart_username=smart_username,
        smart_password=smart_password,
        smart_version=smart_version,
        smart_language=smart_language,
        er_endpoint=er_endpoint,
        er_token=er_token,
        er_username=er_username,
        er_password=er_password,
        er_id=er_id,
        smart_ca_uuids=smart_ca_uuid,
    )

    if cm_file and not datamodel_file:
        raise click.UsageError("--cm-from-file requires --from-file")
    if cm_uuid and not cm_file:
        raise click.UsageError("--cm-uuid requires --cm-from-file")
    resolved_cm_uuid = _resolve_cm_uuid(cm_uuid) if cm_file else None

    sync = _make_synchronizer(config, ctx=ctx)

    # Load the data model (file-based path; API-based path uses the synchronizer's
    # own DM fetch which is not exposed for a per-CA bulk call here — out of scope
    # for the first cut).
    if datamodel_file:
        from smartconnect import ConfigurableDataModel, SmartClient

        sclient = SmartClient(
            api="https://tempuri.org/",
            username="",
            password="",
            use_language_code=smart_language,
        )
        dm = sclient.load_datamodel(filename=datamodel_file)
        cm = None
        if cm_file:
            cm = ConfigurableDataModel(
                use_language_code=smart_language,
                cm_uuid=resolved_cm_uuid,
            )
            with open(cm_file) as f:
                cm.load(f.read())
        choice_sets = build_choice_sets(
            dm=dm.export_as_dict(),
            cm=cm.export_as_dict() if cm else None,
            ca_uuid="smart-ca-import",
        )
    else:
        raise click.UsageError(
            "API-based choices sync is not yet supported. "
            "Use --from-file with --cm-from-file for now."
        )

    stats = upsert_choices(er_client=sync.er_client, choice_sets=choice_sets)
    click.echo(
        f"Choices: created={stats.created} updated={stats.updated} "
        f"unchanged={stats.unchanged} deactivated={stats.deactivated} "
        f"errored={stats.errored}"
    )
    if stats.errored > 0:
        raise click.ClickException(f"{stats.errored} choice operations failed")
```

- [ ] **Step 4: Run, verify green**

```
.venv/bin/pytest tests/test_cli.py -v
.venv/bin/pytest -q
.venv/bin/ruff check src/er_smart_sync/cli.py
.venv/bin/er-smart-sync choices --help
```

- [ ] **Step 5: Commit**

```
git add src/er_smart_sync/cli.py tests/test_cli.py
git commit -m "feat(cli): add 'choices' subcommand for file-based choice upsert"
```

---

## Task 12: `--skip-choices` on `datamodel`

**Files:**
- Modify: `src/er_smart_sync/synchronizer.py`
- Modify: `src/er_smart_sync/cli.py`
- Modify: `tests/test_cli.py`

- [ ] **Step 1: Write failing test**

Append to `tests/test_cli.py`:

```python
def test_datamodel_skip_choices_flag(tmp_path, monkeypatch):
    """--skip-choices makes the synchronizer skip the choices phase even on v2."""
    from click.testing import CliRunner

    from er_smart_sync.cli import main

    captured = {}

    def fake_make_sync(config, ctx=None):
        from er_smart_sync.synchronizer import ERSmartSynchronizer
        sync = ERSmartSynchronizer.__new__(ERSmartSynchronizer)
        sync._event_type_version = config.earthranger.event_type_version
        sync.sync_mode = "both"
        sync.skip_choices = False
        sync.datamodel_stats = {
            "categories_created": 0, "categories_existing": 0,
            "event_types_created": 0, "event_types_updated": 0,
            "event_types_unchanged": 0, "event_types_skipped_by_mode": 0,
            "event_types_skipped_by_conflict": 0,
            "event_types_errored": 0,
            "choices_created": 0, "choices_updated": 0,
            "choices_unchanged": 0, "choices_deactivated": 0,
            "choices_errored": 0,
        }
        sync.push_smart_ca_datamodel_to_earthranger = lambda **kwargs: None
        sync.synchronize_datamodel = lambda: None
        captured["sync"] = sync
        return sync

    monkeypatch.setattr("er_smart_sync.cli._make_synchronizer", fake_make_sync)
    monkeypatch.setattr(
        "smartconnect.SmartClient.load_datamodel",
        lambda self, filename: MagicMock(),
    )

    dm_file = tmp_path / "dm.xml"
    dm_file.write_text("<datamodel/>")

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "datamodel",
            "--from-file", str(dm_file),
            "--er-endpoint", "https://x/api/v1.0",
            "--er-token", "t",
            "--er-id", "i",
            "--event-type-version", "v2",
            "--skip-choices",
        ],
    )
    assert result.exit_code == 0, result.output
    assert captured["sync"].skip_choices is True
```

Also append a synchronizer test:

```python
    def test_v2_skip_choices_bypasses_choices_phase(
        self, sync_config_v2, mock_er_client
    ):
        mock_er_client.get_event_categories.return_value = []
        mock_er_client.get_event_types.return_value = []

        dm = MagicMock()
        dm.export_as_dict.return_value = {"categories": []}

        with patch(
            "er_smart_sync.synchronizer.build_choice_sets",
        ) as build_choices, patch(
            "er_smart_sync.synchronizer.upsert_choices",
        ) as upsert, patch(
            "er_smart_sync.synchronizer.build_event_types_v2",
            return_value=[],
        ):
            sync = ERSmartSynchronizer(
                config=sync_config_v2,
                er_client=mock_er_client,
                smart_client=MagicMock(),
            )
            sync.skip_choices = True
            sync.push_smart_ca_datamodel_to_earthranger(
                dm=dm, smart_ca_uuid="ca-1", ca_label="[TEST]"
            )

        build_choices.assert_not_called()
        upsert.assert_not_called()
```

- [ ] **Step 2: Run, verify red**

Run:
```
.venv/bin/pytest tests/test_cli.py -v -k skip_choices_flag
.venv/bin/pytest tests/test_synchronizer.py -v -k skip_choices_bypasses
```
Expected: both fail — flag/attribute doesn't exist.

- [ ] **Step 3: Implement**

A) In `src/er_smart_sync/synchronizer.py`, add a default attribute in `__init__` (after `self.sync_mode = "both"`):

```python
        self.skip_choices: bool = False
```

B) In `push_smart_ca_datamodel_to_earthranger`, guard the v2 choices block with `self.skip_choices`. Replace:

```python
        if self._event_type_version == "v2":
            choice_sets = build_choice_sets(
                ...
            )
```

with:

```python
        if self._event_type_version == "v2" and not self.skip_choices:
            choice_sets = build_choice_sets(
                ...
            )
```

(Keep the rest of that block unchanged.)

C) In `src/er_smart_sync/cli.py`, add `--skip-choices` to the `datamodel` Click command (after `--event-type-version`):

```python
@click.option(
    "--skip-choices",
    "skip_choices",
    is_flag=True,
    default=False,
    help="Skip the choices upsert phase (v2 only). Use if you've already run `er-smart-sync choices` separately.",
)
```

D) Add `skip_choices` to the `datamodel` function signature (after `event_type_version`). Propagate it into the synchronizer after `_make_synchronizer`:

```python
    if event_type_version:
        config.earthranger.event_type_version = event_type_version
    sync = _make_synchronizer(config, ctx=ctx)
    sync.sync_mode = mode
    sync.skip_choices = skip_choices
```

(Replace the existing `sync = _make_synchronizer(...)` and `sync.sync_mode = mode` lines.)

- [ ] **Step 4: Run, verify green**

```
.venv/bin/pytest tests/test_cli.py -v -k skip_choices
.venv/bin/pytest tests/test_synchronizer.py -v -k skip_choices
.venv/bin/pytest -q
.venv/bin/er-smart-sync datamodel --help | grep skip-choices
```

- [ ] **Step 5: Commit**

```
git add src/er_smart_sync/synchronizer.py src/er_smart_sync/cli.py tests/test_cli.py tests/test_synchronizer.py
git commit -m "feat(cli): --skip-choices flag bypasses choices phase on v2 datamodel"
```

---

## Task 13: `inspect-datamodel` v2 prints choice sets

**Files:**
- Modify: `src/er_smart_sync/cli.py`
- Modify: `tests/test_cli.py`

- [ ] **Step 1: Write failing test**

Append to `tests/test_cli.py`:

```python
def test_inspect_datamodel_v2_prints_choice_sets(tmp_path, monkeypatch):
    """`inspect-datamodel --event-type-version v2` includes a choices section."""
    from click.testing import CliRunner

    from er_smart_sync.cli import main

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
    # Output should mention "Choice sets" header and the option keys.
    assert "Choice" in result.output or "choice" in result.output
    assert "red" in result.output
    assert "blue" in result.output
```

- [ ] **Step 2: Run, verify red**

Run: `.venv/bin/pytest tests/test_cli.py -v -k inspect_datamodel_v2_prints_choice_sets`
Expected: failure — choices not in output.

- [ ] **Step 3: Implement**

In `src/er_smart_sync/cli.py`, find the v2 branch of `inspect_datamodel_cmd`. After the existing `_print_event_type_summary_v2(event_types, ca_label=ca_label)` line, add:

```python
        choice_sets = build_choice_sets(
            dm=dm.export_as_dict(),
            cm=cm.export_as_dict() if cm else None,
            ca_uuid=ca_uuid,
        )
        _print_choice_set_summary(choice_sets)
```

(`build_choice_sets` is already imported at the top of `cli.py` from Task 11.)

Add the printer below `_print_event_type_summary_v2`:

```python
def _print_choice_set_summary(choice_sets) -> None:
    if not choice_sets:
        return
    click.echo("")
    click.echo(f"Choice sets: {len(choice_sets)}")
    for cs in choice_sets:
        click.echo(f"- field: {cs.field}")
        click.echo(f"    options ({len(cs.options)}):")
        for opt in cs.options:
            marker = "" if opt.is_active else " [inactive]"
            click.echo(f"      - {opt.value}: {opt.display}{marker}")
```

- [ ] **Step 4: Run, verify green**

```
.venv/bin/pytest tests/test_cli.py -v -k inspect_datamodel_v2
.venv/bin/pytest -q
```

- [ ] **Step 5: Commit**

```
git add src/er_smart_sync/cli.py tests/test_cli.py
git commit -m "feat(cli): inspect-datamodel v2 prints choice sets after event types"
```

---

## Task 14: `config-template` and USAGE.md docs

**Files:**
- Modify: `src/er_smart_sync/cli.py` (`_CONFIG_YAML_TEMPLATE`)
- Modify: `USAGE.md`
- Modify: `tests/test_cli.py`

- [ ] **Step 1: Write failing test**

Append to `tests/test_cli.py`:

```python
def test_config_template_mentions_choices_base_url():
    from click.testing import CliRunner
    from er_smart_sync.cli import main

    runner = CliRunner()
    result = runner.invoke(main, ["config-template"])
    assert result.exit_code == 0, result.output
    assert "choices_base_url" in result.output
```

- [ ] **Step 2: Run, verify red**

Run: `.venv/bin/pytest tests/test_cli.py -v -k config_template_mentions_choices_base_url`
Expected: failure.

- [ ] **Step 3: Implement template line**

In `src/er_smart_sync/cli.py`, in `_CONFIG_YAML_TEMPLATE`, inside the `earthranger:` block immediately after the `event_type_version:` lines, append:

```yaml

  # URL prefix used in v2 event-type schema $refs (e.g.
  # "{choices_base_url}/choices.json?field=<field>"). Default matches ER's
  # standard /api/v2.0/schemas layout.
  choices_base_url: /api/v2.0/schemas
```

- [ ] **Step 4: Update USAGE.md**

In `USAGE.md`, find the "Event type version" subsection. Add a new subsection after it:

```markdown
### Choices: a v2 prerequisite

ER's v2 event-type meta-schema rejects inline `enum`. Dropdowns must
reference a separate `Choice` record set via `$ref`. `er-smart-sync` handles
this for you in two ways:

**Inline (default for v2):** running `er-smart-sync datamodel
--event-type-version v2 ...` upserts choices automatically before POSTing
event types. The summary line shows `choices_created`, `choices_updated`,
`choices_unchanged`, `choices_deactivated`, `choices_errored` counters
alongside the event-type counters.

**Standalone:** run `er-smart-sync choices --from-file dm.xml --cm-from-file
cm.xml --er-endpoint ... --er-token ...` to upsert choices only, without
touching event types. Useful for debugging or pre-warming a tenant.

If any choice operation errors, the synchronizer **aborts the event-type
POST phase for that CA** — broken `$ref`s would produce empty dropdowns,
which is worse than skipping the push. Investigate the choice errors and
re-run.

The `--skip-choices` flag on `datamodel` bypasses the choices phase even
when v2 is selected. Use it when you've already run `er-smart-sync choices`
separately and want to push event types without re-upserting.
```

- [ ] **Step 5: Run, verify green**

```
.venv/bin/pytest tests/test_cli.py -v -k config_template_mentions_choices_base_url
.venv/bin/pytest -q
.venv/bin/er-smart-sync config-template | grep choices_base_url
```

- [ ] **Step 6: Commit**

```
git add src/er_smart_sync/cli.py USAGE.md tests/test_cli.py
git commit -m "docs: document choices subcommand and choices_base_url config"
```

---

## Task 15: Final integration smoke

- [ ] **Step 1: Run full test suite**

```
.venv/bin/pytest -q
```
Expected: all tests pass.

- [ ] **Step 2: Lint and format**

```
.venv/bin/ruff check src tests
.venv/bin/ruff format --check src tests
```

If `ruff format --check` complains, run `.venv/bin/ruff format src tests` and inspect the diff. Commit only if changes are to files touched by this plan; if pre-existing files were touched, leave them alone (out of scope).

If `ruff check` flags issues introduced by this plan, fix them. If pre-existing (e.g. E501 in unrelated files), leave them.

- [ ] **Step 3: Type check**

```
.venv/bin/ty check
```

Address only regressions introduced by this plan. Pre-existing issues stay.

- [ ] **Step 4: CLI smoke**

```
.venv/bin/er-smart-sync --help
.venv/bin/er-smart-sync choices --help
.venv/bin/er-smart-sync datamodel --help | grep skip-choices
.venv/bin/er-smart-sync config-template | grep choices_base_url
```

All four greps must match; `choices --help` must run without error.

- [ ] **Step 5: Commit any cleanup**

If lint/format/type fixes were needed:

```
git add <touched files>
git commit -m "chore: lint/format pass after choices population work"
```

Otherwise this step is a no-op.

---

## Out of scope (do not implement here)

- **Rewriting `smart_to_er_v2.py` body** to produce meta-schema-valid output. Tracked in the parent v2 spec (`docs/superpowers/specs/2026-05-12-er-v2-event-types-design.md`). After this plan ships, the v2 path's choices phase will run successfully, but event-type POSTs will still 400 until the body rewrite lands.
- **API-based `choices` subcommand** (fetching DM via SMART API rather than `--from-file`). The plan deliberately only supports file-based for now; the spec notes this is an acceptable first cut.
- **TREE hierarchy via `sub_choice_of`.** Flatten to leaves, matching v1 parity.
- **Bulk choices POST.** ER has no endpoint; one-at-a-time is fine for typical CAs.
- **Cross-event-type choice deduplication.** Field names are event-type-scoped by design.

If you discover during implementation that one of these is actually a blocker, **stop** and surface it — don't grow the plan unilaterally.
