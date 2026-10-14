"""Segment time ranges from a leg's days (spec "Patrol times"). Each leg day
takes the most precise source it has: a timed track, the leg day's start and
end, its waypoints, or the calendar day. Track and waypoint ranges are
unioned with the leg day's own times when both exist. Legs SMART filled with
placeholder dates keep only days with evidence and never trust their own
start and end; with no evidence they fall back to the leg's end date."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date as Date
from datetime import datetime, time
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class DayTimes:
    leg_day_uuid: str | None
    date: str
    start: datetime
    end: datetime
    source: str
    track_times: str | None = None


@dataclass(frozen=True)
class LegTimes:
    days: tuple
    start: datetime | None
    end: datetime | None

    @property
    def sources(self) -> dict:
        return {d.date: d.source for d in self.days}


def parse_ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def tracks_by_day(tracks: list) -> dict:
    out: dict = {}
    for t in tracks:
        out.setdefault(t["leg_day_uuid"], []).append(t)
    return out


def _whole_day(day: str, tz: ZoneInfo) -> tuple:
    d = Date.fromisoformat(day)
    return (
        datetime.combine(d, time(0, 0, 0), tz),
        datetime.combine(d, time(23, 59, 59), tz),
    )


def fully_timed(track: dict) -> bool:
    """Every part carries one time per vertex (else the track is spread)."""
    parts = track.get("parts") or []
    return bool(parts) and all(
        p.get("times") and len(p["times"]) == len(p.get("coordinates") or [])
        for p in parts
    )


def _track_times(tracks: list) -> list:
    out = []
    for t in tracks:
        for part in t.get("parts") or []:
            times = part.get("times") or []
            if times and len(times) == len(part.get("coordinates") or []):
                out += [parse_ts(x) for x in times]
    return sorted(out)


def _day(day: dict, tracks: list, wp_times: list, tz: ZoneInfo, trust: bool):
    timed = _track_times(tracks)
    own = (
        (parse_ts(day["start"]), parse_ts(day["end"]))
        if trust and day.get("start") and day.get("end")
        else None
    )
    if timed:
        source, lo, hi = "track", timed[0], timed[-1]
    elif own:
        source, (lo, hi) = "leg_day_times", own
    elif wp_times:
        source, lo, hi = "waypoints", wp_times[0], wp_times[-1]
    else:
        source, (lo, hi) = "date_only", _whole_day(day["date"], tz)
    if own and source == "track":
        lo, hi = min(lo, own[0]), max(hi, own[1])
    if source == "leg_day_times" and wp_times:
        lo, hi = min(lo, wp_times[0]), max(hi, wp_times[-1])
    if any(not fully_timed(t) for t in tracks):
        track_times = "interpolated"
    else:
        track_times = "recorded" if timed else None
    return DayTimes(day["uuid"], day["date"], lo, hi, source, track_times)


def leg_times(leg: dict, by_day: dict, tz: ZoneInfo) -> LegTimes:
    placeholder = bool(leg.get("placeholder_dates"))
    wp_times: dict = {}
    has_wp: set = set()
    for w in leg.get("waypoints") or []:
        has_wp.add(w["leg_day_uuid"])
        if w.get("time"):
            wp_times.setdefault(w["leg_day_uuid"], []).append(parse_ts(w["time"]))
    days = []
    for d in leg.get("days") or []:
        tracks = by_day.get(d["uuid"], [])
        evidence = d.get("has_track") or tracks or d["uuid"] in has_wp
        if placeholder and not evidence:
            continue
        days.append(
            _day(d, tracks, sorted(wp_times.get(d["uuid"], [])), tz, not placeholder)
        )
    if not days and placeholder and leg.get("end_date"):
        lo, hi = _whole_day(leg["end_date"], tz)
        days = [DayTimes(None, leg["end_date"], lo, hi, "placeholder")]
    if not days and leg.get("start_date"):
        lo, _ = _whole_day(leg["start_date"], tz)
        _, hi = _whole_day(leg.get("end_date") or leg["start_date"], tz)
        days = [DayTimes(None, leg["start_date"], lo, hi, "date_only")]
    if not days:
        return LegTimes((), None, None)
    return LegTimes(tuple(days), min(d.start for d in days), max(d.end for d in days))
