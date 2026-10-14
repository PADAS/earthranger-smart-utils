from __future__ import annotations

import json
import shutil
from pathlib import Path
from unittest.mock import MagicMock

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
    f = Finding.of(
        "x", BLOCKING, "m", "fix", [str(i) for i in range(25)], affected=["p1", "p2"]
    )
    assert (
        f.count == 25
        and len(f.examples) == MAX_EXAMPLES
        and f.affected_patrols == ["p1", "p2"]
    )


def test_bundle_only_checks_on_the_demo(tmp_path):
    b = load_bundle(FIXTURE)
    report = run_preflight(
        MagicMock(),
        b,
        checks=[
            check_leg_span,
            check_time_coverage,
            check_timezone,
            check_language,
            check_bundle_warnings,
        ],
    )
    codes = _codes(report.findings)
    assert (
        "timezone",
        WARNING,
    ) in codes  # the demo's zone was inferred and none was asserted
    assert ("leg_span", WARNING) not in codes and (
        "time_coverage",
        WARNING,
    ) not in codes
    assert report.exit_code == 0


def test_timezone_assertion(tmp_path):
    b = load_bundle(FIXTURE)
    name = b.manifest.timezone["name"]
    assert check_timezone_codes(b, name) == []
    assert check_timezone_codes(b, "Europe/Paris") == [("timezone", BLOCKING)]


def check_timezone_codes(b, tz):
    return _codes(
        run_preflight(MagicMock(), b, timezone=tz, checks=[check_timezone]).findings
    )


def test_leg_span_time_coverage_language_and_warnings(tmp_path):
    def edit(name, doc):
        if name == "patrols":
            doc["patrols"][0]["legs"][0]["placeholder_dates"] = True
        if name == "manifest":
            doc["time_coverage"]["date_only"] = 3
            doc["languages"] = [
                {"code": "en", "default": True, "labels": 400, "missing": 70}
            ]
            doc["warnings"] = [
                f"attachment {i} is not in the extracted filestore" for i in range(30)
            ]

    b = _edited(tmp_path, edit)
    report = run_preflight(
        MagicMock(),
        b,
        checks=[
            check_leg_span,
            check_time_coverage,
            check_language,
            check_bundle_warnings,
        ],
    )
    by = {f.code: f for f in report.findings}
    assert by["leg_span"].level == WARNING and by["leg_span"].count == 1
    assert by["leg_span"].affected_patrols == [b.patrols[0]["uuid"]]
    assert by["time_coverage"].count == 3 and "whole day" in by["time_coverage"].message
    assert by["language"].count == 70 and "en" in by["language"].message
    assert (
        by["bundle_warnings"].count == 30
        and len(by["bundle_warnings"].examples) == MAX_EXAMPLES
    )


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


from er_smart_sync.preflight import (  # noqa: E402
    check_choices,
    check_feature_flag,
    check_patrol_types,
    check_permissions,
    check_segment_schema,
)
from er_smart_sync.smart_to_er_patrol_schema import (  # noqa: E402
    build_patrol_types,
    build_segment_schema,
)


class SiteFake:
    """The site as preflight reads it."""

    def __init__(
        self,
        bundle,
        *,
        flag=True,
        me=None,
        types=None,
        schema=None,
        choices=None,
        resolved=None,
        subjects=None,
        groups=None,
        event_types=None,
    ):
        self.flag = flag
        self.me = me if me is not None else {"is_superuser": True, "permissions": {}}
        self.types = (
            types
            if types is not None
            else {
                p.value: {"value": p.value, "is_active": True}
                for p in build_patrol_types(bundle)
            }
        )
        self.schema = schema if schema is not None else build_segment_schema(bundle)
        self.choices = choices or {}
        self.resolved = (
            resolved
            if resolved is not None
            else {"members": [], "assets": [], "teams": []}
        )
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
        return (
            {
                "members": [{"id": g} for g in self.groups],
                "teams": self.resolved["teams"],
            }
            if self.flag
            else None
        )

    def get_patrol_config_resolved(self):
        return self.resolved if self.flag else None

    def list_subjects(self):
        return list(self.subjects)

    def group_subject_ids(self, gid):
        return set(self.groups.get(gid, ()))

    def list_event_types(self, version="v2"):
        return list(self.event_types)

    def choice_records(self, field):
        return [
            {"value": v, "is_active": a} for v, a in self.choices.get(field, {}).items()
        ]


def test_flag_off_reports_once():
    b = load_bundle(FIXTURE)
    report = run_preflight(
        SiteFake(b, flag=False),
        b,
        checks=[
            check_feature_flag,
            check_patrol_types,
            check_segment_schema,
            check_choices,
        ],
    )
    assert _codes(report.findings) == [("feature_flag", BLOCKING)]
    assert "patrol_schemas" in report.findings[0].fix


