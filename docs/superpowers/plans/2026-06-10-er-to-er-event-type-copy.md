# ER → ER Event-Type Copy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `copy-event-type` CLI subcommand that copies one EarthRanger event type (by `value`) from a source ER site to a destination ER site, bringing along the v2 choice option-sets its schema references and attaching it to an existing target category.

**Architecture:** A standalone module `src/er_smart_sync/er_to_er.py` exposes `copy_event_type(source_client, dest_client, ...)` taking two `ERClient`s. It reuses `choices.upsert_choices` / `choices._fetch_existing` for the choices leg and `synchronizer._retry` for resilient writes. A new Click subcommand in `cli.py` builds the two clients and prints a summary. `ERSmartSynchronizer` is untouched (it assumes one ER + one SMART config).

**Tech Stack:** Python 3, Click, Pydantic v1, `erclient.ERClient`, pytest + `unittest.mock`.

---

## File Structure

- **Create** `src/er_smart_sync/er_to_er.py` — copy orchestration, `$ref` field extraction, source-choice-set building, custom exceptions, `CopyEventTypeStats`.
- **Modify** `src/er_smart_sync/cli.py` — add the `copy-event-type` subcommand.
- **Create** `tests/test_er_to_er.py` — unit tests for the module (mocked clients).
- **Modify** `tests/test_cli.py` — one CliRunner test for the subcommand wiring.

Reused (no changes): `choices.ChoiceOption`, `choices.ChoiceSet`, `choices.ChoicesStats`, `choices.upsert_choices`, `choices._fetch_existing`, `smart_to_er_v2.ERV2EventType`, `smartconnect.er_sync_utils.EREventType`, `synchronizer._retry`, `defaults.DryRunERClient`.

### Key facts (verified against the codebase)

- `ERV2EventType(value, display, category, is_active, readonly, schema=<dict>)` — `event_schema` field aliased to `schema`, `allow_population_by_field_name=True`. Imported from `.smart_to_er_v2`.
- `EREventType` (v1) fields `id, category, value, display, event_schema, is_active` — `event_schema` aliased to `schema`, `allow_population_by_field_name=False` (so pass `schema=` as a JSON **string**, not `event_schema=`). Imported from `smartconnect.er_sync_utils`.
- `ERClient.get_event_types(include_inactive=True, include_schema=True, version=...)` → `list[dict]` with keys `value, display, category, schema, is_active, readonly, id`. v2 `schema` comes back as a **JSON-stringified blob**; v1 `schema` is also a JSON string.
- `ERClient.get_event_categories()` → `list[dict]` with `value, display`.
- `ERClient.post_event_type(event_type=<dict>, version=...)` / `patch_event_type(event_type=<dict>, version=...)`.
- `_fetch_existing(er_client=, field=)` (in `choices.py`) lists all `model="activity.event"` choice records for a `field`, handling pagination and treating the "is not one of the available choices" 400 as empty. Works against any client → reuse it to read source choices.
- `_retry(fn, **kwargs)` (in `synchronizer.py`) wraps a write callable with backoff.
- v2 `$ref` URLs are `{choices_base_url}/choices.json?field={field}` — extract the field with a regex on `field=`, base-url-agnostic.

---

## Task 1: Module skeleton — exceptions, stats, `$ref` field extraction

**Files:**
- Create: `src/er_smart_sync/er_to_er.py`
- Test: `tests/test_er_to_er.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_er_to_er.py`:

```python
"""Tests for er_smart_sync.er_to_er."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from er_smart_sync import er_to_er
from er_smart_sync.er_to_er import (
    CopyEventTypeStats,
    EventTypeNotFound,
    TargetCategoryMissing,
    copy_event_type,
    extract_choice_fields,
)


# ── extract_choice_fields ──────────────────────────────────────


def test_extract_choice_fields_finds_refs():
    schema = {
        "json": {
            "properties": {
                "species": {"anyOf": [{"$ref": "/api/v2.0/schemas/choices.json?field=et123_species"}]},
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_er_to_er.py -k extract_choice_fields -v`
Expected: FAIL with `ModuleNotFoundError` / `ImportError: cannot import name 'extract_choice_fields'`.

- [ ] **Step 3: Write minimal implementation**

