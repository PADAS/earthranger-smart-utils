# import-patrols Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `er-smart-sync import-patrols` writes a smart-able bundle's patrol history into an EarthRanger site: one patrol per SMART patrol with a segment per leg, one event per observation linked to its segment, and each leg leader's track as location observations. Re-runs create nothing new.

**Architecture:** Pure builders turn bundle dicts into ER payloads: `patrol_ids.py` derives ids, `patrol_times.py` derives times, and `smart_to_er_patrols.py` builds patrols, events and tracks. `er_patrol_api.py` gains the endpoints. `patrol_import.py` orchestrates the run:
- it runs preflight first as the gate;
- it reactivates retired members for the run;
- each patrol is imported in isolation, in three stages (patrol, events, tracks);
- a JSON ledger lets a re-run skip finished stages.

**Tech Stack:** Python 3.10+, click, erclient (`_get/_post/_patch`, `post_event_file`), stdlib `uuid`, `zoneinfo`, `datetime`; pytest; ruff (88 columns for new files).

**Spec:** `docs/superpowers/specs/2026-10-12-smart-patrol-migration-design.md`, sections "import-patrols", "Historical members", "Patrol times" and "Timezone". das facts were verified 2026-10-14 against `~/padas/das` (see "das facts" below). The spec's verify-first items 1–5 are settled there.

## Global Constraints

- Every ER id is `uuid5(ns, f"{kind}:{smart_uuid}")` with `ns = uuid5(NAMESPACE_URL, f"{er host}/{ca uuid}")`. The namespace can be overridden with `earthranger.patrol_namespace`. Kinds are `patrol`, `segment`, `note`, `event`, `incident` and `source`. Raw SMART UUIDs are never ER ids.
- Patrol: `title = patrol id`, `priority = 0`, `objective`, and one note holding the patrol comment when there is one. Segments: one per leg, in order.
- `segment_details` holds the canonical fields (`objective`, `mandate`, `station`, `patrol_class`, `armed`, `comment`), `smart_<sanitized attr key>` per custom attribute, and the provenance block `smart_`: `patrol_uuid`, `patrol_id`, `leg_uuid`, `leg_id`, `ca_uuid`, `time_source`, `bundle_generated_at`, `importer_version`, plus `track_times` when interpolated.
- Event `event_details`: attribute key, then value. LIST and TREE become `shorten_choice_value(sanitize_choice_value(key))`, MLIST becomes a list of those, and NUMERIC, BOOLEAN, DATE and TEXT pass through as-is. Provenance goes in `event_details.smart_` (`waypoint_uuid`, `observation_uuid`, `group_uuid`). `patrol_segments = [segment id]`.
- A waypoint with more than one observation becomes an incident collection holding the observation events. Attachments are posted once, to the waypoint's first observation event.
- Tracks: one observation per vertex, with `recorded_at` taken from `parts[].times`. Only when a track has no times are vertices spread evenly across the day, and then provenance records `track_times: interpolated`. Each observation carries `additional.smart_leg_day_uuid`. `--skip-tracks` skips tracks.
- Times are passed through unchanged. A configured `smart.timezone` that differs from `manifest.timezone.name` stops the command, using the existing `_check_bundle_timezone`.
- Selection flags: `--since/--until` on the patrol start date, `--patrol <id or uuid>` (repeatable), `--skip-events`, `--skip-tracks`, `--limit N`.
- One failing patrol is logged and skipped. The run ends with imported, skipped and failed counts and a reason per failure. Exit codes: 1 if any patrol failed; 2 when preflight blocks and `--allow-blocking` is absent.
- `import-patrols` runs the preflight checks first. With `--allow-blocking`, each blocked patrol is skipped with its finding codes as the reason.
- Retired members are reactivated for the run and retired again at the end. The ledger records them, so a crashed run finishes the retirement next time.

## das facts (verified 2026-10-14; drive several decisions)

- **Duplicate ids.** `POST activity/patrols/` and `POST activity/events/` accept a client `id`. But an id that already exists hits `SerialNumberModelMixin`'s 40-retry loop and returns **500 after about 14 s**, not 409. So the importer always does a GET by id before a POST: `GET activity/patrols/{id}` and `GET activity/event/{id}`, where a 404 means absent.
- **Patrol PATCH.** `PATCH activity/patrols/{id}` upserts nested segments and notes by id and deletes nothing. New segments on a PATCH cannot carry an id: `_reject_unknown_ids` returns 400. `members` is replaced with `.set()`.
- **Segment fields.** `patrol_type` is the type **value**. `time_range = {"start_time","end_time"}`. `leader = {"content_type": "observations.subject", "id": <subject id>}`. `members` is a list of subject ids. `team` is the PatrolConfigurationTeam **id**. `segment_details` is checked in advisory mode only, so unknown keys persist. Members must be active staff subjects on the roster.
- **Done state.** A patrol whose segments all end in the past is marked `done` automatically, so the payload sends no `state`.
- **Events.** `location = {"latitude","longitude"}`, `time`, `event_type` value. `event_details` are not validated against v2 schemas on create, so the `smart_` block persists. **Do not send `end_time` on a POST**: das returns a 500 there.
- **Linking and collections.** `patrol_segments: [id]` in the event POST links the event to a segment. A child joins a collection through `"collection": {"id": parent_id}` in its own POST. The parent's event type must have `is_collection: true`.
- **Files.** `POST activity/event/{id}/files/` is multipart with field `filecontent.file`; erclient's `post_event_file` does this.
- **Observations.** `POST observations` takes `[{source, recorded_at, location:{latitude,longitude}, additional}]`. das has a unique constraint on (tenant, source, recorded_at): a list skips duplicates silently, and returns 409 only when nothing was inserted. erclient raises `ERClientException` whose text contains `409 from ER`.
- **Sources.**
  - `POST sources` with `{provider, manufacturer_id, source_type, model_name, additional}`; `additional` is required.
  - The provider must exist first: `POST sourceproviders` with `{provider_key, display_name}`.
  - `GET source/{manufacturer_id}` returns 404 when the source is absent.
- **Source assignment.** Assignments are made with `POST subject/{subject_id}/sources` and `{source, assigned_range: {lower, upper}}`. Back-dating is fine, and an exact range is get-or-create.
- **Past times.** No endpoint rejects times in the past.

## Spec deviations (record each as a ledger `Ruling:` when its task runs)

1. **The ledger is a JSON file** (`import-ledger.json` beside the bundle, `--ledger` to move it), not `StateStore`. `StateStore` only stores poll timestamps. Cost if wrong: one adapter later.
2. **Tracks are not diffed before posting.** das's `(source, recorded_at)` constraint makes re-posting a no-op, so a 409 means "already there". Cost if wrong: re-runs post the same points again, which das discards.
3. **Patrols and events use GET-before-POST, not "POST then fall back on 409".** das returns 500 on a duplicate id. Cost if wrong: one extra GET per object.
4. **Missing collection type.** When the site has no active collection event type, a multi-observation waypoint's events are posted ungrouped, and the run summary and a new preflight warning (`collection_type`) say so. Cost if wrong: the waypoints are not grouped.

## Review Focus

1. **A re-run after a crash mid-events** finishes the remaining events and creates no duplicates. Task 7 tests that existing event ids are skipped by GET and that an event created by the half-run is not posted again.
2. **A waypoint with no time or no location** still produces events. The time falls back to its leg day's start, and the location is omitted. Task 5 has the test.
3. **A leg with no members, or no member marked leader,** still produces a segment without `leader`, and tracks fall back to the first member or are skipped with a reason. Tasks 4 and 6 have the tests.
4. **A track part whose `times` length differs from its coordinates** is interpolated over the day, not zipped short. Task 6 has the test.
5. **An attachment whose file is missing from the bundle** is counted and reported, and the patrol still succeeds. **A patrol that exists on the site without one of our segment ids** fails with a fix that says to delete it in ER, while the other patrols continue. Task 7 has both tests.

---

### Task 1: Ids, namespace config and the import ledger

**Files:**
- Create: `src/er_smart_sync/patrol_ids.py`
- Create: `src/er_smart_sync/import_ledger.py`
- Modify: `src/er_smart_sync/config.py` (`EarthRangerConfig`: add `patrol_namespace: str | None = None` after `choices_base_url`)
- Test: `tests/test_patrol_ids.py`, `tests/test_import_ledger.py`

**Interfaces:**
- Produces:
  - `namespace(er_endpoint: str, ca_uuid: str, override: str | None = None) -> uuid.UUID`
  - `er_id(ns: uuid.UUID, kind: str, smart_uuid: str) -> str` (raises `ValueError` on an unknown kind)
  - `KINDS: tuple`
  - `ImportLedger(path: Path, site_key: str, read_only: bool = False)`, with the methods:
    - `.stages(patrol_uuid) -> set[str]`
    - `.done(patrol_uuid, stage, **ids) -> None`
    - `.ids(patrol_uuid) -> dict`
    - `.add_id(patrol_uuid, key, value) -> None`, which appends `value` to the list `ids[key]`
    - `.reactivated() -> list[str]`
    - `.set_reactivated(ids: list[str]) -> None`
  - `STAGES = ("patrol", "events", "tracks")`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_patrol_ids.py
import uuid

import pytest

from er_smart_sync.patrol_ids import er_id, namespace


def test_namespace_is_host_and_ca():
    a = namespace("https://site.pamdas.org/api/v1.0", "ABC-1")
    assert a == uuid.uuid5(uuid.NAMESPACE_URL, "site.pamdas.org/abc-1")
    assert namespace("https://other.pamdas.org/api/v1.0", "abc-1") != a


