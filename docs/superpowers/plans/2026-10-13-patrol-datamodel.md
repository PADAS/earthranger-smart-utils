# patrol-datamodel Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `er-smart-sync patrol-datamodel --bundle DIR`, which reads a smart-able migration bundle and creates or updates, idempotently, the ER patrol types, the universal segment schema and its choices, and the ranger subjects a SMART conservation area needs, then prints the admin checklist for what it could not do (ERA-14137).

**Architecture:** A `bundle` package loads and validates the four JSON files against the vendored schema and exposes label lookup in the chosen language. Pure builders in `smart_to_er_patrol_schema.py` turn the bundle's reference data into plans (patrol types, choice sets, segment schema, subjects, team requirements) with no I/O. `er_patrol_api.py` is the only module that talks to ER, wrapping `ERClient` for the endpoints erclient lacks and honouring `DryRunERClient`. `patrol_datamodel.py` orchestrates the five steps against that API, collects stats and checklist items, and the CLI command in `cli.py` wires config, language and timezone checks around it.

**Tech Stack:** Python 3.10+, Click, pydantic v1 (`<2.0`, existing convention), erclient `ERClient` (`_get`/`_post`/`_patch`, `get_subjects`, `post_subject`, `add_subjects_to_subjectgroup`), `requests` for the one PUT erclient lacks, `jsonschema` (new dependency), pytest with `MagicMock` clients (existing convention in `tests/test_choices.py`).

**Spec:** `docs/superpowers/specs/2026-10-12-smart-patrol-migration-design.md`, sections "The bundle contract", "Architecture", "patrol-datamodel (ERA-14137)", "Historical members", "Language", "Timezone". The other two subcommands get their own plans later.

## Global Constraints

- Pydantic v1 API only (`parse_obj_as`, `.dict()`, validators); no v2 patterns.
- Python `>=3.10`; `ruff check src tests` and `ruff format` clean (line length per `pyproject.toml`).
- The bundle contract is `src/er_smart_sync/bundle/schema.json`, a vendored copy of er-smart-able `export/bundle.schema.json`; `bundle_schema_version` 1 is the only supported version and the loader refuses others.
- Every display string written to ER comes from `labels[language]`, falling back to the bundle's `default_language`, then any language present.
- Identity: subjects match on `additional.smart_member_id` (the employee UUID, hyphenated), then `additional.smart_employee_id`; patrol types on `value`; choices on `(field, value)`.
- Patrol type value is `patrol_` + `sanitize_choice_value(transport key)`; choice fields are `patrol_mandate`, `patrol_station`, `patrol_class` and `patrol_<sanitized attribute key>`; choices are upserted through the existing `choices.upsert_choices` (model `activity.event`).
- The universal segment schema is the das envelope `{"json": {...}, "ui": {...}}` exactly as `smart_to_er_v2._build_one` emits it, PUT to `activity/patrols/segments/types/default/` (PUT only; the endpoint has no PATCH and is 404 while the `patrol_schemas` preview feature is off).
- Nothing is written to ER under `--dry-run`; `DryRunERClient` intercepts `post_*`, `patch_*`, `_post`, `_patch`, `_put`; the raw PUT must go through a method named `_put` on the API layer so the wrapper's prefix rule catches it.
- Verified against das `develop` (2026-10-08) and gundi-dev.staging on 2026-10-13: `GET /api/v1.0/status/` returns `preview_features.patrol_schemas`; `activity/patrols/types/` is a full CRUD viewset keyed by `value` (fields `value`, `display`, `icon`, `default_priority`, `is_active`, `schema`; `ordernum` read-only); `subject/{id}/` accepts PATCH of `name`, `subject_subtype`, `additional`, `is_active`; `subjectgroup/{id}/subjects/` adds members; segment `members` must be active subjects of a `person` subtype in a configured member group, so retired staff are created inactive and the import spec handles reactivation.

## Review Focus

1. A bundle whose default language has no label for a transport or mandate: the display must fall back to another language, never crash or write an empty display. Test added to Task 1 (`test_label_fallback_order`).
2. An ER site where `patrol_schemas` is off (gundi-dev today): patrol types, choices and subjects must still be written, the segment schema step must be skipped with a checklist item, and the exit status must be 0. Test in Task 5 (`test_flag_off_skips_schema_with_checklist`).
3. Two employees with the same name, or an employee with an empty name: subjects are keyed by UUID, so both must be created, and an empty name must fall back to the employee id rather than an empty string that ER rejects. Test in Task 2 (`test_subject_names_and_duplicates`).
4. A second run after an admin edited the universal schema by hand: the admin's extra field must survive the merge and the PUT must be skipped when nothing changed. Test in Task 3 (`test_merge_keeps_admin_fields_and_detects_no_change`).
5. A transport key that sanitises to the same value as another (`Others` twice in Lawin: `others`, `others1`) or to a value longer than ER's 50-character limit: values must stay unique and within the limit. Test in Task 2 (`test_patrol_type_values_unique_and_short`).

---

## File structure

| Path | Responsibility |
| --- | --- |
| `pyproject.toml` | add `jsonschema>=4.23`; package data for `bundle/schema.json` |
| `src/er_smart_sync/bundle/__init__.py` | `load_bundle`, dataclasses for manifest and reference, patrols as dicts with helpers, `Labels` fallback, `BundleError` |
| `src/er_smart_sync/bundle/schema.json` | vendored contract |
| `src/er_smart_sync/smart_to_er_patrol_schema.py` | pure builders: patrol types, choice sets, segment schema + merge, subjects, team requirements |
| `src/er_smart_sync/er_patrol_api.py` | `ERPatrolAPI`: status, patrol types, segment schema, patrol config, subjects, groups |
| `src/er_smart_sync/patrol_datamodel.py` | `run_patrol_datamodel`, `PatrolDatamodelStats`, `ChecklistItem`, `render_checklist` |
| `src/er_smart_sync/config.py` | `BundleConfig`, `PatrolToolConfig` + loader |
| `src/er_smart_sync/cli.py` | `patrol-datamodel` command |
| `tests/fixtures/smart_demo_bundle/*.json` | SMART's demo CA exported by smart-able (5 patrols, 7 demo employees, no real people) |
| `tests/test_bundle.py`, `tests/test_patrol_schema.py`, `tests/test_er_patrol_api.py`, `tests/test_patrol_datamodel.py`, `tests/test_cli.py` | one test file per module |
| `docs/cli-reference/patrol-datamodel.md`, `docs/workflows/migrate-patrols.md`, `mkdocs.yml`, `docs/changelog.md`, `USAGE.md`, `CLAUDE.md` | documentation |

---

### Task 1: Bundle loader, vendored schema, fixture

**Files:**
- Create: `src/er_smart_sync/bundle/__init__.py`, `src/er_smart_sync/bundle/schema.json`, `tests/fixtures/smart_demo_bundle/{manifest,reference,patrols,tracks}.json`, `tests/test_bundle.py`
- Modify: `pyproject.toml`

**Interfaces:**
- Produces: `load_bundle(path: Path, *, language: str | None = None) -> Bundle`; `BundleError(Exception)`; dataclasses `Manifest(bundle_schema_version, generated_at, source: dict, conservation_area: dict, timezone: dict, default_language: str, languages: list[dict], counts: dict, time_coverage: dict, warnings: list[str])`, `Labelled(uuid, key, labels: dict, label: str | None, active: bool, extra: dict)` used for transports, mandates, stations, patrol attribute options, ranks, teams; `PatrolAttribute(uuid, key, labels, label, type, active, options: list[Labelled])`; `Employee(uuid, id, given_name, family_name, agency_uuid, rank_uuid, employment_start, employment_end, active)`; `Reference(patrol_types: list[dict], transports, mandates, stations, patrol_attributes, agencies: list[dict], ranks, teams, employees)`; `Bundle(path, manifest, reference, patrols: list[dict], tracks: list[dict], language: str)` with `Bundle.label(obj) -> str` (fallback order from the spec), `Bundle.ca_uuid`, `Bundle.member_uuids() -> set[str]` (employees with a leg membership), `Bundle.leg_transport_uuids() -> set[str]`, `Bundle.patrol_team_uuids() -> set[str]`.
- `language=None` means the manifest's default; an unknown code raises `BundleError` listing `manifest.languages` with coverage.

- [ ] **Step 1: Vendor the schema, copy the fixture, add the dependency**

```bash
mkdir -p src/er_smart_sync/bundle tests/fixtures/smart_demo_bundle
cp ~/padas/er-smart-able/export/bundle.schema.json src/er_smart_sync/bundle/schema.json
for f in manifest reference patrols tracks; do cp ~/padas/er-smart-able/work/export/SMART/bundle/$f.json tests/fixtures/smart_demo_bundle/; done
```

The fixture is SMART's own demo conservation area ("Example Conservation Area", Gabon) as exported by smart-able `v0.2.0`: 5 patrols, 6 legs, 7 demo employees, 2 transports (foot, vehicle), 4 mandates, 1 station, no teams, no custom patrol attributes. Attachments are not copied; the loader never opens them.

In `pyproject.toml` add `"jsonschema>=4.23",` to `[project] dependencies` and, under `[tool.setuptools]`, package data so the schema ships:

```toml
[tool.setuptools.package-data]
er_smart_sync = ["bundle/schema.json"]
```

Run: `uv pip install -e ".[dev]"` and `uv run python -c "import jsonschema"`.
Expected: no output.

- [ ] **Step 2: Write the failing tests**

`tests/test_bundle.py`:

```python
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from er_smart_sync.bundle import Bundle, BundleError, load_bundle

FIXTURE = Path(__file__).parent / "fixtures" / "smart_demo_bundle"


def test_loads_demo_bundle():
    b = load_bundle(FIXTURE)
    assert isinstance(b, Bundle)
    assert b.manifest.bundle_schema_version == 1
    assert b.manifest.conservation_area["id"] == "SMART"
    assert b.ca_uuid == b.manifest.conservation_area["uuid"]
    assert b.language == "en" and b.manifest.default_language == "en"
    assert [t.key for t in b.reference.transports] == ["foot", "vehicle"]
    assert b.reference.transports[0].extra["patrol_type"] == "GROUND"
    assert len(b.reference.employees) == 7 and b.reference.employees[0].uuid.count("-") == 4
    assert len(b.patrols) == 5 and len(b.tracks) == 11
    assert b.member_uuids() <= {e.uuid for e in b.reference.employees}
    assert len(b.member_uuids()) == 7
    assert b.leg_transport_uuids() == {b.reference.transports[0].uuid}   # every demo leg is on foot
    assert b.patrol_team_uuids() == set()


def test_label_fallback_order(tmp_path):
    shutil.copytree(FIXTURE, tmp_path / "b")
    ref = json.loads((tmp_path / "b" / "reference.json").read_text())
    ref["transports"][0]["labels"] = {"fr": "À pied"}          # no en, no default
    ref["transports"][0]["label"] = None
    ref["transports"][1]["labels"] = {"en": "Vehicle", "fr": "Véhicule"}
    (tmp_path / "b" / "reference.json").write_text(json.dumps(ref))
    m = json.loads((tmp_path / "b" / "manifest.json").read_text())
    m["languages"].append({"code": "fr", "default": False, "labels": 2, "missing": 468})
    (tmp_path / "b" / "manifest.json").write_text(json.dumps(m))
    b = load_bundle(tmp_path / "b", language="fr")
    assert b.label(b.reference.transports[1]) == "Véhicule"
    b_en = load_bundle(tmp_path / "b", language="en")
    assert b_en.label(b_en.reference.transports[1]) == "Vehicle"
    assert b_en.label(b_en.reference.transports[0]) == "À pied"          # only language present wins over nothing
    assert b_en.label(b_en.reference.mandates[0]) == b_en.reference.mandates[0].labels["en"]


def test_unknown_language_lists_available():
    with pytest.raises(BundleError) as e:
        load_bundle(FIXTURE, language="xx")
    assert "xx" in str(e.value) and "en" in str(e.value) and "missing" in str(e.value)


def test_schema_violation_and_version(tmp_path):
    shutil.copytree(FIXTURE, tmp_path / "b")
    m = json.loads((tmp_path / "b" / "manifest.json").read_text())
    m["bundle_schema_version"] = 2
    (tmp_path / "b" / "manifest.json").write_text(json.dumps(m))
    with pytest.raises(BundleError, match="bundle_schema_version 2"):
        load_bundle(tmp_path / "b")
    m["bundle_schema_version"] = 1
    del m["timezone"]
    (tmp_path / "b" / "manifest.json").write_text(json.dumps(m))
    with pytest.raises(BundleError, match="manifest.json"):
        load_bundle(tmp_path / "b")
    (tmp_path / "b" / "tracks.json").unlink()
    with pytest.raises(BundleError, match="tracks.json"):
        load_bundle(tmp_path / "b")
```

- [ ] **Step 3: Run to verify failure**

Run: `uv run pytest tests/test_bundle.py -v`
Expected: FAIL, `No module named 'er_smart_sync.bundle'`

- [ ] **Step 4: Implement**

`src/er_smart_sync/bundle/__init__.py`:

```python
"""The smart-able migration bundle: four JSON files produced by
`smart-able export` (manifest, reference, patrols, tracks). This module
loads them, validates them against the vendored schema, and resolves labels
in the chosen language. It knows nothing about EarthRanger.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import jsonschema

SUPPORTED_SCHEMA_VERSION = 1
SCHEMA_PATH = Path(__file__).with_name("schema.json")
FILES = ("manifest", "reference", "patrols", "tracks")


class BundleError(Exception):
    """The bundle is missing, malformed, or asks for something unsupported."""


@dataclass
class Manifest:
    bundle_schema_version: int
    generated_at: str
    source: dict
    conservation_area: dict
    timezone: dict
    default_language: str
    languages: list
    counts: dict
    time_coverage: dict
    warnings: list


@dataclass
class Labelled:
    uuid: Optional[str]
    key: Optional[str]
    labels: dict
    label: Optional[str]
    active: bool = True
    extra: dict = field(default_factory=dict)


@dataclass
class PatrolAttribute:
    uuid: str
    key: str
    labels: dict
    label: Optional[str]
    type: str
    active: bool
    options: list


@dataclass
class Employee:
    uuid: str
    id: Optional[str]
    given_name: Optional[str]
    family_name: Optional[str]
    agency_uuid: Optional[str]
    rank_uuid: Optional[str]
    employment_start: Optional[str]
    employment_end: Optional[str]
    active: bool


@dataclass
class Reference:
    patrol_types: list
    transports: list
    mandates: list
    stations: list
    patrol_attributes: list
    agencies: list
    ranks: list
    teams: list
    employees: list


def _labelled(d: dict, *extra_keys: str) -> Labelled:
    return Labelled(uuid=d.get("uuid"), key=d.get("key"), labels=d.get("labels") or {}, label=d.get("label"),
                    active=bool(d.get("active", True)), extra={k: d.get(k) for k in extra_keys})


@dataclass
class Bundle:
    path: Path
    manifest: Manifest
    reference: Reference
    patrols: list
    tracks: list
    language: str

    @property
    def ca_uuid(self) -> str:
        return self.manifest.conservation_area["uuid"]

    def label(self, obj: Any) -> str:
        """Display text in the chosen language, then the bundle default, then any; the key as a last resort."""
        labels = getattr(obj, "labels", None) if not isinstance(obj, dict) else obj.get("labels")
        plain = getattr(obj, "label", None) if not isinstance(obj, dict) else obj.get("label")
        labels = labels or {}
        for code in (self.language, self.manifest.default_language):
            if labels.get(code):
                return labels[code]
        if plain:
            return plain
        for v in labels.values():
            if v:
                return v
        key = getattr(obj, "key", None) if not isinstance(obj, dict) else obj.get("key")
        return key or ""

    def member_uuids(self) -> set:
        return {m["employee_uuid"] for p in self.patrols for leg in p["legs"] for m in leg["members"]}

    def leg_transport_uuids(self) -> set:
        return {leg["transport_uuid"] for p in self.patrols for leg in p["legs"] if leg.get("transport_uuid")}

    def patrol_team_uuids(self) -> set:
        return {p["team_uuid"] for p in self.patrols if p.get("team_uuid")}


def _read(path: Path, name: str, schema: dict) -> Any:
    f = path / f"{name}.json"
    if not f.exists():
        raise BundleError(f"{f}: missing; a bundle has {', '.join(n + '.json' for n in FILES)}")
    try:
        doc = json.loads(f.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise BundleError(f"{name}.json: not valid JSON ({e})") from None
    validator = jsonschema.Draft202012Validator({"$ref": f"#/$defs/{name}", "$defs": schema["$defs"]})
    errors = sorted(validator.iter_errors(doc), key=lambda e: list(e.path))
    if errors:
        e = errors[0]
        where = "/".join(str(p) for p in e.path) or "(root)"
        raise BundleError(f"{name}.json: {where}: {e.message}" + (f" (+{len(errors) - 1} more)" if len(errors) > 1 else ""))
    return doc


def load_bundle(path: Path, *, language: Optional[str] = None) -> Bundle:
    path = Path(path)
    if not path.is_dir():
        raise BundleError(f"{path}: not a directory")
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    raw_manifest = json.loads((path / "manifest.json").read_text(encoding="utf-8")) if (path / "manifest.json").exists() else None
    if raw_manifest is not None and raw_manifest.get("bundle_schema_version") != SUPPORTED_SCHEMA_VERSION:
        raise BundleError(f"manifest.json: bundle_schema_version {raw_manifest.get('bundle_schema_version')!r} is not supported; "
                          f"this er-smart-sync reads version {SUPPORTED_SCHEMA_VERSION}")
    docs = {name: _read(path, name, schema) for name in FILES}
    m = docs["manifest"]
    manifest = Manifest(**{k: m[k] for k in Manifest.__dataclass_fields__})
    codes = [l["code"] for l in manifest.languages]
    lang = language or manifest.default_language
    if lang not in codes:
        coverage = ", ".join(f"{l['code']} ({l['labels']} labels, {l['missing']} missing)" for l in manifest.languages)
        raise BundleError(f"language {lang!r} is not in the bundle; available: {coverage}")
    r = docs["reference"]
    reference = Reference(
        patrol_types=r["patrol_types"],
        transports=[_labelled(t, "patrol_type") for t in r["transports"]],
        mandates=[_labelled(x) for x in r["mandates"]],
        stations=[_labelled(x) for x in r["stations"]],
        patrol_attributes=[PatrolAttribute(uuid=a["uuid"], key=a["key"], labels=a.get("labels") or {}, label=a.get("label"),
                                           type=a["type"], active=bool(a["active"]), options=[_labelled(o) for o in a["options"]])
                           for a in r["patrol_attributes"]],
        agencies=r["agencies"],
        ranks=[_labelled(x, "agency_uuid") for x in r["ranks"]],
        teams=[_labelled(x, "member_uuids") for x in r["teams"]],
        employees=[Employee(**{k: e.get(k) for k in Employee.__dataclass_fields__}) for e in r["employees"]],
    )
    return Bundle(path=path, manifest=manifest, reference=reference, patrols=docs["patrols"]["patrols"],
                  tracks=docs["tracks"]["tracks"], language=lang)
```

- [ ] **Step 5: Run tests**

Run: `uv run pytest tests/test_bundle.py -v`
Expected: 4 passed

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml src/er_smart_sync/bundle tests/fixtures/smart_demo_bundle tests/test_bundle.py
git commit -m "Bundle loader with vendored schema and the SMART demo fixture"
```

---

### Task 2: Pure builders: patrol types, choice sets, subjects, team requirements

**Files:**
- Create: `src/er_smart_sync/smart_to_er_patrol_schema.py`, `tests/test_patrol_schema.py`

**Interfaces:**
- Consumes: `Bundle`, `Labelled`, `Employee` (Task 1); `ChoiceSet`, `ChoiceOption` from `er_smart_sync.choices`; `sanitize_choice_value` from `smartconnect.choices`.
- Produces: dataclasses `PatrolTypePlan(value: str, display: str, is_active: bool, transport_uuid: str)`, `SubjectPlan(name: str, smart_member_id: str, smart_employee_id: str | None, smart_ca_uuid: str, is_active: bool)`, `TeamRequirement(uuid: str, name: str, member_names: list[str], referenced_by_patrols: int)`; functions `patrol_type_value(transport_key: str, taken: set[str]) -> str`, `build_patrol_types(bundle) -> list[PatrolTypePlan]`, `build_patrol_choice_sets(bundle) -> list[ChoiceSet]`, `build_subjects(bundle) -> list[SubjectPlan]`, `build_team_requirements(bundle) -> list[TeamRequirement]`, constants `CHOICE_FIELD_MANDATE = "patrol_mandate"`, `CHOICE_FIELD_STATION = "patrol_station"`, `CHOICE_FIELD_CLASS = "patrol_class"`, `custom_choice_field(key) -> str` (`patrol_` + sanitized key), `PATROL_CLASSES = ("GROUND", "AIR", "MARINE", "MIXED")`.

- [ ] **Step 1: Write the failing tests**

`tests/test_patrol_schema.py`:

```python
from __future__ import annotations

import json
import shutil
from pathlib import Path

from er_smart_sync.bundle import load_bundle
from er_smart_sync.smart_to_er_patrol_schema import (
    CHOICE_FIELD_CLASS, CHOICE_FIELD_MANDATE, CHOICE_FIELD_STATION,
    build_patrol_choice_sets, build_patrol_types, build_subjects, build_team_requirements,
    custom_choice_field, patrol_type_value,
)

FIXTURE = Path(__file__).parent / "fixtures" / "smart_demo_bundle"


def _bundle_with(tmp_path, edit):
    shutil.copytree(FIXTURE, tmp_path / "b")
    for name in ("reference", "patrols"):
        doc = json.loads((tmp_path / "b" / f"{name}.json").read_text())
        edit(name, doc)
        (tmp_path / "b" / f"{name}.json").write_text(json.dumps(doc))
    return load_bundle(tmp_path / "b")


def test_patrol_type_values_unique_and_short():
    taken: set = set()
    assert patrol_type_value("foot", taken) == "patrol_foot"
    assert patrol_type_value("Others", taken) == "patrol_others"
    assert patrol_type_value("others1", taken) == "patrol_others1"
    assert patrol_type_value("Others", taken) == "patrol_others_2"          # same key twice stays unique
    long = patrol_type_value("a" * 80, taken)
    assert len(long) <= 50 and long.startswith("patrol_")
    assert patrol_type_value("a" * 80, taken) != long


def test_build_patrol_types(tmp_path):
    b = _bundle_with(tmp_path, lambda n, d: d["transports"].append(
        {"uuid": "11111111-1111-1111-1111-111111111111", "key": "boat", "labels": {"en": "Boat"}, "label": "Boat",
         "patrol_type": "MARINE", "active": False}) if n == "reference" else None)
    plans = build_patrol_types(b)
    assert [(p.value, p.display, p.is_active) for p in plans] == [
        ("patrol_foot", "Foot", True), ("patrol_vehicle", "Vehicle", True), ("patrol_boat", "Boat", False)]
    assert plans[0].transport_uuid == b.reference.transports[0].uuid


def test_build_choice_sets():
    b = load_bundle(FIXTURE)
    sets = {cs.field: cs for cs in build_patrol_choice_sets(b)}
    assert set(sets) == {CHOICE_FIELD_MANDATE, CHOICE_FIELD_STATION, CHOICE_FIELD_CLASS}
    assert [o.value for o in sets[CHOICE_FIELD_MANDATE].options] == ["antipoaching", "followup", "researchandmonitoring", "surveillance"]
    assert sets[CHOICE_FIELD_MANDATE].options[0].display == b.label(b.reference.mandates[0])
    # stations have no key: the value is derived from the label, and unlabelled stations are skipped
    assert [o.value for o in sets[CHOICE_FIELD_STATION].options] == ["headquarters"]
    assert [(o.value, o.display, o.is_active) for o in sets[CHOICE_FIELD_CLASS].options] == [
        ("air", "Air", False), ("ground", "Ground", True), ("marine", "Marine", False), ("mixed", "Mixed", True)]


def test_custom_attribute_choice_sets(tmp_path):
    def edit(n, d):
        if n == "reference":
            d["patrol_attributes"].append({"uuid": "22222222-2222-2222-2222-222222222222", "key": "Weather Now", "labels": {"en": "Weather"},
                                           "label": "Weather", "type": "LIST", "active": True,
                                           "options": [{"uuid": "33333333-3333-3333-3333-333333333333", "key": "sunny", "labels": {"en": "Sunny"}, "label": "Sunny", "active": True},
                                                       {"uuid": "44444444-4444-4444-4444-444444444444", "key": "wet", "labels": {"en": "Wet"}, "label": "Wet", "active": False}]})
            d["patrol_attributes"].append({"uuid": "55555555-5555-5555-5555-555555555555", "key": "km", "labels": {"en": "Km"}, "label": "Km",
                                           "type": "NUMERIC", "active": True, "options": []})
    b = _bundle_with(tmp_path, edit)
    sets = {cs.field: cs for cs in build_patrol_choice_sets(b)}
    assert custom_choice_field("Weather Now") == "patrol_weather_now"
    assert [(o.value, o.display, o.is_active) for o in sets["patrol_weather_now"].options] == [("sunny", "Sunny", True), ("wet", "Wet", False)]
    assert "patrol_km" not in sets                                   # NUMERIC attributes have no choices


def test_subject_names_and_duplicates(tmp_path):
    def edit(n, d):
        if n == "reference":
            d["employees"][1]["given_name"], d["employees"][1]["family_name"] = d["employees"][0]["given_name"], d["employees"][0]["family_name"]
            d["employees"][2]["given_name"] = None
            d["employees"][2]["family_name"] = ""
            d["employees"][3]["active"] = False
    b = _bundle_with(tmp_path, edit)
    subjects = build_subjects(b)
    assert len(subjects) == 7                                         # every member, active or not
    assert subjects[0].name == subjects[1].name and subjects[0].smart_member_id != subjects[1].smart_member_id
    assert subjects[2].name == b.reference.employees[2].id             # empty name falls back to the SMART id
    assert subjects[3].is_active is False
    assert all(s.smart_ca_uuid == b.ca_uuid for s in subjects)
    assert subjects[0].smart_employee_id == b.reference.employees[0].id


