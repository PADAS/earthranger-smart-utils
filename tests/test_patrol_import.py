from collections import Counter
from pathlib import Path

import pytest
from erclient.er_errors import (
    ERClientException,
    ERClientNotFound,  # noqa: F401  (documents the 404 path)
)

from er_smart_sync.bundle import load_bundle
from er_smart_sync.er_patrol_api import ERPatrolAPI
from er_smart_sync.import_ledger import ImportLedger
from er_smart_sync.patrol_ids import namespace
from er_smart_sync.patrol_import import ImportOptions, run_import, select_patrols
from er_smart_sync.preflight import check_event_types, run_preflight

FIXTURE = Path(__file__).parent / "fixtures" / "smart_demo_bundle"
ENDPOINT = "https://site.pamdas.org/api/v1.0"


class FakeAPI:
    """A site that holds what was written; reads see earlier writes."""

    def __init__(
        self, b, *, inactive=(), event_types=None, collection="incident_collection"
    ):
        self.b = b
        members = sorted(b.member_uuids())
        self.subjects = [
            {
                "id": f"s{i}",
                "name": m,
                "is_active": m not in inactive,
                "additional": {"smart_member_id": m},
            }
            for i, m in enumerate(members)
        ]
        self.patrols, self.events, self.sources = {}, {}, {}
        self.observations, self.files, self.calls = [], [], Counter()
        self.patches = []
        self.collection = collection
        self.event_types = event_types
        self.fail_patrol = None
        self.reject_event = None

    # reads (the preflight in these tests runs check_event_types only)
    def list_subjects(self):
        return self.subjects

    def get_patrol_config(self):
        teams = [
            {
                "id": f"t-{r.uuid[:8]}",
                "display": r.name,
                "value": r.name,
                "is_active": True,
            }
            for r in __import__(
                "er_smart_sync.smart_to_er_patrol_schema", fromlist=["x"]
            ).build_team_requirements(self.b)
        ]
        return {"members": [{"id": "g"}], "teams": teams}

    def list_event_types(self, version="v2"):
        if self.event_types is not None:
            return self.event_types
        return [
            {
                "value": f"smart-ca-import_{h.strip('.').replace('.', '_')}",
                "is_active": True,
                "category": "c",
            }
            for h in self.b.observed_category_hkeys()
        ]

    # import reads/writes
    def collection_event_type(self):
        return self.collection

    @staticmethod
    def event_ids_of(patrol):
        return ERPatrolAPI.event_ids_of(patrol)

    def get_patrol(self, i):
        self.calls["get_patrol"] += 1
        p = self.patrols.get(i)
        if p is None:
            return None
        segs = [
            {
                **s,
                "events": [
                    {"id": e["id"]}
                    for e in self.events.values()
                    if s["id"] in e.get("patrol_segments", [])
                ],
            }
            for s in p.get("patrol_segments", [])
        ]
        return {**p, "patrol_segments": segs}

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

    def get_event(self, i):
        self.calls["get_event"] += 1
        return self.events.get(i)

    def create_event(self, e):
        assert "end_time" not in e
        if e["id"] in self.events:  # das answers a duplicate id with a 500
            raise ERClientException("x\n\t 500 from ER. Message: duplicate key")
        self.calls["create_event"] += 1
        self.events[e["id"]] = e
        return e

    def create_events(self, batch):
        self.calls["create_events"] += 1
        if any(e["id"] in self.events for e in batch):
            raise ERClientException("x\n\t 500 from ER. Message: duplicate key")
        if self.reject_event and any(e["id"] == self.reject_event for e in batch):
            raise ERClientException("x\n\t 400 from ER. Message: invalid")
        for e in batch:  # atomic: validated above, written here
            assert "end_time" not in e
            self.calls["create_event"] += 1
            self.events[e["id"]] = e
        return list(batch)

    def attach_file(self, event_id, filepath, comment=""):
        self.files.append((event_id, filepath))
        self.events[event_id].setdefault("files", []).append(
            {"filename": Path(filepath).name}
        )

    def patch_subject(self, sid, payload):
        self.calls["patch_subject"] += 1
        for s in self.subjects:
            if s["id"] == sid:
                s.update(payload)

    def ensure_source_provider(self, key, display):
        pass  # get-or-create

    def get_source(self, mid):
        self.calls["get_source"] += 1
        return self.sources.get(mid)

    def create_source(self, p):
        self.calls["create_source"] += 1
        self.sources[p["manufacturer_id"]] = {"id": f"src-{p['manufacturer_id']}", **p}
        return self.sources[p["manufacturer_id"]]

    def assign_source(self, *a):
        self.calls["assign"] += 1

    def post_observations(self, batch):
        new = [
            o
            for o in batch
            if (o["source"], o["recorded_at"])
            not in {(x["source"], x["recorded_at"]) for x in self.observations}
        ]
        self.observations += new
        return bool(new)