def test_namespace_override_wins():
    ns = "6f1c1a0e-3c9b-4f3e-9a51-6a3e1c2b7d10"
    assert str(namespace("https://x/api/v1.0", "c", override=ns)) == ns


def test_er_id_is_deterministic_and_never_the_smart_uuid():
    ns = namespace("https://site.pamdas.org/api/v1.0", "c")
    smart = "3deb6b5a-f9af-41ae-a2fa-1604b72d9027"
    assert er_id(ns, "patrol", smart) == er_id(ns, "patrol", smart.upper())
    assert er_id(ns, "patrol", smart) != smart
    assert er_id(ns, "patrol", smart) != er_id(ns, "segment", smart)
    with pytest.raises(ValueError):
        er_id(ns, "nope", smart)
```

```python
# tests/test_import_ledger.py
import json

from er_smart_sync.import_ledger import ImportLedger


def test_ledger_records_stages_and_ids(tmp_path):
    p = tmp_path / "l.json"
    led = ImportLedger(p, "site|ca")
    led.done("p1", "patrol", patrol_id="x")
    led.add_id("p1", "attached", "w1")
    again = ImportLedger(p, "site|ca")
    assert again.stages("p1") == {"patrol"}
    assert again.ids("p1") == {"patrol_id": "x", "attached": ["w1"]}
    assert ImportLedger(p, "other|ca").stages("p1") == set()


def test_reactivated_round_trip(tmp_path):
    p = tmp_path / "l.json"
    led = ImportLedger(p, "s")
    led.set_reactivated(["a", "b"])
    assert ImportLedger(p, "s").reactivated() == ["a", "b"]


def test_read_only_never_writes(tmp_path):
    p = tmp_path / "l.json"
    led = ImportLedger(p, "s", read_only=True)
    led.done("p1", "patrol")
    assert not p.exists() and led.stages("p1") == {"patrol"}


def test_existing_file_other_sites_survive(tmp_path):
    p = tmp_path / "l.json"
    p.write_text(json.dumps({"keep": {"patrols": {}, "reactivated": []}}))
    ImportLedger(p, "s").done("p1", "patrol")
    assert "keep" in json.loads(p.read_text())
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_patrol_ids.py tests/test_import_ledger.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'er_smart_sync.patrol_ids'`

- [ ] **Step 3: Implement**

```python
# src/er_smart_sync/patrol_ids.py
"""Deterministic EarthRanger ids for imported SMART objects.

Every id is uuid5 of the object's kind and SMART uuid in a namespace made of
the ER host and the conservation area, so a re-run, or a second operator,
addresses the same rows. SMART uuids are never used directly: a SMART
`client_uuid` can point back at an ER-originated patrol from the outbound
sync.
"""

from __future__ import annotations

import uuid
from urllib.parse import urlparse

KINDS = ("patrol", "segment", "note", "event", "incident", "source")


def namespace(er_endpoint: str, ca_uuid: str, override: str | None = None) -> uuid.UUID:
    if override:
        return uuid.UUID(override)
    host = urlparse(er_endpoint).netloc or er_endpoint
    return uuid.uuid5(uuid.NAMESPACE_URL, f"{host}/{ca_uuid.lower()}")


def er_id(ns: uuid.UUID, kind: str, smart_uuid: str) -> str:
    if kind not in KINDS:
        raise ValueError(f"unknown id kind {kind!r}")
    return str(uuid.uuid5(ns, f"{kind}:{smart_uuid.lower()}"))