def test_subjects_include_members_without_employment(tmp_path):
    """A leg member missing from the employee list still gets a subject named after its uuid."""
    ghost = "99999999-9999-9999-9999-999999999999"

    def edit(n, d):
        if n == "patrols":
            d["patrols"][0]["legs"][0]["members"].append({"employee_uuid": ghost, "leader": False, "pilot": False})
    b = _bundle_with(tmp_path, edit)
    subjects = {s.smart_member_id: s for s in build_subjects(b)}
    assert ghost in subjects and subjects[ghost].name == ghost and subjects[ghost].is_active is False


def test_team_requirements(tmp_path):
    team = "66666666-6666-6666-6666-666666666666"

    def edit(n, d):
        if n == "reference":
            d["teams"].append({"uuid": team, "labels": {"en": "Team One"}, "label": "Team One",
                               "member_uuids": [d["employees"][0]["uuid"], d["employees"][1]["uuid"]]})
        else:
            d["patrols"][0]["team_uuid"] = team
            d["patrols"][1]["team_uuid"] = team
    b = _bundle_with(tmp_path, edit)
    reqs = build_team_requirements(b)
    assert len(reqs) == 1 and reqs[0].name == "Team One" and reqs[0].referenced_by_patrols == 2
    assert len(reqs[0].member_names) == 2
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_patrol_schema.py -v`
Expected: FAIL, `No module named 'er_smart_sync.smart_to_er_patrol_schema'`

- [ ] **Step 3: Implement**

`src/er_smart_sync/smart_to_er_patrol_schema.py`:

```python
"""Pure builders from a migration bundle to EarthRanger patrol configuration
plans: patrol types (one per SMART transport), choice sets for the universal
patrol fields, ranger subjects (one per SMART employee), and the teams an
admin must create by hand. No I/O; the segment schema builders live in the
same module (Task 3).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from smartconnect.choices import sanitize_choice_value

from .bundle import Bundle, Labelled
from .choices import ChoiceOption, ChoiceSet

CHOICE_FIELD_MANDATE = "patrol_mandate"
CHOICE_FIELD_STATION = "patrol_station"
CHOICE_FIELD_CLASS = "patrol_class"
PATROL_CLASSES = ("GROUND", "AIR", "MARINE", "MIXED")
PATROL_TYPE_VALUE_MAX = 50          # PatrolType.value is a CharField(max_length=50)


@dataclass(frozen=True)
class PatrolTypePlan:
    value: str
    display: str
    is_active: bool
    transport_uuid: str


@dataclass(frozen=True)
class SubjectPlan:
    name: str
    smart_member_id: str
    smart_employee_id: Optional[str]
    smart_ca_uuid: str
    is_active: bool


@dataclass(frozen=True)
class TeamRequirement:
    uuid: str
    name: str
    member_names: list
    referenced_by_patrols: int


def custom_choice_field(attribute_key: str) -> str:
    return f"patrol_{sanitize_choice_value(attribute_key)}"


def patrol_type_value(transport_key: str, taken: set) -> str:
    """`patrol_<key>`, unique within `taken` and within ER's 50-character limit."""
    base = f"patrol_{sanitize_choice_value(transport_key)}"[:PATROL_TYPE_VALUE_MAX]
    value, n = base, 1
    while value in taken:
        n += 1
        suffix = f"_{n}"
        value = base[:PATROL_TYPE_VALUE_MAX - len(suffix)] + suffix
    taken.add(value)
    return value


def build_patrol_types(bundle: Bundle) -> list:
    taken: set = set()
    return [PatrolTypePlan(value=patrol_type_value(t.key or t.uuid or "", taken), display=bundle.label(t),
                           is_active=t.active, transport_uuid=t.uuid) for t in bundle.reference.transports]


def _options(bundle: Bundle, items: list) -> tuple:
    out, seen = [], set()
    for it in items:
        key = it.key or bundle.label(it)
        if not key:
            continue                      # nothing to identify the option by
        value = sanitize_choice_value(key)
        if value in seen:
            continue
        seen.add(value)
        out.append(ChoiceOption(value=value, display=bundle.label(it) or key, is_active=it.active))
    return tuple(out)


def build_patrol_choice_sets(bundle: Bundle) -> list:
    ref = bundle.reference
    sets = [
        ChoiceSet(field=CHOICE_FIELD_MANDATE, options=_options(bundle, ref.mandates)),
        ChoiceSet(field=CHOICE_FIELD_STATION, options=_options(bundle, ref.stations)),
    ]
    present = {pt["key"]: bool(pt.get("active", True)) for pt in ref.patrol_types}
    classes = tuple(ChoiceOption(value=c.lower(), display=c.title(), is_active=present.get(c, False))
                    for c in sorted(set(PATROL_CLASSES) | set(present)))
    sets.append(ChoiceSet(field=CHOICE_FIELD_CLASS, options=classes))
    for attr in ref.patrol_attributes:
        if attr.type.strip() in ("LIST", "MLIST", "TREE"):
            sets.append(ChoiceSet(field=custom_choice_field(attr.key), options=_options(bundle, attr.options)))
    return sets


def build_subjects(bundle: Bundle) -> list:
    """One subject per employee that has a leg membership or an active employment.
    A member missing from the employee list (deleted staff) gets a subject named by its uuid."""
    members = bundle.member_uuids()
    by_uuid = {e.uuid: e for e in bundle.reference.employees}
    out = []
    for e in bundle.reference.employees:
        if not (e.active or e.uuid in members):
            continue
        name = " ".join(p for p in (e.given_name, e.family_name) if p).strip() or (e.id or e.uuid)
        out.append(SubjectPlan(name=name, smart_member_id=e.uuid, smart_employee_id=e.id, smart_ca_uuid=bundle.ca_uuid, is_active=e.active))
    for uuid in sorted(members - set(by_uuid)):
        out.append(SubjectPlan(name=uuid, smart_member_id=uuid, smart_employee_id=None, smart_ca_uuid=bundle.ca_uuid, is_active=False))
    return out


def build_team_requirements(bundle: Bundle) -> list:
    counts: dict = {}
    for p in bundle.patrols:
        if p.get("team_uuid"):
            counts[p["team_uuid"]] = counts.get(p["team_uuid"], 0) + 1
    names = {e.uuid: (" ".join(x for x in (e.given_name, e.family_name) if x).strip() or e.id or e.uuid) for e in bundle.reference.employees}
    out = []
    for t in bundle.reference.teams:
        if t.uuid in counts:
            out.append(TeamRequirement(uuid=t.uuid, name=bundle.label(t) or t.uuid,
                                       member_names=[names.get(u, u) for u in (t.extra.get("member_uuids") or [])],
                                       referenced_by_patrols=counts[t.uuid]))
    return out
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_patrol_schema.py -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add src/er_smart_sync/smart_to_er_patrol_schema.py tests/test_patrol_schema.py
git commit -m "Patrol configuration builders: types, choices, subjects, team requirements"
```

---

### Task 3: Universal segment schema builder and merge

**Files:**
- Modify: `src/er_smart_sync/smart_to_er_patrol_schema.py` (append), `tests/test_patrol_schema.py` (append)

**Interfaces:**
- Produces: `CANONICAL_FIELDS = ("objective", "mandate", "station", "patrol_class", "armed", "comment")`; `build_segment_schema(bundle, *, choices_base_url: str = "/api/v2.0/schemas") -> dict` returning the das envelope `{"json": {...}, "ui": {...}}`; `merge_segment_schema(current: dict | None, ours: dict) -> dict` (admin-added properties, fields and sections survive; our canonical keys win; our fields are appended to section-1's left column when absent); `SEGMENT_SECTION = "section-1"`.

Envelope shape (identical to `smart_to_er_v2._build_one`): `json` has `$schema` draft 2020-12, `type: object`, `unevaluatedProperties: False`, `properties`, `required: []`; `ui` has `fields`, `headers: {}`, `order: ["section-1"]`, `sections: {"section-1": {label, columns: 1, isActive: True, leftColumn: [{name, type: "field"}...], rightColumn: []}}`. Choice fields use `{"type": "string", "title", "description": "", "deprecated": False, "anyOf": [{"$ref": "<base>/choices.json?field=<name>"}]}` with the `CHOICE_LIST` ui block from `_build_choice_property_pair`; scalars use `SCALAR_JSON`/`SCALAR_UI` from `smart_to_er_v2`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_patrol_schema.py`:

```python
import copy

from er_smart_sync.smart_to_er_patrol_schema import CANONICAL_FIELDS, SEGMENT_SECTION, build_segment_schema, merge_segment_schema


def test_segment_schema_shape():
    b = load_bundle(FIXTURE)
    doc = build_segment_schema(b)
    props = doc["json"]["properties"]
    assert list(props) == list(CANONICAL_FIELDS)
    assert doc["json"]["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert doc["json"]["unevaluatedProperties"] is False and doc["json"]["required"] == []
    assert props["objective"] == {"type": "string", "title": "Objective", "description": ""}
    assert props["armed"]["type"] == "boolean"
    assert props["mandate"]["anyOf"] == [{"$ref": "/api/v2.0/schemas/choices.json?field=patrol_mandate"}]
    assert props["station"]["anyOf"][0]["$ref"].endswith("field=patrol_station")
    assert props["patrol_class"]["anyOf"][0]["$ref"].endswith("field=patrol_class")
    ui = doc["ui"]
    assert ui["order"] == [SEGMENT_SECTION] and ui["sections"][SEGMENT_SECTION]["label"] == "Patrol details"
    assert [f["name"] for f in ui["sections"][SEGMENT_SECTION]["leftColumn"]] == list(CANONICAL_FIELDS)
    assert ui["fields"]["comment"] == {"type": "TEXT", "inputType": "LONG_TEXT", "parent": SEGMENT_SECTION}
    assert ui["fields"]["mandate"]["type"] == "CHOICE_LIST" and ui["fields"]["mandate"]["choices"]["existingChoiceList"] == ["patrol_mandate"]


def test_segment_schema_custom_attributes(tmp_path):
    def edit(n, d):
        if n == "reference":
            d["patrol_attributes"] += [
                {"uuid": "22222222-2222-2222-2222-222222222222", "key": "Weather Now", "labels": {"en": "Weather"}, "label": "Weather", "type": "LIST", "active": True,
                 "options": [{"uuid": "33333333-3333-3333-3333-333333333333", "key": "sunny", "labels": {"en": "Sunny"}, "label": "Sunny", "active": True}]},
                {"uuid": "55555555-5555-5555-5555-555555555555", "key": "km", "labels": {"en": "Km walked"}, "label": "Km walked", "type": "NUMERIC", "active": True, "options": []},
                {"uuid": "77777777-7777-7777-7777-777777777777", "key": "old", "labels": {"en": "Old"}, "label": "Old", "type": "TEXT", "active": False, "options": []}]
    b = _bundle_with(tmp_path, edit)
    doc = build_segment_schema(b)
    props = doc["json"]["properties"]
    assert "smart_weather_now" in props and props["smart_weather_now"]["anyOf"][0]["$ref"].endswith("field=patrol_weather_now")
    assert props["smart_km"] == {"type": "number", "title": "Km walked", "description": ""}
    assert "smart_old" not in props                                            # inactive attributes are left out
    assert [f["name"] for f in doc["ui"]["sections"][SEGMENT_SECTION]["leftColumn"]][-2:] == ["smart_weather_now", "smart_km"]


def test_merge_keeps_admin_fields_and_detects_no_change():
    b = load_bundle(FIXTURE)
    ours = build_segment_schema(b)
    assert merge_segment_schema(None, ours) == ours
    assert merge_segment_schema({"json": {"properties": {}}, "ui": {}}, ours) == ours      # empty envelope → ours
    current = copy.deepcopy(ours)
    current["json"]["properties"]["radio_callsign"] = {"type": "string", "title": "Callsign", "description": ""}
    current["ui"]["fields"]["radio_callsign"] = {"type": "TEXT", "inputType": "SHORT_TEXT", "parent": SEGMENT_SECTION}
    current["ui"]["sections"][SEGMENT_SECTION]["leftColumn"].append({"name": "radio_callsign", "type": "field"})
    current["ui"]["sections"]["section-2"] = {"label": "Extra", "columns": 1, "isActive": True, "leftColumn": [], "rightColumn": []}
    current["ui"]["order"].append("section-2")
    current["json"]["properties"]["mandate"]["title"] = "Old title"           # ours must win on canonical keys
    merged = merge_segment_schema(current, ours)
    assert "radio_callsign" in merged["json"]["properties"] and "radio_callsign" in merged["ui"]["fields"]
    assert merged["json"]["properties"]["mandate"]["title"] == "Mandate"
    assert merged["ui"]["order"] == [SEGMENT_SECTION, "section-2"]
    names = [f["name"] for f in merged["ui"]["sections"][SEGMENT_SECTION]["leftColumn"]]
    assert names == list(CANONICAL_FIELDS) + ["radio_callsign"] and len(names) == len(set(names))
    assert merge_segment_schema(merged, ours) == merged                          # idempotent: a second merge changes nothing
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_patrol_schema.py -k segment -v`
Expected: FAIL, `cannot import name 'build_segment_schema'`

