from __future__ import annotations

import json
import shutil
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from er_smart_sync.bundle import load_bundle
from er_smart_sync.choices import ChoicesStats
from er_smart_sync.er_patrol_api import ERForbidden
from er_smart_sync.patrol_datamodel import (
    format_summary,
    render_checklist,
    run_patrol_datamodel,
)

FIXTURE = Path(__file__).parent / "fixtures" / "smart_demo_bundle"
EMPTY_SCHEMA = {"json": {"properties": {}}, "ui": {}}
NO_TEAM_MEMBERS = {"teams": [], "members": []}


class FakeAPI:
    """In-memory stand-in for ERPatrolAPI with the state a sandbox would have."""

    def __init__(
        self,
        *,
        flag=True,
        schema=None,
        config=None,
        subjects=None,
        groups=None,
        forbid=(),
    ):
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
        if op in getattr(self, "reject", ()):
            from erclient.er_errors import ERClientException

            raise ERClientException(f"{op} rejected: 400")

    def patrol_schemas_enabled(self):
        return self.flag

    def get_patrol_type(self, value):
        return self.types.get(value)

    def create_patrol_type(self, payload):
        self._check("create_patrol_type")
        self.calls.append(("create_type", payload))
        self.types[payload["value"]] = {"id": self._id(), **payload}
        return self.types[payload["value"]]

    def patch_patrol_type(self, value, payload):
        self._check("patch_patrol_type")
        self.calls.append(("patch_type", value, payload))
        self.types[value].update(payload)
        return self.types[value]

    def get_segment_schema(self):
        return self.schema if self.flag else None

    def put_segment_schema(self, doc):
        self._check("put_segment_schema")
        self.calls.append(("put_schema", doc))
        # das normalises on save: ui.fields[*].choices is dropped
        # (das/schemas/normalization.py)
        import copy

        stored = copy.deepcopy(doc)
        for f in (stored.get("ui") or {}).get("fields", {}).values():
            f.pop("choices", None)
        self.schema = stored
        return {"id": "s"}

    def get_patrol_config(self):
        self._check("get_patrol_config")
        return self.config if self.flag else None

    def list_subjects(self):
        self._check("list_subjects")
        return [dict(s) for s in self.subjects]

    def create_subject(self, payload):
        self._check("create_subject")
        self.calls.append(("create_subject", payload))
        s = {"id": self._id(), **payload}
        self.subjects.append(s)
        return s

    def patch_subject(self, subject_id, payload):
        self._check("patch_subject")
        self.calls.append(("patch_subject", subject_id, payload))
        s = next(x for x in self.subjects if x["id"] == subject_id)
        s.update(payload)
        return s

    def find_subject_group(self, name):
        return next(
            (
                {"id": gid, "name": n}
                for gid, n in self.groups.items()
                if n.lower() == name.lower()
            ),
            None,
        )

    def group_subject_ids(self, group_id):
        return set(self.group_members.get(group_id, set()))

    def add_subjects_to_group(self, group_id, subject_ids):
        self._check("add_subjects_to_group")
        self.calls.append(("group", group_id, list(subject_ids)))
        self.group_members.setdefault(group_id, set()).update(subject_ids)


def _demo_with_team(tmp_path, team_label="Team One"):
    shutil.copytree(FIXTURE, tmp_path / "b")
    ref = json.loads((tmp_path / "b" / "reference.json").read_text())
    team = "66666666-6666-6666-6666-666666666666"
    ref["teams"].append(
        {
            "uuid": team,
            "labels": {"en": team_label},
            "label": team_label,
            "member_uuids": [ref["employees"][0]["uuid"]],
        }
    )
    (tmp_path / "b" / "reference.json").write_text(json.dumps(ref))
    pats = json.loads((tmp_path / "b" / "patrols.json").read_text())
    for p in pats["patrols"]:
        p["team_uuid"] = None
    pats["patrols"][0]["team_uuid"] = team
    (tmp_path / "b" / "patrols.json").write_text(json.dumps(pats))
    return load_bundle(tmp_path / "b"), ref


