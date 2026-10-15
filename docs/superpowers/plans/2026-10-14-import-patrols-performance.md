# import-patrols Performance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cut `import-patrols` from ~23 s to ~2 s effective per patrol (Albay: ~9.5 h → under an hour) without weakening its re-run safety.

**Architecture:** Four client-side changes to `patrol_import.py`/`er_patrol_api.py`, each independently shippable and measurable: hoist the track-source cache to the run; stop probing every event before creating it and instead recover from a duplicate-id failure; create a patrol's events in one list `POST` (das already accepts a list atomically); run patrols on a thread pool behind a `--workers` flag with a locked ledger and one ER client per worker. The read-before-write path stays as the fallback whenever a bulk request is rejected, so idempotence is unchanged. A timing harness becomes a repo script so every task is benchmarked the same way.

**Tech Stack:** Python 3.10+, `earthranger-client` (`ERClient`, synchronous `requests`), `concurrent.futures.ThreadPoolExecutor`, `threading.Lock`, Click, pytest with the existing `FakeAPI` in `tests/test_patrol_import.py` and the `MagicMock` client in `tests/test_er_patrol_api.py`.

**Spec:** `docs/superpowers/specs/2026-10-14-import-patrols-performance.md` (measurements and options), under the migration design `docs/superpowers/specs/2026-10-12-smart-patrol-migration-design.md` (idempotence rules this plan must keep).

## Global Constraints

- `requires-python = ">=3.10"`; Pydantic v1 (`pydantic>=1.10,<2.0`); `ruff check src tests`, `ruff format src tests`, `ty check` and `pytest` clean before every commit.
- Idempotence contract from the migration spec is unchanged: a re-run writes nothing new, with or without the ledger. Event, patrol and collection ids stay `er_id(ns, kind, smart_uuid)`.
- das answers a duplicate-id `POST` with a **500**, not a 409 (module docstring of `patrol_import.py`); any path that creates without probing first must recover from that.
- das `POST activity/events` with a list body is **atomic**: on any invalid item the whole list is rejected with 400 and nothing is written (`das/activity/views/events/base.py`, `EventView.create`). A single-event body returns a dict, a multi-event body returns a list.
- das rejects observations it already has (unique on source + `recorded_at`) and answers 409 only when none in the batch was new; `post_observations` already tolerates that.
- Default behaviour must not change for a user who passes no new flag: `--workers` defaults to **1**; batching and probe-skipping are on by default because they are pure client-side optimisations with a fallback.
- Dry runs (`--dry-run`, `DryRunERClient`) keep working and always run with one worker.
- Log lines emitted from worker threads carry the SMART patrol id so interleaved output stays readable.
- Keep file layout: no new package; new helpers go in `patrol_import.py` and `er_patrol_api.py`; the harness goes in `scripts/time_one_patrol.py`.

## Review Focus

1. A patrol whose list `POST` of events is rejected with 400 because one observation is invalid: the user expects the other observations of that patrol to be imported and the patrol to be reported as failed only if a single-event post also fails. (Task 3 tests the fallback to per-event posts.)
2. A patrol deleted by hand in ER after its events were created, then re-imported: the events still exist with their deterministic ids; the user expects the patrol to come back and the events to be reported as existing, not a 500 crash. (Task 2 tests duplicate-id recovery.)
3. Ctrl-C or an exception in one worker mid-run: the user expects reactivated retired rangers to be retired again and the ledger to be consistent for every patrol that finished. (Task 4 tests that `finally` retires with workers > 1 and that the ledger has no partial stage.)
4. Two patrols with the same leader on different workers create the same track source at the same moment: the user expects one source and both tracks attached to it, not a crash or two sources. (Task 4 tests the shared cache under a race by making `create_source` raise for the second caller.)
5. `--workers 4` together with `--dry-run`: the user expects a readable dry run, not interleaved or duplicated "would call" lines. (Task 4 tests that dry runs force one worker.)

---

### Task 1: Hoist the track-source cache to the run

**Files:**
- Modify: `src/er_smart_sync/patrol_import.py` (`_import_tracks`, `run_import`)
- Test: `tests/test_patrol_import.py`

**Interfaces:**
- Consumes: `api.get_source(manufacturer_id) -> dict | None`, `api.create_source(payload) -> dict` (existing).
- Produces: `_import_tracks(api, patrol, plan, by_day, subjects, counts, sources: dict)` — `sources` maps `"smart:<member uuid>"` to the ER source id and lives for the whole run. Task 4 wraps it in a lock.

- [ ] **Step 1: Write the failing test**

Add to `FakeAPI` a call counter on `get_source`, then the test:

```python
    def get_source(self, mid):
        self.calls["get_source"] += 1
        return self.sources.get(mid)
```

```python
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
```

- [ ] **Step 2: Run it to verify it fails**

Run: `pytest tests/test_patrol_import.py::test_track_sources_are_looked_up_once_per_leader -v`
Expected: FAIL — `get_source` is called once per patrol with a track (5 in the fixture), more than the number of distinct leaders.

- [ ] **Step 3: Implement**

In `_import_tracks`, take the cache as a parameter instead of creating it:

```python
def _import_tracks(api, patrol, plan, by_day, subjects, counts, sources: dict) -> None:
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
```

In `run_import`, create it once before the loop (next to `by_day = tracks_by_day(bundle.tracks)`) and pass it:

```python
    sources: dict = {}
    ...
                if "tracks" in want and "tracks" not in done:
                    _import_tracks(api, p, plan, by_day, subjects, result.counts, sources)
```

- [ ] **Step 4: Run the tests**

Run: `pytest tests/test_patrol_import.py -v`
Expected: all PASS, including the new test.