def test_permissions():
    b = load_bundle(FIXTURE)
    assert run_preflight(SiteFake(b), b, checks=[check_permissions]).findings == []
    me = {
        "is_superuser": False,
        "permissions": {"patrol": ["view"], "event": ["add", "change"]},
    }
    (f,) = run_preflight(SiteFake(b, me=me), b, checks=[check_permissions]).findings
    assert f.code == "permissions" and f.level == BLOCKING
    assert "patrol: add" in f.examples and "eventrelatedsegments: add" in f.examples


def test_patrol_types_missing_and_inactive():
    b = load_bundle(FIXTURE)
    plans = build_patrol_types(b)
    types = {plans[0].value: {"value": plans[0].value, "is_active": False}}
    (f,) = run_preflight(
        SiteFake(b, types=types), b, checks=[check_patrol_types]
    ).findings
    assert f.code == "patrol_types" and f.level == BLOCKING and f.count == len(plans)
    assert any("inactive" in e for e in f.examples) and any(
        "missing" in e for e in f.examples
    )
    assert set(f.affected_patrols) == {
        p["uuid"] for p in b.patrols
    }  # every demo patrol uses one of them


def test_segment_schema_missing_and_deprecated_fields():
    b = load_bundle(FIXTURE)
    schema = build_segment_schema(b)
    del schema["json"]["properties"]["mandate"]
    schema["json"]["properties"]["armed"]["deprecated"] = True
    (f,) = run_preflight(
        SiteFake(b, schema=schema), b, checks=[check_segment_schema]
    ).findings
    assert f.code == "segment_schema" and f.level == BLOCKING
    assert sorted(f.examples) == ["armed (deprecated)", "mandate (missing)"]


def test_choices_used_by_patrols(monkeypatch):
    b = load_bundle(FIXTURE)
    site = SiteFake(b, choices={"patrol_mandate": {}, "patrol_class": {"ground": True}})
    monkeypatch.setattr(
        "er_smart_sync.preflight._choice_records",
        lambda ctx, field: site.choice_records(field),
    )
    (f,) = run_preflight(site, b, checks=[check_choices]).findings
    assert f.code == "choices" and f.level == WARNING
    assert any(e.startswith("patrol_mandate:") for e in f.examples)
    assert not any(e.startswith("patrol_class:") for e in f.examples)


from er_smart_sync.preflight import (  # noqa: E402
    check_inactive_members,
    check_subjects,
    check_teams,
)


def _subject(sid, member, active=True):
    return {
        "id": sid,
        "name": sid,
        "is_active": active,
        "additional": {"smart_member_id": member},
    }


def test_inactive_and_off_roster_members():
    b = load_bundle(FIXTURE)
    members = sorted(b.member_uuids())
    subjects = [_subject(f"s{i}", m) for i, m in enumerate(members)]
    subjects[0]["is_active"] = False  # retired, still in the configured group
    subjects[1]["is_active"] = False  # retired and out of every group
    on_roster = [
        {"id": s["id"], "is_active": True} for s in subjects[3:]
    ]  # s2 is active but off the roster
    site = SiteFake(
        b,
        subjects=subjects,
        resolved={"members": on_roster, "assets": [], "teams": []},
        groups={"g1": {"s0"} | {s["id"] for s in subjects[3:]}},
    )
    report = run_preflight(site, b, checks=[check_subjects, check_inactive_members])
    by = {(f.code, f.level): f for f in report.findings}
    assert (
        by[("subjects", BLOCKING)].count == 1
        and "s2" in by[("subjects", BLOCKING)].examples[0]
    )
    assert by[("inactive_members", WARNING)].count == 2
    assert "reactivat" in by[("inactive_members", WARNING)].fix
    assert (
        by[("inactive_members", BLOCKING)].count == 1
        and "s1" in by[("inactive_members", BLOCKING)].examples[0]
    )


def test_member_without_subject_is_blocking():
    b = load_bundle(FIXTURE)
    report = run_preflight(SiteFake(b, subjects=[]), b, checks=[check_subjects])
    (f,) = report.findings
    assert f.code == "subjects" and f.count == len(b.member_uuids())
    assert set(f.affected_patrols) == {
        p["uuid"] for p in b.patrols if any(leg["members"] for leg in p["legs"])
    }


def test_teams():
    b = load_bundle(FIXTURE)
    (req_uuid,) = b.patrol_team_uuids()
    name = f"SMART team {req_uuid[:8]}"
    ok = {
        "members": [],
        "assets": [],
        "teams": [{"value": "x", "display": name, "is_active": True}],
    }
    assert (
        run_preflight(SiteFake(b, resolved=ok), b, checks=[check_teams]).findings == []
    )
    (f,) = run_preflight(SiteFake(b), b, checks=[check_teams]).findings
    assert f.code == "teams" and f.level == BLOCKING and name in f.examples[0]
    assert set(f.affected_patrols) == {
        p["uuid"] for p in b.patrols if p.get("team_uuid")
    }