@pytest.fixture
def no_choice_writes():
    with patch(
        "er_smart_sync.patrol_datamodel.upsert_choices",
        return_value=ChoicesStats(created=3),
    ) as up:
        yield up


def test_fresh_site_creates_everything(no_choice_writes, tmp_path):
    b, _ = _demo_with_team(
        tmp_path
    )  # the demo's unknown team uuid is replaced by a known one below
    api = FakeAPI(
        flag=True,
        schema=EMPTY_SCHEMA,
        config={
            "teams": [{"id": "t", "value": "team_one", "display": "Team One"}],
            "members": [{"id": "g1", "name": "Rangers"}],
        },
        groups={"g1": "Rangers"},
    )
    r = run_patrol_datamodel(api, MagicMock(), b)
    s = r.stats
    assert (s.types_created, s.types_updated, s.types_unchanged) == (2, 0, 0)
    assert (
        api.types["patrol_foot"]["display"] == "Foot"
        and api.types["patrol_foot"]["is_active"] is True
    )
    assert s.schema_written and not s.schema_unchanged and s.schema_skipped is None
    assert "mandate" in api.schema["json"]["properties"]
    assert no_choice_writes.call_count == 1 and s.choices.created == 3
    # six rangers; the `smart` admin login never patrolled and is not a subject
    assert (s.subjects_created, s.subjects_updated, s.subjects_unchanged) == (6, 0, 0)
    assert s.logins_skipped == ["smart smart"] and s.subjects_deactivated == 0
    created = [c[1] for c in api.calls if c[0] == "create_subject"]
    assert created[0]["subject_subtype"] == "ranger"
    assert created[0]["additional"]["smart_member_id"] == b.reference.employees[0].uuid
    assert (
        created[0]["additional"]["smart_ca_uuid"] == b.ca_uuid
        and created[0]["is_active"] is True
    )
    assert s.subjects_grouped == 6 and api.group_subject_ids("g1") == set(
        r.subject_ids.values()
    )
    assert [c.code for c in r.checklist] == [
        "patrol_type_icons"
    ]  # the API cannot set icons
    assert "patrol_foot" in r.checklist[0].steps[0]
    assert "patrol types: 2 created" in format_summary(r)


def test_second_run_is_a_no_op(no_choice_writes):
    b = load_bundle(FIXTURE)
    api = FakeAPI(
        flag=True,
        schema=EMPTY_SCHEMA,
        config={"teams": [], "members": [{"id": "g1", "name": "Rangers"}]},
        groups={"g1": "Rangers"},
    )
    run_patrol_datamodel(api, MagicMock(), b)
    api.calls.clear()
    r = run_patrol_datamodel(api, MagicMock(), b)
    assert api.calls == []
    assert (
        r.stats.types_unchanged,
        r.stats.subjects_unchanged,
        r.stats.subjects_grouped,
    ) == (2, 6, 0)
    assert r.stats.schema_unchanged and not r.stats.schema_written