```

```python
# src/er_smart_sync/import_ledger.py
"""What import-patrols has written, per site and conservation area: the
stages each patrol reached, the ER ids it wrote, and the subjects it
reactivated for the run. A JSON file beside the bundle (the spec's
StateStore only knows poll timestamps)."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

STAGES = ("patrol", "events", "tracks")


class ImportLedger:
    def __init__(self, path: Path, site_key: str, read_only: bool = False):
        self.path = Path(path)
        self.read_only = read_only
        self._data = (
            json.loads(self.path.read_text(encoding="utf-8"))
            if self.path.exists()
            else {}
        )
        self._site = self._data.setdefault(
            site_key, {"patrols": {}, "reactivated": []}
        )

    def _patrol(self, patrol_uuid: str) -> dict:
        return self._site["patrols"].setdefault(
            patrol_uuid, {"stages": [], "ids": {}}
        )

    def stages(self, patrol_uuid: str) -> set:
        return set(self._site["patrols"].get(patrol_uuid, {}).get("stages", []))

    def ids(self, patrol_uuid: str) -> dict:
        return dict(self._site["patrols"].get(patrol_uuid, {}).get("ids", {}))

    def done(self, patrol_uuid: str, stage: str, **ids) -> None:
        p = self._patrol(patrol_uuid)
        if stage not in p["stages"]:
            p["stages"].append(stage)
        p["ids"].update(ids)
        self._save()

    def add_id(self, patrol_uuid: str, key: str, value: str) -> None:
        p = self._patrol(patrol_uuid)
        p["ids"].setdefault(key, [])
        if value not in p["ids"][key]:
            p["ids"][key].append(value)
        self._save()

    def reactivated(self) -> list:
        return list(self._site["reactivated"])

    def set_reactivated(self, ids: list) -> None:
        self._site["reactivated"] = list(ids)
        self._save()

    def _save(self) -> None:
        if self.read_only:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, suffix=".json")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(self._data, f, indent=1)
            os.replace(tmp, self.path)
        except Exception:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise
```

In `src/er_smart_sync/config.py`, inside `EarthRangerConfig`, after `choices_base_url: str = "/api/v2.0/schemas"` add:

```python
    # import-patrols id namespace; set only for a site pre-seeded under another
    patrol_namespace: str | None = None
```

- [ ] **Step 4: Run them to verify they pass**

Run: `uv run pytest tests/test_patrol_ids.py tests/test_import_ledger.py -q`
Expected: PASS (7 passed)

- [ ] **Step 5: Commit**

```bash
git add src/er_smart_sync/patrol_ids.py src/er_smart_sync/import_ledger.py src/er_smart_sync/config.py tests/test_patrol_ids.py tests/test_import_ledger.py
git commit -m "import-patrols groundwork: deterministic ids, namespace config, import ledger"
```

---

### Task 2: Segment times (`patrol_times.py`)

**Files:**
- Create: `src/er_smart_sync/patrol_times.py`
- Test: `tests/test_patrol_times.py`

**Interfaces:**
- Consumes: bundle dicts only: a leg (`days`, `waypoints`, `placeholder_dates`, `start_date`, `end_date`), plus tracks grouped by `leg_day_uuid`.
- Produces:
  - `DayTimes(leg_day_uuid, date, start: datetime, end: datetime, source: str, track_times: str | None)`. `source` is one of `track`, `leg_day_times`, `waypoints`, `date_only` or `placeholder`. `track_times` is `"recorded"`, `"interpolated"` or `None`.
  - `LegTimes(days: tuple[DayTimes, ...], start: datetime | None, end: datetime | None)`, with the property `.sources -> dict[str, str]` mapping date to source.
  - `tracks_by_day(tracks: list) -> dict[str, list]`
  - `parse_ts(s: str) -> datetime`
  - `leg_times(leg: dict, by_day: dict, tz: ZoneInfo) -> LegTimes`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_patrol_times.py
from pathlib import Path
from zoneinfo import ZoneInfo

from er_smart_sync.bundle import load_bundle
from er_smart_sync.patrol_times import leg_times, parse_ts, tracks_by_day

FIXTURE = Path(__file__).parent / "fixtures" / "smart_demo_bundle"
TZ = ZoneInfo("Africa/Libreville")


def _day(uuid, date, start=None, end=None, has_track=False):
    return {"uuid": uuid, "date": date, "start_time": None, "end_time": None,
            "rest_minutes": None, "start": start, "end": end, "has_track": has_track}


def _wp(day, time):
    return {"uuid": f"w-{day}-{time}", "leg_day_uuid": day, "time": time,
            "location": None, "observation_groups": [], "attachments": []}


def _leg(days, waypoints=(), placeholder=False, start="2020-01-01", end="2020-01-02"):
    return {"uuid": "L", "id": "1", "placeholder_dates": placeholder,
            "start_date": start, "end_date": end, "days": list(days),
            "waypoints": list(waypoints), "members": []}


def test_demo_leg_uses_track_times():
    b = load_bundle(FIXTURE)
    leg = b.patrols[0]["legs"][0]
    lt = leg_times(leg, tracks_by_day(b.tracks), TZ)
    assert all(d.source == "track" and d.track_times == "recorded" for d in lt.days)
    assert lt.start == min(d.start for d in lt.days) and lt.end > lt.start


def test_track_is_unioned_with_leg_day_times():
    day = _day("d1", "2020-01-01", "2020-01-01T08:00:00+01:00",
               "2020-01-01T17:00:00+01:00", has_track=True)
    track = {"leg_day_uuid": "d1", "parts": [{"coordinates": [[0, 0], [1, 1]],
             "times": ["2020-01-01T09:00:00+01:00", "2020-01-01T18:00:00+01:00"]}]}
    (d,) = leg_times(_leg([day]), tracks_by_day([track]), TZ).days
    assert d.source == "track"
    assert d.start == parse_ts("2020-01-01T08:00:00+01:00")
    assert d.end == parse_ts("2020-01-01T18:00:00+01:00")


def test_waypoints_then_date_only():
    days = [_day("d1", "2020-01-01"), _day("d2", "2020-01-02")]
    wps = [_wp("d1", "2020-01-01T10:00:00+01:00"), _wp("d1", "2020-01-01T12:00:00+01:00")]
    d1, d2 = leg_times(_leg(days, wps), {}, TZ).days
    assert (d1.source, d1.start.hour, d1.end.hour) == ("waypoints", 10, 12)
    assert d2.source == "date_only"
    assert (d2.start.hour, d2.end.hour, d2.end.minute) == (0, 23, 59)
    assert d2.start.utcoffset().total_seconds() == 3600


def test_untimed_track_is_interpolated():
    day = _day("d1", "2020-01-01", "2020-01-01T08:00:00+01:00",
               "2020-01-01T17:00:00+01:00", has_track=True)
    track = {"leg_day_uuid": "d1", "parts": [{"coordinates": [[0, 0], [1, 1]]}]}
    (d,) = leg_times(_leg([day]), tracks_by_day([track]), TZ).days
    assert d.source == "leg_day_times" and d.track_times == "interpolated"


def test_placeholder_leg_keeps_only_days_with_evidence():
    days = [_day("d0", "1970-01-01", "1970-01-01T00:00:00+01:00",
                 "1970-01-01T23:00:00+01:00"),
            _day("d1", "2020-01-01", "2020-01-01T08:00:00+01:00",
                 "2020-01-01T17:00:00+01:00")]
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
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_patrol_times.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'er_smart_sync.patrol_times'`

- [ ] **Step 3: Implement**

```python
# src/er_smart_sync/patrol_times.py
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
    track_times = "recorded" if timed else ("interpolated" if tracks else None)
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
    return LegTimes(
        tuple(days), min(d.start for d in days), max(d.end for d in days)
    )
```

- [ ] **Step 4: Run them to verify they pass**

Run: `uv run pytest tests/test_patrol_times.py -q`
Expected: PASS (7 passed)

- [ ] **Step 5: Commit**

```bash
git add src/er_smart_sync/patrol_times.py tests/test_patrol_times.py
git commit -m "import-patrols: segment times from tracks, leg-day times, waypoints or the day"
```

---

### Task 3: API endpoints for the import

**Files:**
- Modify: `src/er_smart_sync/er_patrol_api.py` (add constants and methods; keep existing ones)
- Test: `tests/test_er_patrol_api.py` (append)

**Interfaces:**
- Consumes: the `ERPatrolAPI._get_or_none`, `_write` and `_all_pages` helpers that already exist.
- Produces (all on `ERPatrolAPI`):
  - Patrols:
    - `get_patrol(patrol_id) -> dict | None`
    - `create_patrol(payload) -> dict`
    - `patch_patrol(patrol_id, payload) -> dict`
  - Events:
    - `get_event(event_id) -> dict | None`
    - `create_event(payload) -> dict`
    - `attach_file(event_id, filepath, comment="") -> dict`
  - Event types: `collection_event_type(version="v2") -> str | None`, which returns the value of an active `is_collection` type, preferring `incident_collection`.
  - Sources and tracks:
    - `ensure_source_provider(key, display) -> None`
    - `get_source(manufacturer_id) -> dict | None`
    - `create_source(payload) -> dict`
    - `assign_source(subject_id, source_id, lower, upper) -> dict`
    - `post_observations(batch: list) -> bool`, which returns `False` when das answers 409, meaning every point already exists.
  - Constants: `PATROLS_PATH = "activity/patrols"` and `SOURCE_PROVIDER = "smart_patrol_import"`.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_er_patrol_api.py`; `_client()` and imports already exist there)

```python
def test_patrol_and_event_reads_treat_404_as_absent():
    c = _client()
    c._get.side_effect = ERClientNotFound("nope")
    api = ERPatrolAPI(c)
    assert api.get_patrol("p") is None and api.get_event("e") is None
    assert api.get_source("smart:x") is None


def test_patrol_and_event_writes_use_the_documented_paths():
    c = _client()
    api = ERPatrolAPI(c)
    api.create_patrol({"id": "p"})
    c._post.assert_called_with(path="activity/patrols", payload={"id": "p"})
    api.patch_patrol("p", {"title": "t"})
    c._patch.assert_called_with(path="activity/patrols/p", payload={"title": "t"})
    api.create_event({"id": "e"})
    c._post.assert_called_with(path="activity/events", payload={"id": "e"})
    api.assign_source("s1", "src", "2020-01-01T00:00:00+00:00", "2020-01-02T00:00:00+00:00")
    c._post.assert_called_with(
        path="subject/s1/sources",
        payload={"source": "src", "assigned_range": {
            "lower": "2020-01-01T00:00:00+00:00", "upper": "2020-01-02T00:00:00+00:00"}},
    )


def test_post_observations_treats_409_as_already_there():
    from erclient.er_errors import ERClientException

    c = _client()
    c._post.side_effect = ERClientException("x\n\t 409 from ER. Message: dup")
    assert ERPatrolAPI(c).post_observations([{"recorded_at": "t"}]) is False
    c._post.side_effect = ERClientException("x\n\t 500 from ER. Message: boom")
    with pytest.raises(ERClientException):
        ERPatrolAPI(c).post_observations([{"recorded_at": "t"}])


def test_collection_event_type_prefers_incident_collection():
    c = _client()
    c.get_event_types.return_value = [
        {"value": "other_collection", "is_collection": True, "is_active": True},
        {"value": "incident_collection", "is_collection": True, "is_active": True},
        {"value": "plain", "is_collection": False, "is_active": True},
    ]
    assert ERPatrolAPI(c).collection_event_type() == "incident_collection"
    c.get_event_types.return_value = [{"value": "x", "is_collection": False}]
    assert ERPatrolAPI(c).collection_event_type() is None


def test_source_provider_created_only_when_missing():
    c = _client()
    c._get.return_value = {"results": [{"provider_key": "smart_patrol_import"}]}
    ERPatrolAPI(c).ensure_source_provider("smart_patrol_import", "SMART")
    c._post.assert_not_called()
    c._get.return_value = {"results": []}
    ERPatrolAPI(c).ensure_source_provider("smart_patrol_import", "SMART")
    c._post.assert_called_with(
        path="sourceproviders",
        payload={"provider_key": "smart_patrol_import", "display_name": "SMART"},
    )
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_er_patrol_api.py -q`
Expected: FAIL with `AttributeError: 'ERPatrolAPI' object has no attribute 'get_patrol'`

- [ ] **Step 3: Implement** (add to `er_patrol_api.py`; extend the erclient import with `ERClientException`, already imported)

```python
PATROLS_PATH = "activity/patrols"
SOURCE_PROVIDER = "smart_patrol_import"
```

Inside `class ERPatrolAPI`, in the reads section:

```python
    def get_patrol(self, patrol_id: str) -> dict | None:
        return self._get_or_none(f"{PATROLS_PATH}/{patrol_id}")

    def get_event(self, event_id: str) -> dict | None:
        return self._get_or_none(f"activity/event/{event_id}")

    def get_source(self, manufacturer_id: str) -> dict | None:
        return self._get_or_none(f"source/{manufacturer_id}")

    def collection_event_type(self, version: str = "v2") -> str | None:
        """An active collection event type for multi-observation waypoints;
        ER's stock `incident_collection` when present."""
        values = [
            e["value"]
            for e in self.list_event_types(version)
            if e.get("is_collection") and e.get("is_active", True)
        ]
        if "incident_collection" in values:
            return "incident_collection"
        return sorted(values)[0] if values else None
```

In the writes section:

```python
    def create_patrol(self, payload: dict) -> dict:
        return self._write(self.er._post, path=PATROLS_PATH, payload=payload)

    def patch_patrol(self, patrol_id: str, payload: dict) -> dict:
        return self._write(
            self.er._patch, path=f"{PATROLS_PATH}/{patrol_id}", payload=payload
        )

    def create_event(self, payload: dict) -> dict:
        # never send end_time on a POST: das reads self.instance there and 500s
        return self._write(self.er._post, path="activity/events", payload=payload)

    def attach_file(self, event_id: str, filepath: str, comment: str = "") -> dict:
        return self._write(
            self.er.post_event_file,
            event_id=event_id,
            filepath=filepath,
            comment=comment,
        )

    def ensure_source_provider(self, key: str, display: str) -> None:
        page = self._get_or_none("sourceproviders", provider_key=key) or {}
        if any(p.get("provider_key") == key for p in _results(page)):
            return
        self._write(
            self.er._post,
            path="sourceproviders",
            payload={"provider_key": key, "display_name": display},
        )

    def create_source(self, payload: dict) -> dict:
        return self._write(self.er._post, path="sources", payload=payload)

    def assign_source(self, subject_id: str, source_id: str, lower: str, upper: str):
        return self._write(
            self.er._post,
            path=f"subject/{subject_id}/sources",
            payload={
                "source": source_id,
                "assigned_range": {"lower": lower, "upper": upper},
            },
        )

    def post_observations(self, batch: list) -> bool:
        """das skips points it already has (unique on source + recorded_at)
        and answers 409 only when none was new."""
        try:
            self._write(self.er._post, path="observations", payload=batch)
            return True
        except ERClientException as e:
            if " 409 from ER" in str(e):
                return False
            raise