Create `src/er_smart_sync/er_to_er.py`:

```python
"""Copy an EarthRanger event type from one ER site to another.

ER → ER, not SMART → ER: takes two ERClients (source + destination) and
copies a single event type (by ``value``), bringing along the v2 choice
option-sets its schema references via ``$ref``. Attaches the copy to a
target category that must already exist on the destination.

Lives outside ERSmartSynchronizer because that class is built around one
ER + one SMART config; ER → ER needs two ER clients and no SMART client.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field as _dc_field

from smartconnect.er_sync_utils import EREventType

from .choices import (
    ChoiceOption,
    ChoiceSet,
    ChoicesStats,
    _fetch_existing,
    upsert_choices,
)
from .smart_to_er_v2 import ERV2EventType
from .synchronizer import _retry

logger = logging.getLogger(__name__)

# Pulls the choice field out of a v2 $ref URL like
# ".../choices.json?field=et123_species" or "...?field=x&extra=1".
_CHOICE_REF_FIELD_RE = re.compile(r"[?&]field=([^&\"']+)")


class EventTypeNotFound(Exception):
    """Raised when the source has no event type with the requested value."""


class TargetCategoryMissing(Exception):
    """Raised when the target category does not exist on the destination."""


@dataclass
class CopyEventTypeStats:
    """Summary of one copy_event_type run."""

    event_type_action: str = ""  # "created" | "updated"
    choice_fields_copied: int = 0
    choices: ChoicesStats = _dc_field(default_factory=ChoicesStats)


def extract_choice_fields(schema: dict) -> list[str]:
    """Return the choice ``field`` names referenced by ``$ref`` URLs in a v2 schema.

    Walks the whole schema dict for ``$ref`` strings and extracts the
    ``field=`` query parameter from each. Deduplicates while preserving
    first-seen order. Non-choice ``$ref``s (e.g. ``#/definitions/...``) are
    ignored because they have no ``field=`` parameter.
    """
    found: list[str] = []

    def walk(node):
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "$ref" and isinstance(value, str):
                    match = _CHOICE_REF_FIELD_RE.search(value)
                    if match:
                        found.append(match.group(1))
                else:
                    walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(schema)

    seen: set[str] = set()
    result: list[str] = []
    for f in found:
        if f not in seen:
            seen.add(f)
            result.append(f)
    return result
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_er_to_er.py -k extract_choice_fields -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add src/er_smart_sync/er_to_er.py tests/test_er_to_er.py
git commit -m "feat: er_to_er module skeleton + \$ref choice-field extraction"
```

---

## Task 2: Build choice sets from a source client

**Files:**
- Modify: `src/er_smart_sync/er_to_er.py`
- Test: `tests/test_er_to_er.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_er_to_er.py`:

```python
# ── _source_choice_sets ────────────────────────────────────────


def test_source_choice_sets_orders_and_maps(monkeypatch):
    records_by_field = {
        "et1_species": [
            {"value": "lion", "display": "Lion", "is_active": True, "ordernum": 1},
            {"value": "zebra", "display": "Zebra", "is_active": False, "ordernum": 0},
        ],
    }

    def fake_fetch(*, er_client, field):
        return records_by_field.get(field, [])

    monkeypatch.setattr(er_to_er, "_fetch_existing", fake_fetch)

    sets = er_to_er._source_choice_sets(source_client=MagicMock(), fields=["et1_species"])
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_er_to_er.py -k source_choice_sets -v`
Expected: FAIL with `AttributeError: module 'er_smart_sync.er_to_er' has no attribute '_source_choice_sets'`.

- [ ] **Step 3: Write minimal implementation**

Append to `src/er_smart_sync/er_to_er.py`:

```python
def _source_choice_sets(*, source_client, fields: list[str]) -> list[ChoiceSet]:
    """Read each choice field from the source and build ChoiceSets.

    Reuses ``choices._fetch_existing`` (pagination + 400-as-empty handling).
    Records are ordered by their source ``ordernum`` so upsert_choices
    reassigns equivalent positions on the destination. Fields with no source
    records are skipped with a warning (the dest field will render empty).
    """
    sets: list[ChoiceSet] = []
    for field_name in fields:
        records = _fetch_existing(er_client=source_client, field=field_name)
        if not records:
            logger.warning(
                "Source has no choice records for field %r; the destination "
                "event type may render an empty dropdown for it.",
                field_name,
            )
            continue
        records = sorted(records, key=lambda r: r.get("ordernum") or 0)
        options = tuple(
            ChoiceOption(
                value=r["value"],
                display=r.get("display") or r["value"],
                is_active=bool(r.get("is_active", True)),
            )
            for r in records
        )
        sets.append(ChoiceSet(field=field_name, options=options))
    return sets
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_er_to_er.py -k source_choice_sets -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add src/er_smart_sync/er_to_er.py tests/test_er_to_er.py
git commit -m "feat: build choice sets from a source ER client"
```

---

## Task 3: `copy_event_type` orchestration

**Files:**
- Modify: `src/er_smart_sync/er_to_er.py`
- Test: `tests/test_er_to_er.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_er_to_er.py`:

```python
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
    monkeypatch.setattr(
        er_to_er,
        "_source_choice_sets",
        lambda *, source_client, fields: [ChoiceSet(field=f, options=()) for f in fields],
    )

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


def test_copy_event_type_target_category_missing(monkeypatch):
    source = MagicMock()
    source.get_event_types.return_value = [_src_v2_event_type()]
    dest = MagicMock()
    dest.get_event_categories.return_value = [{"value": "something_else"}]
    monkeypatch.setattr(
        er_to_er, "_source_choice_sets", lambda *, source_client, fields: []
    )
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_er_to_er.py -k copy_event_type -v`
Expected: FAIL with `ImportError: cannot import name 'copy_event_type'` (it is imported at the top of the test file).

- [ ] **Step 3: Write minimal implementation**

Append to `src/er_smart_sync/er_to_er.py`:

```python
def _as_dict(raw) -> dict:
    """Normalize an ER schema blob to a dict.

    ER returns v2 schemas JSON-stringified on GET; mirror the synchronizer's
    handling (synchronizer.py:593). Returns {} for unparseable / unexpected
    shapes.
    """
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            logger.warning("Source schema is a non-JSON string; treating as empty.")
            return {}
    if isinstance(raw, dict):
        return raw
    return {}


def copy_event_type(
    *,
    source_client,
    dest_client,
    value: str,
    target_category: str,
    version: str = "v2",
    copy_choices: bool = True,
) -> CopyEventTypeStats:
    """Copy one event type (by ``value``) from source ER to destination ER.

    For v2, also copies the choice option-sets the schema references onto the
    destination (before writing the event type, so its $refs resolve). The
    target category must already exist on the destination. If an event type
    with the same value already exists on the destination it is patched;
    otherwise it is created.

    Raises:
        EventTypeNotFound: the source has no event type with ``value``.
        TargetCategoryMissing: ``target_category`` is absent on the destination.
    """
    # 1. Fetch the source event type.
    source_types = source_client.get_event_types(
        include_inactive=True, include_schema=True, version=version
    )
    src = next((t for t in source_types if t.get("value") == value), None)
    if src is None:
        raise EventTypeNotFound(
            f"No event type with value {value!r} found on the source "
            f"(version={version})."
        )

    # 2. Verify the target category exists on the destination.
    dest_categories = dest_client.get_event_categories()
    if not any(c.get("value") == target_category for c in dest_categories):
        available = sorted(c.get("value") for c in dest_categories if c.get("value"))
        raise TargetCategoryMissing(
            f"Target category {target_category!r} does not exist on the "
            f"destination. Available categories: {available}"
        )

    stats = CopyEventTypeStats()
    schema_dict = _as_dict(src.get("schema"))

    # 3. Copy referenced choices (v2 only — v1 embeds enums inline).
    if copy_choices and version == "v2":
        fields = extract_choice_fields(schema_dict)
        if fields:
            choice_sets = _source_choice_sets(
                source_client=source_client, fields=fields
            )
            stats.choice_fields_copied = len(choice_sets)
            stats.choices = upsert_choices(
                er_client=dest_client, choice_sets=choice_sets
            )

    # 4. Reconstruct and write the event type with the overridden category.
    dest_types = dest_client.get_event_types(
        include_inactive=True, include_schema=True, version=version
    )
    existing_dest = next((t for t in dest_types if t.get("value") == value), None)

    if version == "v2":
        event_type = ERV2EventType(
            value=src["value"],
            display=src["display"],
            category=target_category,
            is_active=bool(src.get("is_active", True)),
            readonly=bool(src.get("readonly", False)),
            schema=schema_dict,
        )
    else:
        # EREventType.allow_population_by_field_name is False, so pass the
        # alias "schema" (a JSON string) rather than event_schema.
        event_type = EREventType(
            value=src["value"],
            display=src["display"],
            category=target_category,
            is_active=bool(src.get("is_active", True)),
            **{"schema": src.get("schema")},
        )

    if existing_dest is not None:
        event_type.id = existing_dest.get("id")
        _retry(
            dest_client.patch_event_type,
            event_type=event_type.dict(by_alias=True, exclude_none=True),
            version=version,
        )
        stats.event_type_action = "updated"
    else:
        _retry(
            dest_client.post_event_type,
            event_type=event_type.dict(by_alias=True, exclude_none=True),
            version=version,
        )
        stats.event_type_action = "created"

    return stats
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_er_to_er.py -v`
Expected: PASS (all tests in the file).