def test_updates_and_legacy_subject_match(no_choice_writes):
    b = load_bundle(FIXTURE)
    e0, e1 = b.reference.employees[0], b.reference.employees[1]
    api = FakeAPI(
        flag=True,
        schema=EMPTY_SCHEMA,
        config=NO_TEAM_MEMBERS,
        subjects=[
            {
                "id": "old-1",
                "name": "Renamed Person",
                "additional": {
                    "smart_member_id": e0.uuid,
                    "smart_employee_id": e0.id,
                    "smart_ca_uuid": b.ca_uuid,
                },
                "is_active": True,
            },
            {
                "id": "old-2",
                "name": f"{e1.given_name} {e1.family_name}",
                # as the SMART Connect sync wrote it: `ca_uuid`, hyphens optional
                "additional": {
                    "smart_employee_id": e1.id,
                    "ca_uuid": b.ca_uuid.replace("-", ""),
                },
                "is_active": True,
            },
        ],
    )
    api.types["patrol_foot"] = {
        "id": "t",
        "value": "patrol_foot",
        "display": "Walk",
        "is_active": False,
    }
    r = run_patrol_datamodel(api, MagicMock(), b)
    assert (r.stats.types_created, r.stats.types_updated) == (1, 1)
    assert (
        "patch_type",
        "patrol_foot",
        {"display": "Foot", "is_active": True},
    ) in api.calls
    patched = {c[1]: c[2] for c in api.calls if c[0] == "patch_subject"}
    assert patched["old-1"]["name"] == f"{e0.given_name} {e0.family_name}"
    assert (
        patched["old-2"]["additional"]["smart_member_id"] == e1.uuid
    )  # legacy match now carries the uuid
    assert patched["old-2"]["additional"]["ca_uuid"] == b.ca_uuid.replace(
        "-", ""
    )  # existing keys preserved
    assert (r.stats.subjects_created, r.stats.subjects_updated) == (4, 2)
    assert r.subject_ids[e0.uuid] == "old-1" and r.subject_ids[e1.uuid] == "old-2"
    # the demo references a team SMART's table lacks, and no member group is configured
    assert [c.code for c in r.checklist] == [
        "team",
        "roster_group",
        "patrol_type_icons",
    ]


def test_flag_off_skips_schema_with_checklist(no_choice_writes):
    b = load_bundle(FIXTURE)
    api = FakeAPI(flag=False)
    r = run_patrol_datamodel(api, MagicMock(), b)
    assert r.stats.types_created == 2 and r.stats.subjects_created == 6
    assert r.stats.schema_skipped == "patrol_schemas preview feature is off"
    assert not any(c[0] == "put_schema" for c in api.calls)
    assert [c.code for c in r.checklist] == [
        "enable_patrol_schemas",
        "team",
        "roster_group",
        "patrol_type_icons",
    ]
    md = render_checklist(r, b)
    assert "patrol_schemas" in md and "## 1. Enable" in md


def test_forbidden_steps_become_checklist_items(no_choice_writes):
    b = load_bundle(FIXTURE)
    api = FakeAPI(
        flag=True,
        schema=EMPTY_SCHEMA,
        config=NO_TEAM_MEMBERS,
        forbid={"put_segment_schema", "create_patrol_type"},
    )
    r = run_patrol_datamodel(api, MagicMock(), b)
    assert r.stats.types_created == 0 and r.stats.errors
    assert {c.code for c in r.checklist} >= {
        "permission_patrol_types",
        "permission_segment_schema",
        "roster_group",
    }


def test_teams_and_named_roster_group(no_choice_writes, tmp_path):
    b, ref = _demo_with_team(tmp_path)
    api = FakeAPI(
        flag=True,
        schema=EMPTY_SCHEMA,
        config={
            "teams": [{"id": "x", "value": "other", "display": "Other"}],
            "members": [],
        },
        groups={"g9": "Field staff"},
    )
    r = run_patrol_datamodel(api, MagicMock(), b, roster_group="field staff")
    assert r.stats.subjects_grouped == 6 and api.group_subject_ids("g9") == set(
        r.subject_ids.values()
    )
    codes = [c.code for c in r.checklist]
    assert codes == ["team", "patrol_type_icons"] and r.checklist[0].title.endswith(
        "Team One"
    )
    md = render_checklist(r, b)
    assert (
        "Team One" in md
        and ref["employees"][0]["given_name"] in md
        and "1 patrol" in md
    )


def test_replace_segment_schema(no_choice_writes):
    b = load_bundle(FIXTURE)
    current = {
        "json": {
            "properties": {
                "radio": {"type": "string", "title": "Radio", "description": ""}
            }
        },
        "ui": {
            "fields": {
                "radio": {
                    "type": "TEXT",
                    "inputType": "SHORT_TEXT",
                    "parent": "section-1",
                }
            },
            "headers": {},
            "order": ["section-1"],
            "sections": {
                "section-1": {
                    "label": "x",
                    "columns": 1,
                    "isActive": True,
                    "leftColumn": [{"name": "radio", "type": "field"}],
                    "rightColumn": [],
                }
            },
        },
    }
    api = FakeAPI(flag=True, schema=current, config=NO_TEAM_MEMBERS)
    run_patrol_datamodel(api, MagicMock(), b)
    assert "radio" in api.schema["json"]["properties"]  # merged by default
    api2 = FakeAPI(flag=True, schema=current, config=NO_TEAM_MEMBERS)
    run_patrol_datamodel(api2, MagicMock(), b, replace_segment_schema=True)
    assert "radio" not in api2.schema["json"]["properties"]