```

- [ ] **Step 4: Run them to verify they pass**

Run: `uv run pytest tests/test_er_patrol_api.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/er_smart_sync/er_patrol_api.py tests/test_er_patrol_api.py
git commit -m "ER patrol API: patrols, events, files, collection type, sources and observations"
```

---

### Task 4: The patrol payload (`smart_to_er_patrols.py`, part 1)

**Files:**
- Create: `src/er_smart_sync/smart_to_er_patrols.py`
- Test: `tests/test_smart_to_er_patrols.py`

**Interfaces:**
- Consumes:
  - `er_id` and `namespace` (Task 1)
  - `leg_times`, `tracks_by_day` and `LegTimes` (Task 2)
  - `choice_value`, `CUSTOM_FIELD_PREFIX` and `sanitize_choice_value` from `smart_to_er_patrol_schema` / `choices`
- Produces:
  - `Resolver` dataclass with these fields:
    - `subjects: dict` (SMART member uuid to ER subject id)
    - `teams: dict` (SMART team uuid to ER team id)
    - `patrol_types: dict` (transport uuid to patrol type value)
    - `event_types: dict` (category hkey to event type value)
    - `collection_type: str | None = None`
  - `PatrolPlan(patrol: dict, segment_ids: dict, leg_times: dict)`. `segment_ids` maps leg uuid to segment id, and `leg_times` maps leg uuid to `LegTimes`.
  - `build_patrol(bundle, patrol: dict, ns, resolver, tz, by_day, importer_version) -> PatrolPlan`
  - `segment_details(bundle, patrol, leg, lt: LegTimes, importer_version) -> dict`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_smart_to_er_patrols.py
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
    members = {m["employee_uuid"] for p in b.patrols for leg in p["legs"]
               for m in leg["members"]}
    return Resolver(
        subjects={m: f"subj-{m[:8]}" for m in members},
        teams={t: f"team-{t[:8]}" for t in b.patrol_team_uuids()},
        patrol_types={p.transport_uuid: p.value for p in build_patrol_types(b)},
        event_types={},
    )


def _plan(b, i):
    return build_patrol(b, b.patrols[i], NS, _resolver(b), TZ,
                        tracks_by_day(b.tracks), "test")


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
    assert seg["leader"] == {"content_type": "observations.subject",
                             "id": f"subj-{leader['employee_uuid'][:8]}"}
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
        "1- Group A", "1- Group B"]


def test_leg_without_leader_or_members():
    b = load_bundle(FIXTURE)
    p = dict(b.patrols[0])
    p["legs"] = [dict(p["legs"][0], members=[])]
    seg = build_patrol(b, p, NS, _resolver(b), TZ, {}, "t").patrol["patrol_segments"][0]
    assert "leader" not in seg and seg["members"] == []
    p["legs"] = [dict(p["legs"][0], members=[
        dict(m, leader=False) for m in b.patrols[0]["legs"][0]["members"]])]
    seg = build_patrol(b, p, NS, _resolver(b), TZ, {}, "t").patrol["patrol_segments"][0]
    assert "leader" not in seg and len(seg["members"]) == 4
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_smart_to_er_patrols.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'er_smart_sync.smart_to_er_patrols'`

- [ ] **Step 3: Implement**

```python
# src/er_smart_sync/smart_to_er_patrols.py
"""Pure transform from one bundle patrol to the EarthRanger payloads that
import-patrols writes: the patrol with its segments and note, the events per
waypoint, and the track observations per leg day. No I/O; ids come from
patrol_ids, times from patrol_times, values from the same rules the data
model syncs used."""

from __future__ import annotations

from dataclasses import dataclass, field
from zoneinfo import ZoneInfo

from .bundle import Bundle
from .patrol_ids import er_id
from .patrol_times import LegTimes, leg_times
from .smart_to_er_patrol_schema import CUSTOM_FIELD_PREFIX, choice_value
from .choices import sanitize_choice_value


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
```

- [ ] **Step 4: Run them to verify they pass**

Run: `uv run pytest tests/test_smart_to_er_patrols.py -q`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add src/er_smart_sync/smart_to_er_patrols.py tests/test_smart_to_er_patrols.py
git commit -m "import-patrols: patrol payload with a segment per leg, universal fields and provenance"
```

---

### Task 5: Events per waypoint (`smart_to_er_patrols.py`, part 2)

**Files:**
- Modify: `src/er_smart_sync/smart_to_er_patrols.py`
- Test: `tests/test_smart_to_er_patrols.py` (append)

**Interfaces:**
- Consumes: `Resolver.event_types`, `Resolver.collection_type`; `shorten_choice_value` and `sanitize_choice_value` from `choices`.
- Produces:
  - `event_value(attribute: dict)`
  - `WaypointPlan(waypoint_uuid, parent: dict | None, events: list[dict], attachments: list[dict], unmapped: list[str])`
  - `build_waypoint(bundle, waypoint: dict, segment_id: str, ns, resolver, fallback_time: str | None) -> WaypointPlan | None`, which returns `None` for a waypoint with no observations

- [ ] **Step 1: Write the failing tests** (append)

```python
from er_smart_sync.smart_to_er_patrols import build_waypoint, event_value


def _wps(b):
    return [w for p in b.patrols for leg in p["legs"] for w in leg["waypoints"]]


def _ev_resolver(b, collection="incident_collection"):
    r = _resolver(b)
    r.event_types = {h: f"smart-ca-import_{h.strip('.').replace('.', '_')}"
                     for h in b.observed_category_hkeys()}
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
    w = next(w for w in _wps(b)
             if sum(len(g["observations"]) for g in w["observation_groups"]) == 1)
    obs = w["observation_groups"][0]["observations"][0]
    plan = build_waypoint(b, w, "seg", NS, _ev_resolver(b), None)
    assert plan.parent is None
    (ev,) = plan.events
    assert ev["id"] == er_id(NS, "event", obs["uuid"])
    assert ev["patrol_segments"] == ["seg"] and ev["time"] == w["time"]
    assert ev["location"] == {"latitude": w["location"][1], "longitude": w["location"][0]}
    assert ev["event_details"]["smart_"]["observation_uuid"] == obs["uuid"]
    assert "end_time" not in ev and "collection" not in ev


def test_multi_observation_waypoint_becomes_a_collection():
    b = load_bundle(FIXTURE)
    w = next(w for w in _wps(b)
             if sum(len(g["observations"]) for g in w["observation_groups"]) > 1)
    plan = build_waypoint(b, w, "seg", NS, _ev_resolver(b), None)
    assert plan.parent["id"] == er_id(NS, "incident", w["uuid"])
    assert plan.parent["event_type"] == "incident_collection"
    assert all(e["collection"] == {"id": plan.parent["id"]} for e in plan.events)
    ungrouped = build_waypoint(b, w, "seg", NS, _ev_resolver(b, None), None)
    assert ungrouped.parent is None and all("collection" not in e for e in ungrouped.events)


def test_waypoint_without_time_or_location():
    b = load_bundle(FIXTURE)
    w = next(w for w in _wps(b) if w["observation_groups"]
             and w["observation_groups"][0]["observations"])
    w = dict(w, time=None, location=None)
    plan = build_waypoint(b, w, "seg", NS, _ev_resolver(b), "2012-03-17T08:00:00+01:00")
    assert all(e["time"] == "2012-03-17T08:00:00+01:00" for e in plan.events)
    assert all("location" not in e for e in plan.events)


def test_observation_without_event_type_is_reported_not_posted():
    b = load_bundle(FIXTURE)
    w = next(w for w in _wps(b) if w["observation_groups"]
             and w["observation_groups"][0]["observations"])
    r = _ev_resolver(b)
    r.event_types = {}
    plan = build_waypoint(b, w, "seg", NS, r, None)
    assert plan.events == [] and len(plan.unmapped) >= 1


def test_waypoint_without_observations_is_skipped():
    b = load_bundle(FIXTURE)
    w = next(w for w in _wps(b) if not any(g["observations"] for g in w["observation_groups"]))
    assert build_waypoint(b, w, "seg", NS, _ev_resolver(b), None) is None
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_smart_to_er_patrols.py -q`
Expected: FAIL with `ImportError: cannot import name 'build_waypoint'`

- [ ] **Step 3: Implement** (append to `smart_to_er_patrols.py`; extend the choices import to `from .choices import sanitize_choice_value, shorten_choice_value`)

```python
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
    observations = [
        (g, o) for g in w["observation_groups"] for o in g["observations"]
    ]
    if not observations:
        return None
    base: dict = {"time": w.get("time") or fallback_time, "patrol_segments": [segment_id]}
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
    return WaypointPlan(w["uuid"], parent, events, list(w.get("attachments") or []), unmapped)
```

- [ ] **Step 4: Run them to verify they pass**

Run: `uv run pytest tests/test_smart_to_er_patrols.py -q`
Expected: PASS (10 passed)

- [ ] **Step 5: Commit**

```bash
git add src/er_smart_sync/smart_to_er_patrols.py tests/test_smart_to_er_patrols.py
git commit -m "import-patrols: one event per observation, collections for multi-observation waypoints"
```

---

### Task 6: Track observations (`smart_to_er_patrols.py`, part 3)

**Files:**
- Modify: `src/er_smart_sync/smart_to_er_patrols.py`
- Test: `tests/test_smart_to_er_patrols.py` (append)

**Interfaces:**
- Consumes: `LegTimes` / `DayTimes` (Task 2), `parse_ts`.
- Produces:
  - `TrackBatch(member_uuid: str, leg_day_uuid: str, start: str, end: str, observations: list[dict], interpolated: bool)`
  - `track_member(leg) -> str | None`, which picks the leader, else the first member
  - `build_track_batches(leg: dict, lt: LegTimes, by_day: dict) -> list[TrackBatch]`. Each observation has the shape `{"recorded_at", "location": {"latitude", "longitude"}, "additional": {"smart_leg_day_uuid", "smart_track_uuid"}}`, and the `source` key is added by the importer.

- [ ] **Step 1: Write the failing tests** (append)

```python
from er_smart_sync.patrol_times import leg_times
from er_smart_sync.smart_to_er_patrols import build_track_batches, track_member


