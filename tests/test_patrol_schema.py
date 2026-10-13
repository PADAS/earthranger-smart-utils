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
    # stations have no key: identity is the uuid (stable across languages)
    stations = b.reference.stations
    expected = [st.uuid.replace("-", "") for st in stations]
    assert [o.value for o in sets[CHOICE_FIELD_STATION].options] == expected
    by_value = {o.value: o for o in sets[CHOICE_FIELD_STATION].options}
    assert by_value[stations[2].uuid.replace("-", "")].display == "Headquarters"
    assert by_value[stations[0].uuid.replace("-", "")].display == stations[0].uuid
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
    """A leg member missing from the employee list gets a subject named by uuid."""
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


# ── universal segment schema ────────────────────────────────────

import copy  # noqa: E402

from er_smart_sync.smart_to_er_patrol_schema import (  # noqa: E402
    CANONICAL_FIELDS,
    SEGMENT_SECTION,
    build_segment_schema,
    merge_segment_schema,
)


def test_segment_schema_shape():
    b = load_bundle(FIXTURE)
    doc = build_segment_schema(b)
    props = doc["json"]["properties"]
    assert list(props) == list(CANONICAL_FIELDS)
    assert doc["json"]["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert (
        doc["json"]["unevaluatedProperties"] is False and doc["json"]["required"] == []
    )
    # das's meta-schema requires `deprecated` on every property (verified live 2026-10-13)
    assert props["objective"] == {
        "type": "string",
        "title": "Objective",
        "description": "",
        "deprecated": False,
    }
    assert all(p["deprecated"] is False for p in props.values())
    assert props["armed"]["type"] == "boolean"
    assert props["mandate"]["anyOf"] == [
        {"$ref": "/api/v2.0/schemas/choices.json?field=patrol_mandate"}
    ]
    assert props["station"]["anyOf"][0]["$ref"].endswith("field=patrol_station")
    assert props["patrol_class"]["anyOf"][0]["$ref"].endswith("field=patrol_class")
    ui = doc["ui"]
    assert (
        ui["order"] == [SEGMENT_SECTION]
        and ui["sections"][SEGMENT_SECTION]["label"] == "Patrol details"
    )
    assert [f["name"] for f in ui["sections"][SEGMENT_SECTION]["leftColumn"]] == list(
        CANONICAL_FIELDS
    )
    assert ui["fields"]["comment"] == {
        "type": "TEXT",
        "inputType": "LONG_TEXT",
        "parent": SEGMENT_SECTION,
    }
    assert ui["fields"]["mandate"]["type"] == "CHOICE_LIST"
    # das strips ui.fields[*].choices on save (normalize_v2_schema); emitting it
    # would make every run see a difference and PUT again
    assert "choices" not in ui["fields"]["mandate"]


def test_segment_schema_custom_attributes(tmp_path):
    def edit(n, d):
        if n == "reference":
            d["patrol_attributes"] += [
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
                        }
                    ],
                },
                {
                    "uuid": "55555555-5555-5555-5555-555555555555",
                    "key": "km",
                    "labels": {"en": "Km walked"},
                    "label": "Km walked",
                    "type": "NUMERIC",
                    "active": True,
                    "options": [],
                },
                {
                    "uuid": "77777777-7777-7777-7777-777777777777",
                    "key": "old",
                    "labels": {"en": "Old"},
                    "label": "Old",
                    "type": "TEXT",
                    "active": False,
                    "options": [],
                },
            ]

    b = _bundle_with(tmp_path, edit)
    doc = build_segment_schema(b)
    props = doc["json"]["properties"]
    assert "smart_weather_now" in props
    assert props["smart_weather_now"]["anyOf"][0]["$ref"].endswith(
        "field=patrol_weather_now"
    )
    assert props["smart_km"] == {
        "type": "number",
        "title": "Km walked",
        "description": "",
        "deprecated": False,
    }
    assert "smart_old" not in props  # inactive attributes are left out
    names = [f["name"] for f in doc["ui"]["sections"][SEGMENT_SECTION]["leftColumn"]]
    assert names[-2:] == ["smart_weather_now", "smart_km"]


def test_merge_keeps_admin_fields_and_detects_no_change():
    b = load_bundle(FIXTURE)
    ours = build_segment_schema(b)
    assert merge_segment_schema(None, ours) == ours
    assert (
        merge_segment_schema({"json": {"properties": {}}, "ui": {}}, ours) == ours
    )  # empty envelope
    current = copy.deepcopy(ours)
    current["json"]["properties"]["radio_callsign"] = {
        "type": "string",
        "title": "Callsign",
        "description": "",
    }
    current["ui"]["fields"]["radio_callsign"] = {
        "type": "TEXT",
        "inputType": "SHORT_TEXT",
        "parent": SEGMENT_SECTION,
    }
    current["ui"]["sections"][SEGMENT_SECTION]["leftColumn"].append(
        {"name": "radio_callsign", "type": "field"}
    )
    current["ui"]["sections"]["section-2"] = {
        "label": "Extra",
        "columns": 1,
        "isActive": True,
        "leftColumn": [],
        "rightColumn": [],
    }
    current["ui"]["order"].append("section-2")
    current["json"]["properties"]["mandate"]["title"] = (
        "Old title"  # ours must win on canonical keys
    )
    merged = merge_segment_schema(current, ours)
    assert (
        "radio_callsign" in merged["json"]["properties"]
        and "radio_callsign" in merged["ui"]["fields"]
    )
    assert merged["json"]["properties"]["mandate"]["title"] == "Mandate"
    assert merged["ui"]["order"] == [SEGMENT_SECTION, "section-2"]
    names = [f["name"] for f in merged["ui"]["sections"][SEGMENT_SECTION]["leftColumn"]]
    assert names == list(CANONICAL_FIELDS) + ["radio_callsign"] and len(names) == len(
        set(names)
    )
    assert (
        merge_segment_schema(merged, ours) == merged
    )  # idempotent: a second merge changes nothing


def test_merge_keeps_admin_field_order():
    b = load_bundle(FIXTURE)
    ours = build_segment_schema(b)
    current = copy.deepcopy(ours)
    left = current["ui"]["sections"][SEGMENT_SECTION]["leftColumn"]
    left.reverse()  # the admin reordered the canonical fields
    del current["json"]["properties"][
        "armed"
    ]  # and one of ours is missing from the site
    del current["ui"]["fields"]["armed"]
    current["ui"]["sections"][SEGMENT_SECTION]["leftColumn"] = [
        f for f in left if f["name"] != "armed"
    ]
    merged = merge_segment_schema(current, ours)
    names = [f["name"] for f in merged["ui"]["sections"][SEGMENT_SECTION]["leftColumn"]]
    assert names == [
        "comment",
        "patrol_class",
        "station",
        "mandate",
        "objective",
        "armed",
    ]
    assert merge_segment_schema(merged, ours) == merged
