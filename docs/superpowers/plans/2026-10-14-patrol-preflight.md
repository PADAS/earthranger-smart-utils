# patrol-preflight Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `er-smart-sync patrol-preflight --bundle DIR`, a read-only check that tells an operator, before any patrol is imported, exactly what is missing on the EarthRanger site and how to fix it (ERA-14138).

**Architecture:** A new `preflight.py` holds a `Finding` model and one small function per check; each check reads the bundle and, through `ERPatrolAPI`, the site, and returns findings. `run_preflight` runs every check, isolating failures so one broken check never hides the others, and returns a `PreflightReport` that the CLI renders as text or JSON and that `import-patrols` will later reuse: blocking findings carry the uuids of the patrols they affect, so the importer can skip just those under `--allow-blocking`. Nothing in this plan writes to ER.

**Tech Stack:** Python 3.10+, Click, pydantic v1, erclient `ERClient` through the existing `ERPatrolAPI`, pytest with the in-memory `FakeAPI` pattern from `tests/test_patrol_datamodel.py`.

**Spec:** `docs/superpowers/specs/2026-10-12-smart-patrol-migration-design.md`, sections "patrol-preflight (ERA-14138)", "Historical members", "Patrol times", "Language", "Timezone", and verify-first item 6.

## Global Constraints