- [ ] **Step 3: Implement**

Append to `src/er_smart_sync/smart_to_er_patrol_schema.py`:

```python
import copy

from .smart_to_er_v2 import SCALAR_JSON, SCALAR_UI, _build_choice_property_pair

CANONICAL_FIELDS = ("objective", "mandate", "station", "patrol_class", "armed", "comment")
SEGMENT_SECTION = "section-1"
SEGMENT_SECTION_LABEL = "Patrol details"


def _scalar(smart_type: str, title: str) -> tuple:
    json_prop = {**copy.deepcopy(SCALAR_JSON[smart_type]), "title": title}
    ui = {**copy.deepcopy(SCALAR_UI[smart_type]), "parent": SEGMENT_SECTION}
    return json_prop, ui


def _choice(title: str, field_name: str, choices_base_url: str, is_multiple: bool = False) -> tuple:
    json_prop, ui = _build_choice_property_pair(smart_type="MLIST" if is_multiple else "LIST", display=title, is_multiple=is_multiple,
                                                field_name=field_name, choices_base_url=choices_base_url)
    ui["parent"] = SEGMENT_SECTION
    return json_prop, ui


def build_segment_schema(bundle: Bundle, *, choices_base_url: str = "/api/v2.0/schemas") -> dict:
    """The universal patrol fields for this CA, as the das schema envelope."""
    pairs: dict = {}
    pairs["objective"] = _scalar("TEXT", "Objective")
    pairs["mandate"] = _choice("Mandate", CHOICE_FIELD_MANDATE, choices_base_url)
    pairs["station"] = _choice("Station", CHOICE_FIELD_STATION, choices_base_url)
    pairs["patrol_class"] = _choice("Patrol class", CHOICE_FIELD_CLASS, choices_base_url)
    pairs["armed"] = _scalar("BOOLEAN", "Armed")
    comment_json, comment_ui = _scalar("TEXT", "Comment")
    comment_ui["inputType"] = "LONG_TEXT"
    pairs["comment"] = (comment_json, comment_ui)
    for attr in bundle.reference.patrol_attributes:
        if not attr.active:
            continue
        name = f"smart_{sanitize_choice_value(attr.key)}"
        t = attr.type.strip()
        title = bundle.label(attr) or attr.key
        if t in ("LIST", "MLIST", "TREE"):
            pairs[name] = _choice(title, custom_choice_field(attr.key), choices_base_url, is_multiple=(t == "MLIST"))
        elif t in SCALAR_JSON:
            pairs[name] = _scalar(t, title)
        else:
            pairs[name] = _scalar("TEXT", title)
    return {
        "json": {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "unevaluatedProperties": False,
            "properties": {k: v[0] for k, v in pairs.items()},
            "required": [],
        },
        "ui": {
            "fields": {k: v[1] for k, v in pairs.items()},
            "headers": {},
            "order": [SEGMENT_SECTION],
            "sections": {SEGMENT_SECTION: {"label": SEGMENT_SECTION_LABEL, "columns": 1, "isActive": True,
                                           "leftColumn": [{"name": k, "type": "field"} for k in pairs], "rightColumn": []}},
        },
    }


def merge_segment_schema(current: Optional[dict], ours: dict) -> dict:
    """Keep what an admin added by hand; our keys win where they overlap.
    Returns a document equal to `current` when nothing needs to change."""
    if not current or not (current.get("json") or {}).get("properties"):
        return copy.deepcopy(ours)
    merged = copy.deepcopy(current)
    merged["json"].setdefault("$schema", ours["json"]["$schema"])
    merged["json"].setdefault("type", "object")
    merged["json"].setdefault("required", [])
    merged["json"].setdefault("unevaluatedProperties", False)
    merged["json"]["properties"] = {**current["json"].get("properties", {}), **ours["json"]["properties"]}
    ui = merged.setdefault("ui", {})
    ui["fields"] = {**(current.get("ui", {}).get("fields") or {}), **ours["ui"]["fields"]}
    ui.setdefault("headers", {})
    sections = ui.setdefault("sections", {})
    if SEGMENT_SECTION not in sections:
        sections[SEGMENT_SECTION] = copy.deepcopy(ours["ui"]["sections"][SEGMENT_SECTION])
    order = ui.setdefault("order", [])
    if SEGMENT_SECTION not in order:
        order.insert(0, SEGMENT_SECTION)
    placed = {f["name"] for s in sections.values() for col in ("leftColumn", "rightColumn") for f in s.get(col, [])}
    left = sections[SEGMENT_SECTION].setdefault("leftColumn", [])
    ours_names = [f["name"] for f in ours["ui"]["sections"][SEGMENT_SECTION]["leftColumn"]]
    missing = [n for n in ours_names if n not in placed]
    # our fields first, in our order, then whatever the admin placed, without duplicates
    seen: set = set()
    new_left = []
    for entry in [{"name": n, "type": "field"} for n in ours_names if n in missing or n in {f["name"] for f in left}] + left:
        if entry["name"] in seen:
            continue
        seen.add(entry["name"])
        new_left.append(entry)
    sections[SEGMENT_SECTION]["leftColumn"] = new_left
    return merged
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_patrol_schema.py -v`
Expected: 10 passed. If `test_merge_keeps_admin_fields_and_detects_no_change` fails on the left-column order, the ordering block above is what to fix; the expected order is our canonical fields first, then admin fields in their existing order.

- [ ] **Step 5: Commit**

```bash
git add src/er_smart_sync/smart_to_er_patrol_schema.py tests/test_patrol_schema.py
git commit -m "Universal segment schema builder and admin-preserving merge"
```

---

### Task 4: `ERPatrolAPI`, the raw ER layer

**Files:**
- Create: `src/er_smart_sync/er_patrol_api.py`, `tests/test_er_patrol_api.py`

**Interfaces:**
- Consumes: an `ERClient` (or `DryRunERClient` wrapping one) with `_get(path, params=..., max_retries=...)`, `_post(path, payload)`, `_patch(path, payload)`, `get_subjects(**params)`, `post_subject(subject)`, `get_subjectgroups(...)`, `add_subjects_to_subjectgroup(group_id, subjects)`, `auth_headers()`, `_er_url(path)`.
- Produces: `class ERPatrolAPI(er_client)` with `preview_features() -> dict`, `patrol_schemas_enabled() -> bool`, `get_patrol_type(value) -> dict | None`, `create_patrol_type(payload) -> dict`, `patch_patrol_type(value, payload) -> dict`, `get_segment_schema() -> dict | None` (None when 404, i.e. flag off), `put_segment_schema(doc) -> dict`, `get_patrol_config() -> dict | None` (None when 404), `list_subjects() -> list[dict]` (active and inactive), `create_subject(payload) -> dict`, `patch_subject(id, payload) -> dict`, `find_subject_group(name) -> dict | None`, `add_subjects_to_group(group_id, subject_ids) -> None`; `class ERForbidden(Exception)` raised when ER answers 403 so the caller can turn it into a checklist item; `NotFound` is mapped to `None` where listed.
- The one PUT goes through `self._put(path, payload)` so that, when the client is a `DryRunERClient`, the call is recorded and skipped; detection is `type(er_client).__name__ == "DryRunERClient"` (the wrapper proxies attribute access, so `isinstance` on the inner class would be wrong).

- [ ] **Step 1: Write the failing tests**

`tests/test_er_patrol_api.py`:

```python
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from erclient.er_errors import ERClientException, ERClientNotFound, ERClientPermissionDenied

from er_smart_sync.defaults import DryRunERClient
from er_smart_sync.er_patrol_api import ERForbidden, ERPatrolAPI


def _client():
    c = MagicMock()
    c._er_url.side_effect = lambda path, base_url=None: f"https://er.test/api/v1.0/{path}"
    c.auth_headers.return_value = {"Authorization": "Bearer t"}
    return c


def test_preview_features_and_flag():
    c = _client()
    c._get.return_value = {"version": "x", "preview_features": {"patrol_schemas": False}}
    api = ERPatrolAPI(c)
    assert api.preview_features() == {"patrol_schemas": False}
    assert api.patrol_schemas_enabled() is False
    c._get.assert_called_with(path="status", max_retries=0)


def test_patrol_type_get_create_patch():
    c = _client()
    c._get.side_effect = ERClientNotFound("nope")
    api = ERPatrolAPI(c)
    assert api.get_patrol_type("patrol_foot") is None
    c._get.side_effect = None
    c._get.return_value = {"id": "1", "value": "patrol_foot", "display": "Foot", "is_active": True}
    assert api.get_patrol_type("patrol_foot")["id"] == "1"
    c._get.assert_called_with(path="activity/patrols/types/patrol_foot", max_retries=0)
    api.create_patrol_type({"value": "patrol_foot", "display": "Foot", "is_active": True})
    c._post.assert_called_with(path="activity/patrols/types", payload={"value": "patrol_foot", "display": "Foot", "is_active": True})
    api.patch_patrol_type("patrol_foot", {"display": "On foot"})
    c._patch.assert_called_with(path="activity/patrols/types/patrol_foot", payload={"display": "On foot"})


def test_forbidden_is_typed():
    c = _client()
    c._post.side_effect = ERClientPermissionDenied("no")
    with pytest.raises(ERForbidden):
        ERPatrolAPI(c).create_patrol_type({"value": "x", "display": "x"})


def test_segment_schema_get_404_and_put():
    c = _client()
    c._get.side_effect = ERClientNotFound("dark")
    api = ERPatrolAPI(c)
    assert api.get_segment_schema() is None
    c._get.side_effect = None
    c._get.return_value = {"id": "s", "value": "default", "display": "Default", "schema": {"json": {"properties": {}}, "ui": {}}}
    assert api.get_segment_schema() == {"json": {"properties": {}}, "ui": {}}
    c._get.assert_called_with(path="activity/patrols/segments/types/default", params={"include_schema": "true"}, max_retries=0)
    with patch("er_smart_sync.er_patrol_api.requests.put") as put:
        put.return_value.ok = True
        put.return_value.status_code = 200
        put.return_value.json.return_value = {"data": {"id": "s"}}
        assert api.put_segment_schema({"json": {}, "ui": {}}) == {"id": "s"}
        put.assert_called_once()
        args, kwargs = put.call_args
        assert args[0] == "https://er.test/api/v1.0/activity/patrols/segments/types/default"
        assert kwargs["json"] == {"schema": {"json": {}, "ui": {}}} and kwargs["headers"]["Authorization"] == "Bearer t"
    with patch("er_smart_sync.er_patrol_api.requests.put") as put:
        put.return_value.ok = False
        put.return_value.status_code = 403
        put.return_value.text = "forbidden"
        with pytest.raises(ERForbidden):
            api.put_segment_schema({"json": {}, "ui": {}})


def test_put_is_skipped_under_dry_run():
    inner = _client()
    api = ERPatrolAPI(DryRunERClient(inner))
    with patch("er_smart_sync.er_patrol_api.requests.put") as put:
        out = api.put_segment_schema({"json": {}, "ui": {}})
        put.assert_not_called()
    assert out.get("dry_run") is True


def test_subjects_and_groups():
    c = _client()
    c.get_subjects.return_value = [{"id": "a", "name": "A", "additional": {}, "is_active": True}]
    api = ERPatrolAPI(c)
    assert api.list_subjects()[0]["id"] == "a"
    c.get_subjects.assert_called_with(include_inactive=True, page_size=500)
    api.create_subject({"name": "B", "subject_subtype": "ranger", "additional": {}, "is_active": True})
    c.post_subject.assert_called_once()
    api.patch_subject("a", {"is_active": False})
    c._patch.assert_called_with(path="subject/a", payload={"is_active": False})
    c.get_subjectgroups.return_value = [{"id": "g1", "name": "Rangers"}, {"id": "g2", "name": "Vehicles"}]
    assert api.find_subject_group("rangers")["id"] == "g1"
    assert api.find_subject_group("Nope") is None
    api.add_subjects_to_group("g1", ["a", "b"])
    c.add_subjects_to_subjectgroup.assert_called_with("g1", [{"id": "a"}, {"id": "b"}])


def test_patrol_config_404_means_none():
    c = _client()
    c._get.side_effect = ERClientNotFound("dark")
    assert ERPatrolAPI(c).get_patrol_config() is None
    c._get.side_effect = None
    c._get.return_value = {"teams": [{"id": "t", "value": "alpha", "display": "Alpha"}], "members": [{"id": "g1", "name": "Rangers"}]}
    cfg = ERPatrolAPI(c).get_patrol_config()
    assert cfg["teams"][0]["value"] == "alpha" and cfg["members"][0]["id"] == "g1"
    c._get.assert_called_with(path="activity/patrols/config/default", max_retries=0)
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_er_patrol_api.py -v`
Expected: FAIL, `No module named 'er_smart_sync.er_patrol_api'`. If `ERClientNotFound` or `ERClientPermissionDenied` do not exist in `erclient.er_errors`, check `uv run python -c "import erclient.er_errors as e; print([n for n in dir(e) if 'ER' in n])"` and use the names that do (a 404 is raised as a subclass of `ERClientException`; match on the class the client raises for 404 and 403).

