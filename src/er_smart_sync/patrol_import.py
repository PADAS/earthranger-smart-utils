"""`import-patrols`: write a bundle's patrol history into EarthRanger.

Preflight first (blocking findings stop the run, or with --allow-blocking
skip the affected patrols). Retired members are reactivated for the run and
retired again at the end. Each patrol is isolated: a failure is recorded with
its reason and the run moves on. Per patrol, three stages (patrol, events,
tracks) are recorded in the ledger so a re-run skips what is done. The patrol
is read before it is written; its events are not probed one by one: the
patrol read already lists the events on its segments, and a duplicate id (das
answers it with a 500, not a 409) is recovered by reading that one event.
"""

from __future__ import annotations

import logging
import threading
from collections import Counter
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from datetime import date
from typing import Any
from zoneinfo import ZoneInfo

from erclient.er_errors import ERClientException

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
    workers: int = 1


@dataclass
class ImportResult:
    imported: list = field(default_factory=list)
    skipped: dict = field(default_factory=dict)
    failed: dict = field(default_factory=dict)
    report: PreflightReport | None = None
    stopped: bool = False
    stopped_early: bool = False  # a stop event ended the run before every patrol was tried
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
    before = set(ledger.reactivated())
    # recorded before any PATCH: a failure partway still retires every one
    ledger.set_reactivated(sorted(needed | before))
    for sid in sorted(needed - before):
        api.patch_subject(sid, {"is_active": True})


def _retire(api, ledger: ImportLedger) -> None:
    for sid in ledger.reactivated():
        api.patch_subject(sid, {"is_active": False})
    ledger.set_reactivated([])


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


# das spends ~0.5 s per event on a list POST; 20 keeps a list well under a
# 30 s gateway timeout, past which das may commit after the client gave up.
EVENT_BATCH = 20


def _post_event_lists(api, events: list, counts, patrol_id=None) -> tuple[dict, list]:
    """Create `events` with as few requests as possible: lists of EVENT_BATCH.
    A list das rejects (one invalid item, or a duplicate id answered with a
    500) is redone one event at a time, so one bad observation costs only
    its own patrol a slower path, not the whole run. An event das still
    rejects singly is logged and skipped so the rest of the patrol lands;
    returns (created, failed_ids)."""
    created: dict = {}
    failed: list = []
    for i in range(0, len(events), EVENT_BATCH):
        chunk = events[i : i + EVENT_BATCH]
        try:
            for ev in api.create_events(chunk):
                created[ev["id"]] = ev
            counts["events_created"] += len(chunk)
        except ERClientException:
            logger.info(
                "patrol %s: event list rejected; posting %d events singly",
                patrol_id,
                len(chunk),
            )
            for ev in chunk:
                try:
                    created[ev["id"]] = _create_event_safely(api, ev, counts)
                except ERClientException as exc:
                    logger.warning(
                        "patrol %s: event %s rejected: %s", patrol_id, ev["id"], exc
                    )
                    failed.append(ev["id"])
    return created, failed


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
        1
        for _, wp in waypoints
        for ev in ([wp.parent] if wp.parent else []) + wp.events
        if ev["id"] in known
    )
    # parents first: a child names its collection by id, so the parent must
    # exist before the child's list is written
    on_site, failed = _post_event_lists(api, parents, counts, patrol.get("id"))
    more, more_failed = _post_event_lists(api, children, counts, patrol.get("id"))
    on_site.update(more)
    failed += more_failed
    for w, wp in waypoints:
        if wp.events and wp.events[0]["id"] in failed:
            continue  # nothing to attach to; the patrol is reported failed below
        _attach(api, bundle, wp, w, on_site, attached, ledger, patrol, counts)
    if failed:
        raise RuntimeError(
            f"{len(failed)} event(s) rejected by EarthRanger: {', '.join(failed[:5])}"
        )


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
            logger.warning(
                "patrol %s: attachment missing from the bundle: %s",
                patrol.get("id"),
                path,
            )
            continue
        api.attach_file(first_id, str(path), a.get("filename", ""))
        counts["attachments_posted"] += 1
    ledger.add_id(patrol["uuid"], "attached", w["uuid"])


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
            source_id = _get_or_create_source(
                api, mid, batch.member_uuid, sources, lock
            )
            api.assign_source(subject["id"], source_id, batch.start, batch.end)
            obs = [{**o, "source": source_id} for o in batch.observations]
            for i in range(0, len(obs), OBSERVATION_BATCH):
                api.post_observations(obs[i : i + OBSERVATION_BATCH])
            counts["track_points"] += len(obs)


