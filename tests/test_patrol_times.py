from pathlib import Path
from zoneinfo import ZoneInfo

from er_smart_sync.bundle import load_bundle
from er_smart_sync.patrol_times import leg_times, parse_ts, tracks_by_day

FIXTURE = Path(__file__).parent / "fixtures" / "smart_demo_bundle"
TZ = ZoneInfo("Africa/Libreville")


def _day(uuid, date, start=None, end=None, has_track=False):
    return {
        "uuid": uuid,
        "date": date,
        "start_time": None,
        "end_time": None,
        "rest_minutes": None,
        "start": start,
        "end": end,
        "has_track": has_track,
    }


def _wp(day, time):
    return {
        "uuid": f"w-{day}-{time}",
        "leg_day_uuid": day,
        "time": time,
        "location": None,
        "observation_groups": [],
        "attachments": [],
    }


def _leg(days, waypoints=(), placeholder=False, start="2020-01-01", end="2020-01-02"):
    return {
        "uuid": "L",
        "id": "1",
        "placeholder_dates": placeholder,
        "start_date": start,
        "end_date": end,
        "days": list(days),
        "waypoints": list(waypoints),
        "members": [],
    }


def test_demo_leg_uses_track_times():
    b = load_bundle(FIXTURE)
    leg = b.patrols[0]["legs"][0]
    lt = leg_times(leg, tracks_by_day(b.tracks), TZ)
    assert all(d.source == "track" and d.track_times == "recorded" for d in lt.days)
    assert lt.start == min(d.start for d in lt.days) and lt.end > lt.start


def test_track_is_unioned_with_leg_day_times():
    day = _day(
        "d1",
        "2020-01-01",
        "2020-01-01T08:00:00+01:00",
        "2020-01-01T17:00:00+01:00",
        has_track=True,
    )
    track = {
        "leg_day_uuid": "d1",
        "parts": [
            {
                "coordinates": [[0, 0], [1, 1]],
                "times": ["2020-01-01T09:00:00+01:00", "2020-01-01T18:00:00+01:00"],
            }
        ],
    }
    (d,) = leg_times(_leg([day]), tracks_by_day([track]), TZ).days
    assert d.source == "track"
    assert d.start == parse_ts("2020-01-01T08:00:00+01:00")
    assert d.end == parse_ts("2020-01-01T18:00:00+01:00")


def test_waypoints_then_date_only():
    days = [_day("d1", "2020-01-01"), _day("d2", "2020-01-02")]
    wps = [
        _wp("d1", "2020-01-01T10:00:00+01:00"),
        _wp("d1", "2020-01-01T12:00:00+01:00"),
    ]
    d1, d2 = leg_times(_leg(days, wps), {}, TZ).days
    assert (d1.source, d1.start.hour, d1.end.hour) == ("waypoints", 10, 12)
    assert d2.source == "date_only"
    assert (d2.start.hour, d2.end.hour, d2.end.minute) == (0, 23, 59)
    assert d2.start.utcoffset().total_seconds() == 3600


def test_untimed_track_is_interpolated():
    day = _day(
        "d1",
        "2020-01-01",
        "2020-01-01T08:00:00+01:00",
        "2020-01-01T17:00:00+01:00",
        has_track=True,
    )
    track = {"leg_day_uuid": "d1", "parts": [{"coordinates": [[0, 0], [1, 1]]}]}
    (d,) = leg_times(_leg([day]), tracks_by_day([track]), TZ).days
    assert d.source == "leg_day_times" and d.track_times == "interpolated"


def test_placeholder_leg_keeps_only_days_with_evidence():
    days = [
        _day(
            "d0", "1970-01-01", "1970-01-01T00:00:00+01:00", "1970-01-01T23:00:00+01:00"
        ),
        _day(
            "d1", "2020-01-01", "2020-01-01T08:00:00+01:00", "2020-01-01T17:00:00+01:00"
        ),
    ]
    wps = [_wp("d1", "2020-01-01T10:00:00+01:00")]
    (d,) = leg_times(_leg(days, wps, placeholder=True), {}, TZ).days
    # a placeholder leg's own start/end are never trusted
    assert d.date == "2020-01-01" and d.source == "waypoints"


def test_placeholder_leg_without_evidence_falls_back_to_end_date():
    days = [_day("d0", "1970-01-01"), _day("d1", "1970-01-02")]
    lt = leg_times(_leg(days, placeholder=True, end="2021-05-06"), {}, TZ)
    (d,) = lt.days
    assert (d.date, d.source) == ("2021-05-06", "placeholder")
    assert lt.sources == {"2021-05-06": "placeholder"}


def test_leg_without_days_spans_its_dates():
    lt = leg_times(_leg([], start="2020-01-01", end="2020-01-03"), {}, TZ)
    assert [d.source for d in lt.days] == ["date_only"]
    assert lt.start.day == 1 and lt.end.day == 3
