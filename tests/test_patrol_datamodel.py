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
        self.schema = doc
        return {"id": "s"}

    def get_patrol_config(self):
        return self.config if self.flag else None

    def list_subjects(self):
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
    assert (s.subjects_created, s.subjects_updated, s.subjects_unchanged) == (7, 0, 0)
    created = [c[1] for c in api.calls if c[0] == "create_subject"]
    assert created[0]["subject_subtype"] == "ranger"
    assert created[0]["additional"]["smart_member_id"] == b.reference.employees[0].uuid
    assert (
        created[0]["additional"]["smart_ca_uuid"] == b.ca_uuid
        and created[0]["is_active"] is True
    )
    assert s.subjects_grouped == 7 and api.group_subject_ids("g1") == set(
        r.subject_ids.values()
    )
    assert [
        c.code for c in r.checklist
    ] == []  # nothing left for the admin on this site
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
    ) == (2, 7, 0)
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
                "additional": {"smart_employee_id": e1.id, "ca_uuid": "legacy"},
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
    assert (
        patched["old-2"]["additional"]["ca_uuid"] == "legacy"
    )  # existing keys preserved
    assert (r.stats.subjects_created, r.stats.subjects_updated) == (5, 2)
    assert r.subject_ids[e0.uuid] == "old-1" and r.subject_ids[e1.uuid] == "old-2"
    # the demo references a team SMART's table lacks, and no member group is configured
    assert [c.code for c in r.checklist] == ["team", "roster_group"]


def test_flag_off_skips_schema_with_checklist(no_choice_writes):
    b = load_bundle(FIXTURE)
    api = FakeAPI(flag=False)
    r = run_patrol_datamodel(api, MagicMock(), b)
    assert r.stats.types_created == 2 and r.stats.subjects_created == 7
    assert r.stats.schema_skipped == "patrol_schemas preview feature is off"
    assert not any(c[0] == "put_schema" for c in api.calls)
    assert [c.code for c in r.checklist] == [
        "enable_patrol_schemas",
        "team",
        "roster_group",
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
    assert r.stats.subjects_grouped == 7 and api.group_subject_ids("g9") == set(
        r.subject_ids.values()
    )
    codes = [c.code for c in r.checklist]
    assert codes == ["team"] and r.checklist[0].title.endswith("Team One")
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