- [ ] **Step 5: Commit**

```bash
git add src/er_smart_sync/er_to_er.py tests/test_er_to_er.py
git commit -m "feat: copy_event_type orchestration (fetch, choices, create/patch)"
```

---

## Task 4: CLI `copy-event-type` subcommand

**Files:**
- Modify: `src/er_smart_sync/cli.py` (add subcommand after the `patrols` command, before `config-template`)
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_cli.py` (top of file already has `from click.testing import CliRunner` and `from er_smart_sync.cli import main` — if not, add them):

```python
def test_copy_event_type_cmd_invokes_copy(monkeypatch):
    from click.testing import CliRunner

    from er_smart_sync import cli as cli_module
    from er_smart_sync.er_to_er import CopyEventTypeStats
    from er_smart_sync.choices import ChoicesStats

    captured = {}

    def fake_copy(**kwargs):
        captured.update(kwargs)
        return CopyEventTypeStats(
            event_type_action="created",
            choice_fields_copied=1,
            choices=ChoicesStats(created=3),
        )

    # ERClient is constructed inside the command; stub it so no network call.
    monkeypatch.setattr(cli_module, "_make_er_client", lambda **kw: MagicMock())
    monkeypatch.setattr(cli_module, "copy_event_type", fake_copy, raising=False)

    runner = CliRunner()
    result = runner.invoke(
        cli_module.main,
        [
            "copy-event-type",
            "--source-endpoint", "https://src/api/v1.0",
            "--source-token", "srctok",
            "--event-type-value", "ca_lion",
            "--dest-endpoint", "https://dst/api/v1.0",
            "--dest-token", "dsttok",
            "--target-event-category", "target_cat",
        ],
    )

    assert result.exit_code == 0, result.output
    assert captured["value"] == "ca_lion"
    assert captured["target_category"] == "target_cat"
    assert captured["version"] == "v2"
    assert "created" in result.output
    assert "created=3" in result.output


def test_copy_event_type_cmd_requires_auth():
    from click.testing import CliRunner

    from er_smart_sync import cli as cli_module

    runner = CliRunner()
    result = runner.invoke(
        cli_module.main,
        [
            "copy-event-type",
            "--source-endpoint", "https://src/api/v1.0",
            "--event-type-value", "ca_lion",
            "--dest-endpoint", "https://dst/api/v1.0",
            "--dest-token", "dsttok",
            "--target-event-category", "target_cat",
        ],
    )
    assert result.exit_code != 0
    assert "source" in result.output.lower()
```

Ensure `from unittest.mock import MagicMock` is imported at the top of `tests/test_cli.py` (add it if missing).

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_cli.py -k copy_event_type -v`
Expected: FAIL — `Error: No such command 'copy-event-type'` (exit code asserts / output assertions fail).

- [ ] **Step 3: Write minimal implementation**

In `src/er_smart_sync/cli.py`, add this import near the top with the other intra-package imports (after `from .synchronizer import ERSmartSynchronizer`):

```python
from .er_to_er import (
    EventTypeNotFound,
    TargetCategoryMissing,
    copy_event_type,
)
```