- [ ] **Step 3: Implement**

`src/er_smart_sync/er_patrol_api.py`:

```python
"""The EarthRanger calls the patrol migration needs that `erclient.ERClient`
does not wrap: site status, patrol types, the universal segment schema (PUT
only), the patrol configuration, subject updates and subject groups. Thin
on purpose: every method maps one endpoint, converts 404 to None where the
spec says so, and surfaces 403 as `ERForbidden` for the checklist.
"""
from __future__ import annotations

import logging
from typing import Optional

import requests
from erclient.er_errors import ERClientException, ERClientNotFound, ERClientPermissionDenied

logger = logging.getLogger(__name__)

STATUS_PATH = "status"
PATROL_TYPES_PATH = "activity/patrols/types"
SEGMENT_SCHEMA_PATH = "activity/patrols/segments/types/default"
PATROL_CONFIG_PATH = "activity/patrols/config/default"


class ERForbidden(Exception):
    """ER answered 403: the operator lacks a permission the step needs."""


def _is_dry_run(client) -> bool:
    return type(client).__name__ == "DryRunERClient"


class ERPatrolAPI:
    def __init__(self, er_client):
        self.er = er_client

    # -- reads -------------------------------------------------------------
    def _get_or_none(self, path: str, **params):
        try:
            if params:
                return self.er._get(path=path, params=params, max_retries=0)
            return self.er._get(path=path, max_retries=0)
        except ERClientNotFound:
            return None
        except ERClientPermissionDenied as e:
            raise ERForbidden(f"GET {path}: {e}") from None

    def preview_features(self) -> dict:
        status = self.er._get(path=STATUS_PATH, max_retries=0) or {}
        return status.get("preview_features") or {}

    def patrol_schemas_enabled(self) -> bool:
        return bool(self.preview_features().get("patrol_schemas"))

    def get_patrol_type(self, value: str) -> Optional[dict]:
        return self._get_or_none(f"{PATROL_TYPES_PATH}/{value}")

    def get_segment_schema(self) -> Optional[dict]:
        doc = self._get_or_none(SEGMENT_SCHEMA_PATH, include_schema="true")
        return None if doc is None else doc.get("schema")

    def get_patrol_config(self) -> Optional[dict]:
        return self._get_or_none(PATROL_CONFIG_PATH)

    def list_subjects(self) -> list:
        subjects = self.er.get_subjects(include_inactive=True, page_size=500)
        if isinstance(subjects, dict):
            subjects = subjects.get("results", [])
        return list(subjects or [])

    def find_subject_group(self, name: str) -> Optional[dict]:
        groups = self.er.get_subjectgroups(include_inactive=False, include_hidden=True, isvisible=True, flat=True)
        if isinstance(groups, dict):
            groups = groups.get("results", [])
        return next((g for g in groups or [] if (g.get("name") or "").strip().lower() == name.strip().lower()), None)

    # -- writes ------------------------------------------------------------
    def _write(self, fn, **kwargs):
        try:
            return fn(**kwargs)
        except ERClientPermissionDenied as e:
            raise ERForbidden(str(e)) from None

    def create_patrol_type(self, payload: dict) -> dict:
        return self._write(self.er._post, path=PATROL_TYPES_PATH, payload=payload)

    def patch_patrol_type(self, value: str, payload: dict) -> dict:
        return self._write(self.er._patch, path=f"{PATROL_TYPES_PATH}/{value}", payload=payload)

    def _put(self, path: str, payload: dict) -> dict:
        """erclient has no PUT; do it with requests, unless the client is a dry-run wrapper."""
        if _is_dry_run(self.er):
            logger.info("DryRunERClient: would call _put path=%s payload=%s", path, payload)
            self.er.calls.append(("_put", (), {"path": path, "payload": payload}))
            return {"id": "dry-run", "dry_run": True}
        response = requests.put(self.er._er_url(path), json=payload, headers=self.er.auth_headers(), timeout=60)
        if response.status_code == 403:
            raise ERForbidden(f"PUT {path}: {response.text}")
        if not response.ok:
            raise ERClientException(f"PUT {path} failed: {response.status_code} {response.text}")
        body = response.json()
        return body.get("data", body) if isinstance(body, dict) else body

    def put_segment_schema(self, schema_doc: dict) -> dict:
        return self._put(SEGMENT_SCHEMA_PATH, {"schema": schema_doc})

    def create_subject(self, payload: dict) -> dict:
        return self._write(self.er.post_subject, subject=payload)

    def patch_subject(self, subject_id: str, payload: dict) -> dict:
        return self._write(self.er._patch, path=f"subject/{subject_id}", payload=payload)

    def add_subjects_to_group(self, group_id: str, subject_ids: list) -> None:
        self._write(self.er.add_subjects_to_subjectgroup, group_id=group_id, subjects=[{"id": s} for s in subject_ids])
```

`DryRunERClient.calls` exists on the wrapper (`defaults.py`), which is why `_put` appends to it; `post_subject` on the wrapper takes `subject=` because `_write` passes keyword arguments, and `ERClient.post_subject(self, subject)` accepts that.

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_er_patrol_api.py -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add src/er_smart_sync/er_patrol_api.py tests/test_er_patrol_api.py
git commit -m "ERPatrolAPI: the patrol endpoints erclient lacks, dry-run aware"
```

---

### Task 5: Orchestration and checklist

**Files:**
- Create: `src/er_smart_sync/patrol_datamodel.py`, `tests/test_patrol_datamodel.py`

**Interfaces:**
- Consumes: `Bundle`, builders from Tasks 2 and 3, `ERPatrolAPI`/`ERForbidden`, `upsert_choices(er_client=, choice_sets=) -> ChoicesStats`.
- Produces: `@dataclass PatrolDatamodelStats` with counters `types_created, types_updated, types_unchanged, schema_written: bool, schema_unchanged: bool, schema_skipped: str | None, choices: ChoicesStats | None, subjects_created, subjects_updated, subjects_unchanged, subjects_grouped, errors: list[str]`; `@dataclass ChecklistItem(code: str, title: str, steps: list[str])`; `@dataclass PatrolDatamodelResult(stats, checklist: list[ChecklistItem], subject_ids: dict[str, str])` (`smart_member_id -> ER subject id`); `run_patrol_datamodel(api: ERPatrolAPI, er_client, bundle: Bundle, *, replace_segment_schema: bool = False, roster_group: str | None = None, choices_base_url: str = "/api/v2.0/schemas") -> PatrolDatamodelResult`; `render_checklist(result, bundle) -> str` (markdown); `format_summary(result) -> str`.
- Subject matching: `additional.smart_member_id == plan.smart_member_id`, else `additional.smart_employee_id == plan.smart_employee_id` (non-empty), else create. PATCH when `name`, `is_active`, or any of the three identity keys differ; the PATCH sends `additional` as the existing dict updated with ours (ER replaces the whole JSON field).
- Roster: `roster_group` name → `find_subject_group`; else, when the patrol config is readable and lists member groups, the first of them; else a checklist item. Subjects already in the group are not re-added (ER tolerates it, but the stats would lie): the group's membership is read with `get_subjects(subject_group_id=...)` only when the group was found.

- [ ] **Step 1: Write the failing tests**

`tests/test_patrol_datamodel.py`:

```python
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from er_smart_sync.bundle import load_bundle
from er_smart_sync.choices import ChoicesStats
from er_smart_sync.er_patrol_api import ERForbidden
from er_smart_sync.patrol_datamodel import format_summary, render_checklist, run_patrol_datamodel

FIXTURE = Path(__file__).parent / "fixtures" / "smart_demo_bundle"


class FakeAPI:
    """In-memory stand-in for ERPatrolAPI with the state a sandbox would have."""

    def __init__(self, *, flag=True, schema=None, config=None, subjects=None, groups=None, forbid=()):
        self.flag, self.schema, self.config = flag, schema, config
        self.subjects = subjects or []
        self.groups = groups or {}
        self.group_members: dict = {}
        self.types: dict = {}
        self.calls: list = []
        self.forbid = set(forbid)
        self._n = 0

    def _id(self):
        self._n += 1
        return f"id-{self._n}"

    def _check(self, op):
        if op in self.forbid:
            raise ERForbidden(op)

    def patrol_schemas_enabled(self):
        return self.flag

    def get_patrol_type(self, value):
        return self.types.get(value)

    def create_patrol_type(self, payload):
        self._check("create_patrol_type"); self.calls.append(("create_type", payload))
        self.types[payload["value"]] = {"id": self._id(), **payload}
        return self.types[payload["value"]]

    def patch_patrol_type(self, value, payload):
        self._check("patch_patrol_type"); self.calls.append(("patch_type", value, payload))
        self.types[value].update(payload)
        return self.types[value]

    def get_segment_schema(self):
        return self.schema if self.flag else None

    def put_segment_schema(self, doc):
        self._check("put_segment_schema"); self.calls.append(("put_schema", doc)); self.schema = doc
        return {"id": "s"}

    def get_patrol_config(self):
        return self.config if self.flag else None

    def list_subjects(self):
        return [dict(s) for s in self.subjects]

    def create_subject(self, payload):
        self._check("create_subject"); self.calls.append(("create_subject", payload))
        s = {"id": self._id(), **payload}
        self.subjects.append(s)
        return s

    def patch_subject(self, subject_id, payload):
        self._check("patch_subject"); self.calls.append(("patch_subject", subject_id, payload))
        s = next(x for x in self.subjects if x["id"] == subject_id)
        s.update(payload)
        return s

    def find_subject_group(self, name):
        return next(({"id": gid, "name": n} for gid, n in self.groups.items() if n.lower() == name.lower()), None)

    def group_subject_ids(self, group_id):
        return set(self.group_members.get(group_id, set()))

    def add_subjects_to_group(self, group_id, subject_ids):
        self._check("add_subjects_to_group"); self.calls.append(("group", group_id, list(subject_ids)))
        self.group_members.setdefault(group_id, set()).update(subject_ids)


@pytest.fixture
def no_choice_writes():
    with patch("er_smart_sync.patrol_datamodel.upsert_choices", return_value=ChoicesStats(created=3)) as up:
        yield up


def test_fresh_site_creates_everything(no_choice_writes):
    b = load_bundle(FIXTURE)
    api = FakeAPI(flag=True, schema={"json": {"properties": {}}, "ui": {}}, config={"teams": [], "members": [{"id": "g1", "name": "Rangers"}]},
                  groups={"g1": "Rangers"})
    r = run_patrol_datamodel(api, MagicMock(), b)
    s = r.stats
    assert (s.types_created, s.types_updated, s.types_unchanged) == (2, 0, 0)
    assert api.types["patrol_foot"]["display"] == "Foot" and api.types["patrol_foot"]["is_active"] is True
    assert s.schema_written and not s.schema_unchanged and s.schema_skipped is None
    assert "mandate" in api.schema["json"]["properties"]
    assert no_choice_writes.call_count == 1 and s.choices.created == 3
    assert (s.subjects_created, s.subjects_updated, s.subjects_unchanged) == (7, 0, 0)
    created = [c[1] for c in api.calls if c[0] == "create_subject"]
    assert created[0]["subject_subtype"] == "ranger" and created[0]["additional"]["smart_member_id"] == b.reference.employees[0].uuid
    assert created[0]["additional"]["smart_ca_uuid"] == b.ca_uuid and created[0]["is_active"] is True
    assert s.subjects_grouped == 7 and api.group_subject_ids("g1") == set(r.subject_ids.values())
    assert [c.code for c in r.checklist] == []                      # nothing left for the admin on this site
    assert "patrol types: 2 created" in format_summary(r)


def test_second_run_is_a_no_op(no_choice_writes):
    b = load_bundle(FIXTURE)
    api = FakeAPI(flag=True, schema={"json": {"properties": {}}, "ui": {}}, config={"teams": [], "members": [{"id": "g1", "name": "Rangers"}]}, groups={"g1": "Rangers"})
    run_patrol_datamodel(api, MagicMock(), b)
    api.calls.clear()
    r = run_patrol_datamodel(api, MagicMock(), b)
    assert api.calls == []
    assert (r.stats.types_unchanged, r.stats.subjects_unchanged, r.stats.subjects_grouped) == (2, 7, 0)
    assert r.stats.schema_unchanged and not r.stats.schema_written


def test_updates_and_legacy_subject_match(no_choice_writes):
    b = load_bundle(FIXTURE)
    e0, e1 = b.reference.employees[0], b.reference.employees[1]
    api = FakeAPI(flag=True, schema={"json": {"properties": {}}, "ui": {}}, config={"teams": [], "members": []},
                  subjects=[{"id": "old-1", "name": "Renamed Person", "additional": {"smart_member_id": e0.uuid, "smart_employee_id": e0.id, "smart_ca_uuid": b.ca_uuid}, "is_active": True},
                            {"id": "old-2", "name": f"{e1.given_name} {e1.family_name}", "additional": {"smart_employee_id": e1.id, "ca_uuid": "legacy"}, "is_active": True}])
    api.types["patrol_foot"] = {"id": "t", "value": "patrol_foot", "display": "Walk", "is_active": False}
    r = run_patrol_datamodel(api, MagicMock(), b)
    assert (r.stats.types_created, r.stats.types_updated) == (1, 1)
    assert ("patch_type", "patrol_foot", {"display": "Foot", "is_active": True}) in api.calls
    patched = {c[1]: c[2] for c in api.calls if c[0] == "patch_subject"}
    assert patched["old-1"]["name"] == f"{e0.given_name} {e0.family_name}"
    assert patched["old-2"]["additional"]["smart_member_id"] == e1.uuid          # legacy match now carries the uuid
    assert patched["old-2"]["additional"]["ca_uuid"] == "legacy"                  # existing keys preserved
    assert (r.stats.subjects_created, r.stats.subjects_updated) == (5, 2)
    assert r.subject_ids[e0.uuid] == "old-1" and r.subject_ids[e1.uuid] == "old-2"
    assert [c.code for c in r.checklist] == ["roster_group"]                      # config has no member group and none was named