- [ ] **Step 5: Commit**

```bash
git add src/er_smart_sync/patrol_import.py tests/test_patrol_import.py
git commit -m "import-patrols: look each leader's track source up once per run"
```

---

### Task 2: Create events without probing first; recover from a duplicate id

**Files:**
- Modify: `src/er_smart_sync/patrol_import.py` (`_import_patrol_stage`, `_import_events`, `run_import`)
- Modify: `src/er_smart_sync/er_patrol_api.py` (`get_patrol_events`)
- Test: `tests/test_patrol_import.py`, `tests/test_er_patrol_api.py`

**Interfaces:**
- Consumes: `api.get_patrol(id) -> dict | None` whose `patrol_segments[].events` lists `{"id": ...}` for events on the site (das patrol serializer).
- Produces:
  - `_import_patrol_stage(api, plan, patrol) -> set[str]`: the ids of events already on the patrol's segments (empty for a patrol created now).
  - `_import_events(api, bundle, patrol, plan, ns, resolver, ledger, counts, known: set[str] | None)`: `known` is that set, or `None` when the patrol stage was done in an earlier run (then one `api.get_patrol` is made to learn it).
  - `_create_event_safely(api, ev, counts) -> dict`: posts one event; on `ERClientException` probes `api.get_event(ev["id"])` and returns the existing event (counting `events_existing`) or re-raises.
  - `ERPatrolAPI.event_ids_of(patrol: dict) -> set[str]`: pure helper extracting the ids from a patrol dict.

- [ ] **Step 1: Write the failing tests**

In `tests/test_patrol_import.py`, make `FakeAPI` behave like das: `get_patrol` returns segments with their events, `create_event` raises on a duplicate id, and probes are counted.

```python
    def get_patrol(self, i):
        self.calls["get_patrol"] += 1
        p = self.patrols.get(i)
        if p is None:
            return None
        segs = [
            {**s, "events": [{"id": e["id"]} for e in self.events.values()
                              if s["id"] in e.get("patrol_segments", [])]}
            for s in p.get("patrol_segments", [])
        ]
        return {**p, "patrol_segments": segs}

    def get_event(self, i):
        self.calls["get_event"] += 1
        return self.events.get(i)

    def create_event(self, e):
        from erclient.er_errors import ERClientException

        assert "end_time" not in e
        if e["id"] in self.events:  # das answers a duplicate id with a 500
            raise ERClientException("x\n\t 500 from ER. Message: duplicate key")
        self.calls["create_event"] += 1
        self.events[e["id"]] = e
        return e
```

Then the tests:

```python
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
    # the patrol GET carried the event ids; no per-event probes were needed
    assert api.calls["get_event"] == 0


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
```

In `tests/test_er_patrol_api.py`:

```python
def test_event_ids_of_reads_every_segment():
    p = {
        "patrol_segments": [
            {"id": "s1", "events": [{"id": "e1"}, {"id": "e2"}]},
            {"id": "s2", "events": []},
            {"id": "s3"},
        ]
    }
    assert ERPatrolAPI.event_ids_of(p) == {"e1", "e2"}
    assert ERPatrolAPI.event_ids_of(None) == set()
```

- [ ] **Step 2: Run them to verify they fail**

Run: `pytest tests/test_patrol_import.py tests/test_er_patrol_api.py -v -k "probes or event_list or orphaned or event_ids_of"`
Expected: FAIL — `get_event` is called for every event today; `event_ids_of` does not exist.

- [ ] **Step 3: Implement the API helper**

In `er_patrol_api.py`, next to `get_patrol`:

```python
    @staticmethod
    def event_ids_of(patrol: dict | None) -> set:
        """Ids of the events on a patrol's segments, from the patrol GET."""
        if not patrol:
            return set()
        return {
            e["id"]
            for s in patrol.get("patrol_segments") or []
            for e in s.get("events") or []
            if e.get("id")
        }
```

- [ ] **Step 4: Implement the import side**

Replace `_import_patrol_stage` and `_import_events` in `patrol_import.py`:

```python
def _import_patrol_stage(api, plan, patrol: dict) -> set:
    """Create or refresh the patrol. Returns the ids of events already on
    its segments, so the events stage need not probe them one by one."""
    body = plan.patrol
    existing = api.get_patrol(body["id"])
    if existing is None:
        api.create_patrol(body)
        return set()
    have = {s.get("id") for s in existing.get("patrol_segments") or []}
    missing = [i for i in plan.segment_ids.values() if i not in have]
    if missing:
        raise RuntimeError(
            f"patrol {patrol.get('id')} is on the site without {len(missing)} of "
            "its segments; das cannot add a segment with a given id to an existing "
            f"patrol. Delete patrol {body['id']} in EarthRanger and run again."
        )
    api.patch_patrol(body["id"], {k: v for k, v in body.items() if k != "id"})
    return api.event_ids_of(existing)


def _create_event_safely(api, ev: dict, counts) -> dict:
    """POST one event. das answers a duplicate id with a 500; when that
    happens the event is on the site (for example after a patrol was deleted
    by hand but its events were not), so read it and carry on."""
    try:
        created = api.create_event(ev)
        counts["events_created"] += 1
        return created
    except ERClientException:
        existing = api.get_event(ev["id"])
        if existing is None:
            raise
        counts["events_existing"] += 1
        return existing


def _import_events(
    api, bundle, patrol, plan, ns, resolver, ledger, counts, known: set | None
) -> None:
    if known is None:  # patrol stage done in an earlier run: one read, not N
        known = api.event_ids_of(api.get_patrol(plan.patrol["id"]))
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
            on_site: dict = {}
            for ev in ([wp.parent] if wp.parent else []) + wp.events:
                if ev["id"] in known:
                    counts["events_existing"] += 1
                    on_site[ev["id"]] = None  # read lazily, only for attachments
                else:
                    on_site[ev["id"]] = _create_event_safely(api, ev, counts)
            _attach(api, bundle, wp, w, on_site, attached, ledger, patrol, counts)


def _attach(api, bundle, wp, w, on_site, attached, ledger, patrol, counts) -> None:
    if not (wp.attachments and wp.events) or w["uuid"] in attached:
        return
    first_id = wp.events[0]["id"]
    first = on_site.get(first_id)
    if first is None:  # existing event: its file list guards a ledger-less re-run
        first = api.get_event(first_id) or {}
    have = {f.get("filename") for f in first.get("files") or []}
    for a in wp.attachments:
        path = bundle.path / a["path"]
        if path.name in have or a.get("filename") in have:
            counts["attachments_existing"] += 1
            continue
        if not path.is_file():
            counts["attachments_missing"] += 1
            logger.warning("attachment missing from the bundle: %s", path)
            continue
        api.attach_file(first_id, str(path), a.get("filename", ""))
        counts["attachments_posted"] += 1
    ledger.add_id(patrol["uuid"], "attached", w["uuid"])
```