def _run(api, b, tmp_path, **kw):
    led = ImportLedger(tmp_path / "ledger.json", "site|ca")
    report = run_preflight(api, b, checks=[check_event_types])
    return run_import(
        api,
        b,
        endpoint=ENDPOINT,
        ledger=led,
        opts=ImportOptions(timezone="Africa/Libreville", **kw),
        ns=namespace(ENDPOINT, b.ca_uuid),
        importer_version="t",
        report=report,
    )


def test_full_import_then_rerun_writes_nothing(tmp_path):
    b = load_bundle(FIXTURE)
    api = FakeAPI(b)
    r = _run(api, b, tmp_path)
    assert r.exit_code == 0 and len(r.imported) == 5 and not r.failed
    assert len(api.patrols) == 5
    assert api.calls["create_event"] == 141 + 18  # observations + collections
    tracked = {t["leg_uuid"] for t in b.tracks}
    leaders = {
        next(m["employee_uuid"] for m in leg["members"] if m["leader"])
        for p in b.patrols
        for leg in p["legs"]
        if leg["uuid"] in tracked
    }
    assert api.observations and api.calls["create_source"] == len(leaders)
    writes = sum(api.calls.values())
    r2 = _run(api, b, tmp_path)
    assert r2.exit_code == 0 and sum(api.calls.values()) == writes


def test_track_sources_are_looked_up_once_per_leader(tmp_path):
    b = load_bundle(FIXTURE)
    api = FakeAPI(b)
    _run(api, b, tmp_path)
    tracked = {t["leg_uuid"] for t in b.tracks}
    leaders = {
        next(m["employee_uuid"] for m in leg["members"] if m["leader"])
        for p in b.patrols
        for leg in p["legs"]
        if leg["uuid"] in tracked
    }
    # one lookup per distinct leader for the whole run, not one per patrol
    assert api.calls["get_source"] == len(leaders)
    assert api.calls["create_source"] == len(leaders)


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
    api.patrols[pid] = dict(
        api.patrols[pid], patrol_segments=api.patrols[pid]["patrol_segments"][:1]
    )
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
    assert [
        p["id"] for p in select_patrols(b, ImportOptions(patrols=("SMART_000004",)))
    ] == ["SMART_000004"]
    assert len(select_patrols(b, ImportOptions(limit=2))) == 2
    since = select_patrols(b, ImportOptions(since=date(2099, 1, 1)))
    assert since == []


def test_setup_failure_still_retires_reactivated_members(tmp_path):
    b = load_bundle(FIXTURE)
    m = sorted(b.member_uuids())[0]
    api = FakeAPI(b, inactive={m})

    def broken(key, display):
        raise RuntimeError("provider refused")

    api.ensure_source_provider = broken
    with pytest.raises(RuntimeError):
        _run(api, b, tmp_path)
    s = next(s for s in api.subjects if s["additional"]["smart_member_id"] == m)
    assert s["is_active"] is False
    assert ImportLedger(tmp_path / "ledger.json", "site|ca").reactivated() == []


def test_attachment_not_posted_twice_without_the_ledger(tmp_path):
    """The event's own file list, not just the ledger, guards attachments."""
    import shutil

    shutil.copytree(FIXTURE, tmp_path / "b")
    b0 = load_bundle(tmp_path / "b")
    w = next(
        w
        for p in b0.patrols
        for leg in p["legs"]
        for w in leg["waypoints"]
        if w["attachments"]
    )
    a = w["attachments"][0]
    (tmp_path / "b" / a["path"]).parent.mkdir(parents=True)
    (tmp_path / "b" / a["path"]).write_bytes(b"jpg")
    b = load_bundle(tmp_path / "b")
    api = FakeAPI(b)
    _run(api, b, tmp_path, skip_tracks=True)
    assert len(api.files) == 1
    (tmp_path / "ledger.json").unlink()
    r = _run(api, b, tmp_path, skip_tracks=True)
    assert len(api.files) == 1 and r.counts["attachments_existing"] == 1