def test_flag_off_skips_schema_with_checklist(no_choice_writes):
    b = load_bundle(FIXTURE)
    api = FakeAPI(flag=False)
    r = run_patrol_datamodel(api, MagicMock(), b)
    assert r.stats.types_created == 2 and r.stats.subjects_created == 7
    assert r.stats.schema_skipped == "patrol_schemas preview feature is off"
    assert not any(c[0] == "put_schema" for c in api.calls)
    assert [c.code for c in r.checklist] == ["enable_patrol_schemas", "roster_group"]
    md = render_checklist(r, b)
    assert "patrol_schemas" in md and "Rangers" not in md


def test_forbidden_steps_become_checklist_items(no_choice_writes):
    b = load_bundle(FIXTURE)
    api = FakeAPI(flag=True, schema={"json": {"properties": {}}, "ui": {}}, config={"teams": [], "members": []}, forbid={"put_segment_schema", "create_patrol_type"})
    r = run_patrol_datamodel(api, MagicMock(), b)
    assert r.stats.types_created == 0 and r.stats.errors
    assert {c.code for c in r.checklist} >= {"permission_patrol_types", "permission_segment_schema", "roster_group"}


def test_teams_and_named_roster_group(no_choice_writes, tmp_path):
    import json, shutil
    shutil.copytree(FIXTURE, tmp_path / "b")
    ref = json.loads((tmp_path / "b" / "reference.json").read_text())
    team = "66666666-6666-6666-6666-666666666666"
    ref["teams"].append({"uuid": team, "labels": {"en": "Team One"}, "label": "Team One", "member_uuids": [ref["employees"][0]["uuid"]]})
    (tmp_path / "b" / "reference.json").write_text(json.dumps(ref))
    pats = json.loads((tmp_path / "b" / "patrols.json").read_text())
    pats["patrols"][0]["team_uuid"] = team
    (tmp_path / "b" / "patrols.json").write_text(json.dumps(pats))
    b = load_bundle(tmp_path / "b")
    api = FakeAPI(flag=True, schema={"json": {"properties": {}}, "ui": {}}, config={"teams": [{"id": "x", "value": "other", "display": "Other"}], "members": []},
                  groups={"g9": "Field staff"})
    r = run_patrol_datamodel(api, MagicMock(), b, roster_group="field staff")
    assert r.stats.subjects_grouped == 7 and api.group_subject_ids("g9") == set(r.subject_ids.values())
    codes = [c.code for c in r.checklist]
    assert codes == ["team"] and r.checklist[0].title.endswith("Team One")
    md = render_checklist(r, b)
    assert "Team One" in md and ref["employees"][0]["given_name"] in md and "1 patrol" in md


def test_replace_segment_schema(no_choice_writes):
    b = load_bundle(FIXTURE)
    current = {"json": {"properties": {"radio": {"type": "string", "title": "Radio", "description": ""}}}, "ui": {"fields": {"radio": {"type": "TEXT", "inputType": "SHORT_TEXT", "parent": "section-1"}}, "headers": {}, "order": ["section-1"], "sections": {"section-1": {"label": "x", "columns": 1, "isActive": True, "leftColumn": [{"name": "radio", "type": "field"}], "rightColumn": []}}}}
    api = FakeAPI(flag=True, schema=current, config={"teams": [], "members": []})
    run_patrol_datamodel(api, MagicMock(), b)
    assert "radio" in api.schema["json"]["properties"]                             # merged by default
    api2 = FakeAPI(flag=True, schema=current, config={"teams": [], "members": []})
    run_patrol_datamodel(api2, MagicMock(), b, replace_segment_schema=True)
    assert "radio" not in api2.schema["json"]["properties"]
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_patrol_datamodel.py -v`
Expected: FAIL, `No module named 'er_smart_sync.patrol_datamodel'`

- [ ] **Step 3: Implement**

`src/er_smart_sync/patrol_datamodel.py`:

```python
"""`patrol-datamodel`: from a smart-able bundle, create or update the ER
patrol types, the universal segment schema and its choices, and the ranger
subjects, then list what the admin still has to do by hand. Every step is
idempotent; a failed step is recorded and the next one runs.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

from .bundle import Bundle
from .choices import ChoicesStats, upsert_choices
from .er_patrol_api import ERForbidden, ERPatrolAPI
from .smart_to_er_patrol_schema import (
    build_patrol_choice_sets, build_patrol_types, build_segment_schema, build_subjects,
    build_team_requirements, merge_segment_schema,
)

logger = logging.getLogger(__name__)

SUBJECT_SUBTYPE = "ranger"
IDENTITY_KEYS = ("smart_member_id", "smart_employee_id", "smart_ca_uuid")


@dataclass
class PatrolDatamodelStats:
    types_created: int = 0
    types_updated: int = 0
    types_unchanged: int = 0
    schema_written: bool = False
    schema_unchanged: bool = False
    schema_skipped: Optional[str] = None
    choices: Optional[ChoicesStats] = None
    subjects_created: int = 0
    subjects_updated: int = 0
    subjects_unchanged: int = 0
    subjects_grouped: int = 0
    errors: list = field(default_factory=list)


@dataclass
class ChecklistItem:
    code: str
    title: str
    steps: list


@dataclass
class PatrolDatamodelResult:
    stats: PatrolDatamodelStats
    checklist: list
    subject_ids: dict


def _sync_patrol_types(api: ERPatrolAPI, bundle: Bundle, stats: PatrolDatamodelStats, checklist: list) -> None:
    for plan in build_patrol_types(bundle):
        try:
            existing = api.get_patrol_type(plan.value)
            if existing is None:
                api.create_patrol_type({"value": plan.value, "display": plan.display, "is_active": plan.is_active})
                stats.types_created += 1
                continue
            patch = {}
            if existing.get("display") != plan.display:
                patch["display"] = plan.display
            if bool(existing.get("is_active", True)) != plan.is_active and plan.is_active:
                patch["is_active"] = True      # never deactivate a type an admin may have switched on
            if patch:
                api.patch_patrol_type(plan.value, patch)
                stats.types_updated += 1
            else:
                stats.types_unchanged += 1
        except ERForbidden as e:
            stats.errors.append(f"patrol type {plan.value}: {e}")
            if not any(c.code == "permission_patrol_types" for c in checklist):
                checklist.append(ChecklistItem("permission_patrol_types", "Grant the operator permission to manage patrol types",
                                               ["Django admin > Users > the operator > add `activity | patrol type | Can add/change patrol type`",
                                                "Re-run `er-smart-sync patrol-datamodel`."]))


def _sync_segment_schema(api: ERPatrolAPI, bundle: Bundle, stats: PatrolDatamodelStats, checklist: list, *, replace: bool, choices_base_url: str) -> None:
    if not api.patrol_schemas_enabled():
        stats.schema_skipped = "patrol_schemas preview feature is off"
        checklist.append(ChecklistItem("enable_patrol_schemas", "Enable the `patrol_schemas` preview feature on this site",
                                       ["Django admin > Preview features > patrol_schemas > enable for this tenant (ER Core can do this if the admin page is not available).",
                                        "Re-run `er-smart-sync patrol-datamodel` so the universal patrol fields are written."]))
        return
    ours = build_segment_schema(bundle, choices_base_url=choices_base_url)
    try:
        current = api.get_segment_schema()
        target = ours if replace else merge_segment_schema(current, ours)
        if current is not None and target == current:
            stats.schema_unchanged = True
            return
        api.put_segment_schema(target)
        stats.schema_written = True
    except ERForbidden as e:
        stats.errors.append(f"segment schema: {e}")
        stats.schema_skipped = "forbidden"
        checklist.append(ChecklistItem("permission_segment_schema", "Grant the operator permission to write the universal patrol fields",
                                       ["Django admin > Users > the operator > add `activity | segment type | Can change segment type` (superuser today).",
                                        "Re-run `er-smart-sync patrol-datamodel`."]))


def _sync_choices(er_client, bundle: Bundle, stats: PatrolDatamodelStats) -> None:
    stats.choices = upsert_choices(er_client=er_client, choice_sets=build_patrol_choice_sets(bundle))


def _subject_payload(plan) -> dict:
    return {"name": plan.name, "subject_subtype": SUBJECT_SUBTYPE, "is_active": plan.is_active,
            "additional": {"smart_member_id": plan.smart_member_id, "smart_employee_id": plan.smart_employee_id, "smart_ca_uuid": plan.smart_ca_uuid}}


def _sync_subjects(api: ERPatrolAPI, bundle: Bundle, stats: PatrolDatamodelStats, checklist: list) -> dict:
    existing = api.list_subjects()
    by_member = {(s.get("additional") or {}).get("smart_member_id"): s for s in existing if (s.get("additional") or {}).get("smart_member_id")}
    by_employee = {(s.get("additional") or {}).get("smart_employee_id"): s for s in existing if (s.get("additional") or {}).get("smart_employee_id")}
    ids: dict = {}
    for plan in build_subjects(bundle):
        match = by_member.get(plan.smart_member_id) or (by_employee.get(plan.smart_employee_id) if plan.smart_employee_id else None)
        payload = _subject_payload(plan)
        try:
            if match is None:
                created = api.create_subject(payload)
                stats.subjects_created += 1
                ids[plan.smart_member_id] = created.get("id")
                continue
            ids[plan.smart_member_id] = match["id"]
            additional = dict(match.get("additional") or {})
            patch: dict = {}
            if match.get("name") != plan.name:
                patch["name"] = plan.name
            if bool(match.get("is_active", True)) != plan.is_active:
                patch["is_active"] = plan.is_active
            if any(additional.get(k) != payload["additional"][k] for k in IDENTITY_KEYS):
                additional.update(payload["additional"])
                patch["additional"] = additional
            if patch:
                api.patch_subject(match["id"], patch)
                stats.subjects_updated += 1
            else:
                stats.subjects_unchanged += 1
        except ERForbidden as e:
            stats.errors.append(f"subject {plan.name}: {e}")
            if not any(c.code == "permission_subjects" for c in checklist):
                checklist.append(ChecklistItem("permission_subjects", "Grant the operator permission to manage subjects",
                                               ["Django admin > Users > the operator > add `observations | subject | Can add/change subject`.",
                                                "Re-run `er-smart-sync patrol-datamodel`."]))
    return ids


def _sync_roster(api: ERPatrolAPI, bundle: Bundle, stats: PatrolDatamodelStats, checklist: list, ids: dict, roster_group: Optional[str]) -> None:
    group = None
    if roster_group:
        group = api.find_subject_group(roster_group)
        if group is None:
            stats.errors.append(f"roster group {roster_group!r} not found")
    else:
        config = api.get_patrol_config()
        members = (config or {}).get("members") or []
        if members:
            group = {"id": members[0]["id"], "name": members[0].get("name", "")}
    want = {i for i in ids.values() if i and i != "dry-run"}
    if group is None:
        checklist.append(ChecklistItem("roster_group", "Put the ranger subjects in the patrol roster subject group",
                                       ["Django admin > Patrol configuration > member subject groups: choose or create the group rangers are picked from (for example `Rangers`).",
                                        f"Add the {len(ids)} subjects this run created or matched to that group, or re-run with `--roster-group <name>` to let the tool do it."]))
        return
    have = api.group_subject_ids(group["id"]) if hasattr(api, "group_subject_ids") else set()
    missing = sorted(want - have)
    if missing:
        try:
            api.add_subjects_to_group(group["id"], missing)
            stats.subjects_grouped = len(missing)
        except ERForbidden as e:
            stats.errors.append(f"subject group {group['name']}: {e}")
            checklist.append(ChecklistItem("permission_subject_group", f"Add the subjects to the `{group['name']}` subject group",
                                           ["The operator cannot change subject-group membership; an admin adds the subjects in Django admin > Subject groups."]))


def _team_items(bundle: Bundle, api: ERPatrolAPI, checklist: list) -> None:
    reqs = build_team_requirements(bundle)
    if not reqs:
        return
    config = api.get_patrol_config()
    existing = {(t.get("display") or "").strip().lower() for t in ((config or {}).get("teams") or [])} | \
               {(t.get("value") or "").strip().lower() for t in ((config or {}).get("teams") or [])}
    for req in reqs:
        if req.name.strip().lower() in existing:
            continue
        checklist.append(ChecklistItem("team", f"Create the patrol team {req.name}",
                                       [f"Django admin > Patrol configuration > Teams > add a team with display `{req.name}`.",
                                        "Members (from SMART): " + (", ".join(req.member_names) or "none recorded") + ".",
                                        f"Referenced by {req.referenced_by_patrols} patrol{'s' if req.referenced_by_patrols != 1 else ''} in the bundle; the import resolves the team by this name."]))


def run_patrol_datamodel(api: ERPatrolAPI, er_client, bundle: Bundle, *, replace_segment_schema: bool = False,
                         roster_group: Optional[str] = None, choices_base_url: str = "/api/v2.0/schemas") -> PatrolDatamodelResult:
    stats = PatrolDatamodelStats()
    checklist: list = []
    _sync_patrol_types(api, bundle, stats, checklist)
    _sync_segment_schema(api, bundle, stats, checklist, replace=replace_segment_schema, choices_base_url=choices_base_url)
    _sync_choices(er_client, bundle, stats)
    ids = _sync_subjects(api, bundle, stats, checklist)
    _sync_roster(api, bundle, stats, checklist, ids, roster_group)
    _team_items(bundle, api, checklist)
    order = ["enable_patrol_schemas", "permission_patrol_types", "permission_segment_schema", "permission_subjects", "permission_subject_group", "team", "roster_group"]
    checklist.sort(key=lambda c: order.index(c.code) if c.code in order else len(order))
    return PatrolDatamodelResult(stats=stats, checklist=checklist, subject_ids=ids)


def format_summary(result: PatrolDatamodelResult) -> str:
    s = result.stats
    schema = "written" if s.schema_written else "unchanged" if s.schema_unchanged else f"skipped ({s.schema_skipped})"
    ch = s.choices
    lines = ["Patrol datamodel summary:",
             f"  patrol types: {s.types_created} created, {s.types_updated} updated, {s.types_unchanged} unchanged",
             f"  universal patrol fields: {schema}",
             f"  choices: {ch.created if ch else 0} created, {ch.updated if ch else 0} updated, {ch.unchanged if ch else 0} unchanged, {ch.deactivated if ch else 0} deactivated, {ch.errored if ch else 0} errored",
             f"  subjects: {s.subjects_created} created, {s.subjects_updated} updated, {s.subjects_unchanged} unchanged, {s.subjects_grouped} added to the roster group",
             f"  checklist items: {len(result.checklist)}",
             f"  errors: {len(s.errors)}"]
    lines += [f"    - {e}" for e in s.errors]
    return "\n".join(lines)


def render_checklist(result: PatrolDatamodelResult, bundle: Bundle) -> str:
    ca = bundle.manifest.conservation_area
    out = [f"# Admin checklist for {ca.get('name')} ({ca.get('id')})", "",
           "Steps `er-smart-sync patrol-datamodel` could not do through the API. Do them in order, then run `er-smart-sync patrol-preflight`.", ""]
    if not result.checklist:
        out.append("Nothing to do: every step completed through the API.")
    for n, item in enumerate(result.checklist, 1):
        out.append(f"## {n}. {item.title}")
        out.append("")
        out += [f"- {s}" for s in item.steps]
        out.append("")
    return "\n".join(out)
```

The `ChoicesStats` field names (`created`, `updated`, `unchanged`, `deactivated`, `errored`) come from `choices.py`; check `uv run python -c "from er_smart_sync.choices import ChoicesStats; print(ChoicesStats())"` and adjust `format_summary` if they differ.

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_patrol_datamodel.py -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add src/er_smart_sync/patrol_datamodel.py tests/test_patrol_datamodel.py
git commit -m "patrol-datamodel orchestration: types, schema, choices, subjects, roster, checklist"
```

---

### Task 6: Config, CLI command, docs

**Files:**
- Modify: `src/er_smart_sync/config.py` (append), `src/er_smart_sync/cli.py` (new command after `patrols`), `tests/test_cli.py` (append), `tests/test_config.py` (append), `USAGE.md`, `CLAUDE.md`, `mkdocs.yml`, `docs/changelog.md`
- Create: `docs/cli-reference/patrol-datamodel.md`, `docs/workflows/migrate-patrols.md`

**Interfaces:**
- Produces: `BundleConfig(path: str)`; `PatrolToolConfig(earthranger: EarthRangerConfig, bundle: BundleConfig | None = None, language: str | None = None, timezone: str | None = None)` with `load_patrol_tool_config(path: str) -> PatrolToolConfig` reading the same `sync.yaml` (`earthranger:` block, optional `bundle: {path: ...}`, `smart.use_language_code`, `smart.timezone`); CLI `er-smart-sync patrol-datamodel [--config sync.yaml] [--bundle DIR] [--er-endpoint ...] [--er-token ...] [--smart-language CODE] [--timezone IANA] [--replace-segment-schema] [--roster-group NAME] [--checklist PATH]`, exit 0 on success (checklist items are not failures), 1 on a `BundleError`, a timezone mismatch, or any step error.
- Timezone assertion (spec "Timezone"): when `--timezone`/`smart.timezone` is set and differs from `manifest.timezone.name`, stop with an error naming both and the command `SMART_ABLE_TIMEZONE=<zone> ./smart-able export <ca_id>`; when it matches, no warning; when unset and the manifest's zone was inferred, print a warning.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_config.py`:

```python
def test_patrol_tool_config_from_yaml(tmp_path):
    from er_smart_sync.config import load_patrol_tool_config
    p = tmp_path / "sync.yaml"
    p.write_text("""