def test_read_side_forbidden_becomes_checklist_not_traceback(no_choice_writes):
    b = load_bundle(FIXTURE)
    api = FakeAPI(
        flag=True,
        schema=EMPTY_SCHEMA,
        config=NO_TEAM_MEMBERS,
        forbid={"list_subjects", "get_patrol_config"},
    )
    r = run_patrol_datamodel(api, MagicMock(), b)
    assert r.stats.subjects_created == 0
    assert {c.code for c in r.checklist} >= {
        "permission_subjects",
        "permission_patrol_config",
    }
    assert any("subjects" in e for e in r.stats.errors)
    assert any("patrol config" in e for e in r.stats.errors)


def test_schema_put_rejection_is_reported(no_choice_writes):
    b = load_bundle(FIXTURE)
    api = FakeAPI(flag=True, schema=EMPTY_SCHEMA, config=NO_TEAM_MEMBERS)
    api.reject = {"put_segment_schema"}
    r = run_patrol_datamodel(api, MagicMock(), b)
    assert r.stats.schema_skipped.startswith("rejected") and not r.stats.schema_written
    assert any("segment schema" in e for e in r.stats.errors)


def test_choice_errors_count_as_errors():
    b = load_bundle(FIXTURE)
    api = FakeAPI(flag=True, schema=EMPTY_SCHEMA, config=NO_TEAM_MEMBERS)
    with patch(
        "er_smart_sync.patrol_datamodel.upsert_choices",
        return_value=ChoicesStats(created=1, errored=2),
    ):
        r = run_patrol_datamodel(api, MagicMock(), b)
    assert any("2 choice" in e for e in r.stats.errors)


def test_previous_segment_schema_is_saved_before_overwrite(no_choice_writes, tmp_path):
    b = load_bundle(FIXTURE)
    current = {
        "json": {
            "properties": {
                "radio": {"type": "string", "title": "Radio", "description": ""}
            }
        },
        "ui": {"fields": {}, "headers": {}, "order": [], "sections": {}},
    }
    api = FakeAPI(flag=True, schema=current, config=NO_TEAM_MEMBERS)
    r = run_patrol_datamodel(api, MagicMock(), b, schema_backup_dir=tmp_path)
    backups = sorted(tmp_path.glob("segment-schema-before-*.json"))
    assert len(backups) == 1 and json.loads(backups[0].read_text()) == current
    assert r.stats.schema_backup == str(backups[0])
    # a no-op run writes no backup
    api.calls.clear()
    r2 = run_patrol_datamodel(api, MagicMock(), b, schema_backup_dir=tmp_path)
    assert (
        r2.stats.schema_unchanged
        and len(list(tmp_path.glob("segment-schema-before-*.json"))) == 1
    )
    assert r2.stats.schema_backup is None


