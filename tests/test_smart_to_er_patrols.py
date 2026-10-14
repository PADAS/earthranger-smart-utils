from pathlib import Path
from zoneinfo import ZoneInfo

from er_smart_sync.bundle import load_bundle
from er_smart_sync.patrol_ids import er_id, namespace
from er_smart_sync.patrol_times import leg_times, tracks_by_day
from er_smart_sync.smart_to_er_patrol_schema import build_patrol_types
from er_smart_sync.smart_to_er_patrols import (
    Resolver,
    build_patrol,
    build_track_batches,
    build_waypoint,
    event_value,
    track_member,
)

FIXTURE = Path(__file__).parent / "fixtures" / "smart_demo_bundle"
TZ = ZoneInfo("Africa/Libreville")
NS = namespace("https://site.pamdas.org/api/v1.0", "ca")


def _resolver(b):
    members = {
        m["employee_uuid"]
        for p in b.patrols
        for leg in p["legs"]
        for m in leg["members"]
    }
    return Resolver(
        subjects={m: f"subj-{m[:8]}" for m in members},
        teams={t: f"team-{t[:8]}" for t in b.patrol_team_uuids()},
        patrol_types={p.transport_uuid: p.value for p in build_patrol_types(b)},
        event_types={},
    )


def _plan(b, i):
    return build_patrol(
        b, b.patrols[i], NS, _resolver(b), TZ, tracks_by_day(b.tracks), "test"
    )


def test_patrol_payload_shape():
    b = load_bundle(FIXTURE)
    p = b.patrols[2]  # SMART_000001: team, comment
    plan = _plan(b, 2)
    body = plan.patrol
    assert body["id"] == er_id(NS, "patrol", p["uuid"])
    assert body["title"] == "SMART_000001" and body["priority"] == 0
    assert "state" not in body  # das marks past patrols done
    assert body["notes"] == [{"id": er_id(NS, "note", p["uuid"]), "text": p["comment"]}]
    (seg,) = body["patrol_segments"]
    leg = p["legs"][0]
    assert seg["id"] == er_id(NS, "segment", leg["uuid"])
    assert seg["team"] == f"team-{p['team_uuid'][:8]}"
    leader = next(m for m in leg["members"] if m["leader"])
    assert seg["leader"] == {
        "content_type": "observations.subject",
        "id": f"subj-{leader['employee_uuid'][:8]}",
    }
    assert len(seg["members"]) == len(leg["members"])
    assert seg["time_range"]["start_time"] < seg["time_range"]["end_time"]
    assert "end_time" not in seg


def test_segment_details_carry_fields_and_provenance():
    b = load_bundle(FIXTURE)
    seg = _plan(b, 0).patrol["patrol_segments"][0]
    d = seg["segment_details"]
    assert d["patrol_class"] == "ground" and d["armed"] is False
    assert "mandate" in d
    prov = d["smart_"]
    assert prov["patrol_id"] == "SMART_000002" and prov["leg_id"] == "1"
    assert prov["ca_uuid"] == b.ca_uuid and prov["importer_version"] == "test"
    assert set(prov["time_source"].values()) == {"track"}
    assert "track_times" not in prov


def test_two_legs_two_segments_in_order():
    b = load_bundle(FIXTURE)
    segs = _plan(b, 4).patrol["patrol_segments"]  # SMART_000005
    assert [s["segment_details"]["smart_"]["leg_id"] for s in segs] == [
        "1- Group A",
        "1- Group B",
    ]


def test_leg_without_leader_or_members():
    b = load_bundle(FIXTURE)
    p = dict(b.patrols[0])
    p["legs"] = [dict(p["legs"][0], members=[])]
    seg = build_patrol(b, p, NS, _resolver(b), TZ, {}, "t").patrol["patrol_segments"][0]
    assert "leader" not in seg and seg["members"] == []
    p["legs"] = [
        dict(
            p["legs"][0],
            members=[dict(m, leader=False) for m in b.patrols[0]["legs"][0]["members"]],
        )
    ]
    seg = build_patrol(b, p, NS, _resolver(b), TZ, {}, "t").patrol["patrol_segments"][0]
    assert "leader" not in seg and len(seg["members"]) == 4