Add a small client factory near `_make_synchronizer` (so tests can monkeypatch it):

```python
def _make_er_client(*, endpoint, token, username, password):
    """Construct a bare ERClient for one ER site (used by copy-event-type)."""
    from erclient import ERClient

    return ERClient(
        service_root=endpoint,
        username=username,
        password=password,
        token=token,
        client_id="das_web_client",
    )
```

Add the subcommand after the `patrols` command definition (before `config-template`):

```python
# ── copy-event-type subcommand (ER → ER) ────────────────────────


@main.command("copy-event-type")
@click.option("--source-endpoint", required=True, help="Source EarthRanger API URL")
@click.option("--source-token", default="", help="Source ER API token")
@click.option("--source-username", default="", help="Source ER username")
@click.option("--source-password", default="", help="Source ER password")
@click.option(
    "--event-type-value",
    required=True,
    help="`value` of the event type to copy from the source",
)
@click.option("--dest-endpoint", required=True, help="Destination EarthRanger API URL")
@click.option("--dest-token", default="", help="Destination ER API token")
@click.option("--dest-username", default="", help="Destination ER username")
@click.option("--dest-password", default="", help="Destination ER password")
@click.option(
    "--target-event-category",
    required=True,
    help="`value` of the destination event category to attach the copy to "
    "(must already exist on the destination)",
)
@click.option(
    "--version",
    "version",
    type=click.Choice(["v1", "v2"]),
    default="v2",
    help="EarthRanger event-type API version on both sites. Default: v2.",
)
@click.pass_context
def copy_event_type_cmd(
    ctx,
    source_endpoint,
    source_token,
    source_username,
    source_password,
    event_type_value,
    dest_endpoint,
    dest_token,
    dest_username,
    dest_password,
    target_event_category,
    version,
):
    """Copy one event type from a source ER site to a destination ER site.

    For v2 event types, also copies the choice option-sets the schema
    references onto the destination. The target category must already exist
    on the destination. Honors the global --dry-run flag (no writes to the
    destination).
    """
    if not source_token and not (source_username and source_password):
        raise click.UsageError(
            "source auth requires either --source-token or both "
            "--source-username and --source-password."
        )
    if not dest_token and not (dest_username and dest_password):
        raise click.UsageError(
            "dest auth requires either --dest-token or both "
            "--dest-username and --dest-password."
        )

    source_client = _make_er_client(
        endpoint=source_endpoint,
        token=source_token,
        username=source_username,
        password=source_password,
    )
    dest_client = _make_er_client(
        endpoint=dest_endpoint,
        token=dest_token,
        username=dest_username,
        password=dest_password,
    )

    if ctx.obj and ctx.obj.get("dry_run"):
        dest_client = DryRunERClient(dest_client)
        click.echo(
            "Dry run mode: no writes will be sent to the destination ER.",
            err=True,
        )

    try:
        stats = copy_event_type(
            source_client=source_client,
            dest_client=dest_client,
            value=event_type_value,
            target_category=target_event_category,
            version=version,
        )
    except (EventTypeNotFound, TargetCategoryMissing) as e:
        raise click.ClickException(str(e)) from e

    click.echo(f"Event type {event_type_value!r}: {stats.event_type_action}")
    click.echo(f"Choice fields copied: {stats.choice_fields_copied}")
    cs = stats.choices
    click.echo(
        f"Choices: created={cs.created} updated={cs.updated} "
        f"unchanged={cs.unchanged} deactivated={cs.deactivated} "
        f"errored={cs.errored}"
    )
    if cs.errored > 0:
        raise click.ClickException(f"{cs.errored} choice operations failed")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_cli.py -k copy_event_type -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add src/er_smart_sync/cli.py tests/test_cli.py
git commit -m "feat: copy-event-type CLI subcommand"
```

---

## Task 5: Full suite, lint, format, type check

**Files:** none (verification only)

- [ ] **Step 1: Run the full test suite**

Run: `pytest`
Expected: PASS (no regressions; the new `tests/test_er_to_er.py` and `tests/test_cli.py` cases included).

- [ ] **Step 2: Lint**

Run: `ruff check src tests`
Expected: no errors. (If the reuse of `choices._fetch_existing` trips an "unused import" or private-access lint, leave a one-line comment explaining the intentional intra-package reuse; do not silence with a blanket noqa.)

