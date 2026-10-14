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
            on_site: dict = {}
            for ev in ([wp.parent] if wp.parent else []) + wp.events:
                existing = api.get_event(ev["id"])
                on_site[ev["id"]] = existing
                if existing is None:
                    api.create_event(ev)
                    counts["events_created"] += 1
                else:
                    counts["events_existing"] += 1
            if wp.attachments and wp.events and w["uuid"] not in attached:
                first = on_site.get(wp.events[0]["id"]) or {}
                # the event's own file list guards a re-run without the ledger
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
                    api.attach_file(
                        wp.events[0]["id"], str(path), a.get("filename", "")
                    )
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
    resolver.collection_type = api.collection_event_type()
    tz = ZoneInfo(bundle.manifest.timezone["name"])
    by_day = tracks_by_day(bundle.tracks)
    todo = [p for p in selected if not {"patrol"} <= ledger.stages(p["uuid"])]
    try:
        # inside the try: whatever fails after this, the finally retires them
        _reactivate(api, ledger, subjects, todo)
        if not opts.skip_tracks:
            api.ensure_source_provider(SOURCE_PROVIDER, "SMART patrol import")
        for p in selected:
            done = ledger.stages(p["uuid"])
            want = (
                {"patrol"}
                | (set() if opts.skip_events else {"events"})
                | (set() if opts.skip_tracks else {"tracks"})
            )
            if want <= done:
                result.counts["already_imported"] += 1
                result.imported.append(p["uuid"])
                continue
            try:
                plan = build_patrol(
                    bundle, p, ns, resolver, tz, by_day, importer_version
                )
                if "patrol" not in done:
                    _import_patrol_stage(api, plan, p)
                    ledger.done(p["uuid"], "patrol", patrol_id=plan.patrol["id"])
                if "events" in want and "events" not in done:
                    _import_events(
                        api, bundle, p, plan, ns, resolver, ledger, result.counts
                    )
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