def test_reactivation_failure_still_retires_those_reactivated(tmp_path):
    b = load_bundle(FIXTURE)
    first, second = sorted(b.member_uuids())[:2]
    api = FakeAPI(b, inactive={first, second})
    real = api.patch_subject
    calls = []

    def flaky(sid, payload):
        calls.append(payload)
        if payload == {"is_active": True} and len(calls) == 2:
            raise RuntimeError("503")
        real(sid, payload)

    api.patch_subject = flaky
    with pytest.raises(RuntimeError):
        _run(api, b, tmp_path)
    for m in (first, second):
        s = next(s for s in api.subjects if s["additional"]["smart_member_id"] == m)
        assert s["is_active"] is False
    assert ImportLedger(tmp_path / "ledger.json", "site|ca").reactivated() == []


def test_limit_moves_on_to_patrols_not_yet_imported(tmp_path):
    b = load_bundle(FIXTURE)
    api = FakeAPI(b)
    _run(api, b, tmp_path, limit=2, skip_tracks=True)
    _run(api, b, tmp_path, limit=2, skip_tracks=True)
    assert len(api.patrols) == 4


def test_attached_ids_are_dropped_once_events_are_done(tmp_path):
    import shutil

    shutil.copytree(FIXTURE, tmp_path / "b")
    b0 = load_bundle(tmp_path / "b")
    w = next(
        w
        for p in b0.patrols
        for leg in p["legs"]
        for w in leg["waypoints"]
        if w["attachments"]
    )
    path = tmp_path / "b" / w["attachments"][0]["path"]
    path.parent.mkdir(parents=True)
    path.write_bytes(b"jpg")
    b = load_bundle(tmp_path / "b")
    _run(FakeAPI(b), b, tmp_path, skip_tracks=True)
    led = ImportLedger(tmp_path / "ledger.json", "site|ca")
    assert all("attached" not in led.ids(p["uuid"]) for p in b.patrols)


def test_fresh_import_never_probes_events(tmp_path):
    b = load_bundle(FIXTURE)
    api = FakeAPI(b)
    r = _run(api, b, tmp_path)
    assert r.exit_code == 0 and not r.failed
    assert api.calls["create_event"] == 141 + 18
    assert api.calls["get_event"] == 0


def test_rerun_without_ledger_uses_the_patrol_event_list(tmp_path):
    b = load_bundle(FIXTURE)
    api = FakeAPI(b)
    _run(api, b, tmp_path)
    created = api.calls["create_event"]
    (tmp_path / "ledger.json").unlink()
    r = _run(api, b, tmp_path)
    assert r.exit_code == 0 and api.calls["create_event"] == created
    assert r.counts["events_existing"] == 141 + 18
    # the patrol GET carried the event ids; the only event reads are the lazy
    # file-list reads in _attach, one per waypoint with photos
    assert api.calls["get_event"] == 1


def test_orphaned_events_after_a_deleted_patrol_are_recovered(tmp_path):
    b = load_bundle(FIXTURE)
    api = FakeAPI(b)
    _run(api, b, tmp_path)
    created = api.calls["create_event"]
    # an admin deletes one patrol in ER; its events stay, with their ids
    victim = next(iter(api.patrols))
    del api.patrols[victim]
    (tmp_path / "ledger.json").unlink()
    r = _run(api, b, tmp_path)
    assert r.exit_code == 0 and not r.failed
    assert len(api.patrols) == 5 and api.calls["create_event"] == created
    # the duplicate-id 500 was turned into a probe, once per orphaned event
    assert api.calls["get_event"] > 0


def _all_event_ids(b, uuids=None):
    from er_smart_sync.patrol_ids import er_id

    ns = namespace(ENDPOINT, b.ca_uuid)
    return [
        er_id(ns, "event", o["uuid"])
        for p in b.patrols
        if uuids is None or p["uuid"] in uuids
        for leg in p["legs"]
        for w in leg["waypoints"]
        for g in w["observation_groups"]
        for o in g["observations"]
    ]