smart:
  endpoint: https://smart.example.org/server
  login: u
  password: p
  use_language_code: fr
  timezone: Asia/Manila
earthranger:
  id: site
  endpoint: site.pamdas.org
  token: tok
bundle:
  path: /data/bundle
""")
    c = load_patrol_tool_config(str(p))
    assert c.earthranger.endpoint == "https://site.pamdas.org/api/v1.0" and c.earthranger.token == "tok"
    assert c.bundle.path == "/data/bundle" and c.language == "fr" and c.timezone == "Asia/Manila"
    p.write_text("earthranger:\n  id: site\n  endpoint: site.pamdas.org\n  token: tok\n")
    c = load_patrol_tool_config(str(p))
    assert c.bundle is None and c.language is None and c.timezone is None     # smart: block is optional here
```

Append to `tests/test_cli.py`:

```python
from pathlib import Path
from unittest.mock import MagicMock, patch

from er_smart_sync.patrol_datamodel import ChecklistItem, PatrolDatamodelResult, PatrolDatamodelStats

FIXTURE = Path(__file__).parent / "fixtures" / "smart_demo_bundle"


def _result(**kw):
    stats = PatrolDatamodelStats(**kw)
    return PatrolDatamodelResult(stats=stats, checklist=[ChecklistItem("team", "Create the patrol team Alpha", ["do it"])], subject_ids={})


def test_patrol_datamodel_help():
    result = CliRunner().invoke(main, ["patrol-datamodel", "--help"])
    assert result.exit_code == 0 and "--bundle" in result.output and "--roster-group" in result.output


def test_patrol_datamodel_runs_and_writes_checklist(tmp_path):
    with patch("er_smart_sync.cli._make_er_client", return_value=MagicMock()), \
         patch("er_smart_sync.cli.run_patrol_datamodel", return_value=_result(types_created=2)) as run:
        result = CliRunner().invoke(main, ["patrol-datamodel", "--bundle", str(FIXTURE), "--er-endpoint", "x.pamdas.org", "--er-token", "t",
                                           "--checklist", str(tmp_path / "checklist.md"), "--roster-group", "Rangers"])
    assert result.exit_code == 0, result.output
    assert "patrol types: 2 created" in result.output and "checklist items: 1" in result.output
    assert (tmp_path / "checklist.md").read_text().startswith("# Admin checklist for Example Conservation Area")
    assert run.call_args.kwargs["roster_group"] == "Rangers"
    assert "inferred" in result.output                                   # the demo bundle's zone was inferred and no --timezone given


def test_patrol_datamodel_dry_run_wraps_client(tmp_path):
    with patch("er_smart_sync.cli._make_er_client", return_value=MagicMock()), \
         patch("er_smart_sync.cli.run_patrol_datamodel", return_value=_result()) as run:
        result = CliRunner().invoke(main, ["--dry-run", "patrol-datamodel", "--bundle", str(FIXTURE), "--er-endpoint", "x.pamdas.org", "--er-token", "t",
                                           "--checklist", str(tmp_path / "c.md")])
    assert result.exit_code == 0, result.output
    assert type(run.call_args.args[1]).__name__ == "DryRunERClient"
    assert "Dry run mode" in result.output


def test_patrol_datamodel_timezone_assertion(tmp_path):
    with patch("er_smart_sync.cli._make_er_client", return_value=MagicMock()):
        result = CliRunner().invoke(main, ["patrol-datamodel", "--bundle", str(FIXTURE), "--er-endpoint", "x.pamdas.org", "--er-token", "t",
                                           "--timezone", "Europe/Paris", "--checklist", str(tmp_path / "c.md")])
    assert result.exit_code == 1
    assert "Europe/Paris" in result.output and "Africa/Libreville" in result.output and "SMART_ABLE_TIMEZONE=Europe/Paris" in result.output


def test_patrol_datamodel_bad_language_and_bundle(tmp_path):
    with patch("er_smart_sync.cli._make_er_client", return_value=MagicMock()):
        r1 = CliRunner().invoke(main, ["patrol-datamodel", "--bundle", str(FIXTURE), "--er-endpoint", "x.pamdas.org", "--er-token", "t", "--smart-language", "xx"])
        r2 = CliRunner().invoke(main, ["patrol-datamodel", "--bundle", str(tmp_path), "--er-endpoint", "x.pamdas.org", "--er-token", "t"])
    assert r1.exit_code == 1 and "xx" in r1.output and "en" in r1.output
    assert r2.exit_code == 1 and "manifest.json" in r2.output


def test_patrol_datamodel_step_errors_exit_1(tmp_path):
    with patch("er_smart_sync.cli._make_er_client", return_value=MagicMock()), \
         patch("er_smart_sync.cli.run_patrol_datamodel", return_value=_result(errors=["segment schema: forbidden"])):
        result = CliRunner().invoke(main, ["patrol-datamodel", "--bundle", str(FIXTURE), "--er-endpoint", "x.pamdas.org", "--er-token", "t",
                                           "--checklist", str(tmp_path / "c.md")])
    assert result.exit_code == 1 and "segment schema: forbidden" in result.output
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_config.py tests/test_cli.py -k "patrol_tool or patrol_datamodel" -v`
Expected: FAIL, `cannot import name 'load_patrol_tool_config'` and "No such command 'patrol-datamodel'"

- [ ] **Step 3: Implement config**

Append to `src/er_smart_sync/config.py`:

```python
class BundleConfig(pydantic.BaseModel):
    """Where the smart-able migration bundle for this site lives (the directory holding manifest.json)."""

    path: str


class PatrolToolConfig(pydantic.BaseModel):
    """What the bundle-driven patrol subcommands need: an ER site, the bundle,
    and the two SMART-side settings that still apply without SMART Connect."""

    earthranger: EarthRangerConfig
    bundle: Optional[BundleConfig] = None
    language: Optional[str] = None
    timezone: Optional[str] = None


def load_patrol_tool_config(path: str) -> PatrolToolConfig:
    import yaml

    with open(path) as f:
        data = yaml.safe_load(f) or {}
    smart = data.get("smart") or {}
    return PatrolToolConfig(
        earthranger=EarthRangerConfig(**data["earthranger"]),
        bundle=BundleConfig(**data["bundle"]) if data.get("bundle") else None,
        language=smart.get("use_language_code"),
        timezone=smart.get("timezone"),
    )
```

(`Optional` is already imported in `config.py` through `typing`; if not, add `from typing import Optional`.)

- [ ] **Step 4: Implement the command**

In `src/er_smart_sync/cli.py`, add the imports near the top (`from .bundle import BundleError, load_bundle`, `from .er_patrol_api import ERPatrolAPI`, `from .patrol_datamodel import format_summary, render_checklist, run_patrol_datamodel`, `from .config import load_patrol_tool_config`) and, after the `patrols` command:

```python
# ── patrol-datamodel subcommand (bundle → ER patrol configuration) ────


def _resolve_bundle_options(config_file, bundle, er_endpoint, er_token, er_username, er_password, er_id, smart_language, timezone):
    """Merge --config with the flags for the bundle-driven subcommands; flags win."""
    from .config import EarthRangerConfig

    cfg = load_patrol_tool_config(config_file) if config_file else None
    bundle_path = bundle or (cfg.bundle.path if cfg and cfg.bundle else None)
    if not bundle_path:
        raise click.UsageError("--bundle DIR (or bundle.path in --config) is required")
    if cfg and not er_endpoint:
        er = cfg.earthranger
    else:
        if not er_endpoint:
            raise click.UsageError("--er-endpoint (or an earthranger block in --config) is required")
        er = EarthRangerConfig(id=er_id, endpoint=er_endpoint, token=er_token or "", login=er_username or "", password=er_password or "")
    language = smart_language or (cfg.language if cfg else None)
    tz = timezone or (cfg.timezone if cfg else None)
    return bundle_path, er, language, tz


def _check_bundle_timezone(bundle, timezone: str | None) -> None:
    """The importer never reinterprets timestamps: a configured zone must match the bundle's."""
    name = bundle.manifest.timezone.get("name")
    source = bundle.manifest.timezone.get("source")
    ca_id = bundle.manifest.conservation_area.get("id", "")
    if timezone and timezone != name:
        raise click.ClickException(
            f"timezone {timezone!r} is configured but the bundle was written for {name!r} ({source}). "
            f"Re-export it with the right zone: SMART_ABLE_TIMEZONE={timezone} ./smart-able export {ca_id}"
        )
    if not timezone and source == "inferred":
        click.echo(f"warning: the bundle's timezone {name} was inferred from waypoint positions; set smart.timezone "
                   f"(or --timezone) to {name} to confirm it, or re-export with SMART_ABLE_TIMEZONE set.", err=True)