def test_demo_tracks_use_recorded_times():
    b = load_bundle(FIXTURE)
    leg = b.patrols[0]["legs"][0]
    by_day = tracks_by_day(b.tracks)
    batches = build_track_batches(leg, leg_times(leg, by_day, TZ), by_day)
    leader = next(m["employee_uuid"] for m in leg["members"] if m["leader"])
    assert batches and all(x.member_uuid == leader and not x.interpolated for x in batches)
    first = batches[0].observations[0]
    track = by_day[batches[0].leg_day_uuid][0]
    assert first["recorded_at"] == track["parts"][0]["times"][0]
    lon, lat = track["parts"][0]["coordinates"][0]
    assert first["location"] == {"latitude": lat, "longitude": lon}
    assert first["additional"]["smart_leg_day_uuid"] == batches[0].leg_day_uuid


def test_mismatched_times_are_interpolated_over_the_day():
    day = {"uuid": "d1", "date": "2020-01-01", "start_time": None, "end_time": None,
           "rest_minutes": None, "start": "2020-01-01T08:00:00+01:00",
           "end": "2020-01-01T10:00:00+01:00", "has_track": True}
    leg = {"uuid": "L", "id": "1", "placeholder_dates": False, "start_date": "2020-01-01",
           "end_date": "2020-01-01", "days": [day], "waypoints": [],
           "members": [{"employee_uuid": "m1", "leader": True, "pilot": False}]}
    track = {"uuid": "t", "leg_day_uuid": "d1", "parts": [{
        "coordinates": [[0, 0], [1, 1], [2, 2]],
        "times": ["2020-01-01T08:00:00+01:00"]}]}
    by_day = tracks_by_day([track])
    (batch,) = build_track_batches(leg, leg_times(leg, by_day, TZ), by_day)
    assert batch.interpolated
    times = [o["recorded_at"] for o in batch.observations]
    assert times == ["2020-01-01T08:00:00+01:00", "2020-01-01T09:00:00+01:00",
                     "2020-01-01T10:00:00+01:00"]


def test_track_member_falls_back_and_can_be_absent():
    m = lambda u, lead: {"employee_uuid": u, "leader": lead, "pilot": False}  # noqa: E731
    assert track_member({"members": [m("a", False), m("b", True)]}) == "b"
    assert track_member({"members": [m("a", False), m("b", False)]}) == "a"
    assert track_member({"members": []}) is None
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_smart_to_er_patrols.py -q`
Expected: FAIL with `ImportError: cannot import name 'build_track_batches'`

- [ ] **Step 3: Implement** (append; add `from .patrol_times import DayTimes, LegTimes, leg_times` to the imports)

```python
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
                for c, ts in zip(coords, times)
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
```

- [ ] **Step 4: Run them to verify they pass**

Run: `uv run pytest tests/test_smart_to_er_patrols.py -q`
Expected: PASS (13 passed)

- [ ] **Step 5: Commit**

```bash
git add src/er_smart_sync/smart_to_er_patrols.py tests/test_smart_to_er_patrols.py
git commit -m "import-patrols: track observations per leg day with recorded or interpolated times"
```

---

### Task 7: The import run (`patrol_import.py`) and the preflight collection check

**Files:**
- Create: `src/er_smart_sync/patrol_import.py`
- Modify: `src/er_smart_sync/preflight.py` (add `check_collection_type`, a warning, and add it to the final `ALL_CHECKS`)
- Test: `tests/test_patrol_import.py`, `tests/test_preflight.py` (append one test)

**Interfaces:**
- Consumes: everything above, plus `run_preflight` and `PreflightReport` (`.blocking`, `.blocked_patrols`, `.event_types_map`, `.findings`) from `preflight`, `build_patrol_types` and `build_team_requirements` from `smart_to_er_patrol_schema`, and `ImportLedger`.
- Produces:
  - `ImportOptions(since: date | None = None, until: date | None = None, patrols: tuple = (), limit: int | None = None, skip_events: bool = False, skip_tracks: bool = False, allow_blocking: bool = False, event_type_prefix: str | None = None, event_type_version: str = "v2", timezone: str | None = None)`
  - `ImportResult`, with these fields:
    - `imported: list`
    - `skipped: dict` (uuid to reason)
    - `failed: dict` (uuid to reason)
    - `report: PreflightReport | None`
    - `stopped: bool` (true when preflight blocked)
    - `counts: Counter`
    - `.exit_code` property: 2 if stopped, 1 if failed, else 0
  - `select_patrols(bundle, opts) -> list`
  - `run_import(api, bundle, *, endpoint: str, ledger: ImportLedger, opts: ImportOptions, ns, importer_version: str, report: PreflightReport | None = None) -> ImportResult`. When `report` is None, the preflight is run here. Tests pass a report built from the checks under test.
  - `format_result(result: ImportResult, bundle) -> str`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_patrol_import.py
from collections import Counter
from pathlib import Path

from erclient.er_errors import ERClientNotFound  # noqa: F401  (documents the 404 path)

from er_smart_sync.bundle import load_bundle
from er_smart_sync.import_ledger import ImportLedger
from er_smart_sync.patrol_ids import namespace
from er_smart_sync.patrol_import import ImportOptions, run_import, select_patrols
from er_smart_sync.preflight import check_event_types, run_preflight

FIXTURE = Path(__file__).parent / "fixtures" / "smart_demo_bundle"
ENDPOINT = "https://site.pamdas.org/api/v1.0"


class FakeAPI:
    """A site that holds what was written; reads see earlier writes."""

    def __init__(self, b, *, inactive=(), event_types=None, collection="incident_collection"):
        self.b = b
        members = sorted(b.member_uuids())
        self.subjects = [{"id": f"s{i}", "name": m, "is_active": m not in inactive,
                          "additional": {"smart_member_id": m}}
                         for i, m in enumerate(members)]
        self.patrols, self.events, self.sources = {}, {}, {}
        self.observations, self.files, self.calls = [], [], Counter()
        self.patches = []
        self.collection = collection
        self.event_types = event_types
        self.fail_patrol = None

    # reads (the preflight in these tests runs check_event_types only)
    def list_subjects(self): return self.subjects
    def get_patrol_config(self):
        teams = [{"id": f"t-{r.uuid[:8]}", "display": r.name, "value": r.name,
                  "is_active": True}
                 for r in __import__("er_smart_sync.smart_to_er_patrol_schema",
                                     fromlist=["x"]).build_team_requirements(self.b)]
        return {"members": [{"id": "g"}], "teams": teams}
    def list_event_types(self, version="v2"):
        if self.event_types is not None:
            return self.event_types
        return [{"value": f"smart-ca-import_{h.strip('.').replace('.', '_')}",
                 "is_active": True, "category": "c"}
                for h in self.b.observed_category_hkeys()]

    # import reads/writes
    def collection_event_type(self, version="v2"): return self.collection
    def get_patrol(self, i): return self.patrols.get(i)
    def create_patrol(self, p):
        if self.fail_patrol and p["title"] == self.fail_patrol:
            raise RuntimeError("boom")
        self.calls["create_patrol"] += 1
        self.patrols[p["id"]] = p
        return p
    def patch_patrol(self, i, p):
        self.calls["patch_patrol"] += 1
        self.patches.append(p)
        return p
    def get_event(self, i): return self.events.get(i)
    def create_event(self, e):
        assert e["id"] not in self.events and "end_time" not in e
        self.calls["create_event"] += 1
        self.events[e["id"]] = e
        return e
    def attach_file(self, event_id, filepath, comment=""):
        self.files.append((event_id, filepath))
    def patch_subject(self, sid, payload):
        self.calls["patch_subject"] += 1
        for s in self.subjects:
            if s["id"] == sid:
                s.update(payload)
    def ensure_source_provider(self, key, display): pass  # get-or-create
    def get_source(self, mid): return self.sources.get(mid)
    def create_source(self, p):
        self.calls["create_source"] += 1
        self.sources[p["manufacturer_id"]] = {"id": f"src-{p['manufacturer_id']}", **p}
        return self.sources[p["manufacturer_id"]]
    def assign_source(self, *a): self.calls["assign"] += 1
    def post_observations(self, batch):
        new = [o for o in batch if (o["source"], o["recorded_at"]) not in
               {(x["source"], x["recorded_at"]) for x in self.observations}]
        self.observations += new
        return bool(new)


def _run(api, b, tmp_path, **kw):
    led = ImportLedger(tmp_path / "ledger.json", "site|ca")
    report = run_preflight(api, b, checks=[check_event_types])
    return run_import(api, b, endpoint=ENDPOINT, ledger=led,
                      opts=ImportOptions(timezone="Africa/Libreville", **kw),
                      ns=namespace(ENDPOINT, b.ca_uuid), importer_version="t",
                      report=report)


def test_full_import_then_rerun_writes_nothing(tmp_path):
    b = load_bundle(FIXTURE)
    api = FakeAPI(b)
    r = _run(api, b, tmp_path)
    assert r.exit_code == 0 and len(r.imported) == 5 and not r.failed
    assert len(api.patrols) == 5
    assert api.calls["create_event"] == 141 + 18  # observations + collections
    leaders = {next(m["employee_uuid"] for m in leg["members"] if m["leader"])
               for p in b.patrols for leg in p["legs"]}
    assert api.observations and api.calls["create_source"] == len(leaders)
    writes = sum(api.calls.values())
    r2 = _run(api, b, tmp_path)
    assert r2.exit_code == 0 and sum(api.calls.values()) == writes


def test_half_finished_events_are_completed_without_duplicates(tmp_path):
    b = load_bundle(FIXTURE)
    api = FakeAPI(b)
    _run(api, b, tmp_path, skip_tracks=True)
    first = next(iter(api.events))
    del api.events[first]
    led = ImportLedger(tmp_path / "ledger.json", "site|ca")
    for p in b.patrols:
        led._site["patrols"][p["uuid"]]["stages"] = ["patrol"]  # crash after patrol
    led._save()
    before = api.calls["create_event"]
    _run(api, b, tmp_path, skip_tracks=True)
    assert api.calls["create_event"] == before + 1  # only the missing one


def test_retired_members_are_reactivated_then_retired(tmp_path):
    b = load_bundle(FIXTURE)
    m = sorted(b.member_uuids())[0]
    api = FakeAPI(b, inactive={m})
    r = _run(api, b, tmp_path)
    assert r.exit_code == 0
    s = next(s for s in api.subjects if s["additional"]["smart_member_id"] == m)
    assert s["is_active"] is False and api.calls["patch_subject"] == 2
    assert ImportLedger(tmp_path / "ledger.json", "site|ca").reactivated() == []


def test_one_failing_patrol_does_not_stop_the_others(tmp_path):
    b = load_bundle(FIXTURE)
    api = FakeAPI(b)
    api.fail_patrol = "SMART_000003"
    r = _run(api, b, tmp_path)
    assert r.exit_code == 1 and len(r.imported) == 4
    (reason,) = r.failed.values()
    assert "boom" in reason


def test_existing_patrol_missing_a_segment_fails_with_a_fix(tmp_path):
    b = load_bundle(FIXTURE)
    api = FakeAPI(b)
    _run(api, b, tmp_path, skip_events=True, skip_tracks=True)
    pid = next(i for i, p in api.patrols.items() if p["title"] == "SMART_000005")
    api.patrols[pid] = dict(api.patrols[pid], patrol_segments=api.patrols[pid]["patrol_segments"][:1])
    (tmp_path / "ledger.json").unlink()
    r = _run(api, b, tmp_path, skip_events=True, skip_tracks=True)
    p5 = next(p["uuid"] for p in b.patrols if p["id"] == "SMART_000005")
    assert list(r.failed) == [p5] and "delete" in r.failed[p5].lower()


def test_missing_attachment_file_is_counted_not_fatal(tmp_path):
    b = load_bundle(FIXTURE)  # the fixture ships no attachment files
    r = _run(FakeAPI(b), b, tmp_path, skip_tracks=True)
    assert r.exit_code == 0 and r.counts["attachments_missing"] == 1


def test_blocking_preflight_stops_unless_allowed(tmp_path):
    b = load_bundle(FIXTURE)
    api = FakeAPI(b, event_types=[])
    r = _run(api, b, tmp_path)
    assert r.stopped and r.exit_code == 2 and not api.patrols
    r = _run(api, b, tmp_path, allow_blocking=True)
    assert r.skipped and all("event_types" in v for v in r.skipped.values())


def test_selection_filters():
    from datetime import date

    b = load_bundle(FIXTURE)
    assert [p["id"] for p in select_patrols(b, ImportOptions(patrols=("SMART_000004",)))] == ["SMART_000004"]
    assert len(select_patrols(b, ImportOptions(limit=2))) == 2
    since = select_patrols(b, ImportOptions(since=date(2099, 1, 1)))
    assert since == []
```