def _wps(b):
    return [w for p in b.patrols for leg in p["legs"] for w in leg["waypoints"]]


def _ev_resolver(b, collection="incident_collection"):
    r = _resolver(b)
    r.event_types = {
        h: f"smart-ca-import_{h.strip('.').replace('.', '_')}"
        for h in b.observed_category_hkeys()
    }
    r.collection_type = collection
    return r


def test_event_values_follow_the_choice_rules():
    assert event_value({"type": "LIST", "value": "Fresh Sign"}) == "fresh_sign"
    assert event_value({"type": "TREE", "value": "a.b.c"}) == "a_b_c"
    assert event_value({"type": "MLIST", "value": ["x y", "z"]}) == ["x_y", "z"]
    assert event_value({"type": "NUMERIC", "value": 3.0}) == 3.0
    assert event_value({"type": "BOOLEAN", "value": True}) is True


def test_single_observation_waypoint():
    b = load_bundle(FIXTURE)
    w = next(
        w
        for w in _wps(b)
        if sum(len(g["observations"]) for g in w["observation_groups"]) == 1
    )
    obs = w["observation_groups"][0]["observations"][0]
    plan = build_waypoint(b, w, "seg", NS, _ev_resolver(b), None)
    assert plan.parent is None
    (ev,) = plan.events
    assert ev["id"] == er_id(NS, "event", obs["uuid"])
    assert ev["patrol_segments"] == ["seg"] and ev["time"] == w["time"]
    assert ev["location"] == {
        "latitude": w["location"][1],
        "longitude": w["location"][0],
    }
    assert ev["event_details"]["smart_"]["observation_uuid"] == obs["uuid"]
    assert "end_time" not in ev and "collection" not in ev


def test_multi_observation_waypoint_becomes_a_collection():
    b = load_bundle(FIXTURE)
    w = next(
        w
        for w in _wps(b)
        if sum(len(g["observations"]) for g in w["observation_groups"]) > 1
    )
    plan = build_waypoint(b, w, "seg", NS, _ev_resolver(b), None)
    assert plan.parent["id"] == er_id(NS, "incident", w["uuid"])
    assert plan.parent["event_type"] == "incident_collection"
    assert all(e["collection"] == {"id": plan.parent["id"]} for e in plan.events)
    ungrouped = build_waypoint(b, w, "seg", NS, _ev_resolver(b, None), None)
    assert ungrouped.parent is None and all(
        "collection" not in e for e in ungrouped.events
    )


def test_waypoint_without_time_or_location():
    b = load_bundle(FIXTURE)
    w = next(
        w
        for w in _wps(b)
        if w["observation_groups"] and w["observation_groups"][0]["observations"]
    )
    w = dict(w, time=None, location=None)
    plan = build_waypoint(b, w, "seg", NS, _ev_resolver(b), "2012-03-17T08:00:00+01:00")
    assert all(e["time"] == "2012-03-17T08:00:00+01:00" for e in plan.events)
    assert all("location" not in e for e in plan.events)


def test_observation_without_event_type_is_reported_not_posted():
    b = load_bundle(FIXTURE)
    w = next(
        w
        for w in _wps(b)
        if w["observation_groups"] and w["observation_groups"][0]["observations"]
    )
    r = _ev_resolver(b)
    r.event_types = {}
    plan = build_waypoint(b, w, "seg", NS, r, None)
    assert plan.events == [] and len(plan.unmapped) >= 1


def test_waypoint_without_observations_is_skipped():
    b = load_bundle(FIXTURE)
    w = next(
        w
        for w in _wps(b)
        if not any(g["observations"] for g in w["observation_groups"])
    )
    assert build_waypoint(b, w, "seg", NS, _ev_resolver(b), None) is None