def _import_one(
    api,
    bundle,
    p,
    *,
    want,
    ledger,
    ns,
    resolver,
    subjects,
    tz,
    by_day,
    importer_version,
    sources,
    sources_lock,
) -> tuple:
    """Import one patrol through its remaining stages. Returns
    (patrol uuid, counts for this patrol, error text or None)."""
    counts: Counter = Counter()
    done = ledger.stages(p["uuid"])
    if want <= done:
        counts["already_imported"] += 1
        return p["uuid"], counts, None
    logger.info("patrol %s: starting", p.get("id"))
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
            _import_tracks(
                api, p, plan, by_day, subjects, counts, sources, sources_lock
            )
            ledger.done(p["uuid"], "tracks")
        return p["uuid"], counts, None
    except Exception as e:  # one patrol's failure must not stop the run
        logger.exception("patrol %s failed", p.get("id"))
        return p["uuid"], counts, f"{type(e).__name__}: {e}"


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
    progress: Callable[[dict], None] | None = None,
    stop: threading.Event | None = None,
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
    want = (
        {"patrol"}
        | (set() if opts.skip_events else {"events"})
        | (set() if opts.skip_tracks else {"tracks"})
    )
    # --limit counts patrols still to do, so successive runs move on
    pending = 0
    for p in select_patrols(bundle, replace(opts, limit=None)):
        if p["uuid"] in reasons:
            result.skipped[p["uuid"]] = "blocked by " + ", ".join(reasons[p["uuid"]])
            continue
        if not want <= ledger.stages(p["uuid"]):
            if opts.limit and pending >= opts.limit:
                continue
            pending += 1
        selected.append(p)
    resolver, subjects = _resolver(api, bundle, report)
    resolver.collection_type = api.collection_event_type()
    tz = ZoneInfo(bundle.manifest.timezone["name"])
    by_day = tracks_by_day(bundle.tracks)
    todo = [p for p in selected if not {"patrol"} <= ledger.stages(p["uuid"])]
    sources: dict = {}
    sources_lock = threading.Lock()
    per_patrol = dict(
        want=want,
        ledger=ledger,
        ns=ns,
        resolver=resolver,
        subjects=subjects,
        tz=tz,
        by_day=by_day,
        importer_version=importer_version,
        sources=sources,
        sources_lock=sources_lock,
    )

    tally = {"total": len(selected), "done": 0, "failed": 0, "already_imported": 0}

    def record(outcome):
        uuid, counts, error = outcome
        result.counts.update(counts)
        if error:
            result.failed[uuid] = error
            tally["failed"] += 1
        else:
            result.imported.append(uuid)
        tally["done"] += 1
        tally["already_imported"] += counts.get("already_imported", 0)
        if progress:
            progress(dict(tally))

    workers = max(1, opts.workers)
    try:
        # inside the try: whatever fails after this, the finally retires them
        _reactivate(api, ledger, subjects, todo)
        if not opts.skip_tracks:
            api.ensure_source_provider(SOURCE_PROVIDER, "SMART patrol import")
        if workers == 1 or api_factory is None:
            for p in selected:
                if stop is not None and stop.is_set():
                    result.stopped_early = True
                    break
                record(_import_one(api, bundle, p, **per_patrol))
        else:
            local = threading.local()

            def worker(p):
                if stop is not None and stop.is_set():
                    return None
                if not hasattr(local, "api"):
                    try:
                        local.api = api_factory()
                    except Exception as e:
                        logger.exception(
                            "patrol %s: no EarthRanger client", p.get("id")
                        )
                        return p["uuid"], Counter(), f"{type(e).__name__}: {e}"
                return _import_one(local.api, bundle, p, **per_patrol)

            pool = ThreadPoolExecutor(max_workers=workers)
            try:
                for outcome in pool.map(worker, selected):
                    if outcome is None:
                        result.stopped_early = True
                        continue
                    record(outcome)
            except BaseException:
                # drop queued patrols; only running ones finish, then retire
                pool.shutdown(wait=True, cancel_futures=True)
                raise
            pool.shutdown(wait=True)
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