Add the import at the top of `patrol_import.py`:

```python
from erclient.er_errors import ERClientException
```

In `run_import`, thread `known` through:

```python
                known: set | None = None
                if "patrol" not in done:
                    known = _import_patrol_stage(api, plan, p)
                    ledger.done(p["uuid"], "patrol", patrol_id=plan.patrol["id"])
                if "events" in want and "events" not in done:
                    _import_events(
                        api, bundle, p, plan, ns, resolver, ledger, result.counts, known
                    )
```

Update the module docstring's last sentence to describe the new rule:

```python
"""... Per patrol, three stages (patrol, events, tracks) are recorded in the
ledger so a re-run skips what is done. The patrol is read before it is
written; its events are not probed one by one: the patrol read already lists
the events on its segments, and a duplicate id (das answers it with a 500,
not a 409) is recovered by reading that one event.
"""
```

- [ ] **Step 5: Run the tests**

Run: `pytest tests/test_patrol_import.py tests/test_er_patrol_api.py -v`
Expected: all PASS. `test_half_finished_events_are_completed_without_duplicates` and `test_attachment_not_posted_twice_without_the_ledger` must still pass unchanged: they exercise the `known is None` path and the lazy attachment read.

- [ ] **Step 6: Lint, type-check, commit**

Run: `ruff check src tests && ruff format src tests && ty check && pytest`
Expected: clean.

```bash
git add src/er_smart_sync/patrol_import.py src/er_smart_sync/er_patrol_api.py tests/test_patrol_import.py tests/test_er_patrol_api.py
git commit -m "import-patrols: create events without probing each one; recover a duplicate id by reading it"
```

---

### Task 3: Create a patrol's events in one request

**Files:**
- Modify: `src/er_smart_sync/er_patrol_api.py` (`create_events`)
- Modify: `src/er_smart_sync/patrol_import.py` (`_import_events`, new `_post_event_lists`, `EVENT_BATCH`)
- Test: `tests/test_er_patrol_api.py`, `tests/test_patrol_import.py`

**Interfaces:**
- Consumes: `_create_event_safely(api, ev, counts)` from Task 2 as the per-event fallback; `api.event_ids_of`.
- Produces:
  - `ERPatrolAPI.create_events(payload: list) -> list`: one `POST activity/events` with a list body; normalises das's dict-for-one/list-for-many response to a list.
  - `_post_event_lists(api, events: list, counts) -> dict`: posts `events` in chunks of `EVENT_BATCH = 50`, parents before children, returning `{event id: created dict}`; on `ERClientException` for a chunk, falls back to `_create_event_safely` for each event in that chunk.
  - `EVENT_BATCH = 50` module constant.

- [ ] **Step 1: Write the failing tests**

`tests/test_er_patrol_api.py`:

```python
def test_create_events_posts_a_list_and_normalises_the_reply():
    c = _client()
    c._post.return_value = [{"id": "a"}, {"id": "b"}]
    out = ERPatrolAPI(c).create_events([{"id": "a"}, {"id": "b"}])
    assert out == [{"id": "a"}, {"id": "b"}]
    c._post.assert_called_once_with(
        path="activity/events", payload=[{"id": "a"}, {"id": "b"}]
    )
    c._post.return_value = {"id": "a"}  # das unwraps a one-item list
    assert ERPatrolAPI(c).create_events([{"id": "a"}]) == [{"id": "a"}]
```

`tests/test_patrol_import.py` — teach `FakeAPI` the list endpoint with das's atomic semantics, plus a switch to reject a chosen event:

```python
    def create_events(self, batch):
        from erclient.er_errors import ERClientException

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
```

and `self.reject_event = None` in `FakeAPI.__init__`. Then:

```python
def test_events_are_created_in_batches_not_one_by_one(tmp_path):
    b = load_bundle(FIXTURE)
    api = FakeAPI(b)
    r = _run(api, b, tmp_path)
    assert r.exit_code == 0 and api.calls["create_event"] == 141 + 18
    # 5 patrols; each patrol's parents (if any) and children go in a few lists
    assert api.calls["create_events"] <= 15
    assert api.calls["get_event"] == 0
    # every child still points at a parent that exists
    for e in api.events.values():
        if e.get("collection"):
            assert e["collection"]["id"] in api.events


def test_a_rejected_batch_falls_back_to_single_posts(tmp_path):
    from erclient.er_errors import ERClientException

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
    assert api.calls["create_event"] >= 141 + 18 - 20
    assert len(api.events) == api.calls["create_event"]
```

