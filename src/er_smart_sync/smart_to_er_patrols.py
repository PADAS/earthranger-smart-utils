"""Pure transform from one bundle patrol to the EarthRanger payloads that
import-patrols writes: the patrol with its segments and note, the events per
waypoint, and the track observations per leg day. No I/O; ids come from
patrol_ids, times from patrol_times, values from the same rules the data
model syncs used."""

from __future__ import annotations

from dataclasses import dataclass, field
from zoneinfo import ZoneInfo

from .bundle import Bundle
from .choices import sanitize_choice_value, shorten_choice_value
from .patrol_ids import er_id
from .patrol_times import DayTimes, LegTimes, leg_times
from .smart_to_er_patrol_schema import CUSTOM_FIELD_PREFIX, choice_value


@dataclass
class Resolver:
    subjects: dict
    teams: dict
    patrol_types: dict
    event_types: dict
    collection_type: str | None = None


@dataclass
class PatrolPlan:
    patrol: dict
    segment_ids: dict = field(default_factory=dict)
    leg_times: dict = field(default_factory=dict)


def _attr_value(attr, value):
    """A custom patrol attribute's value as the segment schema stores it."""
    t = (attr.type or "").strip()
    if t in ("LIST", "TREE", "MLIST"):

        def one(v):
            opt = next((o for o in attr.options if o.key == v), None)
            return choice_value(opt) if opt else sanitize_choice_value(str(v))

        if isinstance(value, list):
            return [one(v) for v in value]
        return one(value)
    return value


def segment_details(
    bundle: Bundle, patrol: dict, leg: dict, lt: LegTimes, importer_version: str
) -> dict:
    ref = bundle.reference
    by_uuid = {x.uuid: x for x in ref.mandates + ref.stations if x.uuid}
    d: dict = {}
    if patrol.get("objective"):
        d["objective"] = patrol["objective"]
    if leg.get("mandate_uuid") in by_uuid:
        d["mandate"] = choice_value(by_uuid[leg["mandate_uuid"]])
    if patrol.get("station_uuid") in by_uuid:
        d["station"] = choice_value(by_uuid[patrol["station_uuid"]])
    if patrol.get("patrol_type"):
        d["patrol_class"] = patrol["patrol_type"].lower()
    if patrol.get("armed") is not None:
        d["armed"] = bool(patrol["armed"])
    if patrol.get("comment"):
        d["comment"] = patrol["comment"]
    attrs = {a.uuid: a for a in ref.patrol_attributes}
    for a in patrol.get("attributes") or []:
        attr = attrs.get(a.get("attribute_uuid"))
        if attr is not None and a.get("value") is not None:
            key = f"{CUSTOM_FIELD_PREFIX}{sanitize_choice_value(attr.key)}"
            d[key] = _attr_value(attr, a["value"])
    prov = {
        "patrol_uuid": patrol["uuid"],
        "patrol_id": patrol.get("id"),
        "leg_uuid": leg["uuid"],
        "leg_id": leg.get("id"),
        "ca_uuid": bundle.ca_uuid,
        "time_source": lt.sources,
        "bundle_generated_at": bundle.manifest.generated_at,
        "importer_version": importer_version,
    }
    if any(day.track_times == "interpolated" for day in lt.days):
        prov["track_times"] = "interpolated"
    d["smart_"] = prov
    return d


def build_patrol(
    bundle: Bundle,
    patrol: dict,
    ns,
    resolver: Resolver,
    tz: ZoneInfo,
    by_day: dict,
    importer_version: str,
) -> PatrolPlan:
    plan = PatrolPlan(patrol={})
    segments = []
    for leg in patrol["legs"]:
        lt = leg_times(leg, by_day, tz)
        seg_id = er_id(ns, "segment", leg["uuid"])
        plan.segment_ids[leg["uuid"]] = seg_id
        plan.leg_times[leg["uuid"]] = lt
        seg: dict = {"id": seg_id}
        ptype = resolver.patrol_types.get(leg.get("transport_uuid"))
        if ptype:
            seg["patrol_type"] = ptype
        if lt.start is not None:
            seg["time_range"] = {
                "start_time": lt.start.isoformat(),
                "end_time": lt.end.isoformat(),
            }
        leader = next((m for m in leg["members"] if m["leader"]), None)
        if leader and leader["employee_uuid"] in resolver.subjects:
            seg["leader"] = {
                "content_type": "observations.subject",
                "id": resolver.subjects[leader["employee_uuid"]],
            }
        seg["members"] = [
            resolver.subjects[m["employee_uuid"]]
            for m in leg["members"]
            if m["employee_uuid"] in resolver.subjects
        ]
        if patrol.get("team_uuid") in resolver.teams:
            seg["team"] = resolver.teams[patrol["team_uuid"]]
        seg["segment_details"] = segment_details(
            bundle, patrol, leg, lt, importer_version
        )
        segments.append(seg)
    body = {
        "id": er_id(ns, "patrol", patrol["uuid"]),
        "title": patrol.get("id") or patrol["uuid"],
        "priority": 0,
        "patrol_segments": segments,
    }
    if patrol.get("objective"):
        body["objective"] = patrol["objective"]
    if patrol.get("comment"):
        body["notes"] = [
            {"id": er_id(ns, "note", patrol["uuid"]), "text": patrol["comment"]}
        ]
    plan.patrol = body
    return plan