- Read-only: no `post_*`, `patch_*`, `_post`, `_patch`, `_put` call anywhere in the preflight path. A test asserts it against a `MagicMock` client.
- Exit codes: 0 when there is no blocking finding (warnings allowed), 2 when there is at least one, 1 on an error (bad bundle, unreachable site, bad flags).
- Every finding has `code`, `level` (`blocking` or `warning`), `message`, `fix`, `count`, `examples` (at most 10 strings) and `affected_patrols` (patrol uuids, empty when the finding is site-wide or not per-patrol).
- The patrol type value, choice values, segment field names and subject identity are computed by the same builders `patrol-datamodel` uses (`build_patrol_types`, `build_patrol_choice_sets`' value rule, `build_segment_schema`, `build_subjects`), never re-derived.
- Pydantic v1 only; `ruff check` and `ruff format` clean on the files this plan creates (main is not ruff-clean; do not reformat existing files).

Facts verified on 2026-10-14 that the checks rely on:

- `GET /api/v1.0/status/` returns `preview_features.patrol_schemas` (the flag check reads it directly instead of inferring it from a 404).
- `GET /api/v1.0/user/me/` returns `is_superuser` and `permissions` as `{model_name: [actions]}`; patrol-related models are `patrol`, `patroltype`, `segmenttype`, `patrolconfiguration`, `patrolconfigurationteam`, `patrolsegmentmember`, `eventrelatedsegments`, `event`. `subject` and `observation` add/change do not appear even for a superuser, so they are not checked.
- `GET /api/v1.0/activity/patrols/config/default/resolved/` returns `members` (active, person-type subjects on the configured member roster, with nested groups expanded), `assets` and `teams` (`value`, `display`, `is_active`).
- das `_validate_rosters` (`das/activity/serializers/patrols.py`) rejects any segment member that is inactive, not person-type, or outside the configured member groups, whatever the segment's dates. Verify-first item 6 is settled: retired staff must be reactivated for the import.
- das `_validate_team` rejects an inactive team on a new segment.
- Event-type values written by `er-smart-sync datamodel` are `<prefix>_<category path with dots as underscores>`: `smart-ca-import_threats_charcoal` for `--from-file` runs, `smart-ca-import_<cm uuid>_threats_charcoal` with a configurable model (split or consolidate), the SMART CA uuid instead of `smart-ca-import` for Connect runs. A bundle observation's `category_hkey` is `threats.charcoal.`.

## Review Focus

1. A site with the `patrol_schemas` flag off: every check that needs the new endpoints must report one blocking `feature_flag` finding and skip itself with a note, not raise or report a cascade of misleading "missing" findings. Test in Task 3 (`test_flag_off_reports_once`).
2. One check raising unexpectedly (a 500 from ER, a malformed response): the other checks still run and the failure is a blocking `check_failed` finding naming the check. Test in Task 2 (`test_a_failing_check_does_not_hide_the_others`).
3. A category matched by two event types (two CAs pushed to the same site, or with and without a configurable model): an ambiguity warning listing the candidates, resolved by `--event-type-prefix`; never a silent pick. Test in Task 5 (`test_ambiguous_event_types_need_a_prefix`).
4. A member who is on the roster but inactive, and one who is active but off the roster: the first is a warning (reactivated by the import), the second blocking. Test in Task 4 (`test_inactive_and_off_roster_members`).
5. A bundle with thousands of patrols: findings stay readable (counts plus at most ten examples) and the JSON carries the full affected-patrol lists. Test in Task 2 (`test_examples_are_capped_but_affected_is_complete`).

---

## File structure

| Path | Responsibility |
| --- | --- |
| `src/er_smart_sync/preflight.py` | `Finding`, `PreflightReport`, the checks, `run_preflight`, `render_text`, `render_json` |
| `src/er_smart_sync/er_patrol_api.py` | add `get_patrol_config_resolved`, `get_me`, `list_event_types` |
| `src/er_smart_sync/smart_to_er_patrol_schema.py` | add `choice_value(item)` (extracted from `_options`, which then uses it) |
| `src/er_smart_sync/cli.py` | `patrol-preflight` command |
| `tests/test_preflight.py` | all check tests, with a `PreflightFakeAPI` |
| `tests/test_er_patrol_api.py`, `tests/test_patrol_schema.py`, `tests/test_cli.py` | additions |
| `docs/cli-reference/patrol-preflight.md`, `docs/workflows/migrate-patrols.md`, `mkdocs.yml`, `docs/changelog.md`, `USAGE.md`, `CLAUDE.md`, the spec | documentation |

---

### Task 1: API reads and the shared choice-value rule

**Files:**
- Modify: `src/er_smart_sync/er_patrol_api.py`, `src/er_smart_sync/smart_to_er_patrol_schema.py`, `tests/test_er_patrol_api.py`, `tests/test_patrol_schema.py`

**Interfaces:**
- Produces: `ERPatrolAPI.get_patrol_config_resolved() -> dict | None` (None on 404), `ERPatrolAPI.get_me() -> dict` (403 → `ERForbidden`), `ERPatrolAPI.list_event_types(version: str = "v2") -> list[dict]` (calls `er.get_event_types(include_inactive=True, include_schema=False, version=version)`; 403 → `ERForbidden`); `choice_value(item: Labelled) -> str | None` (sanitized key, else the uuid without hyphens, else None).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_er_patrol_api.py`:

```python
def test_resolved_config_me_and_event_types():
    c = _client()
    api = ERPatrolAPI(c)
    c._get.side_effect = ERClientNotFound("dark")
    assert api.get_patrol_config_resolved() is None
    c._get.side_effect = None
    c._get.return_value = {"members": [{"id": "s1", "is_active": True}], "assets": [], "teams": []}
    assert api.get_patrol_config_resolved()["members"][0]["id"] == "s1"
    c._get.assert_called_with(path="activity/patrols/config/default/resolved", max_retries=0)
    c._get.return_value = {"is_superuser": False, "permissions": {"patrol": ["add"]}}
    assert api.get_me()["permissions"] == {"patrol": ["add"]}
    c._get.assert_called_with(path="user/me", max_retries=0)
    c.get_event_types.return_value = [{"value": "a"}]
    assert api.list_event_types() == [{"value": "a"}]
    c.get_event_types.assert_called_with(include_inactive=True, include_schema=False, version="v2")
    c.get_event_types.side_effect = ERClientPermissionDenied("no")
    with pytest.raises(ERForbidden):
        api.list_event_types()
```

Append to `tests/test_patrol_schema.py`:

```python
def test_choice_value_rule():
    from er_smart_sync.bundle import Labelled
    from er_smart_sync.smart_to_er_patrol_schema import choice_value

    assert choice_value(Labelled(uuid="a-b", key="Anti Poaching", labels={}, label=None)) == "anti_poaching"
    assert choice_value(Labelled(uuid="aaaa-bbbb", key=None, labels={}, label="HQ")) == "aaaabbbb"
    assert choice_value(Labelled(uuid=None, key=None, labels={}, label="HQ")) is None
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_er_patrol_api.py tests/test_patrol_schema.py -k "resolved_config or choice_value" -q`
Expected: FAIL (`AttributeError: ... get_patrol_config_resolved`, `ImportError: cannot import name 'choice_value'`)

- [ ] **Step 3: Implement**

In `er_patrol_api.py`, after `get_patrol_config`:

```python
    def get_patrol_config_resolved(self) -> dict | None:
        """Active, person-type subjects on the configured member roster (nested
        groups expanded) and the active teams: what das will accept on a segment."""
        return self._get_or_none(f"{PATROL_CONFIG_PATH}/resolved")

    def get_me(self) -> dict:
        try:
            return self.er._get(path="user/me", max_retries=0) or {}
        except ERClientPermissionDenied as e:
            raise ERForbidden(f"GET user/me: {e}") from None

    def list_event_types(self, version: str = "v2") -> list:
        return list(
            self._read(self.er.get_event_types, include_inactive=True, include_schema=False, version=version)
            or []
        )
```

In `smart_to_er_patrol_schema.py`, before `_options`:

```python
def choice_value(item) -> str | None:
    """The Choice value for a SMART list item: its key, sanitized, or for
    key-less rows (stations) its uuid without hyphens. Never the label."""
    if item.key:
        return sanitize_choice_value(item.key)
    if item.uuid:
        return item.uuid.replace("-", "")
    return None
```

and in `_options` replace the `if it.key: ... elif it.uuid: ... else: continue` block with:

```python
        value = choice_value(it)
        if value is None:
            continue  # nothing to identify the option by
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest -q`
Expected: all pass (the `_options` refactor keeps every existing schema test green).

- [ ] **Step 5: Commit**

```bash
git add src/er_smart_sync/er_patrol_api.py src/er_smart_sync/smart_to_er_patrol_schema.py tests/test_er_patrol_api.py tests/test_patrol_schema.py
git commit -m "Preflight groundwork: resolved roster, user/me, event types; shared choice-value rule"
```

---

### Task 2: Findings, the runner, and the bundle-only checks

**Files:**
- Create: `src/er_smart_sync/preflight.py`, `tests/test_preflight.py`

**Interfaces:**
- Produces: `@dataclass Finding(code: str, level: str, message: str, fix: str, count: int = 0, examples: list[str] = [], affected_patrols: list[str] = [])` with `Finding.of(code, level, message, fix, items: list[str], affected=())` building `count=len(items)`, `examples=items[:MAX_EXAMPLES]`; `MAX_EXAMPLES = 10`; `BLOCKING, WARNING = "blocking", "warning"`; `@dataclass PreflightReport(findings: list[Finding], context: dict)` with `.blocking -> list[Finding]`, `.warnings -> list[Finding]`, `.exit_code -> int` (2 if blocking else 0), `.blocked_patrols -> set[str]`; `run_preflight(api, bundle, *, timezone: str | None = None, event_type_prefix: str | None = None, event_type_version: str = "v2", checks=None) -> PreflightReport`; check functions `check_leg_span(ctx)`, `check_time_coverage(ctx)`, `check_timezone(ctx)`, `check_language(ctx)`, `check_bundle_warnings(ctx)`, each `(ctx: PreflightContext) -> list[Finding]`; `@dataclass PreflightContext(api, bundle, timezone, event_type_prefix, event_type_version, cache: dict)` with `ctx.once(key, fn)` memoising site reads shared between checks; `ALL_CHECKS` (ordered list, extended in Tasks 3 to 5).

- [ ] **Step 1: Write the failing tests**

`tests/test_preflight.py`:

```python
from __future__ import annotations

import json
import shutil
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from er_smart_sync.bundle import load_bundle
from er_smart_sync.preflight import (
    BLOCKING,
    MAX_EXAMPLES,
    WARNING,
    Finding,
    check_bundle_warnings,
    check_language,
    check_leg_span,
    check_time_coverage,
    check_timezone,
    run_preflight,
)

FIXTURE = Path(__file__).parent / "fixtures" / "smart_demo_bundle"


def _edited(tmp_path, edit):
    shutil.copytree(FIXTURE, tmp_path / "b")
    for name in ("manifest", "reference", "patrols"):
        f = tmp_path / "b" / f"{name}.json"
        doc = json.loads(f.read_text())
        edit(name, doc)
        f.write_text(json.dumps(doc))
    return load_bundle(tmp_path / "b")


def _codes(findings):
    return [(f.code, f.level) for f in findings]


def test_finding_of_caps_examples():
    f = Finding.of("x", BLOCKING, "m", "fix", [str(i) for i in range(25)], affected=["p1", "p2"])
    assert f.count == 25 and len(f.examples) == MAX_EXAMPLES and f.affected_patrols == ["p1", "p2"]


def test_bundle_only_checks_on_the_demo(tmp_path):
    b = load_bundle(FIXTURE)
    report = run_preflight(MagicMock(), b, checks=[check_leg_span, check_time_coverage, check_timezone, check_language, check_bundle_warnings])
    codes = _codes(report.findings)
    assert ("timezone", WARNING) in codes  # the demo's zone was inferred and none was asserted
    assert ("leg_span", WARNING) not in codes and ("time_coverage", WARNING) not in codes
    assert report.exit_code == 0


def test_timezone_assertion(tmp_path):
    b = load_bundle(FIXTURE)
    name = b.manifest.timezone["name"]
    assert check_timezone_codes(b, name) == []
    assert check_timezone_codes(b, "Europe/Paris") == [("timezone", BLOCKING)]


def check_timezone_codes(b, tz):
    return _codes(run_preflight(MagicMock(), b, timezone=tz, checks=[check_timezone]).findings)


def test_leg_span_time_coverage_language_and_warnings(tmp_path):
    def edit(name, doc):
        if name == "patrols":
            doc["patrols"][0]["legs"][0]["placeholder_dates"] = True
        if name == "manifest":
            doc["time_coverage"]["date_only"] = 3
            doc["languages"] = [{"code": "en", "default": True, "labels": 400, "missing": 70}]
            doc["warnings"] = [f"attachment {i} is not in the extracted filestore" for i in range(30)]

    b = _edited(tmp_path, edit)
    report = run_preflight(MagicMock(), b, checks=[check_leg_span, check_time_coverage, check_language, check_bundle_warnings])
    by = {f.code: f for f in report.findings}
    assert by["leg_span"].level == WARNING and by["leg_span"].count == 1
    assert by["leg_span"].affected_patrols == [b.patrols[0]["uuid"]]
    assert by["time_coverage"].count == 3 and "whole day" in by["time_coverage"].message
    assert by["language"].count == 70 and "en" in by["language"].message
    assert by["bundle_warnings"].count == 30 and len(by["bundle_warnings"].examples) == MAX_EXAMPLES


def test_a_failing_check_does_not_hide_the_others():
    b = load_bundle(FIXTURE)

    def boom(ctx):
        raise RuntimeError("ER said 500")

    report = run_preflight(MagicMock(), b, checks=[boom, check_timezone])
    codes = _codes(report.findings)
    assert ("check_failed", BLOCKING) in codes and ("timezone", WARNING) in codes
    failed = next(f for f in report.findings if f.code == "check_failed")
    assert "boom" in failed.message and "ER said 500" in failed.message
    assert report.exit_code == 2


def test_examples_are_capped_but_affected_is_complete(tmp_path):
    def edit(name, doc):
        if name == "patrols":
            for p in doc["patrols"]:
                for leg in p["legs"]:
                    leg["placeholder_dates"] = True

    b = _edited(tmp_path, edit)
    report = run_preflight(MagicMock(), b, checks=[check_leg_span])
    (f,) = report.findings
    assert f.count == sum(len(p["legs"]) for p in b.patrols)
    assert sorted(f.affected_patrols) == sorted(p["uuid"] for p in b.patrols)
    assert report.blocked_patrols == set()  # a warning never blocks a patrol
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_preflight.py -q`
Expected: FAIL, `No module named 'er_smart_sync.preflight'`

- [ ] **Step 3: Implement**

`src/er_smart_sync/preflight.py`:

```python
"""`patrol-preflight`: a read-only check of an ER site against a smart-able
bundle, run before `import-patrols`. Each check returns findings; a blocking
finding names the patrols it affects so the importer can skip just those.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field

from .bundle import Bundle

logger = logging.getLogger(__name__)

BLOCKING, WARNING = "blocking", "warning"
MAX_EXAMPLES = 10


@dataclass
class Finding:
    code: str
    level: str
    message: str
    fix: str
    count: int = 0
    examples: list = field(default_factory=list)
    affected_patrols: list = field(default_factory=list)

    @classmethod
    def of(cls, code, level, message, fix, items=(), affected=()) -> Finding:
        items = [str(i) for i in items]
        return cls(code, level, message, fix, count=len(items), examples=items[:MAX_EXAMPLES],
                   affected_patrols=sorted(set(affected)))

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class PreflightContext:
    api: object
    bundle: Bundle
    timezone: str | None = None
    event_type_prefix: str | None = None
    event_type_version: str = "v2"
    cache: dict = field(default_factory=dict)

    def once(self, key: str, fn):
        """Site reads shared by several checks are made once per run."""
        if key not in self.cache:
            self.cache[key] = fn()
        return self.cache[key]


@dataclass
class PreflightReport:
    findings: list
    context: dict

    @property
    def blocking(self) -> list:
        return [f for f in self.findings if f.level == BLOCKING]

    @property
    def warnings(self) -> list:
        return [f for f in self.findings if f.level == WARNING]

    @property
    def exit_code(self) -> int:
        return 2 if self.blocking else 0

    @property
    def blocked_patrols(self) -> set:
        return {p for f in self.blocking for p in f.affected_patrols}


# ── bundle-only checks ───────────────────────────────────────────


def check_leg_span(ctx: PreflightContext) -> list:
    legs = [(p, leg) for p in ctx.bundle.patrols for leg in p["legs"] if leg.get("placeholder_dates")]
    if not legs:
        return []
    days = sum(len(leg.get("days") or []) for _, leg in legs)
    return [Finding.of(
        "leg_span", WARNING,
        f"{len(legs)} leg(s) start on SMART's 1970-01-01 placeholder or span more than a year "
        f"({days} leg days as stored)",
        "Nothing to do: import-patrols keeps only the leg days with a track or a waypoint. "
        "To correct the dates, fix the leg in SMART and re-export.",
        [f"{p['id']} leg {leg.get('id')}" for p, leg in legs],
        affected=[p["uuid"] for p, _ in legs],
    )]


def check_time_coverage(ctx: PreflightContext) -> list:
    tc = ctx.bundle.manifest.time_coverage or {}
    n = int(tc.get("date_only") or 0)
    if not n:
        return []
    return [Finding(
        "time_coverage", WARNING,
        f"{n} of {tc.get('leg_days')} leg days have no track, start/end time or timed waypoint; "
        "their segments will cover the whole day",
        "Nothing to do; the imported segment records time_source: date_only. "
        f"Coverage: track {tc.get('track')}, start/end {tc.get('leg_day_times')}, "
        f"waypoints {tc.get('waypoints')}, date only {n}.",
        count=n,
    )]


def check_timezone(ctx: PreflightContext) -> list:
    tz = ctx.bundle.manifest.timezone or {}
    name, source = tz.get("name"), tz.get("source")
    ca_id = ctx.bundle.manifest.conservation_area.get("id", "")
    if ctx.timezone and ctx.timezone != name:
        return [Finding(
            "timezone", BLOCKING,
            f"timezone {ctx.timezone!r} is configured but the bundle was written for {name!r} ({source})",
            f"Re-export the bundle with the right zone: SMART_ABLE_TIMEZONE={ctx.timezone} ./smart-able export {ca_id}",
        )]
    if not ctx.timezone and source == "inferred":
        return [Finding(
            "timezone", WARNING,
            f"the bundle's timezone {name} was inferred from waypoint positions",
            f"Confirm it by setting smart.timezone: {name} in sync.yaml (or --timezone {name}); "
            "if it is wrong, re-export with SMART_ABLE_TIMEZONE set.",
        )]
    return []


def check_language(ctx: PreflightContext) -> list:
    b = ctx.bundle
    lang = next((x for x in b.manifest.languages if x["code"] == b.language), None)
    if not lang or not lang.get("missing"):
        return []
    return [Finding(
        "language", WARNING,
        f"{lang['missing']} element(s) have no label in {b.language!r}; "
        f"they fall back to {b.manifest.default_language!r} or any language with one",
        "Pick the best-covered language in smart-able's Data Model tab and pass it as --smart-language.",
        count=int(lang["missing"]),
    )]


def check_bundle_warnings(ctx: PreflightContext) -> list:
    warnings = list(ctx.bundle.manifest.warnings or [])
    if not warnings:
        return []
    return [Finding.of(
        "bundle_warnings", WARNING,
        f"smart-able recorded {len(warnings)} warning(s) when it wrote the bundle",
        "Read them in the bundle's manifest.json; smart-able's quirks.html explains each kind.",
        warnings,
    )]


ALL_CHECKS: list = [check_leg_span, check_time_coverage, check_timezone, check_language, check_bundle_warnings]


def run_preflight(api, bundle: Bundle, *, timezone=None, event_type_prefix=None,
                  event_type_version="v2", checks=None) -> PreflightReport:
    ctx = PreflightContext(api=api, bundle=bundle, timezone=timezone,
                           event_type_prefix=event_type_prefix, event_type_version=event_type_version)
    findings: list = []
    for check in checks if checks is not None else ALL_CHECKS:
        try:
            findings.extend(check(ctx))
        except Exception as e:  # one check's failure must not hide the others
            logger.exception("preflight check %s failed", check.__name__)
            findings.append(Finding(
                "check_failed", BLOCKING, f"{check.__name__} could not run: {type(e).__name__}: {e}",
                "Fix the cause (often access or connectivity) and run patrol-preflight again.",
            ))
    findings.sort(key=lambda f: (f.level != BLOCKING,))
    ca = bundle.manifest.conservation_area
    return PreflightReport(findings=findings, context={
        "conservation_area": {"id": ca.get("id"), "name": ca.get("name"), "uuid": ca.get("uuid")},
        "patrols": len(bundle.patrols), "language": bundle.language,
        "timezone": bundle.manifest.timezone,
    })
```

Run `uv run ruff format src/er_smart_sync/preflight.py tests/test_preflight.py` after writing; the condensed literal layout above is reformatted then.

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_preflight.py -q`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add src/er_smart_sync/preflight.py tests/test_preflight.py
git commit -m "Preflight: findings, runner, bundle-only checks"
```

---

### Task 3: Site checks: feature flag, permissions, patrol types, universal fields, choices

**Files:**
- Modify: `src/er_smart_sync/preflight.py`, `tests/test_preflight.py`

**Interfaces:**
- Consumes: `ERPatrolAPI.patrol_schemas_enabled`, `get_me`, `get_patrol_type`, `get_segment_schema` (Task 1 and existing); `build_patrol_types`, `build_segment_schema`, `choice_value`, `CHOICE_FIELD_*`, `custom_choice_field` from `smart_to_er_patrol_schema`; `choices._fetch_existing(er_client=, field=)` for choice records.
- Produces: `check_feature_flag`, `check_permissions`, `check_patrol_types`, `check_segment_schema`, `check_choices`; `flag_on(ctx) -> bool` (memoised); `REQUIRED_PERMISSIONS = {"patrol": ("add", "change", "view"), "patrolconfiguration": ("view",), "eventrelatedsegments": ("add",), "event": ("add", "change")}`. Checks that need the new endpoints return `[]` when `flag_on(ctx)` is false, so only `feature_flag` reports. `ALL_CHECKS` becomes `[check_feature_flag, check_permissions, check_patrol_types, check_segment_schema, check_choices, *bundle-only checks]`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_preflight.py`:

```python
from er_smart_sync.preflight import (  # noqa: E402
    check_choices,
    check_feature_flag,
    check_patrol_types,
    check_permissions,
    check_segment_schema,
)
from er_smart_sync.smart_to_er_patrol_schema import build_patrol_types, build_segment_schema  # noqa: E402


class SiteFake:
    """The site as preflight reads it."""

    def __init__(self, bundle, *, flag=True, me=None, types=None, schema=None, choices=None, resolved=None,
                 subjects=None, groups=None, event_types=None):
        self.flag = flag
        self.me = me if me is not None else {"is_superuser": True, "permissions": {}}
        self.types = types if types is not None else {p.value: {"value": p.value, "is_active": True} for p in build_patrol_types(bundle)}
        self.schema = schema if schema is not None else build_segment_schema(bundle)
        self.choices = choices or {}
        self.resolved = resolved if resolved is not None else {"members": [], "assets": [], "teams": []}
        self.subjects = subjects or []
        self.groups = groups or {}
        self.event_types = event_types or []
        self.er = MagicMock()

    def patrol_schemas_enabled(self):
        return self.flag

    def get_me(self):
        return self.me

    def get_patrol_type(self, value):
        return self.types.get(value)

    def get_segment_schema(self):
        return self.schema if self.flag else None

    def get_patrol_config(self):
        return {"members": [{"id": g} for g in self.groups], "teams": self.resolved["teams"]} if self.flag else None

    def get_patrol_config_resolved(self):
        return self.resolved if self.flag else None

    def list_subjects(self):
        return list(self.subjects)

    def group_subject_ids(self, gid):
        return set(self.groups.get(gid, ()))

    def list_event_types(self, version="v2"):
        return list(self.event_types)

    def choice_records(self, field):
        return [{"value": v, "is_active": a} for v, a in self.choices.get(field, {}).items()]


def test_flag_off_reports_once():
    b = load_bundle(FIXTURE)
    report = run_preflight(SiteFake(b, flag=False), b, checks=[check_feature_flag, check_patrol_types, check_segment_schema, check_choices])
    assert _codes(report.findings) == [("feature_flag", BLOCKING)]
    assert "patrol_schemas" in report.findings[0].fix


def test_permissions():
    b = load_bundle(FIXTURE)
    assert run_preflight(SiteFake(b), b, checks=[check_permissions]).findings == []
    me = {"is_superuser": False, "permissions": {"patrol": ["view"], "event": ["add", "change"]}}
    (f,) = run_preflight(SiteFake(b, me=me), b, checks=[check_permissions]).findings
    assert f.code == "permissions" and f.level == BLOCKING
    assert "patrol: add" in f.examples and "eventrelatedsegments: add" in f.examples


def test_patrol_types_missing_and_inactive():
    b = load_bundle(FIXTURE)
    plans = build_patrol_types(b)
    types = {plans[0].value: {"value": plans[0].value, "is_active": False}}
    (f,) = run_preflight(SiteFake(b, types=types), b, checks=[check_patrol_types]).findings
    assert f.code == "patrol_types" and f.level == BLOCKING and f.count == len(plans)
    assert any("inactive" in e for e in f.examples) and any("missing" in e for e in f.examples)
    assert set(f.affected_patrols) == {p["uuid"] for p in b.patrols}  # every demo patrol uses one of them


def test_segment_schema_missing_and_deprecated_fields():
    b = load_bundle(FIXTURE)
    schema = build_segment_schema(b)
    del schema["json"]["properties"]["mandate"]
    schema["json"]["properties"]["armed"]["deprecated"] = True
    (f,) = run_preflight(SiteFake(b, schema=schema), b, checks=[check_segment_schema]).findings
    assert f.code == "segment_schema" and f.level == BLOCKING
    assert sorted(f.examples) == ["armed (deprecated)", "mandate (missing)"]


def test_choices_used_by_patrols(monkeypatch):
    b = load_bundle(FIXTURE)
    site = SiteFake(b, choices={"patrol_mandate": {}, "patrol_class": {"ground": True}})
    monkeypatch.setattr("er_smart_sync.preflight._choice_records", lambda ctx, field: site.choice_records(field))
    (f,) = run_preflight(site, b, checks=[check_choices]).findings
    assert f.code == "choices" and f.level == WARNING
    assert any(e.startswith("patrol_mandate:") for e in f.examples)
    assert not any(e.startswith("patrol_class:") for e in f.examples)
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_preflight.py -q`
Expected: FAIL on the import of `check_feature_flag`.

- [ ] **Step 3: Implement**

Append to `preflight.py` (and add the imports `from .choices import _fetch_existing` and `from .smart_to_er_patrol_schema import CHOICE_FIELD_CLASS, CHOICE_FIELD_MANDATE, CHOICE_FIELD_STATION, build_patrol_types, build_segment_schema, choice_value, custom_choice_field`):

```python
# ── site checks ─────────────────────────────────────────────────

REQUIRED_PERMISSIONS = {
    "patrol": ("add", "change", "view"),
    "patrolconfiguration": ("view",),
    "eventrelatedsegments": ("add",),
    "event": ("add", "change"),
}


def flag_on(ctx: PreflightContext) -> bool:
    return bool(ctx.once("flag", ctx.api.patrol_schemas_enabled))


def check_feature_flag(ctx: PreflightContext) -> list:
    if flag_on(ctx):
        return []
    return [Finding(
        "feature_flag", BLOCKING,
        "the patrol_schemas preview feature is off on this site, so it has no patrol legs, "
        "universal patrol fields, teams or rosters to import into",
        "Django admin > Preview features > patrol_schemas > enable it for this tenant "
        "(ask ER Core if the page is not available), then run patrol-datamodel and patrol-preflight again.",
        affected=[p["uuid"] for p in ctx.bundle.patrols],
    )]


def check_permissions(ctx: PreflightContext) -> list:
    me = ctx.once("me", ctx.api.get_me)
    if me.get("is_superuser"):
        return []
    have = me.get("permissions") or {}
    missing = [f"{model}: {action}" for model, actions in REQUIRED_PERMISSIONS.items()
               for action in actions if action not in (have.get(model) or [])]
    if not missing:
        return []
    return [Finding.of(
        "permissions", BLOCKING, f"the operator lacks {len(missing)} permission(s) the import needs",
        "Django admin > Users > the operator: grant each permission listed (app `activity`).",
        missing, affected=[p["uuid"] for p in ctx.bundle.patrols],
    )]


def check_patrol_types(ctx: PreflightContext) -> list:
    if not flag_on(ctx):
        return []
    by_transport = {p.transport_uuid: p for p in build_patrol_types(ctx.bundle)}
    problems, affected = {}, set()
    for p in ctx.bundle.patrols:
        for leg in p["legs"]:
            plan = by_transport.get(leg.get("transport_uuid"))
            if plan is None:
                continue
            if plan.value not in problems:
                existing = ctx.api.get_patrol_type(plan.value)
                problems[plan.value] = ("missing" if existing is None
                                        else "inactive" if not existing.get("is_active", True) else None)
            if problems[plan.value]:
                affected.add(p["uuid"])
    bad = [f"{value} ({why})" for value, why in sorted(problems.items()) if why]
    if not bad:
        return []
    return [Finding.of(
        "patrol_types", BLOCKING, f"{len(bad)} patrol type(s) the legs need are missing or inactive",
        "Run patrol-datamodel; activate an inactive type in EarthRanger admin > Patrol types.",
        bad, affected=affected,
    )]


def check_segment_schema(ctx: PreflightContext) -> list:
    if not flag_on(ctx):
        return []
    site = (ctx.api.get_segment_schema() or {}).get("json", {}).get("properties", {})
    ours = build_segment_schema(ctx.bundle)["json"]["properties"]
    bad = []
    for name in ours:
        if name not in site:
            bad.append(f"{name} (missing)")
        elif site[name].get("deprecated"):
            bad.append(f"{name} (deprecated)")
    if not bad:
        return []
    return [Finding.of(
        "segment_schema", BLOCKING, f"{len(bad)} universal patrol field(s) are missing or deprecated on the site",
        "Run patrol-datamodel; if an admin deprecated a field on purpose, restore it in the patrol form builder.",
        bad, affected=[p["uuid"] for p in ctx.bundle.patrols],
    )]


def _choice_records(ctx: PreflightContext, field: str) -> list:
    return ctx.once(f"choices:{field}", lambda: _fetch_existing(er_client=ctx.api.er, field=field))


def _used_choices(bundle: Bundle) -> dict:
    """field -> {value: set of patrol uuids} for every option a patrol uses."""
    ref = bundle.reference
    by_uuid = {x.uuid: x for x in ref.mandates + ref.stations if x.uuid}
    used: dict = {}

    def add(field, value, patrol):
        if value:
            used.setdefault(field, {}).setdefault(value, set()).add(patrol)

    attrs = {a.uuid: a for a in ref.patrol_attributes}
    for p in bundle.patrols:
        if p.get("station_uuid") in by_uuid:
            add(CHOICE_FIELD_STATION, choice_value(by_uuid[p["station_uuid"]]), p["uuid"])
        if p.get("patrol_type"):
            add(CHOICE_FIELD_CLASS, p["patrol_type"].lower(), p["uuid"])
        for leg in p["legs"]:
            if leg.get("mandate_uuid") in by_uuid:
                add(CHOICE_FIELD_MANDATE, choice_value(by_uuid[leg["mandate_uuid"]]), p["uuid"])
        for a in p.get("attributes") or []:
            attr = attrs.get(a.get("attribute_uuid"))
            if attr and attr.type.strip() in ("LIST", "MLIST", "TREE"):
                values = a["value"] if isinstance(a.get("value"), list) else [a.get("value")]
                for v in values:
                    opt = next((o for o in attr.options if o.key == v), None)
                    add(custom_choice_field(attr.key), choice_value(opt) if opt else None, p["uuid"])
    return used


def check_choices(ctx: PreflightContext) -> list:
    if not flag_on(ctx):
        return []
    bad, affected = [], set()
    for field_name, values in sorted(_used_choices(ctx.bundle).items()):
        records = {r["value"]: r for r in _choice_records(ctx, field_name)}
        for value, patrols in sorted(values.items()):
            r = records.get(value)
            if r is None or not r.get("is_active", True):
                bad.append(f"{field_name}: {value} ({'missing' if r is None else 'inactive'})")
                affected |= patrols
    if not bad:
        return []
    return [Finding.of(
        "choices", WARNING,
        f"{len(bad)} option(s) the patrols use are missing or inactive; those fields would be stored "
        "without a matching dropdown entry",
        "Run patrol-datamodel; re-activate an option an admin switched off in Django admin > Choices.",
        bad, affected=affected,
    )]
```

Set `ALL_CHECKS = [check_feature_flag, check_permissions, check_patrol_types, check_segment_schema, check_choices, check_leg_span, check_time_coverage, check_timezone, check_language, check_bundle_warnings]`.

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_preflight.py -q`
Expected: 11 passed

- [ ] **Step 5: Commit**

```bash
git add src/er_smart_sync/preflight.py tests/test_preflight.py
git commit -m "Preflight: feature flag, permissions, patrol types, universal fields, choices"
```

---

### Task 4: Subjects, roster, retired staff, teams

**Files:**
- Modify: `src/er_smart_sync/preflight.py`, `tests/test_preflight.py`

**Interfaces:**
- Consumes: `list_subjects`, `get_patrol_config_resolved`, `get_patrol_config`, `group_subject_ids`; `build_team_requirements`.
- Produces: `check_subjects` (blocking: a leg member with no subject matched by `additional.smart_member_id`, or an active one absent from the resolved roster); `check_inactive_members` (warning: matched subjects that are inactive; blocking subset when an inactive one is not in any configured member group either, since reactivating it would still leave it off the roster); `check_teams` (blocking: a referenced team missing from the resolved teams by display or value, or inactive). `subjects_by_member(ctx) -> dict[str, dict]` memoised. Added to `ALL_CHECKS` after `check_choices`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_preflight.py`:

```python
from er_smart_sync.preflight import check_inactive_members, check_subjects, check_teams  # noqa: E402


def _subject(sid, member, active=True):
    return {"id": sid, "name": sid, "is_active": active, "additional": {"smart_member_id": member}}


def test_inactive_and_off_roster_members():
    b = load_bundle(FIXTURE)
    members = sorted(b.member_uuids())
    subjects = [_subject(f"s{i}", m) for i, m in enumerate(members)]
    subjects[0]["is_active"] = False  # retired, still in the configured group
    subjects[1]["is_active"] = False  # retired and out of every group
    on_roster = [{"id": s["id"], "is_active": True} for s in subjects[3:]]  # s2 is active but off the roster
    site = SiteFake(b, subjects=subjects, resolved={"members": on_roster, "assets": [], "teams": []},
                    groups={"g1": {"s0"} | {s["id"] for s in subjects[3:]}})
    report = run_preflight(site, b, checks=[check_subjects, check_inactive_members])
    by = {(f.code, f.level): f for f in report.findings}
    assert by[("subjects", BLOCKING)].count == 1 and "s2" in by[("subjects", BLOCKING)].examples[0]
    assert by[("inactive_members", WARNING)].count == 2
    assert "reactivat" in by[("inactive_members", WARNING)].fix
    assert by[("inactive_members", BLOCKING)].count == 1 and "s1" in by[("inactive_members", BLOCKING)].examples[0]


def test_member_without_subject_is_blocking():
    b = load_bundle(FIXTURE)
    report = run_preflight(SiteFake(b, subjects=[]), b, checks=[check_subjects])
    (f,) = report.findings
    assert f.code == "subjects" and f.count == len(b.member_uuids())
    assert set(f.affected_patrols) == {p["uuid"] for p in b.patrols if any(leg["members"] for leg in p["legs"])}


def test_teams():
    b = load_bundle(FIXTURE)
    (req_uuid,) = b.patrol_team_uuids()
    name = f"SMART team {req_uuid[:8]}"
    ok = {"members": [], "assets": [], "teams": [{"value": "x", "display": name, "is_active": True}]}
    assert run_preflight(SiteFake(b, resolved=ok), b, checks=[check_teams]).findings == []
    (f,) = run_preflight(SiteFake(b), b, checks=[check_teams]).findings
    assert f.code == "teams" and f.level == BLOCKING and name in f.examples[0]
    assert set(f.affected_patrols) == {p["uuid"] for p in b.patrols if p.get("team_uuid")}
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_preflight.py -q`
Expected: FAIL on the import of `check_subjects`.

- [ ] **Step 3: Implement**

Append to `preflight.py` (import `build_team_requirements` too):

```python
def subjects_by_member(ctx: PreflightContext) -> dict:
    def build():
        out = {}
        for s in ctx.api.list_subjects():
            mid = (s.get("additional") or {}).get("smart_member_id")
            if mid:
                out[str(mid)] = s
        return out

    return ctx.once("subjects", build)


def _member_patrols(bundle: Bundle) -> dict:
    out: dict = {}
    for p in bundle.patrols:
        for leg in p["legs"]:
            for m in leg["members"]:
                out.setdefault(m["employee_uuid"], set()).add(p["uuid"])
    return out


def _resolved(ctx):
    return ctx.once("resolved", ctx.api.get_patrol_config_resolved) or {}


def check_subjects(ctx: PreflightContext) -> list:
    if not flag_on(ctx):
        return []
    subjects = subjects_by_member(ctx)
    on_roster = {m["id"] for m in _resolved(ctx).get("members") or []}
    bad, affected = [], set()
    for member, patrols in sorted(_member_patrols(ctx.bundle).items()):
        s = subjects.get(member)
        if s is None:
            bad.append(f"{member}: no subject with this smart_member_id")
        elif s.get("is_active", True) and s["id"] not in on_roster:
            bad.append(f"{s['id']} ({s.get('name')}): active but not on the patrol member roster")
        else:
            continue
        affected |= patrols
    if not bad:
        return []
    return [Finding.of(
        "subjects", BLOCKING, f"{len(bad)} patrol member(s) cannot be put on a segment",
        "Run patrol-datamodel (it creates the subjects and, with --roster-group, adds them to the roster); "
        "otherwise add them in Django admin > Subject groups to a group listed under Patrol configuration > members.",
        bad, affected=affected,
    )]


def check_inactive_members(ctx: PreflightContext) -> list:
    if not flag_on(ctx):
        return []
    subjects = subjects_by_member(ctx)
    configured = [g["id"] for g in (ctx.once("config", ctx.api.get_patrol_config) or {}).get("members") or []]
    in_groups = set().union(*[ctx.api.group_subject_ids(g) for g in configured]) if configured else set()
    retired, stranded, affected, stranded_affected = [], [], set(), set()
    for member, patrols in sorted(_member_patrols(ctx.bundle).items()):
        s = subjects.get(member)
        if s is None or s.get("is_active", True):
            continue
        label = f"{s['id']} ({s.get('name')})"
        retired.append(label)
        affected |= patrols
        if s["id"] not in in_groups:
            stranded.append(label)
            stranded_affected |= patrols
    out = []
    if retired:
        out.append(Finding.of(
            "inactive_members", WARNING,
            f"{len(retired)} patrol member(s) are retired (inactive subjects)",
            "Nothing to do: das accepts only active members on a segment, so import-patrols reactivates "
            "these subjects for the run and retires them again when it finishes (recorded in its ledger).",
            retired, affected=affected,
        ))
    if stranded:
        out.append(Finding.of(
            "inactive_members", BLOCKING,
            f"{len(stranded)} retired member(s) are in no configured member group, so reactivating them "
            "would still leave them off the roster",
            "Add them to a group listed under Patrol configuration > members (Django admin > Subject groups).",
            stranded, affected=stranded_affected,
        ))
    return out


def check_teams(ctx: PreflightContext) -> list:
    if not flag_on(ctx):
        return []
    teams = _resolved(ctx).get("teams") or []
    names = {}
    for t in teams:
        for k in ("display", "value"):
            if t.get(k):
                names[t[k].strip().lower()] = t
    team_patrols: dict = {}
    for p in ctx.bundle.patrols:
        if p.get("team_uuid"):
            team_patrols.setdefault(p["team_uuid"], set()).add(p["uuid"])
    bad, affected = [], set()
    for req in build_team_requirements(ctx.bundle):
        t = names.get(req.name.strip().lower())
        if t is None or not t.get("is_active", True):
            bad.append(f"{req.name} ({'missing' if t is None else 'inactive'}, {req.referenced_by_patrols} patrols)")
            affected |= team_patrols.get(req.uuid, set())
    if not bad:
        return []
    return [Finding.of(
        "teams", BLOCKING, f"{len(bad)} patrol team(s) are missing or inactive",
        "Django admin > Patrol configuration > Teams: add (or activate) a team whose display is the name shown; "
        "patrol-datamodel's checklist lists their SMART members.",
        bad, affected=affected,
    )]
```

Add `check_subjects, check_inactive_members, check_teams` to `ALL_CHECKS` after `check_choices`. In `SiteFake.get_patrol_config`, `members` lists the configured groups by id (already so).

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_preflight.py -q`
Expected: 14 passed

- [ ] **Step 5: Commit**

```bash
git add src/er_smart_sync/preflight.py tests/test_preflight.py
git commit -m "Preflight: subjects on the roster, retired members, teams"
```

---

### Task 5: Event types for every observed category

**Files:**
- Modify: `src/er_smart_sync/preflight.py`, `tests/test_preflight.py`

**Interfaces:**
- Produces: `category_path(hkey: str) -> str` (`"threats.charcoal."` → `"threats_charcoal"`, lower-case); `resolve_event_types(bundle, event_types: list[dict], prefix: str | None) -> tuple[dict[str, str], dict[str, list[str]], list[str]]` returning (hkey → chosen value, hkey → ambiguous candidates, unmatched hkeys); a value matches a category when it equals `<anything>_<path>`, and, when `prefix` is given, starts with `<prefix>_`; inactive event types do not count. `check_event_types` (blocking for unmatched, warning for ambiguous, with the candidate values as examples). `ctx.cache["event_types_map"]` holds the resolved map for `import-patrols` to reuse. Added to `ALL_CHECKS` after `check_teams`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_preflight.py`:

```python
from er_smart_sync.preflight import category_path, check_event_types, resolve_event_types  # noqa: E402


def _observed(b):
    return sorted({o["category_hkey"] for p in b.patrols for leg in p["legs"] for w in leg["waypoints"]
                   for g in w["observation_groups"] for o in g["observations"]})


def test_category_path():
    assert category_path("threats.charcoal.") == "threats_charcoal"
    assert category_path("Animals.LiveAnimals") == "animals_liveanimals"


def test_event_types_all_present_and_map_kept():
    b = load_bundle(FIXTURE)
    ets = [{"value": f"smart-ca-import_{category_path(h)}", "is_active": True} for h in _observed(b)]
    ctx_report = run_preflight(SiteFake(b, event_types=ets), b, checks=[check_event_types])
    assert ctx_report.findings == []


def test_missing_event_types_block_the_patrols_that_use_them():
    b = load_bundle(FIXTURE)
    observed = _observed(b)
    ets = [{"value": f"smart-ca-import_{category_path(h)}", "is_active": True} for h in observed[1:]]
    (f,) = run_preflight(SiteFake(b, event_types=ets), b, checks=[check_event_types]).findings
    assert f.code == "event_types" and f.level == BLOCKING and f.count == 1 and observed[0] in f.examples[0]
    assert f.affected_patrols and all(isinstance(p, str) for p in f.affected_patrols)


def test_ambiguous_event_types_need_a_prefix():
    b = load_bundle(FIXTURE)
    observed = _observed(b)
    ets = [{"value": f"smart-ca-import_{category_path(h)}", "is_active": True} for h in observed]
    ets.append({"value": f"0c7cbb41-1a60_{category_path(observed[0])}", "is_active": True})
    (f,) = run_preflight(SiteFake(b, event_types=ets), b, checks=[check_event_types]).findings
    assert f.code == "event_types" and f.level == WARNING and "--event-type-prefix" in f.fix
    assert run_preflight(SiteFake(b, event_types=ets), b, event_type_prefix="smart-ca-import",
                         checks=[check_event_types]).findings == []
    chosen, ambiguous, unmatched = resolve_event_types(b, ets, "smart-ca-import")
    assert chosen[observed[0]] == f"smart-ca-import_{category_path(observed[0])}" and not ambiguous and not unmatched


def test_inactive_event_types_do_not_count():
    b = load_bundle(FIXTURE)
    observed = _observed(b)
    ets = [{"value": f"smart-ca-import_{category_path(h)}", "is_active": h != observed[0]} for h in observed]
    (f,) = run_preflight(SiteFake(b, event_types=ets), b, checks=[check_event_types]).findings
    assert f.level == BLOCKING and f.count == 1
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_preflight.py -q`
Expected: FAIL on the import of `category_path`.

- [ ] **Step 3: Implement**

Append to `preflight.py`:

```python
def category_path(hkey: str) -> str:
    """The part of an event-type value that names the SMART category: the
    hkey without its trailing dot, dots as underscores, lower-case (the rule
    in choices.event_type_value_for)."""
    return hkey.strip(".").replace(".", "_").lower()


def _observed_categories(bundle: Bundle) -> dict:
    out: dict = {}
    for p in bundle.patrols:
        for leg in p["legs"]:
            for w in leg["waypoints"]:
                for g in w["observation_groups"]:
                    for o in g["observations"]:
                        if o.get("category_hkey"):
                            out.setdefault(o["category_hkey"], set()).add(p["uuid"])
    return out


def resolve_event_types(bundle: Bundle, event_types: list, prefix: str | None):
    active = [e["value"] for e in event_types if e.get("value") and e.get("is_active", True)]
    chosen, ambiguous, unmatched = {}, {}, []
    for hkey in sorted(_observed_categories(bundle)):
        suffix = "_" + category_path(hkey)
        candidates = [v for v in active if v.lower().endswith(suffix)]
        if prefix:
            candidates = [v for v in candidates if v.lower().startswith(prefix.lower() + "_")]
        if len(candidates) == 1:
            chosen[hkey] = candidates[0]
        elif candidates:
            ambiguous[hkey] = sorted(candidates)
        else:
            unmatched.append(hkey)
    return chosen, ambiguous, unmatched


def check_event_types(ctx: PreflightContext) -> list:
    ets = ctx.once("event_types", lambda: ctx.api.list_event_types(ctx.event_type_version))
    chosen, ambiguous, unmatched = resolve_event_types(ctx.bundle, ets, ctx.event_type_prefix)
    ctx.cache["event_types_map"] = chosen
    used = _observed_categories(ctx.bundle)
    out = []
    if unmatched:
        out.append(Finding.of(
            "event_types", BLOCKING,
            f"{len(unmatched)} observed SMART categor(ies) have no active event type on the site",
            "Push the data model first: er-smart-sync datamodel --from-file <ca>/datamodel.xml "
            "[--cm-from-file <ca>/configurable_model_<uuid>.xml] --ca-identifier <ID>. "
            "With --event-type-prefix set, check the prefix is the one that push used.",
            unmatched, affected=set().union(*[used[h] for h in unmatched]),
        ))
    if ambiguous:
        out.append(Finding.of(
            "event_types", WARNING,
            f"{len(ambiguous)} categor(ies) match more than one event type (several data model pushes on this site)",
            "Pass --event-type-prefix with the prefix of the push to import against: smart-ca-import for "
            "--from-file, smart-ca-import_<cm uuid> with a configurable model, or the SMART CA uuid for Connect.",
            [f"{h}: {', '.join(c)}" for h, c in sorted(ambiguous.items())],
            affected=set().union(*[used[h] for h in ambiguous]),
        ))
    return out
```

Add `check_event_types` to `ALL_CHECKS` after `check_teams`. `check_event_types` does not depend on the flag: event types exist with or without it.

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_preflight.py -q`
Expected: 19 passed

- [ ] **Step 5: Commit**

```bash
git add src/er_smart_sync/preflight.py tests/test_preflight.py
git commit -m "Preflight: event types for every observed category, with ambiguity handling"
```

---

### Task 6: Renderers, CLI command, read-only guarantee, docs

**Files:**
- Modify: `src/er_smart_sync/preflight.py`, `src/er_smart_sync/cli.py`, `tests/test_preflight.py`, `tests/test_cli.py`, docs listed in the file structure
- Create: `docs/cli-reference/patrol-preflight.md`

**Interfaces:**
- Produces: `render_text(report) -> str` (header with CA and counts, then BLOCKING then WARNING sections, each finding as `- [code] message (count)` plus indented `fix:` and up to ten `e.g.` lines, then `All clear: no blocking findings.` or `N blocking finding(s): import-patrols will refuse to run (or skip the M affected patrols with --allow-blocking).`); `render_json(report) -> str` (`{"context", "exit_code", "blocking": [...], "warnings": [...]}` with full `affected_patrols`); CLI `er-smart-sync patrol-preflight [--config] [--bundle] [er options] [--smart-language] [--timezone] [--event-type-prefix] [--event-type-version v1|v2] [--json]`, exit per the report or 1 on `BundleError`/connection failure.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_preflight.py`:

```python
from er_smart_sync.preflight import ALL_CHECKS, render_json, render_text  # noqa: E402


def test_render_text_and_json():
    b = load_bundle(FIXTURE)
    report = run_preflight(SiteFake(b, flag=False), b, checks=[check_feature_flag, check_timezone])
    text = render_text(report)
    assert text.splitlines()[0].startswith("Patrol preflight for Example Conservation Area (SMART)")
    assert "BLOCKING" in text and "[feature_flag]" in text and "fix:" in text and "WARNING" in text
    assert "import-patrols will refuse to run" in text
    doc = json.loads(render_json(report))
    assert doc["exit_code"] == 2 and doc["blocking"][0]["code"] == "feature_flag"
    assert len(doc["blocking"][0]["affected_patrols"]) == len(b.patrols)


def test_all_checks_are_read_only():
    """Every check against a MagicMock client: no write method is ever called."""
    from er_smart_sync.er_patrol_api import ERPatrolAPI

    b = load_bundle(FIXTURE)
    client = MagicMock()
    client._get.return_value = {"preview_features": {"patrol_schemas": True}, "is_superuser": True}
    client.get_subjects.return_value = []
    client.get_event_types.return_value = []
    run_preflight(ERPatrolAPI(client), b, checks=ALL_CHECKS)
    written = [c for c in client.method_calls if c[0].startswith(("post_", "patch_", "_post", "_patch", "_put", "delete_", "add_"))]
    assert written == []
```

Append to `tests/test_cli.py`:

```python
def test_patrol_preflight_exit_codes_and_json(tmp_path):
    from er_smart_sync.preflight import BLOCKING, Finding, PreflightReport

    def report(findings):
        return PreflightReport(findings=findings, context={"conservation_area": {"id": "SMART", "name": "Demo"}, "patrols": 5})

    args = ["patrol-preflight", "--bundle", str(_pd_fixture()), "--er-endpoint", "x.pamdas.org", "--er-token", "t"]
    with patch("er_smart_sync.cli._make_er_client", return_value=MagicMock()), \
         patch("er_smart_sync.cli.run_preflight", return_value=report([])) as run:
        ok = CliRunner().invoke(main, args + ["--event-type-prefix", "smart-ca-import"])
    assert ok.exit_code == 0, ok.output
    assert "All clear" in ok.output
    assert run.call_args.kwargs["event_type_prefix"] == "smart-ca-import"
    blocked = report([Finding("teams", BLOCKING, "m", "f", count=1, affected_patrols=["p"])])
    with patch("er_smart_sync.cli._make_er_client", return_value=MagicMock()), \
         patch("er_smart_sync.cli.run_preflight", return_value=blocked):
        r = CliRunner().invoke(main, args + ["--json"])
    assert r.exit_code == 2 and json.loads(r.output)["blocking"][0]["code"] == "teams"


def test_patrol_preflight_bad_bundle(tmp_path):
    with patch("er_smart_sync.cli._make_er_client", return_value=MagicMock()):
        r = CliRunner().invoke(main, ["patrol-preflight", "--bundle", str(tmp_path), "--er-endpoint", "x", "--er-token", "t"])
    assert r.exit_code == 1 and "manifest.json" in r.output
```

(`json` is imported at the top of `tests/test_cli.py`; add `import json` there if it is not.)

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_preflight.py tests/test_cli.py -k "render or read_only or preflight" -q`
Expected: FAIL on `render_text` import and "No such command 'patrol-preflight'".

- [ ] **Step 3: Implement the renderers**

Append to `preflight.py`:

```python
def _section(title: str, findings: list) -> list:
    if not findings:
        return []
    out = [f"{title} ({len(findings)})"]
    for f in findings:
        out.append(f"- [{f.code}] {f.message}" + (f" ({f.count})" if f.count else ""))
        out.append(f"    fix: {f.fix}")
        out += [f"    e.g. {e}" for e in f.examples]
        if f.count > len(f.examples) and f.examples:
            out.append(f"    … and {f.count - len(f.examples)} more (see --json)")
    out.append("")
    return out


def render_text(report: PreflightReport) -> str:
    ca = report.context.get("conservation_area") or {}
    lines = [f"Patrol preflight for {ca.get('name')} ({ca.get('id')}): {report.context.get('patrols')} patrols, "
             f"{len(report.blocking)} blocking, {len(report.warnings)} warning(s)", ""]
    lines += _section("BLOCKING", report.blocking)
    lines += _section("WARNING", report.warnings)
    if report.blocking:
        lines.append(f"{len(report.blocking)} blocking finding(s): import-patrols will refuse to run "
                     f"(or skip the {len(report.blocked_patrols)} affected patrols with --allow-blocking).")
    else:
        lines.append("All clear: no blocking findings.")
    return "\n".join(lines)


def render_json(report: PreflightReport) -> str:
    import json

    return json.dumps({
        "context": report.context, "exit_code": report.exit_code,
        "blocking": [f.to_dict() for f in report.blocking],
        "warnings": [f.to_dict() for f in report.warnings],
    }, indent=1, ensure_ascii=False)
```

- [ ] **Step 4: Implement the command**

In `cli.py` import `from .preflight import render_json, render_text, run_preflight` and add after `patrol_datamodel_cmd`:

```python
@main.command("patrol-preflight")
@click.option("--config", "config_file", type=click.Path(exists=True), help="YAML config file (as for patrol-datamodel)")
@click.option("--bundle", "bundle_dir", type=click.Path(file_okay=False), help="smart-able migration bundle directory")
@er_options
@click.option("--smart-language", default=None, help="Language the import will use for labels")
@click.option("--timezone", default=None, help="Assert the bundle's timezone (IANA name)")
@click.option("--event-type-prefix", default=None,
              help="Prefix of the data model push to import against (smart-ca-import, smart-ca-import_<cm uuid>, or the CA uuid)")
@click.option("--event-type-version", type=click.Choice(["v1", "v2"]), default=None,
              help="Event type API version (default: earthranger.event_type_version, v2)")
@click.option("--json", "as_json", is_flag=True, help="Print the report as JSON")
@click.pass_context
def patrol_preflight_cmd(ctx, config_file, bundle_dir, er_endpoint, er_token, er_username, er_password, er_id,
                         smart_language, timezone, event_type_prefix, event_type_version, as_json):
    """Read-only check that an ER site is ready for import-patrols.

    Exit status: 0 no blocking findings, 2 blocking findings, 1 error.
    """
    from pathlib import Path

    bundle_path, er, language, tz = _resolve_bundle_options(
        config_file, bundle_dir, er_endpoint, er_token, er_username, er_password, er_id, smart_language, timezone
    )
    try:
        bundle = load_bundle(Path(bundle_path), language=language)
    except BundleError as e:
        raise click.ClickException(str(e)) from None
    er_client = _make_er_client(endpoint=er.endpoint, token=er.token, username=er.login, password=er.password)
    report = run_preflight(ERPatrolAPI(er_client), bundle, timezone=tz, event_type_prefix=event_type_prefix,
                           event_type_version=event_type_version or er.event_type_version)
    click.echo(render_json(report) if as_json else render_text(report))
    ctx.exit(report.exit_code)
```

A connection failure during a check is turned into a `check_failed` blocking finding by the runner, so the command still prints a full report and exits 2; a failure creating the client (bad endpoint, bad credentials) raises before the report and Click exits 1.

- [ ] **Step 5: Run tests, lint**

Run: `uv run pytest -q && uv run ruff check src/er_smart_sync/preflight.py tests/test_preflight.py && uv run ruff format --check src/er_smart_sync/preflight.py tests/test_preflight.py`
Expected: all pass; on the modified existing files, only lines this plan added are checked (`git blame` as before).

- [ ] **Step 6: Docs**

`docs/cli-reference/patrol-preflight.md`: purpose, usage (`er-smart-sync patrol-preflight --config sync.yaml`, `--json`), the check table from the spec with the level, what each checks and the fix, the exit codes, and two paragraphs: "Retired staff" (das accepts only active members, so `import-patrols` reactivates retired subjects for the run; a retired subject outside every member group is blocking), "Event types" (the value rule and when `--event-type-prefix` is needed, with the three prefixes). `docs/workflows/migrate-patrols.md`: replace step 6 with "Run `er-smart-sync patrol-preflight --config sync.yaml` until it reports no blocking findings." and add "import-patrols follows in a later release." `mkdocs.yml`: `- patrol-preflight: cli-reference/patrol-preflight.md` after `patrol-datamodel`. `docs/changelog.md`: an Unreleased "Added" entry for `patrol-preflight` (ERA-14138). `USAGE.md` and `CLAUDE.md`: the new subcommand next to `patrol-datamodel`. The spec: in the preflight table, `feature_flag` reads `GET status` `preview_features.patrol_schemas`; `permissions` checks `user/me` for the models named in Global Constraints (no dry-run endpoint exists); `subjects` uses `patrols/config/default/resolved`; `inactive_members` adds the blocking case; `check_failed` is added; and in "Verify-first items", item 6 is marked settled with the das reference.

Run: `uv run mkdocs build --strict 2>&1 | tail -1`
Expected: build succeeds.

- [ ] **Step 7: Commit**

```bash
git add src/er_smart_sync/preflight.py src/er_smart_sync/cli.py tests docs mkdocs.yml USAGE.md CLAUDE.md
git commit -m "patrol-preflight command, report renderers, docs"
```

---

### Task 7: Live check against the gundi-dev sandbox (read-only)

**Files:**
- Modify: `docs/changelog.md`, `docs/cli-reference/patrol-preflight.md` (record what the sandbox showed)

The sandbox has `patrol_schemas` on, the demo CA's patrol types, universal fields, choices and subjects from `patrol-datamodel`, a team `SMART team 3eef8a91`, and a member roster group with two of the seven demo rangers. It has no event types for the demo CA's categories. The session is earthranger-cli's (`er --profile gundi-dev auth status` refreshes it; the token is `access_token` in `~/.config/er-events/tokens/gundi-dev.json`, never printed). This step makes no writes, so it needs no approval.

- [ ] **Step 1: Run it**

```bash
TOKEN=$(python3 -c "import json; print(json.load(open('$HOME/.config/er-events/tokens/gundi-dev.json'))['access_token'])")
uv run er-smart-sync patrol-preflight --bundle tests/fixtures/smart_demo_bundle --er-endpoint gundi-dev.staging.pamdas.org --er-token "$TOKEN"; echo "exit=$?"
uv run er-smart-sync patrol-preflight --bundle tests/fixtures/smart_demo_bundle --er-endpoint gundi-dev.staging.pamdas.org --er-token "$TOKEN" --json | python3 -c "import json,sys; d=json.load(sys.stdin); print(d['exit_code'], [f['code'] for f in d['blocking']], [f['code'] for f in d['warnings']])"
```

Expected: exit 2; blocking `event_types` (no data model pushed for the demo CA) and `subjects` (members off the two-person roster); warning `timezone`; no `feature_flag`, `patrol_types`, `segment_schema` or `teams` finding. Any other finding is either a real site state (write it down) or a bug (fix it test-first).

- [ ] **Step 2: Record and commit**

Add to the changelog entry and the CLI reference what the sandbox run reported and the date.

```bash
git add docs
git commit -m "Docs: patrol-preflight verified against the gundi-dev sandbox"
```

---

## Self-review notes

- Spec coverage: every row of the spec's preflight table has a check (`feature_flag`, `permissions`, `patrol_types`, `segment_schema`, `choices`, `subjects`, `inactive_members`, `teams`, `event_types`, `leg_span`, `time_coverage`, `timezone`, `language`, `bundle_warnings`), plus `check_failed`. Output in text and `--json`; exit codes 0/2/1. The `--allow-blocking` behaviour belongs to `import-patrols`; this plan gives it `PreflightReport.blocked_patrols` and per-finding `affected_patrols`, and leaves the resolved event-type map in the context cache.
- Deviations from the spec, deliberate: the flag is read from `status` (direct) rather than inferred from a 404; permissions come from `user/me` because das has no dry-run patrol POST; the roster check uses das's own resolved roster, which applies das's eligibility rule exactly (active, person-type, nested groups); `inactive_members` gains a blocking case for retired members outside every group; timezone mismatch is a blocking finding here rather than a hard error, so the operator sees it alongside everything else.
- Type consistency: `run_preflight(api, bundle, *, timezone, event_type_prefix, event_type_version, checks)` is called that way by the CLI; `SiteFake` implements every `ERPatrolAPI` method the checks call (`patrol_schemas_enabled`, `get_me`, `get_patrol_type`, `get_segment_schema`, `get_patrol_config`, `get_patrol_config_resolved`, `list_subjects`, `group_subject_ids`, `list_event_types`) and exposes `.er` for `_choice_records`, which tests patch.
- Review Focus tests: `test_flag_off_reports_once` (1), `test_a_failing_check_does_not_hide_the_others` (2), `test_ambiguous_event_types_need_a_prefix` (3), `test_inactive_and_off_roster_members` (4), `test_examples_are_capped_but_affected_is_complete` (5).
