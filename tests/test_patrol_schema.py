from __future__ import annotations

import json
import shutil
from pathlib import Path

from er_smart_sync.bundle import load_bundle
from er_smart_sync.smart_to_er_patrol_schema import (
    CHOICE_FIELD_CLASS,
    CHOICE_FIELD_MANDATE,
    CHOICE_FIELD_STATION,
    build_patrol_choice_sets,
    build_patrol_types,
    build_subjects,
    build_team_requirements,
    custom_choice_field,
    patrol_type_value,
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
    assert (
        patrol_type_value("Others", taken) == "patrol_others_2"
    )  # same key twice stays unique
    long = patrol_type_value("a" * 80, taken)
    assert len(long) <= 50 and long.startswith("patrol_")
    assert patrol_type_value("a" * 80, taken) != long


def test_build_patrol_types(tmp_path):
    def edit(n, d):
        if n == "reference":
            d["transports"].append(
                {
                    "uuid": "11111111-1111-1111-1111-111111111111",
                    "key": "boat",
                    "labels": {"en": "Boat"},
                    "label": "Boat",
                    "patrol_type": "MARINE",
                    "active": False,
                }
            )

    b = _bundle_with(tmp_path, edit)
    plans = build_patrol_types(b)
    assert [(p.value, p.display, p.is_active) for p in plans] == [
        ("patrol_foot", "Foot", True),
        ("patrol_vehicle", "Vehicle", True),
        ("patrol_boat", "Boat", False),
    ]
    assert plans[0].transport_uuid == b.reference.transports[0].uuid


def test_build_choice_sets():
    b = load_bundle(FIXTURE)
    sets = {cs.field: cs for cs in build_patrol_choice_sets(b)}
    assert set(sets) == {CHOICE_FIELD_MANDATE, CHOICE_FIELD_STATION, CHOICE_FIELD_CLASS}
    assert [o.value for o in sets[CHOICE_FIELD_MANDATE].options] == [
        "antipoaching",
        "followup",
        "researchandmonitoring",
        "surveillance",
    ]
    assert sets[CHOICE_FIELD_MANDATE].options[0].display == b.label(
        b.reference.mandates[0]
    )
    # stations have no key: the value comes from the label; unlabelled ones are skipped
    assert [o.value for o in sets[CHOICE_FIELD_STATION].options] == ["headquarters"]
    assert [
        (o.value, o.display, o.is_active) for o in sets[CHOICE_FIELD_CLASS].options
    ] == [
        ("air", "Air", False),
        ("ground", "Ground", True),
        ("marine", "Marine", False),
        ("mixed", "Mixed", True),
    ]


def test_custom_attribute_choice_sets(tmp_path):
    def edit(n, d):
        if n == "reference":
            d["patrol_attributes"].append(
                {
                    "uuid": "22222222-2222-2222-2222-222222222222",
                    "key": "Weather Now",
                    "labels": {"en": "Weather"},
                    "label": "Weather",
                    "type": "LIST",
                    "active": True,
                    "options": [
                        {
                            "uuid": "33333333-3333-3333-3333-333333333333",
                            "key": "sunny",
                            "labels": {"en": "Sunny"},
                            "label": "Sunny",
                            "active": True,
                        },
                        {
                            "uuid": "44444444-4444-4444-4444-444444444444",
                            "key": "wet",
                            "labels": {"en": "Wet"},
                            "label": "Wet",
                            "active": False,
                        },
                    ],
                }
            )
            d["patrol_attributes"].append(
                {
                    "uuid": "55555555-5555-5555-5555-555555555555",
                    "key": "km",
                    "labels": {"en": "Km"},
                    "label": "Km",
                    "type": "NUMERIC",
                    "active": True,
                    "options": [],
                }
            )

    b = _bundle_with(tmp_path, edit)
    sets = {cs.field: cs for cs in build_patrol_choice_sets(b)}
    assert custom_choice_field("Weather Now") == "patrol_weather_now"
    assert [
        (o.value, o.display, o.is_active) for o in sets["patrol_weather_now"].options
    ] == [("sunny", "Sunny", True), ("wet", "Wet", False)]
    assert "patrol_km" not in sets  # NUMERIC attributes have no choices


def test_subject_names_and_duplicates(tmp_path):
    def edit(n, d):
        if n == "reference":
            d["employees"][1]["given_name"] = d["employees"][0]["given_name"]
            d["employees"][1]["family_name"] = d["employees"][0]["family_name"]
            d["employees"][2]["given_name"] = None
            d["employees"][2]["family_name"] = ""
            d["employees"][3]["active"] = False

    b = _bundle_with(tmp_path, edit)
    subjects = build_subjects(b)
    assert len(subjects) == 7  # every employee: 6 with memberships, 1 active without
    assert (
        subjects[0].name == subjects[1].name
        and subjects[0].smart_member_id != subjects[1].smart_member_id
    )
    assert (
        subjects[2].name == b.reference.employees[2].id
    )  # empty name falls back to the SMART id
    assert subjects[3].is_active is False
    assert all(s.smart_ca_uuid == b.ca_uuid for s in subjects)
    assert subjects[0].smart_employee_id == b.reference.employees[0].id


def test_subjects_include_members_without_employment(tmp_path):
    """A leg member missing from the employee list still gets a subject named by uuid."""
    ghost = "99999999-9999-9999-9999-999999999999"

    def edit(n, d):
        if n == "patrols":
            d["patrols"][0]["legs"][0]["members"].append(
                {"employee_uuid": ghost, "leader": False, "pilot": False}
            )

    b = _bundle_with(tmp_path, edit)
    subjects = {s.smart_member_id: s for s in build_subjects(b)}
    assert (
        ghost in subjects
        and subjects[ghost].name == ghost
        and subjects[ghost].is_active is False
    )


def test_team_requirements(tmp_path):
    team = "66666666-6666-6666-6666-666666666666"

    def edit(n, d):
        if n == "reference":
            d["teams"].append(
                {
                    "uuid": team,
                    "labels": {"en": "Team One"},
                    "label": "Team One",
                    "member_uuids": [
                        d["employees"][0]["uuid"],
                        d["employees"][1]["uuid"],
                    ],
                }
            )
        else:
            for p in d["patrols"]:
                p["team_uuid"] = None
            d["patrols"][0]["team_uuid"] = team
            d["patrols"][1]["team_uuid"] = team

    b = _bundle_with(tmp_path, edit)
    reqs = build_team_requirements(b)
    assert (
        len(reqs) == 1
        and reqs[0].name == "Team One"
        and reqs[0].referenced_by_patrols == 2
    )
    assert len(reqs[0].member_names) == 2


def test_team_requirements_for_unknown_team():
    """The demo patrols name a team absent from SMART's team table: still required."""
    b = load_bundle(FIXTURE)
    reqs = build_team_requirements(b)
    assert len(reqs) == 1
    (req,) = reqs
    assert (
        req.uuid in b.patrol_team_uuids()
        and req.name.startswith("SMART team ")
        and req.member_names == []
    )
    assert req.referenced_by_patrols == sum(1 for p in b.patrols if p.get("team_uuid"))