Append to `tests/test_preflight.py`:

```python
def test_collection_type_warning():
    from er_smart_sync.preflight import check_collection_type

    b = load_bundle(FIXTURE)
    ets = [{"value": "x", "is_active": True, "is_collection": False}]
    (f,) = run_preflight(SiteFake(b, event_types=ets), b, checks=[check_collection_type]).findings
    assert f.code == "collection_type" and f.level == WARNING and f.count == 18
    ets.append({"value": "incident_collection", "is_active": True, "is_collection": True})
    assert run_preflight(SiteFake(b, event_types=ets), b, checks=[check_collection_type]).findings == []
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_patrol_import.py tests/test_preflight.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'er_smart_sync.patrol_import'` and `ImportError: cannot import name 'check_collection_type'`

- [ ] **Step 3: Implement the preflight check** (in `preflight.py`, before the final `ALL_CHECKS`; then add `check_collection_type` to that list after `check_event_types`)

```python
def check_collection_type(ctx: PreflightContext) -> list:
    multi = [
        (p["uuid"], w["uuid"])
        for p in ctx.bundle.patrols
        for leg in p["legs"]
        for w in leg["waypoints"]
        if sum(len(g["observations"]) for g in w["observation_groups"]) > 1
    ]
    if not multi:
        return []
    ets = ctx.once(
        "event_types", lambda: ctx.api.list_event_types(ctx.event_type_version)
    )
    if any(e.get("is_collection") and e.get("is_active", True) for e in ets):
        return []
    return [
        Finding.of(
            "collection_type",
            WARNING,
            f"{len(multi)} waypoint(s) hold several observations but the site has "
            "no active collection event type, so their events import ungrouped",
            "Activate ER's incident_collection event type (EarthRanger admin > "
            "Event types) to group them.",
            [w for _, w in multi],
            affected=[p for p, _ in multi],
        )
    ]
```

- [ ] **Step 4: Implement the run**