def test_demo_tracks_use_recorded_times():
    b = load_bundle(FIXTURE)
    leg = b.patrols[0]["legs"][0]
    by_day = tracks_by_day(b.tracks)
    batches = build_track_batches(leg, leg_times(leg, by_day, TZ), by_day)
    leader = next(m["employee_uuid"] for m in leg["members"] if m["leader"])
    assert batches and all(
        x.member_uuid == leader and not x.interpolated for x in batches
    )
    first = batches[0].observations[0]
    track = by_day[batches[0].leg_day_uuid][0]
    assert first["recorded_at"] == track["parts"][0]["times"][0]
    lon, lat = track["parts"][0]["coordinates"][0]
    assert first["location"] == {"latitude": lat, "longitude": lon}
    assert first["additional"]["smart_leg_day_uuid"] == batches[0].leg_day_uuid


def test_mismatched_times_are_interpolated_over_the_day():
    day = {
        "uuid": "d1",
        "date": "2020-01-01",
        "start_time": None,
        "end_time": None,
        "rest_minutes": None,
        "start": "2020-01-01T08:00:00+01:00",
        "end": "2020-01-01T10:00:00+01:00",
        "has_track": True,
    }
    leg = {
        "uuid": "L",
        "id": "1",
        "placeholder_dates": False,
        "start_date": "2020-01-01",
        "end_date": "2020-01-01",
        "days": [day],
        "waypoints": [],
        "members": [{"employee_uuid": "m1", "leader": True, "pilot": False}],
    }
    track = {
        "uuid": "t",
        "leg_day_uuid": "d1",
        "parts": [
            {
                "coordinates": [[0, 0], [1, 1], [2, 2]],
                "times": ["2020-01-01T08:00:00+01:00"],
            }
        ],
    }
    by_day = tracks_by_day([track])
    (batch,) = build_track_batches(leg, leg_times(leg, by_day, TZ), by_day)
    assert batch.interpolated
    times = [o["recorded_at"] for o in batch.observations]
    assert times == [
        "2020-01-01T08:00:00+01:00",
        "2020-01-01T09:00:00+01:00",
        "2020-01-01T10:00:00+01:00",
    ]


def test_track_member_falls_back_and_can_be_absent():
    m = lambda u, lead: {"employee_uuid": u, "leader": lead, "pilot": False}  # noqa: E731
    assert track_member({"members": [m("a", False), m("b", True)]}) == "b"
    assert track_member({"members": [m("a", False), m("b", False)]}) == "a"
    assert track_member({"members": []}) is None


def test_untimed_tracks_on_one_day_get_distinct_times():
    day = {
        "uuid": "d1",
        "date": "2020-01-01",
        "start_time": None,
        "end_time": None,
        "rest_minutes": None,
        "start": "2020-01-01T08:00:00+01:00",
        "end": "2020-01-01T10:00:00+01:00",
        "has_track": True,
    }
    leg = {
        "uuid": "L",
        "id": "1",
        "placeholder_dates": False,
        "start_date": "2020-01-01",
        "end_date": "2020-01-01",
        "days": [day],
        "waypoints": [],
        "members": [{"employee_uuid": "m1", "leader": True, "pilot": False}],
    }
    tracks = [
        {
            "uuid": f"t{i}",
            "leg_day_uuid": "d1",
            "parts": [{"coordinates": [[i, 0], [i, 1], [i, 2]]}],
        }
        for i in range(2)
    ]
    by_day = tracks_by_day(tracks)
    batches = build_track_batches(leg, leg_times(leg, by_day, TZ), by_day)
    times = [o["recorded_at"] for b in batches for o in b.observations]
    assert len(times) == 6 and len(set(times)) == 6
    assert all(b.interpolated for b in batches)