@main.command("patrol-datamodel")
@click.option("--config", "config_file", type=click.Path(exists=True), help="YAML config file (earthranger block, optional bundle.path)")
@click.option("--bundle", "bundle_dir", type=click.Path(file_okay=False), help="smart-able migration bundle directory (holds manifest.json)")
@er_options
@click.option("--smart-language", default=None, help="Language code for the labels written to ER (default: the bundle's default language)")
@click.option("--timezone", default=None, help="Assert the bundle's timezone (IANA name); mismatch stops the run")
@click.option("--replace-segment-schema", is_flag=True, help="Overwrite the universal patrol fields instead of merging with what is on the site")
@click.option("--roster-group", default=None, help="Subject group to add the ranger subjects to (default: the patrol configuration's first member group)")
@click.option("--checklist", "checklist_path", default=None, help="Where to write checklist.md (default: next to the bundle directory)")
@click.pass_context
def patrol_datamodel_cmd(ctx, config_file, bundle_dir, er_endpoint, er_token, er_username, er_password, er_id,
                         smart_language, timezone, replace_segment_schema, roster_group, checklist_path):
    """Create or update the ER patrol types, universal patrol fields, choices and
    ranger subjects a SMART conservation area needs, from a smart-able bundle.

    Idempotent: a second run against an unchanged bundle makes no writes.
    Ends with an admin checklist for the steps the API cannot do.
    """
    from pathlib import Path

    bundle_path, er, language, tz = _resolve_bundle_options(config_file, bundle_dir, er_endpoint, er_token, er_username, er_password, er_id, smart_language, timezone)
    try:
        bundle = load_bundle(Path(bundle_path), language=language)
    except BundleError as e:
        raise click.ClickException(str(e)) from None
    _check_bundle_timezone(bundle, tz)
    er_client = _make_er_client(endpoint=er.endpoint, token=er.token, username=er.login, password=er.password)
    if ctx.obj and ctx.obj.get("dry_run"):
        er_client = DryRunERClient(er_client)
        click.echo("Dry run mode: no writes will be sent to ER.", err=True)
    result = run_patrol_datamodel(ERPatrolAPI(er_client), er_client, bundle, replace_segment_schema=replace_segment_schema,
                                  roster_group=roster_group, choices_base_url=er.choices_base_url)
    out = Path(checklist_path) if checklist_path else Path(bundle_path).resolve().parent / "checklist.md"
    out.write_text(render_checklist(result, bundle), encoding="utf-8")
    click.echo(format_summary(result))
    click.echo(f"Checklist written to {out}")
    for item in result.checklist:
        click.echo(f"  - {item.title}")
    if result.stats.errors:
        ctx.exit(1)
```

`DryRunERClient` is already imported in `cli.py` for `_make_synchronizer`. `click.echo(..., err=True)` lines reach `result.output` under `CliRunner` because it mixes stderr in by default.

- [ ] **Step 5: Run tests, lint**

Run: `uv run pytest -q && uv run ruff check src tests && uv run ruff format --check src tests`
Expected: all pass; fix formatting with `uv run ruff format src tests` if the check fails.

- [ ] **Step 6: Docs**

`docs/cli-reference/patrol-datamodel.md`:

```markdown
# patrol-datamodel

Creates or updates, from a [smart-able](https://github.com/PADAS/er-smart-able) migration bundle, everything an EarthRanger site needs before SMART patrols can be imported:

| Step | What it writes | Idempotency |
| --- | --- | --- |
| Patrol types | one per SMART transport, `value = patrol_<transport key>`, display in the chosen language | upsert on `value`; inactive transports are created inactive, never deactivated by the tool |
| Universal patrol fields | the default segment schema: `objective`, `mandate`, `station`, `patrol_class`, `armed`, `comment`, plus `smart_<key>` per custom patrol attribute | merged with what is on the site (admin-added fields survive); `--replace-segment-schema` overwrites; skipped with a checklist item while the `patrol_schemas` preview feature is off |
| Choices | `patrol_mandate`, `patrol_station`, `patrol_class`, `patrol_<attribute>` | the same upsert as `choices`; orphans are deactivated |
| Subjects | one `ranger` subject per employee with a leg membership or an active employment, `additional.smart_member_id` = SMART employee UUID | match on `smart_member_id`, then `smart_employee_id`; patched when name, active flag or identity metadata differ |
| Roster | subjects added to the patrol configuration's member group, or `--roster-group NAME` | only missing members are added |

Then it writes `checklist.md` next to the bundle with the steps an admin has to do by hand: enabling the preview feature, creating teams, granting permissions, choosing the roster group.

## Usage

    er-smart-sync patrol-datamodel --bundle work/export/050500/bundle --er-endpoint site.pamdas.org --er-token TOKEN
    er-smart-sync --dry-run patrol-datamodel --config sync.yaml       # bundle.path and earthranger from the file

## Options

| Option | Meaning |
| --- | --- |
| `--bundle DIR` | the bundle directory (holds `manifest.json`); or `bundle.path` in `--config` |
| `--smart-language CODE` | language for every display string written to ER; must be one of the bundle's languages (the Data Model tab in smart-able shows them with coverage) |
| `--timezone NAME` | assert the bundle's timezone; a mismatch stops the run and tells you how to re-export |
| `--replace-segment-schema` | overwrite the universal patrol fields instead of merging |
| `--roster-group NAME` | subject group to add the subjects to |
| `--checklist PATH` | where to write the checklist (default: beside the bundle directory) |
| `--dry-run` (before the subcommand) | log every write instead of sending it |

Exit status is 0 even when the checklist has items; it is 1 when a step failed (for example a 403), when the bundle is invalid, or when the timezone assertion fails.
```

`docs/workflows/migrate-patrols.md` (the runbook grows with the later subcommands):

```markdown
# Migrate SMART patrols

1. Get the site's SMART backup or Conservation Area export zip and run `./smart-able all <zip>` in [smart-able](https://github.com/PADAS/er-smart-able). This writes `work/export/<ca_id>/datamodel.xml`, the configurable models, and `work/export/<ca_id>/bundle/`.
2. Push the event data model: `er-smart-sync datamodel --from-file work/export/<ca_id>/datamodel.xml --cm-from-file work/export/<ca_id>/configurable_model_<uuid>.xml --ca-identifier <ID> ...` (see [Push a data model](push-datamodel.md)).
3. Pick the label language in smart-able's Data Model tab and the timezone the bundle was written for (`manifest.json`); set `smart.use_language_code` and `smart.timezone` in `sync.yaml`.
4. Run `er-smart-sync --dry-run patrol-datamodel --config sync.yaml`, read the plan, then run it without `--dry-run`.
5. Work through `checklist.md`: enable the `patrol_schemas` preview feature if it is off (then re-run step 4 so the universal fields are written), create the teams it lists, put the subjects in the roster group if the tool could not.
6. `patrol-preflight` and `import-patrols` follow in later releases.

Quirks of the SMART data that matter here (zone-less times, placeholder dates, retired staff) are written up in smart-able's `quirks.html`, published with every browser.
```

`mkdocs.yml`: add `- patrol-datamodel: cli-reference/patrol-datamodel.md` after `patrols` under CLI reference and `- Migrate patrols: workflows/migrate-patrols.md` after `Sync patrols` under Workflows. `docs/changelog.md`: an "Unreleased" entry: "`patrol-datamodel`: patrol types, universal patrol fields, choices and ranger subjects from a smart-able migration bundle (ERA-14137)." `USAGE.md`: a bullet for `patrol-datamodel` in the subcommand list. `CLAUDE.md`: add a fifth flow, "Patrol configuration (bundle → ER)", and `patrol-datamodel` to the CLI subcommand list.

- [ ] **Step 7: Build the docs and commit**

Run: `uv run mkdocs build --strict 2>&1 | tail -3` (the `docs` extra; `uv pip install -e ".[dev,docs]"` if mkdocs is missing).
Expected: build succeeds with no warnings.

```bash
git add src/er_smart_sync/config.py src/er_smart_sync/cli.py tests/test_cli.py tests/test_config.py docs mkdocs.yml USAGE.md CLAUDE.md
git commit -m "patrol-datamodel command, config, docs"
```

---

### Task 7: Live check against the gundi-dev sandbox

**Files:**
- Modify: `docs/changelog.md` (record the verification), `docs/workflows/migrate-patrols.md` (note what the sandbox showed)

The sandbox `https://gundi-dev.staging.pamdas.org` has `patrol_schemas` off (checked 2026-10-13), two patrol types of its own (`routine_patrol`, `bicycle_patrol`), and no `Rangers` group. earthranger-cli holds a session for it: the token is in `~/.config/er-events/tokens/gundi-dev.json` (`access_token`); never print it.

- [ ] **Step 1: Dry run with the demo bundle (reads only)**

```bash
TOKEN=$(python3 -c "import json; print(json.load(open('$HOME/.config/er-events/tokens/gundi-dev.json'))['access_token'])")
uv run er-smart-sync --dry-run patrol-datamodel --bundle tests/fixtures/smart_demo_bundle --er-endpoint gundi-dev.staging.pamdas.org --er-token "$TOKEN" --checklist /tmp/claude-smart-able/checklist-demo.md 2>&1 | grep -v Bearer | tail -30
```

Expected: `Dry run mode`; "would call _post ... activity/patrols/types" twice (`patrol_foot`, `patrol_vehicle`); universal patrol fields `skipped (patrol_schemas preview feature is off)`; choices planned for `patrol_mandate` (4), `patrol_station` (1), `patrol_class` (4); 7 subject creates; checklist with `enable_patrol_schemas` and `roster_group`; exit 0. Any 403 means the cached session lacks a permission: record it in the changelog note and stop.

- [ ] **Step 2: Real run, only with the user's explicit go-ahead**

Creating patrol types and subjects on a shared sandbox is a visible change: stop and ask before running without `--dry-run`. When approved, run the same command without `--dry-run`, then run it a second time and confirm the second run reports everything unchanged and makes no writes (`-v` shows no POST/PATCH lines). Then check in the ER UI (or `GET activity/patrols/types/patrol_foot`) that the type and a subject exist.

- [ ] **Step 3: Record and commit**

Add to the changelog entry what was verified on the sandbox and the date; add a line to `docs/workflows/migrate-patrols.md` saying the universal fields need the preview feature, as seen on gundi-dev.

```bash
git add docs
git commit -m "Docs: patrol-datamodel verified against the gundi-dev sandbox"
```

---

## Self-review notes

- Spec coverage for `patrol-datamodel`: patrol types (Task 2 builders, Task 5 upsert), universal segment schema with merge semantics and `--replace-segment-schema` (Tasks 3, 5), choices through the existing upsert (Tasks 2, 5), subjects with the two-key match and identity PATCH (Tasks 2, 5), checklist content: preview feature, teams with rosters, roster group, permissions (Task 5), summary line (Task 5), language validation and fallback (Task 1, CLI in Task 6), timezone assertion (Task 6), bundle validation and version refusal (Task 1), `BundleConfig` and optional SMART block (Task 6), "Historical members": retired staff created inactive, nothing blocks on them here (Task 2 and 5). The `inactive_members` preflight warning and reactivation belong to the preflight and import plans.
- Deviations from the spec, deliberate: the roster group is assigned through the API when a group can be identified (the spec listed it as checklist-only; `subjectgroup/{id}/subjects/` exists and erclient wraps it), with the checklist item kept for the case where no group is known. Patrol type icons are not set (the spec mentioned a small icon map; ER's icon ids are site-specific and a wrong one is a 400), to revisit when the UI shows a need.
- Type consistency: `run_patrol_datamodel(api, er_client, bundle, *, replace_segment_schema, roster_group, choices_base_url)` is called that way in Task 6; `FakeAPI` in Task 5 implements every `ERPatrolAPI` method the orchestration calls plus `group_subject_ids`, which the real API gains in Task 5 via `hasattr` (Task 4's class does not define it; add `def group_subject_ids(self, group_id) -> set: return {s["id"] for s in (self.er.get_subjects(subject_group_id=group_id, include_inactive=True, page_size=500) or [])}` to `ERPatrolAPI` while implementing Task 5, with a test in `tests/test_er_patrol_api.py`).
- Review Focus tests: `test_label_fallback_order` (1), `test_flag_off_skips_schema_with_checklist` (2), `test_subject_names_and_duplicates` (3), `test_merge_keeps_admin_fields_and_detects_no_change` (4), `test_patrol_type_values_unique_and_short` (5).