def test_subject_an_earlier_run_made_for_a_login_is_deactivated(no_choice_writes):
    """gundi-dev got a `smart smart` ranger before logins were skipped."""
    b = load_bundle(FIXTURE)
    smart = next(
        e
        for e in b.reference.employees
        if e.has_smart_login and e.given_name == "smart"
    )
    stale = {
        "id": "stale",
        "name": "smart smart",
        "is_active": True,
        "additional": {
            "smart_member_id": smart.uuid,
            "smart_employee_id": smart.id,
            "smart_ca_uuid": b.ca_uuid,
        },
    }
    other_ca = {
        "id": "other",
        "name": "Elsewhere",
        "is_active": True,
        "additional": {"smart_member_id": "x", "smart_ca_uuid": "another-ca"},
    }
    api = FakeAPI(
        flag=True,
        schema=EMPTY_SCHEMA,
        config=NO_TEAM_MEMBERS,
        subjects=[stale, other_ca],
    )
    r = run_patrol_datamodel(api, MagicMock(), b)
    assert ("patch_subject", "stale", {"is_active": False}) in api.calls
    assert not any(c[0] == "patch_subject" and c[1] == "other" for c in api.calls)
    assert r.stats.subjects_deactivated == 1 and "stale" not in r.subject_ids.values()
    summary = format_summary(r)
    assert "1 deactivated" in summary
    assert (
        "SMART logins that never patrolled, not made subjects: smart smart" in summary
    )
    api.calls.clear()
    r = run_patrol_datamodel(api, MagicMock(), b)
    assert r.stats.subjects_deactivated == 0 and not any(
        c[0] == "patch_subject" for c in api.calls
    )


def test_unnamed_stations_in_summary_and_checklist(no_choice_writes, tmp_path):
    shutil.copytree(FIXTURE, tmp_path / "b")
    pats = json.loads((tmp_path / "b" / "patrols.json").read_text())
    pats["patrols"][0]["station_uuid"] = "11f95b3a-ec44-4092-a266-d5e391c7bbce"
    (tmp_path / "b" / "patrols.json").write_text(json.dumps(pats))
    b = load_bundle(tmp_path / "b")
    api = FakeAPI(flag=True, schema=EMPTY_SCHEMA, config=NO_TEAM_MEMBERS)
    r = run_patrol_datamodel(api, MagicMock(), b)
    assert (r.stats.stations_placeholder, r.stats.stations_inactive) == (2, 1)
    assert "2 given a placeholder name, 1 of them inactive" in format_summary(r)
    item = next(c for c in r.checklist if c.code == "station_names")
    assert "`Unnamed station 11f95b3a`" in item.steps[0]
    assert [c.code for c in r.checklist][-1] == "station_names"


def test_choices_never_deactivate_options_this_bundle_does_not_name(no_choice_writes):
    b = load_bundle(FIXTURE)
    api = FakeAPI(flag=True, schema=EMPTY_SCHEMA, config=NO_TEAM_MEMBERS)
    run_patrol_datamodel(api, MagicMock(), b)
    assert no_choice_writes.call_args.kwargs["deactivate_orphans"] is False


def test_legacy_match_never_reclaims_another_employees_subject(no_choice_writes):
    """SMART employee ids repeat; a subject that already has a smart_member_id is
    never matched by id, and an id shared by two legacy subjects matches neither."""
    b = load_bundle(FIXTURE)
    e0, e1 = b.reference.employees[0], b.reference.employees[1]
    owned = {
        "id": "owned",
        "name": "Someone Else",
        "is_active": True,
        "additional": {
            "smart_member_id": "other-employee",
            "smart_employee_id": e0.id,
            "smart_ca_uuid": "another-ca",
        },
    }
    twins = [
        {
            "id": f"twin-{n}",
            "name": f"Twin {n}",
            "is_active": True,
            "additional": {"smart_employee_id": e1.id},
        }
        for n in (1, 2)
    ]
    api = FakeAPI(
        flag=True, schema=EMPTY_SCHEMA, config=NO_TEAM_MEMBERS, subjects=[owned, *twins]
    )
    r = run_patrol_datamodel(api, MagicMock(), b)
    assert not any(c[0] == "patch_subject" for c in api.calls)
    assert r.subject_ids[e0.uuid] not in ("owned",)
    assert r.subject_ids[e1.uuid] not in ("twin-1", "twin-2")
    assert r.stats.subjects_created == 6


def test_a_failing_step_does_not_stop_the_next(no_choice_writes):
    b = load_bundle(FIXTURE)
    api = FakeAPI(flag=True, schema=EMPTY_SCHEMA, config=NO_TEAM_MEMBERS)

    def boom(value):
        raise RuntimeError("network down")

    api.get_patrol_type = boom
    r = run_patrol_datamodel(api, MagicMock(), b)
    assert r.stats.errors == ["patrol types: RuntimeError: network down"]
    assert r.stats.schema_written and r.stats.subjects_created == 6