def _choice_key(v) -> str:
    return shorten_choice_value(sanitize_choice_value(str(v)))


def event_value(a: dict):
    """An observation attribute value as the event type built by the data
    model sync stores it (option values are sanitized, shortened keys)."""
    t, v = (a.get("type") or "").strip(), a.get("value")
    if v is None:
        return None
    if t in ("LIST", "TREE"):
        return _choice_key(v)
    if t == "MLIST":
        return [_choice_key(x) for x in (v if isinstance(v, list) else [v])]
    return v


@dataclass
class WaypointPlan:
    waypoint_uuid: str
    parent: dict | None
    events: list
    attachments: list
    unmapped: list


def build_waypoint(
    bundle: Bundle,
    w: dict,
    segment_id: str,
    ns,
    resolver: Resolver,
    fallback_time: str | None,
) -> WaypointPlan | None:
    observations = [(g, o) for g in w["observation_groups"] for o in g["observations"]]
    if not observations:
        return None
    base: dict = {
        "time": w.get("time") or fallback_time,
        "patrol_segments": [segment_id],
    }
    if w.get("location"):
        lon, lat = w["location"]
        base["location"] = {"latitude": lat, "longitude": lon}
    parent = None
    if len(observations) > 1 and resolver.collection_type:
        parent = {
            **base,
            "id": er_id(ns, "incident", w["uuid"]),
            "event_type": resolver.collection_type,
            "title": f"SMART waypoint {w.get('id') or w['uuid']}",
            "event_details": {"smart_": {"waypoint_uuid": w["uuid"]}},
        }
    events, unmapped = [], []
    for g, o in observations:
        etype = resolver.event_types.get(o["category_hkey"])
        if not etype:
            unmapped.append(o["uuid"])
            continue
        details = {
            a["key"]: event_value(a)
            for a in o["attributes"]
            if event_value(a) is not None
        }
        details["smart_"] = {
            "waypoint_uuid": w["uuid"],
            "observation_uuid": o["uuid"],
            "group_uuid": g["uuid"],
        }
        ev = {
            **base,
            "id": er_id(ns, "event", o["uuid"]),
            "event_type": etype,
            "event_details": details,
        }
        if parent:
            ev["collection"] = {"id": parent["id"]}
        events.append(ev)
    return WaypointPlan(
        w["uuid"], parent, events, list(w.get("attachments") or []), unmapped
    )


@dataclass
class TrackBatch:
    member_uuid: str
    leg_day_uuid: str
    start: str
    end: str
    observations: list
    interpolated: bool


def track_member(leg: dict) -> str | None:
    """Whose track it is: the leg leader, else the first member."""
    members = leg.get("members") or []
    leader = next((m for m in members if m["leader"]), None)
    pick = leader or (members[0] if members else None)
    return pick["employee_uuid"] if pick else None


def _spread(day: DayTimes, n: int) -> list:
    if n == 1:
        return [day.start.isoformat()]
    step = (day.end - day.start) / (n - 1)
    return [(day.start + step * i).isoformat() for i in range(n)]


def build_track_batches(leg: dict, lt: LegTimes, by_day: dict) -> list:
    member = track_member(leg)
    if member is None:
        return []
    out = []
    for day in lt.days:
        for t in by_day.get(day.leg_day_uuid, []) if day.leg_day_uuid else []:
            parts = t.get("parts") or []
            coords = [c for p in parts for c in p.get("coordinates") or []]
            if not coords:
                continue
            recorded = all(
                p.get("times") and len(p["times"]) == len(p.get("coordinates") or [])
                for p in parts
            )
            times = (
                [x for p in parts for x in p["times"]]
                if recorded
                else _spread(day, len(coords))
            )
            obs = [
                {
                    "recorded_at": ts,
                    "location": {"latitude": c[1], "longitude": c[0]},
                    "additional": {
                        "smart_leg_day_uuid": day.leg_day_uuid,
                        "smart_track_uuid": t.get("uuid"),
                    },
                }
                for c, ts in zip(coords, times, strict=True)
            ]
            out.append(
                TrackBatch(
                    member,
                    day.leg_day_uuid,
                    day.start.isoformat(),
                    day.end.isoformat(),
                    obs,
                    not recorded,
                )
            )
    return out