with the helper at module level:

```python
def _all_event_ids(b):
    from er_smart_sync.patrol_ids import er_id

    ns = namespace(ENDPOINT, b.ca_uuid)
    return [
        er_id(ns, "event", o["uuid"])
        for p in b.patrols
        for leg in p["legs"]
        for w in leg["waypoints"]
        for g in w["observation_groups"]
        for o in g["observations"]
    ]
```

- [ ] **Step 2: Run them to verify they fail**

Run: `pytest tests/test_patrol_import.py tests/test_er_patrol_api.py -v -k "batches or rejected_batch or create_events"`
Expected: FAIL — `create_events` does not exist; `create_event` is still called per event.

- [ ] **Step 3: Implement the API method**

In `er_patrol_api.py`, after `create_event`:

```python
    def create_events(self, payload: list) -> list:
        """One POST for several events. das writes the whole list in one
        transaction and rejects all of them if any is invalid. It answers a
        one-item list with a dict; return a list either way."""
        out = self._write(self.er._post, path="activity/events", payload=payload)
        if isinstance(out, dict):
            return [out]
        return list(out or [])
```

- [ ] **Step 4: Implement the batching**

In `patrol_import.py`, add the constant and the helper, and rewrite the body of `_import_events` to collect first, then post:

```python
EVENT_BATCH = 50


def _post_event_lists(api, events: list, counts) -> dict:
    """Create `events` with as few requests as possible: lists of EVENT_BATCH.
    A list das rejects (one invalid item, or a duplicate id answered with a
    500) is redone one event at a time, so one bad observation costs only
    its own patrol a slower path, not the whole run."""
    created: dict = {}
    for i in range(0, len(events), EVENT_BATCH):
        chunk = events[i : i + EVENT_BATCH]
        try:
            for ev in api.create_events(chunk):
                created[ev["id"]] = ev
            counts["events_created"] += len(chunk)
        except ERClientException:
            logger.info("event list rejected; posting %d events singly", len(chunk))
            for ev in chunk:
                created[ev["id"]] = _create_event_safely(api, ev, counts)
    return created


def _import_events(
    api, bundle, patrol, plan, ns, resolver, ledger, counts, known: set | None
) -> None:
    if known is None:  # patrol stage done in an earlier run: one read, not N
        known = api.event_ids_of(api.get_patrol(plan.patrol["id"]))
    attached = set(ledger.ids(patrol["uuid"]).get("attached", []))
    parents, children, waypoints = [], [], []
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
            waypoints.append((w, wp))
            if wp.parent and wp.parent["id"] not in known:
                parents.append(wp.parent)
            children += [e for e in wp.events if e["id"] not in known]
    counts["events_existing"] += sum(
        1 for _, wp in waypoints
        for ev in ([wp.parent] if wp.parent else []) + wp.events
        if ev["id"] in known
    )
    # parents first: a child names its collection by id, so the parent must
    # exist before the child's list is written
    on_site = _post_event_lists(api, parents, counts)
    on_site.update(_post_event_lists(api, children, counts))
    for w, wp in waypoints:
        _attach(api, bundle, wp, w, on_site, attached, ledger, patrol, counts)
```

`_attach` from Task 2 is unchanged: for an event in `known` it is absent from `on_site`, so `on_site.get(first_id)` is `None` and the single lazy read happens only when attachments are pending.

- [ ] **Step 5: Run the tests**

Run: `pytest tests/test_patrol_import.py tests/test_er_patrol_api.py -v`
Expected: all PASS. `test_full_import_then_rerun_writes_nothing` still asserts `create_event == 141 + 18` because the fake counts per event inside `create_events`.

- [ ] **Step 6: Lint, type-check, commit**

Run: `ruff check src tests && ruff format src tests && ty check && pytest`

```bash
git add src/er_smart_sync/patrol_import.py src/er_smart_sync/er_patrol_api.py tests/test_patrol_import.py tests/test_er_patrol_api.py
git commit -m "import-patrols: create a patrol's events in lists of 50, parents first, singles as the fallback"
```

---

### Task 4: Run patrols on a thread pool (`--workers`)

**Files:**
- Modify: `src/er_smart_sync/import_ledger.py` (lock)
- Modify: `src/er_smart_sync/patrol_import.py` (`ImportOptions.workers`, `run_import(api_factory=...)`, `_import_one`)
- Modify: `src/er_smart_sync/cli.py` (`--workers`, factory, dry-run guard)
- Modify: `docs/cli-reference/import-patrols.md`, `docs/changelog.md`, `USAGE.md`
- Test: `tests/test_import_ledger.py`, `tests/test_patrol_import.py`, `tests/test_cli.py`

**Interfaces:**
- Consumes: `_import_patrol_stage`, `_import_events`, `_import_tracks(..., sources)` from Tasks 1–3.
- Produces:
  - `ImportOptions.workers: int = 1`.
  - `run_import(api, bundle, *, ..., api_factory: Callable[[], Any] | None = None)`: with `opts.workers > 1`, each worker thread calls `api_factory()` once and uses that API for its patrols; `api` (the main-thread one) is used for preflight, reactivation and retirement. With `workers == 1` or no factory, behaviour is exactly today's.
  - `_import_one(api, bundle, p, *, want, ledger, ns, resolver, subjects, tz, by_day, importer_version, sources, sources_lock) -> tuple[str, Counter, str | None]`: imports one patrol and returns `(patrol uuid, its counts, error or None)`; all per-patrol logic lives here.
  - `ImportLedger` methods are safe to call from several threads (a `threading.RLock` around every read and write).
  - CLI: `--workers N` (default 1); forced to 1 under `--dry-run` with a notice on stderr.

