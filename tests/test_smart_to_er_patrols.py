from pathlib import Path
from zoneinfo import ZoneInfo

from er_smart_sync.bundle import load_bundle
from er_smart_sync.patrol_ids import er_id, namespace
from er_smart_sync.patrol_times import tracks_by_day
from er_smart_sync.smart_to_er_patrol_schema import build_patrol_types
from er_smart_sync.smart_to_er_patrols import Resolver, build_patrol

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