@pytest.mark.parametrize(
    "additional",
    [
        {"ca_uuid": "another-ca"},  # another conservation area's employee
        {},  # area unknown: never claimed
    ],
)
def test_legacy_match_needs_this_bundles_area(no_choice_writes, additional):
    b = load_bundle(FIXTURE)
    e0 = b.reference.employees[0]
    legacy = {
        "id": "legacy",
        "name": "Someone",
        "is_active": True,
        "additional": {"smart_employee_id": e0.id, **additional},
    }
    api = FakeAPI(
        flag=True, schema=EMPTY_SCHEMA, config=NO_TEAM_MEMBERS, subjects=[legacy]
    )
    r = run_patrol_datamodel(api, MagicMock(), b)
    assert r.subject_ids[e0.uuid] != "legacy"
    assert not any(c[0] == "patch_subject" for c in api.calls)


def test_run_record_lists_every_write_with_what_it_replaced(no_choice_writes, tmp_path):
    from er_smart_sync.patrol_datamodel import write_run_record

    no_choice_writes.return_value = ChoicesStats(
        created=1,
        changes=[
            {"action": "created", "field": "patrol_mandate", "value": "x", "id": "c1"}
        ],
    )
    b = load_bundle(FIXTURE)
    e0 = b.reference.employees[0]
    api = FakeAPI(
        flag=True,
        schema=EMPTY_SCHEMA,
        config={"teams": [], "members": [{"id": "g1", "name": "Rangers"}]},
        groups={"g1": "Rangers"},
        subjects=[
            {
                "id": "old",
                "name": "Old Name",
                "is_active": True,
                "additional": {
                    "smart_member_id": e0.uuid,
                    "smart_employee_id": e0.id,
                    "smart_ca_uuid": b.ca_uuid,
                },
            }
        ],
    )
    r = run_patrol_datamodel(api, MagicMock(), b, schema_backup_dir=tmp_path)
    by_kind: dict = {}
    for c in r.stats.changes:
        by_kind.setdefault(c["kind"], []).append(c)
    assert [c["value"] for c in by_kind["patrol_type"]] == [
        "patrol_foot",
        "patrol_vehicle",
    ]
    assert by_kind["universal_patrol_fields"][0]["before_file"] == r.stats.schema_backup
    assert by_kind["choice"] == [
        {
            "kind": "choice",
            "action": "created",
            "field": "patrol_mandate",
            "value": "x",
            "id": "c1",
        }
    ]
    created = [c["id"] for c in by_kind["subject"] if c["action"] == "created"]
    assert len(created) == 5
    assert {
        "kind": "subject",
        "action": "updated",
        "id": "old",
        "before": {"name": "Old Name"},
    } in by_kind["subject"]
    # only the members this run added, never ones already in the group
    assert by_kind["roster"][0]["subject_ids"] == sorted(r.subject_ids.values())
    path = write_run_record(r, b, "https://x/api/v1.0", tmp_path)
    doc = json.loads(path.read_text())
    assert (
        doc["changes"] == r.stats.changes and doc["conservation_area"]["id"] == "SMART"
    )
    no_choice_writes.return_value = ChoicesStats()
    again = run_patrol_datamodel(api, MagicMock(), b, schema_backup_dir=tmp_path)
    assert (
        again.stats.changes == [] and write_run_record(again, b, "x", tmp_path) is None
    )


def test_backups_in_the_same_instant_never_overwrite(tmp_path):
    from er_smart_sync.patrol_datamodel import unique_path

    a = unique_path(tmp_path, "segment-schema-before", ".json")
    a.write_text("{}")
    with patch("er_smart_sync.patrol_datamodel.datetime") as dt:
        dt.now.return_value.strftime.return_value = a.name[
            len("segment-schema-before-") : -5
        ]
        b = unique_path(tmp_path, "segment-schema-before", ".json")
    assert b != a and b.name.endswith("-2.json")