- [ ] **Step 1: Write the failing tests**

`tests/test_import_ledger.py`:

```python
def test_ledger_survives_concurrent_writers(tmp_path):
    import threading

    led = ImportLedger(tmp_path / "l.json", "site|ca")

    def work(i):
        for s in ("patrol", "events", "tracks"):
            led.done(f"p{i}", s, patrol_id=f"id{i}")
            led.add_id(f"p{i}", "attached", f"w{i}")

    threads = [threading.Thread(target=work, args=(i,)) for i in range(16)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    again = ImportLedger(tmp_path / "l.json", "site|ca")
    for i in range(16):
        assert again.stages(f"p{i}") == {"patrol", "events", "tracks"}
        assert again.ids(f"p{i}") == {"patrol_id": f"id{i}", "attached": [f"w{i}"]}
```

`tests/test_patrol_import.py` — a factory that hands out fakes sharing one site, and the worker tests:

```python
class SharedSite:
    """Several FakeAPI views over one set of dicts, as N workers would see
    one EarthRanger site."""

    def __init__(self, b, **kw):
        self.base = FakeAPI(b, **kw)

    def __call__(self):
        api = FakeAPI(self.base.b)
        for name in ("subjects", "patrols", "events", "sources", "observations",
                     "files", "calls", "patches"):
            setattr(api, name, getattr(self.base, name))
        api.collection, api.event_types = self.base.collection, self.base.event_types
        return api


def _run_workers(site, b, tmp_path, workers, **kw):
    led = ImportLedger(tmp_path / "ledger.json", "site|ca")
    api = site()
    report = run_preflight(api, b, checks=[check_event_types])
    return run_import(
        api, b, endpoint=ENDPOINT, ledger=led,
        opts=ImportOptions(timezone="Africa/Libreville", workers=workers, **kw),
        ns=namespace(ENDPOINT, b.ca_uuid), importer_version="t", report=report,
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
    from erclient.er_errors import ERClientException

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
        for p in b.patrols for leg in p["legs"] if leg["uuid"] in tracked
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
```

`SharedSite.__call__` must copy the `fail_patrol` too; add `api.fail_patrol = self.base.fail_patrol` to it. `FakeAPI.create_patrol` reads `self.fail_patrol`, so the failing patrol fails on whichever worker gets it.