```python
# src/er_smart_sync/patrol_import.py
"""`import-patrols`: write a bundle's patrol history into EarthRanger.

Preflight first (blocking findings stop the run, or with --allow-blocking
skip the affected patrols). Retired members are reactivated for the run and
retired again at the end. Each patrol is isolated: a failure is recorded with
its reason and the run moves on. Per patrol, three stages (patrol, events,
tracks) are recorded in the ledger so a re-run skips what is done; every
write is preceded by a read by id, because das answers a duplicate id with a
500 rather than a 409.
"""

from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from zoneinfo import ZoneInfo

from .bundle import Bundle
from .er_patrol_api import SOURCE_PROVIDER
from .import_ledger import ImportLedger
from .patrol_times import tracks_by_day
from .preflight import PreflightReport, run_preflight
from .smart_to_er_patrol_schema import build_patrol_types, build_team_requirements
from .smart_to_er_patrols import (
    Resolver,
    build_patrol,
    build_track_batches,
    build_waypoint,
)

logger = logging.getLogger(__name__)
OBSERVATION_BATCH = 1000


@dataclass
class ImportOptions:
    since: date | None = None
    until: date | None = None
    patrols: tuple = ()
    limit: int | None = None
    skip_events: bool = False
    skip_tracks: bool = False
    allow_blocking: bool = False
    event_type_prefix: str | None = None
    event_type_version: str = "v2"
    timezone: str | None = None


@dataclass
class ImportResult:
    imported: list = field(default_factory=list)
    skipped: dict = field(default_factory=dict)
    failed: dict = field(default_factory=dict)
    report: PreflightReport | None = None
    stopped: bool = False
    counts: Counter = field(default_factory=Counter)

    @property
    def exit_code(self) -> int:
        return 2 if self.stopped else 1 if self.failed else 0


def select_patrols(bundle: Bundle, opts: ImportOptions) -> list:
    wanted = {w.lower() for w in opts.patrols}
    out = []
    for p in bundle.patrols:
        if wanted and not ({(p.get("id") or "").lower(), p["uuid"].lower()} & wanted):
            continue
        start = date.fromisoformat(p["start_date"]) if p.get("start_date") else None
        if opts.since and (start is None or start < opts.since):
            continue
        if opts.until and (start is None or start > opts.until):
            continue
        out.append(p)
    return out[: opts.limit] if opts.limit else out


def _resolver(api, bundle: Bundle, report: PreflightReport) -> tuple:
    """(Resolver, member uuid -> the whole subject dict)."""
    subjects = {
        (s.get("additional") or {}).get("smart_member_id"): s
        for s in api.list_subjects()
        if (s.get("additional") or {}).get("smart_member_id")
    }
    teams_on_site = {}
    for t in (api.get_patrol_config() or {}).get("teams") or []:
        for k in ("display", "value"):
            if t.get(k):
                teams_on_site[t[k].strip().lower()] = t["id"]
    teams = {
        r.uuid: teams_on_site[r.name.strip().lower()]
        for r in build_team_requirements(bundle)
        if r.name.strip().lower() in teams_on_site
    }
    return Resolver(
        subjects={m: s["id"] for m, s in subjects.items()},
        teams=teams,
        patrol_types={p.transport_uuid: p.value for p in build_patrol_types(bundle)},
        event_types=dict(report.event_types_map),
        collection_type=None,
    ), subjects


def _reactivate(api, ledger: ImportLedger, subjects: dict, patrols: list) -> None:
    needed = {
        subjects[m["employee_uuid"]]["id"]
        for p in patrols
        for leg in p["legs"]
        for m in leg["members"]
        if m["employee_uuid"] in subjects
        and not subjects[m["employee_uuid"]].get("is_active", True)
    }
    for sid in sorted(needed - set(ledger.reactivated())):
        api.patch_subject(sid, {"is_active": True})
    ledger.set_reactivated(sorted(needed | set(ledger.reactivated())))


def _retire(api, ledger: ImportLedger) -> None:
    for sid in ledger.reactivated():
        api.patch_subject(sid, {"is_active": False})
    ledger.set_reactivated([])


def _import_patrol_stage(api, plan, patrol: dict) -> None:
    body = plan.patrol
    existing = api.get_patrol(body["id"])
    if existing is None:
        api.create_patrol(body)
        return
    have = {s.get("id") for s in existing.get("patrol_segments") or []}
    missing = [i for i in plan.segment_ids.values() if i not in have]
    if missing:
        raise RuntimeError(
            f"patrol {patrol.get('id')} is on the site without {len(missing)} of "
            "its segments; das cannot add a segment with a given id to an existing "
            f"patrol. Delete patrol {body['id']} in EarthRanger and run again."
        )
    api.patch_patrol(body["id"], {k: v for k, v in body.items() if k != "id"})


def _import_events(api, bundle, patrol, plan, ns, resolver, ledger, counts) -> None:
    attached = set(ledger.ids(patrol["uuid"]).get("attached", []))
    for leg in patrol["legs"]:
        seg_id = plan.segment_ids[leg["uuid"]]
        lt = plan.leg_times[leg["uuid"]]
        day_start = {d.leg_day_uuid: d.start.isoformat() for d in lt.days}
        for w in leg["waypoints"]:
            wp = build_waypoint(
                bundle, w, seg_id, ns, resolver, day_start.get(w["leg_day_uuid"])
            )
            if wp is None:
                continue
            counts["observations_unmapped"] += len(wp.unmapped)
            for ev in ([wp.parent] if wp.parent else []) + wp.events:
                if api.get_event(ev["id"]) is None:
                    api.create_event(ev)
                    counts["events_created"] += 1
                else:
                    counts["events_existing"] += 1
            if wp.attachments and wp.events and w["uuid"] not in attached:
                for a in wp.attachments:
                    path = bundle.path / a["path"]
                    if not path.is_file():
                        counts["attachments_missing"] += 1
                        logger.warning("attachment missing from the bundle: %s", path)
                        continue
                    api.attach_file(wp.events[0]["id"], str(path), a.get("filename", ""))
                    counts["attachments_posted"] += 1
                ledger.add_id(patrol["uuid"], "attached", w["uuid"])


def _import_tracks(api, patrol, plan, by_day, subjects, counts) -> None:
    sources: dict = {}
    for leg in patrol["legs"]:
        for batch in build_track_batches(leg, plan.leg_times[leg["uuid"]], by_day):
            subject = subjects.get(batch.member_uuid)
            if subject is None:
                counts["tracks_without_subject"] += 1
                continue
            mid = f"smart:{batch.member_uuid}"
            if mid not in sources:
                src = api.get_source(mid) or api.create_source(
                    {
                        "provider": SOURCE_PROVIDER,
                        "manufacturer_id": mid,
                        "source_type": "tracking-device",
                        "model_name": "SMART patrol track",
                        "additional": {"smart_member_id": batch.member_uuid},
                    }
                )
                sources[mid] = src["id"]
            api.assign_source(subject["id"], sources[mid], batch.start, batch.end)
            obs = [{**o, "source": sources[mid]} for o in batch.observations]
            for i in range(0, len(obs), OBSERVATION_BATCH):
                api.post_observations(obs[i : i + OBSERVATION_BATCH])
            counts["track_points"] += len(obs)


def run_import(
    api,
    bundle: Bundle,
    *,
    endpoint: str,
    ledger: ImportLedger,
    opts: ImportOptions,
    ns,
    importer_version: str,
    report: PreflightReport | None = None,
) -> ImportResult:
    result = ImportResult()
    if report is None:
        report = run_preflight(
            api,
            bundle,
            timezone=opts.timezone,
            event_type_prefix=opts.event_type_prefix,
            event_type_version=opts.event_type_version,
        )
    result.report = report
    if report.blocking and not opts.allow_blocking:
        result.stopped = True
        return result
    reasons: dict = {}
    for f in report.blocking:
        for p in f.affected_patrols:
            reasons.setdefault(p, []).append(f.code)
    selected = []
    for p in select_patrols(bundle, opts):
        if p["uuid"] in reasons:
            result.skipped[p["uuid"]] = "blocked by " + ", ".join(reasons[p["uuid"]])
        else:
            selected.append(p)
    resolver, subjects = _resolver(api, bundle, report)
    resolver.collection_type = api.collection_event_type(opts.event_type_version)
    tz = ZoneInfo(bundle.manifest.timezone["name"])
    by_day = tracks_by_day(bundle.tracks)
    todo = [p for p in selected if not {"patrol"} <= ledger.stages(p["uuid"])]
    _reactivate(api, ledger, subjects, todo)
    if not opts.skip_tracks:
        api.ensure_source_provider(SOURCE_PROVIDER, "SMART patrol import")
    try:
        for p in selected:
            done = ledger.stages(p["uuid"])
            want = {"patrol"} | (set() if opts.skip_events else {"events"}) | (
                set() if opts.skip_tracks else {"tracks"}
            )
            if want <= done:
                result.counts["already_imported"] += 1
                result.imported.append(p["uuid"])
                continue
            try:
                plan = build_patrol(bundle, p, ns, resolver, tz, by_day, importer_version)
                if "patrol" not in done:
                    _import_patrol_stage(api, plan, p)
                    ledger.done(p["uuid"], "patrol", patrol_id=plan.patrol["id"])
                if "events" in want and "events" not in done:
                    _import_events(api, bundle, p, plan, ns, resolver, ledger, result.counts)
                    ledger.done(p["uuid"], "events")
                if "tracks" in want and "tracks" not in done:
                    _import_tracks(api, p, plan, by_day, subjects, result.counts)
                    ledger.done(p["uuid"], "tracks")
                result.imported.append(p["uuid"])
            except Exception as e:  # one patrol's failure must not stop the run
                logger.exception("patrol %s failed", p.get("id"))
                result.failed[p["uuid"]] = f"{type(e).__name__}: {e}"
    finally:
        _retire(api, ledger)
    return result


def format_result(result: ImportResult, bundle: Bundle) -> str:
    ids = {p["uuid"]: p.get("id") or p["uuid"] for p in bundle.patrols}
    lines = [
        f"Imported {len(result.imported)}, skipped {len(result.skipped)}, "
        f"failed {len(result.failed)} patrol(s)."
    ]
    for k, v in sorted(result.counts.items()):
        lines.append(f"  {k}: {v}")
    for u, why in result.skipped.items():
        lines.append(f"skipped {ids.get(u, u)}: {why}")
    for u, why in result.failed.items():
        lines.append(f"FAILED {ids.get(u, u)}: {why}")
    return "\n".join(lines)
```

- [ ] **Step 5: Run them to verify they pass**

Run: `uv run pytest tests/test_patrol_import.py tests/test_preflight.py -q`
Expected: PASS. If `test_full_import_then_rerun_writes_nothing` disagrees on the event count, print `api.calls` and `r.counts`. The 141 observations and 18 collection parents come from the fixture (verified 2026-10-14). Do not change the assertion to fit the output.

- [ ] **Step 6: Full suite and lint, then commit**

Run: `uv run pytest -q && uv run ruff check src/er_smart_sync/patrol_import.py src/er_smart_sync/smart_to_er_patrols.py src/er_smart_sync/patrol_times.py src/er_smart_sync/patrol_ids.py src/er_smart_sync/import_ledger.py && uv run ruff format src/er_smart_sync/patrol_import.py src/er_smart_sync/smart_to_er_patrols.py src/er_smart_sync/patrol_times.py src/er_smart_sync/patrol_ids.py src/er_smart_sync/import_ledger.py tests/test_patrol_import.py tests/test_smart_to_er_patrols.py tests/test_patrol_times.py tests/test_patrol_ids.py tests/test_import_ledger.py`
Expected: all pass; ruff clean.

```bash
git add src/er_smart_sync/patrol_import.py src/er_smart_sync/preflight.py tests/test_patrol_import.py tests/test_preflight.py
git commit -m "import-patrols: the run (preflight gate, reactivation, staged per-patrol import, ledger)"
```

---

### Task 8: The `import-patrols` command and its docs

**Files:**
- Modify: `src/er_smart_sync/cli.py` (new command after `patrol-preflight`)
- Create: `docs/cli-reference/import-patrols.md`
- Modify:
  - `docs/workflows/migrate-patrols.md` (replace step 7; drop "the readiness check and the history import follow in later releases")
  - `docs/cli-reference/patrol-preflight.md` (add a `collection_type` row)
  - `mkdocs.yml` (nav entry after patrol-preflight)
  - `docs/changelog.md` (Unreleased > Added)
  - `USAGE.md` and `CLAUDE.md` (add the command; CLAUDE.md says "six main subcommands" and becomes seven)
  - the spec (mark verify-first items 1–5 settled, with the das facts from this plan)
- Test: `tests/test_cli.py` (append)

**Interfaces:**
- Consumes: `run_import`, `ImportOptions`, `format_result`, `ImportLedger`, `namespace`; and from `cli.py`'s module-level imports, `render_text`, `ERPatrolAPI`, `load_bundle`, `BundleError`, `DryRunERClient`, `_resolve_bundle_options`, `_check_bundle_timezone` and `_make_er_client`.
- Produces: the `import-patrols` CLI command. Exit codes: 0 when clean, 1 when any patrol failed, 2 when preflight blocks.

- [ ] **Step 1: Write the failing test** (append to `tests/test_cli.py`)

