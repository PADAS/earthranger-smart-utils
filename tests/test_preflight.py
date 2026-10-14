from __future__ import annotations

import json
import shutil
from pathlib import Path
from unittest.mock import MagicMock

from er_smart_sync.bundle import load_bundle
from er_smart_sync.preflight import (
    ALL_CHECKS,
    BLOCKING,
    MAX_EXAMPLES,
    WARNING,
    Finding,
    PreflightReport,
    category_path,
    check_bundle_warnings,
    check_choices,
    check_event_types,
    check_feature_flag,
    check_inactive_members,
    check_language,
    check_leg_span,
    check_patrol_types,
    check_permissions,
    check_segment_schema,
    check_subjects,
    check_teams,
    check_time_coverage,
    check_timezone,
    render_json,
    render_text,
    resolve_event_types,
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


def test_finding_keeps_every_item_and_text_caps():
    """--json carries every item; the text report shows MAX_EXAMPLES and
    points at --json for the rest."""
    f = Finding.of(
        "x", BLOCKING, "m", "fix", [str(i) for i in range(25)], affected=["p1", "p2"]
    )
    assert f.count == 25 and len(f.examples) == 25
    assert f.affected_patrols == ["p1", "p2"]
    report = PreflightReport(findings=[f], context={})
    text = render_text(report)
    assert text.count("e.g. ") == MAX_EXAMPLES and "15 more (see --json)" in text
    assert len(json.loads(render_json(report))["blocking"][0]["examples"]) == 25


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
    assert by["bundle_warnings"].count == 30


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
                "teams": (
                    self.config_teams
                    if getattr(self, "config_teams", None) is not None
                    else self.resolved["teams"]
                ),
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


def _observed(b):
    return sorted(
        {
            o["category_hkey"]
            for p in b.patrols
            for leg in p["legs"]
            for w in leg["waypoints"]
            for g in w["observation_groups"]
            for o in g["observations"]
        }
    )


def test_category_path():
    assert category_path("threats.charcoal.") == "threats_charcoal"
    assert category_path("Animals.LiveAnimals") == "animals_liveanimals"


def test_event_types_all_present_and_map_kept():
    b = load_bundle(FIXTURE)
    ets = [
        {"value": f"smart-ca-import_{category_path(h)}", "is_active": True}
        for h in _observed(b)
    ]
    ctx_report = run_preflight(
        SiteFake(b, event_types=ets), b, checks=[check_event_types]
    )
    assert ctx_report.findings == []


def test_missing_event_types_block_the_patrols_that_use_them():
    b = load_bundle(FIXTURE)
    observed = _observed(b)
    ets = [
        {"value": f"smart-ca-import_{category_path(h)}", "is_active": True}
        for h in observed[1:]
    ]
    (f,) = run_preflight(
        SiteFake(b, event_types=ets), b, checks=[check_event_types]
    ).findings
    assert (
        f.code == "event_types"
        and f.level == BLOCKING
        and f.count == 1
        and observed[0] in f.examples[0]
    )
    assert f.affected_patrols and all(isinstance(p, str) for p in f.affected_patrols)


def test_ambiguous_event_types_need_a_prefix():
    b = load_bundle(FIXTURE)
    observed = _observed(b)
    ets = [
        {"value": f"smart-ca-import_{category_path(h)}", "is_active": True}
        for h in observed
    ]
    ets.append({"value": f"{CM_UUID}_{category_path(observed[0])}", "is_active": True})
    (f,) = run_preflight(
        SiteFake(b, event_types=ets), b, checks=[check_event_types]
    ).findings
    assert (
        f.code == "event_types"
        and f.level == BLOCKING  # no silent pick: the import would lack a type
        and "--event-type-prefix" in f.fix
        and f.affected_patrols
    )
    assert (
        run_preflight(
            SiteFake(b, event_types=ets),
            b,
            event_type_prefix="smart-ca-import",
            checks=[check_event_types],
        ).findings
        == []
    )
    chosen, ambiguous, unmatched, _ = resolve_event_types(b, ets, "smart-ca-import")
    assert (
        chosen[observed[0]] == f"smart-ca-import_{category_path(observed[0])}"
        and not ambiguous
        and not unmatched
    )


def test_render_text_and_json():
    b = load_bundle(FIXTURE)
    report = run_preflight(
        SiteFake(b, flag=False), b, checks=[check_feature_flag, check_timezone]
    )
    text = render_text(report)
    assert text.splitlines()[0].startswith(
        "Patrol preflight for Example Conservation Area (SMART)"
    )
    assert (
        "BLOCKING" in text
        and "[feature_flag]" in text
        and "fix:" in text
        and "WARNING" in text
    )
    assert "import-patrols will refuse to run" in text
    doc = json.loads(render_json(report))
    assert doc["exit_code"] == 2 and doc["blocking"][0]["code"] == "feature_flag"
    assert len(doc["blocking"][0]["affected_patrols"]) == len(b.patrols)


def test_all_checks_are_read_only():
    """Every check against a MagicMock client: no write method is ever called."""
    from er_smart_sync.er_patrol_api import ERPatrolAPI

    b = load_bundle(FIXTURE)
    client = MagicMock()
    client._get.return_value = {
        "preview_features": {"patrol_schemas": True},
        "is_superuser": True,
    }
    client.get_subjects.return_value = []
    client.get_event_types.return_value = []
    run_preflight(ERPatrolAPI(client), b, checks=ALL_CHECKS)
    written = [
        c
        for c in client.method_calls
        if c[0].startswith(
            ("post_", "patch_", "_post", "_patch", "_put", "delete_", "add_")
        )
    ]
    assert written == []


# ── review fixes ─────────────────────────────────────────────────

CM_UUID = "3b4cc64a-dcef-4422-9a44-00bce2a58901"


def test_prefix_separates_file_and_cm_pushes():
    b = load_bundle(FIXTURE)
    observed = _observed(b)
    ets = [
        {"value": f"smart-ca-import_{category_path(h)}", "is_active": True}
        for h in observed
    ]
    ets += [
        {"value": f"smart-ca-import_{CM_UUID}_{category_path(h)}", "is_active": True}
        for h in observed
    ]
    chosen, ambiguous, unmatched, _ = resolve_event_types(b, ets, None)
    assert len(ambiguous) == len(observed) and not chosen
    chosen, ambiguous, unmatched, _ = resolve_event_types(b, ets, "smart-ca-import")
    assert not ambiguous and not unmatched
    assert all(v == f"smart-ca-import_{category_path(h)}" for h, v in chosen.items())
    chosen, ambiguous, unmatched, _ = resolve_event_types(
        b, ets, f"smart-ca-import_{CM_UUID}"
    )
    assert not ambiguous and all(CM_UUID in v for v in chosen.values())


def test_no_silent_pick_of_a_longer_path():
    b = load_bundle(FIXTURE)
    observed = _observed(b)
    target = observed[0]  # e.g. animals.bushmeat.
    others = [
        {"value": f"smart-ca-import_{category_path(h)}", "is_active": True}
        for h in observed[1:]
    ]
    wrong = {
        "value": f"smart-ca-import_animals_{category_path(target)}",
        "is_active": True,
    }
    chosen, ambiguous, unmatched, _ = resolve_event_types(b, others + [wrong], None)
    assert target in unmatched and target not in chosen


def test_split_variants_get_their_own_finding():
    b = load_bundle(FIXTURE)
    observed = _observed(b)
    ets = [
        {"value": f"smart-ca-import_{category_path(h)}", "is_active": True}
        for h in observed[1:]
    ]
    ets.append(
        {
            "value": f"smart-ca-import_{CM_UUID}_{category_path(observed[0])}"
            "_ranger_form_0a1b2c3d",
            "is_active": True,
        }
    )
    _, _, unmatched, variants = resolve_event_types(b, ets, None)
    assert observed[0] in variants and observed[0] not in unmatched
    findings = run_preflight(
        SiteFake(b, event_types=ets), b, checks=[check_event_types]
    ).findings
    (f,) = findings
    assert f.level == BLOCKING and "--cm-variant-mode consolidate" in f.fix


def test_unmatched_fix_names_every_cause():
    b = load_bundle(FIXTURE)
    (f,) = run_preflight(
        SiteFake(b, event_types=[]), b, checks=[check_event_types]
    ).findings
    assert "parent" in f.fix and "inactive" in f.fix and "category permission" in f.fix


def test_permissions_follow_what_das_enforces():
    b = load_bundle(FIXTURE)
    observed = _observed(b)
    ets = [
        {
            "value": f"smart-ca-import_{category_path(h)}",
            "is_active": True,
            "category": "smartdemo",
        }
        for h in observed
    ]
    me = {
        "is_superuser": False,
        "permissions": {
            "patrol": ["add", "change", "view"],
            "eventrelatedsegments": ["add"],
            "smartdemo": ["add", "view"],
        },
    }
    assert (
        run_preflight(
            SiteFake(b, me=me, event_types=ets), b, checks=[check_permissions]
        ).findings
        == []
    )
    me["permissions"].pop("smartdemo")
    (f,) = run_preflight(
        SiteFake(b, me=me, event_types=ets), b, checks=[check_permissions]
    ).findings
    assert f.examples == ["smartdemo: add (event category)"]


def test_inactive_team_is_reported_as_inactive():
    b = load_bundle(FIXTURE)
    (req_uuid,) = b.patrol_team_uuids()
    name = f"SMART team {req_uuid[:8]}"
    site = SiteFake(b)
    site.config_teams = [{"value": "t", "display": name, "is_active": False}]
    (f,) = run_preflight(site, b, checks=[check_teams]).findings
    assert "inactive" in f.examples[0]


def test_inactive_event_type_resolves_with_a_warning():
    """datamodel --bundle creates inactive types for parent or retired
    categories; das accepts events against them."""
    b = load_bundle(FIXTURE)
    observed = _observed(b)
    ets = [
        {"value": f"smart-ca-import_{category_path(h)}", "is_active": True}
        for h in observed[1:]
    ]
    ets.append(
        {"value": f"smart-ca-import_{category_path(observed[0])}", "is_active": False}
    )
    chosen, ambiguous, unmatched, _ = resolve_event_types(b, ets, None)
    assert chosen[observed[0]] == f"smart-ca-import_{category_path(observed[0])}"
    (f,) = run_preflight(
        SiteFake(b, event_types=ets), b, checks=[check_event_types]
    ).findings
    assert f.level == WARNING and observed[0] in f.examples[0]


def test_active_event_type_wins_over_inactive():
    b = load_bundle(FIXTURE)
    observed = _observed(b)
    ets = [
        {"value": f"smart-ca-import_{category_path(h)}", "is_active": True}
        for h in observed
    ]
    ets.append({"value": f"{CM_UUID}_{category_path(observed[0])}", "is_active": False})
    chosen, ambiguous, _, _ = resolve_event_types(b, ets, None)
    assert not ambiguous and chosen[observed[0]].startswith("smart-ca-import_")


def _patrols_observing(b, hkey):
    return sorted(
        {
            p["uuid"]
            for p in b.patrols
            for leg in p["legs"]
            for w in leg["waypoints"]
            for g in w["observation_groups"]
            for o in g["observations"]
            if o.get("category_hkey") == hkey
        }
    )


def test_site_wide_blocker_blocks_every_patrol():
    b = load_bundle(FIXTURE)
    report = run_preflight(
        SiteFake(b), b, timezone="Europe/Paris", checks=[check_timezone]
    )
    assert report.blocking and report.blocked_patrols == {p["uuid"] for p in b.patrols}
    assert "skip the 0" not in render_text(report)


def test_report_carries_the_event_type_map():
    b = load_bundle(FIXTURE)
    observed = _observed(b)
    ets = [
        {"value": f"smart-ca-import_{category_path(h)}", "is_active": True}
        for h in observed
    ]
    report = run_preflight(SiteFake(b, event_types=ets), b, checks=[check_event_types])
    assert report.event_types_map[observed[0]] == (
        f"smart-ca-import_{category_path(observed[0])}"
    )
    doc = json.loads(render_json(report))
    assert doc["event_types_map"] == report.event_types_map


def test_category_permission_blocks_only_its_patrols():
    b = load_bundle(FIXTURE)
    observed = _observed(b)
    target = observed[0]
    ets = [
        {
            "value": f"smart-ca-import_{category_path(h)}",
            "is_active": True,
            "category": "cat_a" if h == target else "cat_b",
        }
        for h in observed
    ]
    me = {
        "is_superuser": False,
        "permissions": {
            "patrol": ["add", "change", "view"],
            "eventrelatedsegments": ["add"],
            "cat_b": ["add"],
        },
    }
    (f,) = run_preflight(
        SiteFake(b, me=me, event_types=ets), b, checks=[check_permissions]
    ).findings
    assert f.examples == ["cat_a: add (event category)"]
    assert f.affected_patrols == _patrols_observing(b, target)
    assert len(f.affected_patrols) < len(b.patrols)