`tests/test_cli.py` (append; follow the file's existing pattern for invoking `main` with `CliRunner` and a patched `run_import`):

```python
def test_import_patrols_workers_flag_and_dry_run_guard(tmp_path, monkeypatch):
    from click.testing import CliRunner

    from er_smart_sync import cli, patrol_import

    seen = {}

    def fake_run_import(api, bundle, **kw):
        seen["workers"] = kw["opts"].workers
        seen["factory"] = kw.get("api_factory")
        r = patrol_import.ImportResult()
        return r

    monkeypatch.setattr(patrol_import, "run_import", fake_run_import)
    monkeypatch.setattr(cli, "_check_bundle_timezone", lambda b, tz: None)
    args = ["import-patrols", "--bundle", str(FIXTURE), "--er-endpoint",
            "site.pamdas.org", "--er-token", "t", "--timezone", "Africa/Libreville"]
    res = CliRunner().invoke(cli.main, args + ["--workers", "4"])
    assert res.exit_code == 0, res.output
    assert seen["workers"] == 4 and callable(seen["factory"])
    res = CliRunner().invoke(cli.main, ["--dry-run"] + args + ["--workers", "4"])
    assert res.exit_code == 0, res.output
    assert seen["workers"] == 1 and "one worker" in res.output
```

(`FIXTURE` in `tests/test_cli.py` must point at `tests/fixtures/smart_demo_bundle`; add the constant if the file does not have it.)

- [ ] **Step 2: Run them to verify they fail**

Run: `pytest tests/test_import_ledger.py tests/test_patrol_import.py tests/test_cli.py -v -k "concurrent or workers"`
Expected: FAIL — `ImportOptions` has no `workers`, `run_import` rejects `api_factory`, the CLI has no `--workers`.

- [ ] **Step 3: Lock the ledger**

In `import_ledger.py`:

```python
import threading
...
class ImportLedger:
    def __init__(self, path: Path, site_key: str, read_only: bool = False):
        self._lock = threading.RLock()
        ...

    def stages(self, patrol_uuid: str) -> set:
        with self._lock:
            return set(self._site["patrols"].get(patrol_uuid, {}).get("stages", []))

    def ids(self, patrol_uuid: str) -> dict:
        with self._lock:
            return dict(self._site["patrols"].get(patrol_uuid, {}).get("ids", {}))

    def done(self, patrol_uuid: str, stage: str, **ids) -> None:
        with self._lock:
            p = self._patrol(patrol_uuid)
            if stage not in p["stages"]:
                p["stages"].append(stage)
            p["ids"].update(ids)
            self._save()

    def add_id(self, patrol_uuid: str, key: str, value: str) -> None:
        with self._lock:
            p = self._patrol(patrol_uuid)
            p["ids"].setdefault(key, [])
            if value not in p["ids"][key]:
                p["ids"][key].append(value)
            self._save()

    def drop_id(self, patrol_uuid: str, key: str) -> None:
        with self._lock:
            p = self._site["patrols"].get(patrol_uuid)
            if p and p["ids"].pop(key, None) is not None:
                self._save()

    def reactivated(self) -> list:
        with self._lock:
            return list(self._site["reactivated"])

    def set_reactivated(self, ids: list) -> None:
        with self._lock:
            self._site["reactivated"] = list(ids)
            self._save()
```

`_save` is only called under the lock, so it needs no change.

- [ ] **Step 4: Split the per-patrol work out and add the pool**

In `patrol_import.py`:

```python
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any
```

```python
@dataclass
class ImportOptions:
    ...
    timezone: str | None = None
    workers: int = 1
```

```python
def _get_or_create_source(api, mid: str, member_uuid: str, sources: dict, lock) -> str:
    with lock:
        if mid in sources:
            return sources[mid]
    src = api.get_source(mid)
    if src is None:
        try:
            src = api.create_source(
                {
                    "provider": SOURCE_PROVIDER,
                    "manufacturer_id": mid,
                    "source_type": "tracking-device",
                    "model_name": "SMART patrol track",
                    "additional": {"smart_member_id": member_uuid},
                }
            )
        except ERClientException:
            src = api.get_source(mid)  # another worker created it first
            if src is None:
                raise
    with lock:
        sources.setdefault(mid, src["id"])
        return sources[mid]


def _import_tracks(api, patrol, plan, by_day, subjects, counts, sources, lock) -> None:
    for leg in patrol["legs"]:
        for batch in build_track_batches(leg, plan.leg_times[leg["uuid"]], by_day):
            subject = subjects.get(batch.member_uuid)
            if subject is None:
                counts["tracks_without_subject"] += 1
                continue
            mid = f"smart:{batch.member_uuid}"
            source_id = _get_or_create_source(api, mid, batch.member_uuid, sources, lock)
            api.assign_source(subject["id"], source_id, batch.start, batch.end)
            obs = [{**o, "source": source_id} for o in batch.observations]
            for i in range(0, len(obs), OBSERVATION_BATCH):
                api.post_observations(obs[i : i + OBSERVATION_BATCH])
            counts["track_points"] += len(obs)


def _import_one(
    api, bundle, p, *, want, ledger, ns, resolver, subjects, tz, by_day,
    importer_version, sources, sources_lock,
) -> tuple:
    """Import one patrol through its remaining stages. Returns
    (patrol uuid, counts for this patrol, error text or None)."""
    counts: Counter = Counter()
    done = ledger.stages(p["uuid"])
    if want <= done:
        counts["already_imported"] += 1
        return p["uuid"], counts, None
    try:
        plan = build_patrol(bundle, p, ns, resolver, tz, by_day, importer_version)
        known: set | None = None
        if "patrol" not in done:
            known = _import_patrol_stage(api, plan, p)
            ledger.done(p["uuid"], "patrol", patrol_id=plan.patrol["id"])
        if "events" in want and "events" not in done:
            _import_events(api, bundle, p, plan, ns, resolver, ledger, counts, known)
            ledger.done(p["uuid"], "events")
            ledger.drop_id(p["uuid"], "attached")
        if "tracks" in want and "tracks" not in done:
            _import_tracks(api, p, plan, by_day, subjects, counts, sources, sources_lock)
            ledger.done(p["uuid"], "tracks")
        return p["uuid"], counts, None
    except Exception as e:  # one patrol's failure must not stop the run
        logger.exception("patrol %s failed", p.get("id"))
        return p["uuid"], counts, f"{type(e).__name__}: {e}"
```

Then the tail of `run_import` (from `todo = ...` to the end) becomes:

```python
    todo = [p for p in selected if not {"patrol"} <= ledger.stages(p["uuid"])]
    sources: dict = {}
    sources_lock = threading.Lock()
    per_patrol = dict(
        want=want, ledger=ledger, ns=ns, resolver=resolver, subjects=subjects,
        tz=tz, by_day=by_day, importer_version=importer_version,
        sources=sources, sources_lock=sources_lock,
    )

    def record(outcome):
        uuid, counts, error = outcome
        result.counts.update(counts)
        if error:
            result.failed[uuid] = error
        else:
            result.imported.append(uuid)

    workers = max(1, opts.workers) if api_factory else 1
    try:
        # inside the try: whatever fails after this, the finally retires them
        _reactivate(api, ledger, subjects, todo)
        if not opts.skip_tracks:
            api.ensure_source_provider(SOURCE_PROVIDER, "SMART patrol import")
        if workers == 1:
            for p in selected:
                record(_import_one(api, bundle, p, **per_patrol))
        else:
            local = threading.local()

            def worker(p):
                if not hasattr(local, "api"):
                    local.api = api_factory()
                return _import_one(local.api, bundle, p, **per_patrol)

            with ThreadPoolExecutor(max_workers=workers) as pool:
                for outcome in pool.map(worker, selected):
                    record(outcome)
    finally:
        _retire(api, ledger)
    return result
```

and the signature gains the factory:

```python
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
    api_factory: Callable[[], Any] | None = None,
) -> ImportResult:
```

`pool.map` keeps patrol order for `result.imported`, and an exception inside `worker` is already caught by `_import_one`, so `map` never raises for a patrol failure. `KeyboardInterrupt` propagates out of the `with` block (the executor waits for running patrols to finish their current stage), and the `finally` retires the reactivated members. Add one line to `select_patrols`' caller or to `_import_one`'s start so interleaved logs are attributable:

```python
    logger.info("patrol %s: starting", p.get("id"))
```

Remove the now-unused single-threaded loop body and `Counter` merging from the old `run_import`; `result.counts` is only ever updated from the main thread (inside `record`), so it needs no lock.

- [ ] **Step 5: Wire the CLI**

In `cli.py`, add after `--ledger`:

```python
@click.option("--workers", type=click.IntRange(1, 32), default=1, show_default=True,
              help="Import this many patrols at a time (one ER connection each). "
                   "Dry runs always use one.")
```

add `workers` to the function parameters, and before building `opts`:

```python
    if dry and workers > 1:
        click.echo("Dry run: using one worker so the planned writes read in order.",
                   err=True)
        workers = 1

    def api_factory():
        return ERPatrolAPI(_make_er_client(
            endpoint=er.endpoint, token=er.token, username=er.login,
            password=er.password,
        ))
```

then `workers=workers` in `ImportOptions(...)` and `api_factory=api_factory if workers > 1 else None` in the `run_import(...)` call.

- [ ] **Step 6: Run the tests**

Run: `pytest tests/test_import_ledger.py tests/test_patrol_import.py tests/test_cli.py -v`
Expected: all PASS, including every pre-existing `test_patrol_import.py` test through the `workers == 1` path.

- [ ] **Step 7: Docs**

`docs/cli-reference/import-patrols.md`: add `--workers` to the options table —

```markdown
| `--workers N` | Import N patrols at a time, each on its own connection. Default 1. Start with 4; watch for 429 or 5xx in the log and lower it if they appear. Dry runs always use one worker. |
```

and a short section:

```markdown
## Speed

A patrol costs a handful of requests: the patrol, one list of its events
(parents of multi-observation waypoints first, then the rest, 50 per
request), its attachments, and its leader's track in batches of 1,000
points. Re-run safety comes from deterministic ids and from reading the
patrol (which lists the events already on it) before writing; a request
das rejects is redone one event at a time for that patrol only.
`--workers 4` runs four patrols at once. On Albay (1,527 patrols) the
sequential rate was ~2.6 patrols a minute; see
`docs/superpowers/specs/2026-10-14-import-patrols-performance.md` for the
measurements behind these changes.
```

`docs/changelog.md`, under a new `## Unreleased` heading above 0.7.0:

```markdown
## Unreleased

### Changed

**`import-patrols` is several times faster.** Events are created in lists
(das writes a list atomically), the patrol read replaces one probe per
event, a leader's track source is looked up once per run, and `--workers N`
imports N patrols at a time. Re-run behaviour is unchanged; a rejected list
or a duplicate id falls back to the previous one-at-a-time path for that
patrol.
```

`USAGE.md`: add `--workers N` to the `import-patrols` bullet.

- [ ] **Step 8: Lint, type-check, build docs, commit**

Run: `ruff check src tests && ruff format src tests && ty check && pytest && uv run --extra docs mkdocs build --strict -d /tmp/site`

```bash
git add src/er_smart_sync/import_ledger.py src/er_smart_sync/patrol_import.py src/er_smart_sync/cli.py tests/test_import_ledger.py tests/test_patrol_import.py tests/test_cli.py docs/cli-reference/import-patrols.md docs/changelog.md USAGE.md
git commit -m "import-patrols: --workers N runs patrols on a thread pool; ledger and source cache are locked"
```

---

### Task 5: Timing harness in the repo

**Files:**
- Create: `scripts/time_one_patrol.py`
- Modify: `docs/superpowers/specs/2026-10-14-import-patrols-performance.md` ("Verifying any change" section: point at the script)
- Test: none (a developer tool; it is exercised in Task 6)

**Interfaces:**
- Produces: `python scripts/time_one_patrol.py --config <yaml> --patrol <smart id> [--workers N] [--ledger PATH]` prints wall time, request count, time inside HTTP, and a table of requests grouped by method, status and path with counts and medians.

- [ ] **Step 1: Write the script**

```python
"""Time every HTTP request import-patrols makes for one patrol.

    uv run python scripts/time_one_patrol.py --config sync.yaml --patrol 050500_001453

Imports the patrol through the real CLI into a throwaway ledger (so the
main ledger is untouched; the main run later finds the patrol on the site
and skips it), then prints the requests grouped by method, status and path.
Run it before and after a change on the same patrol: that is the benchmark.
"""

from __future__ import annotations

import argparse
import collections
import re
import statistics
import sys
import tempfile
import time

import requests
from click.testing import CliRunner

CALLS: list = []
_orig = requests.Session.request


def _timed(self, method, url, *a, **kw):
    start = time.perf_counter()
    resp = _orig(self, method, url, *a, **kw)
    path = url.split("/api/v1.0/")[-1].split("?")[0]
    path = re.sub(r"[0-9a-f]{8}-[0-9a-f-]{27}", "<id>", path)
    CALLS.append((method, path, resp.status_code, time.perf_counter() - start))
    return resp


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--config", required=True)
    ap.add_argument("--patrol", required=True, help="SMART patrol id or uuid")
    ap.add_argument("--workers", type=int, default=1)
    ap.add_argument("--ledger", default=None, help="default: a temp file")
    args = ap.parse_args(argv)

    requests.Session.request = _timed  # type: ignore[assignment]
    from er_smart_sync.cli import main as cli_main

    ledger = args.ledger or tempfile.mktemp(suffix=".json", prefix="time-one-patrol-")
    t0 = time.perf_counter()
    res = CliRunner().invoke(
        cli_main,
        ["import-patrols", "--config", args.config, "--patrol", args.patrol,
         "--ledger", ledger, "--workers", str(args.workers)],
        catch_exceptions=False,
    )
    wall = time.perf_counter() - t0
    print("\n".join(res.output.strip().splitlines()[-4:]))
    inside = sum(c[3] for c in CALLS)
    print(f"\nwall {wall:.1f}s, {len(CALLS)} HTTP requests, {inside:.1f}s inside HTTP")
    groups: dict = collections.defaultdict(list)
    for method, path, status, elapsed in CALLS:
        groups[(method, path, status)].append(elapsed)
    for (method, path, status), xs in sorted(groups.items(), key=lambda kv: -sum(kv[1])):
        print(f"  {method:6} {status} {path:55} n={len(xs):3} "
              f"total={sum(xs):5.1f}s median={statistics.median(xs) * 1000:5.0f}ms")
    return res.exit_code or 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Run it against a site you may write to**

Run: `uv run python scripts/time_one_patrol.py --config sync-phinney.yaml --patrol 050500_001454`
Expected: a summary ending in `Imported 1, skipped 0, failed 0 patrol(s).` and a table whose top rows are `POST activity/events` (now one or two rows with `n=1`–`2`, not `n=9`) and `POST observations`. Record the `wall` and `inside HTTP` numbers in the spec's "Verifying any change" section as the "after" for Tasks 1–3.

- [ ] **Step 3: Point the spec at the script and commit**

Replace the harness paragraph in `docs/superpowers/specs/2026-10-14-import-patrols-performance.md` "Verifying any change" with:

```markdown
`scripts/time_one_patrol.py` wraps `requests.Session.request`, imports a
single patrol through the real CLI into a throwaway ledger, and prints the
requests grouped by method, status and path with counts and medians.
Before/after on the same patrol is the honest benchmark; the full-run rate
(patrols per minute, from the ledger's `stages`) is the acceptance test.

| Patrol `050500_001453` (10 observations, 81 points) | Requests | Wall | In HTTP |
| --- | --- | --- | --- |
| Before (0.7.0) | 45 | 32.7 s | 21.7 s |
| After Tasks 1–3 | <fill from Step 2> | | |
```

```bash
git add scripts/time_one_patrol.py docs/superpowers/specs/2026-10-14-import-patrols-performance.md
git commit -m "scripts: time every request import-patrols makes for one patrol"
```

---

### Task 6: Live verification and the full-run benchmark

**Files:**
- Modify: `docs/superpowers/specs/2026-10-14-import-patrols-performance.md` (results table)
- Modify: `docs/cli-reference/import-patrols.md` ("Speed" section: the measured rate)

This task writes to a real EarthRanger site. **Get the user's explicit go-ahead for the site and the window before Step 2.** A fresh staging site (phinney, or one the user names) is the right place; never a customer site.

- [ ] **Step 1: Pick a slice with nothing on the site yet**

Run: `uv run er-smart-sync patrol-preflight --config <site>.yaml` and confirm `0 blocking`. Choose a date window of about 50 patrols that are not in the ledger (for Albay on phinney after the initial load, use the second CA, Guinobatan `050504`, or ask the user to wipe and re-create the site's patrols).

- [ ] **Step 2: Sequential baseline on the new code**

Run: `time uv run er-smart-sync import-patrols --config <site>.yaml --since <d1> --until <d2>`
Record: patrols imported, wall time, patrols/minute. Expected: roughly 2–3× the 0.7.0 rate of 2.6 patrols/minute (from Tasks 1–3 alone).

- [ ] **Step 3: Concurrent run on the next slice**

Run: `time uv run er-smart-sync import-patrols --config <site>.yaml --since <d3> --until <d4> --workers 4`
Record the same numbers, and `grep -cE " (429|5[0-9][0-9]) from ER" ` on the log. Expected: ~3–4× Step 2's rate with no 429/5xx. If 429s appear, repeat with `--workers 2` and note the ceiling.

- [ ] **Step 4: Prove the re-run is still a no-op**

Run both windows again with the same flags. Expected: `already_imported: N`, zero `create_*` calls in a verbose log, exit 0. Also delete the ledger file and run one window once more: expected `events_existing` equals the window's event count and nothing is created.

- [ ] **Step 5: Verify a patrol in the UI and by API**

Open one patrol from each window in EarthRanger: legs, members, team, events on the map, the leader's track. By API, `GET activity/patrols/<id>` shows `segment_details` with the universal fields and the `smart_` block, and each event's `patrol_segments` names the segment. Multi-observation waypoints show an incident collection with its children.

- [ ] **Step 6: Record and commit**

Fill the results table in the spec and the measured rate in the "Speed" section of the CLI reference, then:

```bash
git add docs/superpowers/specs/2026-10-14-import-patrols-performance.md docs/cli-reference/import-patrols.md
git commit -m "Docs: import-patrols speed verified on <site>: <N> patrols/min with --workers 4"
```

---

## Self-review notes

- **Spec coverage.** Options 1–4 of the spec are Tasks 4, 3, 2, 1; option 7 (two-pass) needs no code; option 5 (run in-region) and option 8 (das changes) are operational or das follow-ups, deliberately outside this plan and noted in the spec. Option 6 (observation batch size) is measured by Task 5's harness (the `POST observations` row) and left at 1,000 unless that measurement says otherwise.
- **Placeholders.** The only `<fill ...>` tokens are in Task 5 Step 3 and Task 6, where the content is a measurement that does not exist until the step runs.
- **Type consistency.** `_import_tracks` gains `sources` in Task 1 and `sources, lock` in Task 4 — Task 4 rewrites the function and its single call site in `_import_one`, so no stale call remains. `_import_events` has the `known` parameter from Task 2 in both the Task 2 and Task 3 bodies. `_attach` is defined in Task 2 and unchanged in Task 3. `create_events` returns a list in the API and in `FakeAPI`.
- **Review Focus.** Item 1 → Task 3 `test_a_rejected_batch_falls_back_to_single_posts`; item 2 → Task 2 `test_orphaned_events_after_a_deleted_patrol_are_recovered`; item 3 → Task 4 `test_workers_still_retire_reactivated_members_on_failure` and `test_ledger_survives_concurrent_writers`; item 4 → Task 4 `test_workers_share_one_source_per_leader`; item 5 → Task 4's CLI test.
- **Not changed on purpose.** `post_observations` and `OBSERVATION_BATCH`; the preflight; the `_import_patrol_stage` "missing segment" error; `--limit` semantics (counted before the pool, as today).