- [ ] **Step 3: Format**

Run: `ruff format src tests`
Expected: files already formatted, or auto-formatted with no semantic change.

- [ ] **Step 4: Type check**

Run: `ty check`
Expected: no new errors introduced by `er_to_er.py` or the CLI changes.

- [ ] **Step 5: Commit any lint/format fixups**

```bash
git add -A
git commit -m "chore: lint/format/type fixups for copy-event-type" || echo "nothing to commit"
```

---

## Task 6: Documentation

**Files:**
- Modify: `CLAUDE.md` (the repo-level one at `earthranger-smart-utils/CLAUDE.md`)
- Modify: `README.md` if it documents CLI subcommands (check first)

- [ ] **Step 1: Check whether README documents subcommands**

Run: `grep -n "datamodel\|events\|patrols\|copy-event-type" README.md`
Expected: shows the existing subcommand docs (or no README hits, in which case skip the README edit).

- [ ] **Step 2: Add a sync-flow bullet to CLAUDE.md**

In `earthranger-smart-utils/CLAUDE.md`, under "## What This Project Does", after the three numbered sync flows, add:

```markdown
4. **Event-type copy** (ER → ER): Copy a single event type (and the v2
   choices its schema references) from one EarthRanger site to another via
   the `copy-event-type` CLI subcommand
```

- [ ] **Step 3: Document the subcommand in README (only if Step 1 found subcommand docs)**

Add a section mirroring the style of the existing subcommand docs:

````markdown
### Copy an event type between ER sites

```bash
er-smart-sync copy-event-type \
  --source-endpoint https://source.pamdas.org/api/v1.0 \
  --source-username USER --source-password PASS \
  --event-type-value ca_lion \
  --dest-endpoint https://dest.pamdas.org/api/v1.0 \
  --dest-username USER --dest-password PASS \
  --target-event-category my_category
```

Copies the event type `ca_lion` to the destination, attaching it to the
existing `my_category` category. For v2 event types, the choice option-sets
its schema references are copied too. Use `--source-token` / `--dest-token`
instead of username/password if you have API tokens, and `--version v1` for
legacy v1 event types. Add the global `--dry-run` flag to preview without
writing.
````

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md README.md
git commit -m "docs: document the copy-event-type subcommand"
```

---

## Self-Review

**1. Spec coverage** (against `docs/superpowers/specs/2026-06-10-er-to-er-event-type-copy-design.md`):
- CLI subcommand + all options + token-or-user/pass per side → Task 4. ✓
- Default v2 with `--version` override → Task 4 (`--version`, default v2). ✓
- Fetch source event type / `EventTypeNotFound` → Task 3. ✓
- Require dest category / `TargetCategoryMissing` → Task 3. ✓
- Copy referenced choices for v2, skip for v1; upsert before event-type write → Task 3 (ordering: choices block precedes write). ✓
- `$ref` field extraction, base-url-agnostic → Task 1. ✓
- Source choice fetch with pagination + 400-as-empty → Task 2 (reuses `_fetch_existing`). ✓
- Reconstruct event type, override category, patch-on-exists else create, `_retry` → Task 3. ✓
- `CopyEventTypeStats` + summary print + nonzero exit on choice errors → Task 3 (stats) + Task 4 (print/exit). ✓
- Dry-run wraps destination client → Task 4. ✓
- Tests for v2 happy, v1 happy, $ref extraction, not-found, category-missing, value-exists→patch, pagination/400 → Tasks 1–4. ✓

**2. Placeholder scan:** No TBD/TODO; every code step contains complete code and exact commands.

**3. Type/name consistency:** `copy_event_type`, `extract_choice_fields`, `_source_choice_sets`, `_as_dict`, `CopyEventTypeStats(event_type_action, choice_fields_copied, choices)`, `EventTypeNotFound`, `TargetCategoryMissing`, `_make_er_client` — names are identical across the module, tests, and CLI. `ERV2EventType(..., schema=dict)` vs `EREventType(..., schema=str)` aliasing matches the verified model configs. `upsert_choices(er_client=, choice_sets=)` and `_fetch_existing(er_client=, field=)` keyword signatures match `choices.py`.