def test_events_are_created_in_batches_not_one_by_one(tmp_path):
    b = load_bundle(FIXTURE)
    api = FakeAPI(b)
    r = _run(api, b, tmp_path)
    assert r.exit_code == 0 and api.calls["create_event"] == 141 + 18
    # 5 patrols; each patrol's parents (if any) and children go in a few lists
    assert 1 <= api.calls["create_events"] <= 15
    assert api.calls["get_event"] == 0
    # every child still points at a parent that exists
    for e in api.events.values():
        if e.get("collection"):
            assert e["collection"]["id"] in api.events


def test_a_rejected_batch_falls_back_to_single_posts(tmp_path):
    b = load_bundle(FIXTURE)
    api = FakeAPI(b)
    api.reject_event = next(iter(_all_event_ids(b)))  # one invalid observation
    orig = api.create_event

    def single(e):
        if e["id"] == api.reject_event:
            raise ERClientException("x\n\t 400 from ER. Message: invalid")
        return orig(e)

    api.create_event = single
    r = _run(api, b, tmp_path)
    # the patrol with the bad observation fails; every other event of every
    # other patrol is on the site; nothing is duplicated
    assert len(r.failed) == 1
    assert len(r.imported) == 4
    assert set(_all_event_ids(b, set(r.imported))) <= set(api.events)
    assert len(api.events) == api.calls["create_event"]


class SharedSite:
    """Several FakeAPI views over one set of dicts, as N workers would see
    one EarthRanger site."""

    def __init__(self, b, **kw):
        self.base = FakeAPI(b, **kw)

    def __call__(self):
        api = FakeAPI(self.base.b)
        for name in (
            "subjects",
            "patrols",
            "events",
            "sources",
            "observations",
            "files",
            "calls",
            "patches",
        ):
            setattr(api, name, getattr(self.base, name))
        api.collection, api.event_types = self.base.collection, self.base.event_types
        api.fail_patrol = self.base.fail_patrol
        for name in ("create_source", "create_event", "create_events"):
            if name in self.base.__dict__:  # a test monkeypatched the base instance
                setattr(api, name, self.base.__dict__[name])
        return api


def _run_workers(site, b, tmp_path, workers, **kw):
    led = ImportLedger(tmp_path / "ledger.json", "site|ca")
    api = site()
    report = run_preflight(api, b, checks=[check_event_types])
    return run_import(
        api,
        b,
        endpoint=ENDPOINT,
        ledger=led,
        opts=ImportOptions(timezone="Africa/Libreville", workers=workers, **kw),
        ns=namespace(ENDPOINT, b.ca_uuid),
        importer_version="t",
        report=report,
        api_factory=site,
    )


def test_workers_import_everything_once(tmp_path):
    b = load_bundle(FIXTURE)
    site = SharedSite(b)
    r = _run_workers(site, b, tmp_path, workers=4)
    assert r.exit_code == 0 and len(r.imported) == 5 and not r.failed
    assert len(site.base.patrols) == 5
    assert site.base.calls["create_event"] == 141 + 18
    assert len(site.base.events) == 141 + 18
    r2 = _run_workers(site, b, tmp_path, workers=4)
    assert r2.counts["already_imported"] == 5


def test_workers_share_one_source_per_leader(tmp_path):
    b = load_bundle(FIXTURE)
    site = SharedSite(b)
    real = site.base.create_source

    def once(p):  # das: a second create for the same manufacturer id fails
        if p["manufacturer_id"] in site.base.sources:
            raise ERClientException("x\n\t 400 from ER. Message: exists")
        return real(p)

    site.base.create_source = once
    r = _run_workers(site, b, tmp_path, workers=4)
    assert not r.failed
    tracked = {t["leg_uuid"] for t in b.tracks}
    leaders = {
        next(m["employee_uuid"] for m in leg["members"] if m["leader"])
        for p in b.patrols
        for leg in p["legs"]
        if leg["uuid"] in tracked
    }
    assert len(site.base.sources) == len(leaders)


def test_workers_still_retire_reactivated_members_on_failure(tmp_path):
    b = load_bundle(FIXTURE)
    retired = sorted(b.member_uuids())[:2]
    site = SharedSite(b, inactive=retired)
    site.base.fail_patrol = b.patrols[2]["id"]
    r = _run_workers(site, b, tmp_path, workers=3)
    assert len(r.failed) == 1
    for s in site.base.subjects:
        if s["additional"]["smart_member_id"] in retired:
            assert s["is_active"] is False