```python
def test_import_patrols_wires_options_and_exit_code(tmp_path, monkeypatch):
    seen = {}

    def fake_run(api, bundle, *, endpoint, ledger, opts, ns, importer_version):
        from er_smart_sync.patrol_import import ImportResult

        seen.update(opts=opts, ledger=ledger, endpoint=endpoint)
        r = ImportResult()
        r.failed["x"] = "boom"
        return r

    monkeypatch.setattr("er_smart_sync.patrol_import.run_import", fake_run)
    monkeypatch.setattr("er_smart_sync.cli._make_er_client", lambda **k: MagicMock())
    bundle = Path(__file__).parent / "fixtures" / "smart_demo_bundle"
    result = CliRunner().invoke(main, [
        "import-patrols", "--bundle", str(bundle), "--er-endpoint", "er.test",
        "--er-token", "t", "--since", "2012-01-01", "--patrol", "SMART_000001",
        "--skip-tracks", "--limit", "3", "--ledger", str(tmp_path / "l.json"),
    ])
    assert result.exit_code == 1, result.output
    o = seen["opts"]
    assert o.since.isoformat() == "2012-01-01" and o.patrols == ("SMART_000001",)
    assert o.skip_tracks and o.limit == 3 and not seen["ledger"].read_only
    assert "FAILED" in result.output
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_cli.py -q -k import_patrols`
Expected: FAIL (exit code 2: "No such command 'import-patrols'")

- [ ] **Step 3: Implement the command** (in `cli.py`, after `patrol_preflight_cmd`)

```python
@main.command("import-patrols")
@click.option("--config", "config_file", type=click.Path(exists=True),
              help="YAML config file (as for patrol-datamodel)")
@click.option("--bundle", "bundle_dir", type=click.Path(file_okay=False),
              help="smart-able migration bundle directory")
@er_options
@click.option("--smart-language", default=None, help="Language for labels")
@click.option("--timezone", default=None, help="Assert the bundle's timezone (IANA name)")
@click.option("--event-type-prefix", default=None,
              help="Prefix of the data model push to import against")
@click.option("--event-type-version", type=click.Choice(["v1", "v2"]), default=None)
@click.option("--since", type=click.DateTime(["%Y-%m-%d"]), default=None,
              help="Only patrols starting on or after this date")
@click.option("--until", type=click.DateTime(["%Y-%m-%d"]), default=None,
              help="Only patrols starting on or before this date")
@click.option("--patrol", "patrol_ids", multiple=True,
              help="A SMART patrol id or uuid (repeatable)")
@click.option("--limit", type=int, default=None, help="At most N patrols")
@click.option("--skip-events", is_flag=True, help="Do not import waypoint events")
@click.option("--skip-tracks", is_flag=True, help="Do not import track points")
@click.option("--allow-blocking", is_flag=True,
              help="Skip patrols with blocking preflight findings instead of stopping")
@click.option("--ledger", "ledger_path", type=click.Path(dir_okay=False), default=None,
              help="Import ledger (default: import-ledger.json beside the bundle)")
@click.pass_context
def import_patrols_cmd(ctx, config_file, bundle_dir, er_endpoint, er_token,
                       er_username, er_password, er_id, smart_language, timezone,
                       event_type_prefix, event_type_version, since, until,
                       patrol_ids, limit, skip_events, skip_tracks, allow_blocking,
                       ledger_path):
    """Import a bundle's patrols, waypoint events and tracks into EarthRanger.

    Runs the patrol-preflight checks first. Exit status: 0 done, 1 some
    patrols failed, 2 blocking preflight findings.
    """
    from importlib.metadata import version as pkg_version
    from pathlib import Path
    from urllib.parse import urlparse

    from . import patrol_import
    from .import_ledger import ImportLedger
    from .patrol_ids import namespace

    bundle_path, er, language, tz = _resolve_bundle_options(
        config_file, bundle_dir, er_endpoint, er_token, er_username, er_password,
        er_id, smart_language, timezone,
    )
    try:
        bundle = load_bundle(Path(bundle_path), language=language)
    except BundleError as e:
        raise click.ClickException(str(e)) from None
    _check_bundle_timezone(bundle, tz)
    er_client = _make_er_client(
        endpoint=er.endpoint, token=er.token, username=er.login, password=er.password
    )
    dry = bool(ctx.obj and ctx.obj.get("dry_run"))
    if dry:
        er_client = DryRunERClient(er_client)
        click.echo("Dry run mode: no writes will be sent to ER.", err=True)
    ledger = ImportLedger(
        Path(ledger_path) if ledger_path
        else Path(bundle_path).resolve().parent / "import-ledger.json",
        f"{urlparse(er.endpoint).netloc}|{bundle.ca_uuid}",
        read_only=dry,
    )
    opts = patrol_import.ImportOptions(
        since=since.date() if since else None,
        until=until.date() if until else None,
        patrols=tuple(patrol_ids),
        limit=limit,
        skip_events=skip_events,
        skip_tracks=skip_tracks,
        allow_blocking=allow_blocking,
        event_type_prefix=event_type_prefix,
        event_type_version=event_type_version or er.event_type_version,
        timezone=tz,
    )
    result = patrol_import.run_import(
        ERPatrolAPI(er_client), bundle, endpoint=er.endpoint, ledger=ledger, opts=opts,
        ns=namespace(er.endpoint, bundle.ca_uuid, er.patrol_namespace),
        importer_version=pkg_version("er-smart-sync"),
    )
    if result.stopped:
        click.echo(render_text(result.report))
        click.echo("Fix the blocking findings, or pass --allow-blocking to skip "
                   "the affected patrols.")
    else:
        click.echo(patrol_import.format_result(result, bundle))
    ctx.exit(result.exit_code)
```

The test patches `er_smart_sync.patrol_import.run_import`. The command reaches it as an attribute of the `patrol_import` module, so the patch applies.

- [ ] **Step 4: Run it to verify it passes, then the full suite**

Run: `uv run pytest tests/test_cli.py -q -k import_patrols && uv run pytest -q`
Expected: PASS; full suite green.

- [ ] **Step 5: Write the docs**

`docs/cli-reference/import-patrols.md`, with these sections:
- **Synopsis:** the command line.
- **Options:** a table with every flag above.
- **What it writes:** the patrol, a segment per leg, events with collections, attachments, tracks and the source per leader.
- **Identity and re-runs:** the uuid5 namespace, the ledger file, and GET-before-POST.
- **Retired staff:** reactivated for the run, then retired again.
- **Exit status.**
- **Undoing an import:** delete the patrols whose `segment_details.smart_.ca_uuid` is the conservation area; delete events whose `event_details.smart_` names its waypoints; delete the `smart:<member uuid>` sources of provider `smart_patrol_import`. The ledger lists every patrol id.

Replace workflow step 7 with:

"7. Run `er-smart-sync import-patrols --config sync.yaml --since <first day> --until <a week later>`, check one patrol in ER (legs, members, events on the map, the leader's track), then run without the date window. Re-running is safe: finished patrols are skipped. See [import-patrols](../cli-reference/import-patrols.md)."

Add `- import-patrols: cli-reference/import-patrols.md` to `mkdocs.yml` after patrol-preflight.

Add the `collection_type` row to the patrol-preflight checks table: `| collection_type | warning | waypoints with several observations and no active collection event type | activate incident_collection |`.

Changelog entry under Unreleased > Added: `**import-patrols.**`, one paragraph covering what it writes, re-runs, `--allow-blocking`, and the exit codes (ERA-14139, ERA-14140, ERA-14141).

Spec: under "Verify-first items", mark 1–5 "Settled 2026-10-14", each with one line of das fact from this plan's "das facts".

Run: `uv run mkdocs build --strict`
Expected: "Documentation built", no warnings.

- [ ] **Step 6: Commit**

```bash
git add src/er_smart_sync/cli.py tests/test_cli.py docs mkdocs.yml USAGE.md CLAUDE.md
git commit -m "import-patrols command and docs"
```

---

### Task 9: Live verification on the gundi-dev sandbox

**Files:**
- Modify: `docs/changelog.md` and `docs/cli-reference/import-patrols.md` (record what was verified)

**Interfaces:**
- Consumes: the finished command. gundi-dev (`https://gundi-dev.staging.pamdas.org`) is a sandbox the user allows writes to. It already has the demo data model (pushed with `--bundle`), the patrol data model, the roster group and the team.

- [ ] **Step 1: Refresh the token** (never print it): `cd ~/padas/earthranger-cli && uv run er --profile gundi-dev auth status`. The token is in `~/.config/er-events/tokens/gundi-dev.json` under `access_token`.

- [ ] **Step 2: Dry run**

Run: `uv run er-smart-sync --dry-run import-patrols --bundle ~/padas/er-smart-able/work/export/SMART/bundle --er-endpoint gundi-dev.staging.pamdas.org --er-token "$TOK" --timezone Africa/Libreville`
Expected: exit 0. The summary shows 5 imported and counts for events and track points, and no ledger file is written.

- [ ] **Step 3: One patrol, for real**

Run the same command without `--dry-run`, adding `--patrol SMART_000001`.
Expected: exit 0. Then, read-only, `GET activity/patrols/<id>` (the id is in `import-ledger.json`) must show one segment with the team, leader, members, a `time_range` and `segment_details.smart_`. `GET activity/events/?patrol_segment=<segment id>` must list the events. `GET observations?source_id=<source>` must return track points. If any of these is wrong, stop and debug with superpowers:systematic-debugging; do not proceed to Step 4.

- [ ] **Step 4: The rest, then a re-run**

Run without `--patrol`. Expected: exit 0, 5 imported. Run again. Expected: exit 0, and `already_imported: 5`, with no `events_created` and no new patrols.

- [ ] **Step 5: Record and commit**

Add a "Verified" sentence to the changelog entry and an "Example" section to `docs/cli-reference/import-patrols.md`, with the real summary output (counts only, no tokens).

```bash
git add docs/changelog.md docs/cli-reference/import-patrols.md
git commit -m "Docs: import-patrols verified against the gundi-dev sandbox"
```
