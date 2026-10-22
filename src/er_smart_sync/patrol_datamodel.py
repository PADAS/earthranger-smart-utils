"""`patrol-datamodel`: from a smart-able bundle, create or update the ER
patrol types, the universal segment schema and its choices, and the ranger
subjects, then list what the admin still has to do by hand. Every step is
idempotent; a failed step is recorded and the next one runs.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from erclient.er_errors import ERClientException

from .bundle import Bundle
from .choices import ChoicesStats, upsert_choices
from .er_patrol_api import ERForbidden, ERPatrolAPI
from .smart_to_er_patrol_schema import (
    build_patrol_choice_sets,
    build_patrol_types,
    build_segment_schema,
    build_subjects,
    build_team_requirements,
    login_only_employees,
    merge_segment_schema,
    unnamed_stations,
)

logger = logging.getLogger(__name__)

SUBJECT_SUBTYPE = "ranger"
IDENTITY_KEYS = ("smart_member_id", "smart_employee_id", "smart_ca_uuid")
CHECKLIST_ORDER = (
    "enable_patrol_schemas",
    "permission_patrol_types",
    "permission_segment_schema",
    "permission_subjects",
    "permission_patrol_config",
    "permission_subject_group",
    "team",
    "roster_group",
    "patrol_type_icons",
    "station_names",
)


@dataclass
class PatrolDatamodelStats:
    types_created: int = 0
    types_updated: int = 0
    types_unchanged: int = 0
    schema_written: bool = False
    schema_unchanged: bool = False
    schema_skipped: str | None = None
    schema_backup: str | None = None
    choices: ChoicesStats | None = None
    subjects_created: int = 0
    subjects_updated: int = 0
    subjects_unchanged: int = 0
    subjects_grouped: int = 0
    subjects_deactivated: int = 0
    logins_skipped: list = field(default_factory=list)
    stations_placeholder: int = 0
    stations_inactive: int = 0
    # every write made, with what it replaced: the run record (see write_run_record)
    changes: list = field(default_factory=list)
    errors: list = field(default_factory=list)


@dataclass
class ChecklistItem:
    code: str
    title: str
    steps: list

    def to_dict(self) -> dict:
        return {"code": self.code, "title": self.title, "steps": list(self.steps)}


@dataclass
class PatrolDatamodelResult:
    stats: PatrolDatamodelStats
    checklist: list
    subject_ids: dict

    def checklist_dicts(self) -> list:
        """The checklist as data, for callers that render it themselves."""
        return [c.to_dict() for c in self.checklist]


RERUN = "Re-run `er-smart-sync patrol-datamodel`."


def _permission_item(code: str, title: str, grant: str) -> ChecklistItem:
    return ChecklistItem(
        code, title, [f"Django admin > Users > the operator > add {grant}.", RERUN]
    )


def _add_once(checklist: list, item: ChecklistItem) -> None:
    if not any(c.code == item.code for c in checklist):
        checklist.append(item)


def _sync_patrol_types(
    api: ERPatrolAPI, bundle: Bundle, stats: PatrolDatamodelStats, checklist: list
) -> None:
    plans = build_patrol_types(bundle)
    if plans:
        # ER's API cannot set a patrol type's icon (icon_id is read-only)
        names = ", ".join(f"{p.value} ({p.display})" for p in plans)
        checklist.append(
            ChecklistItem(
                "patrol_type_icons",
                "Pick an icon for each patrol type",
                [
                    f"EarthRanger admin > Patrol types: choose an icon for {names}.",
                    "The API cannot set icons; without one ER shows the default "
                    "patrol icon.",
                ],
            )
        )
    for plan in plans:
        try:
            existing = api.get_patrol_type(plan.value)
            if existing is None:
                api.create_patrol_type(
                    {
                        "value": plan.value,
                        "display": plan.display,
                        "is_active": plan.is_active,
                    }
                )
                stats.types_created += 1
                stats.changes.append(
                    {"kind": "patrol_type", "action": "created", "value": plan.value}
                )
                continue
            patch = {}
            if existing.get("display") != plan.display:
                patch["display"] = plan.display
            if plan.is_active and not existing.get("is_active", True):
                patch["is_active"] = (
                    True  # never deactivate a type an admin may have switched on
                )
            if patch:
                api.patch_patrol_type(plan.value, patch)
                stats.types_updated += 1
                stats.changes.append(
                    {
                        "kind": "patrol_type",
                        "action": "updated",
                        "value": plan.value,
                        "before": {k: existing.get(k) for k in patch},
                    }
                )
            else:
                stats.types_unchanged += 1
        except ERForbidden as e:
            stats.errors.append(f"patrol type {plan.value}: {e}")
            _add_once(
                checklist,
                _permission_item(
                    "permission_patrol_types",
                    "Grant the operator permission to manage patrol types",
                    "`activity | patrol type | Can add/change patrol type`",
                ),
            )


def _sync_segment_schema(
    api: ERPatrolAPI,
    bundle: Bundle,
    stats: PatrolDatamodelStats,
    checklist: list,
    *,
    replace: bool,
    choices_base_url: str,
    backup_dir: Path | None,
) -> None:
    if not api.patrol_schemas_enabled():
        stats.schema_skipped = "patrol_schemas preview feature is off"
        checklist.append(
            ChecklistItem(
                "enable_patrol_schemas",
                "Enable the `patrol_schemas` preview feature on this site",
                [
                    "Django admin > Preview features > patrol_schemas > enable it for "
                    "this tenant (ask ER Core if the admin page is not available).",
                    "Re-run `er-smart-sync patrol-datamodel` so the universal patrol "
                    "fields are written.",
                ],
            )
        )
        return
    ours = build_segment_schema(bundle, choices_base_url=choices_base_url)
    try:
        current = api.get_segment_schema()
        target = ours if replace else merge_segment_schema(current, ours)
        if current is not None and target == current:
            stats.schema_unchanged = True
            return
        if current is not None and backup_dir is not None:
            # keep what the site had so an admin can PUT it back (docs: Undoing a run)
            backup = unique_path(Path(backup_dir), "segment-schema-before", ".json")
            backup.write_text(json.dumps(current, indent=1) + "\n", encoding="utf-8")
            stats.schema_backup = str(backup)
        api.put_segment_schema(target)
        stats.schema_written = True
        stats.changes.append(
            {
                "kind": "universal_patrol_fields",
                "action": "written",
                # the saved previous document; None when the site had no form
                "before_file": stats.schema_backup,
            }
        )
    except ERForbidden as e:
        stats.errors.append(f"segment schema: {e}")
        stats.schema_skipped = "forbidden"
        checklist.append(
            _permission_item(
                "permission_segment_schema",
                "Grant the operator permission to write the universal patrol fields",
                "`activity | segment type | Can change segment type` (superuser today)",
            )
        )
    except ERClientException as e:
        stats.errors.append(f"segment schema: {e}")
        stats.schema_skipped = f"rejected: {e}"


def _subject_payload(plan) -> dict:
    return {
        "name": plan.name,
        "subject_subtype": SUBJECT_SUBTYPE,
        "is_active": plan.is_active,
        "additional": {
            "smart_member_id": plan.smart_member_id,
            "smart_employee_id": plan.smart_employee_id,
            "smart_ca_uuid": plan.smart_ca_uuid,
        },
    }


def _subjects_permission_item() -> ChecklistItem:
    return _permission_item(
        "permission_subjects",
        "Grant the operator permission to manage subjects",
        "`observations | subject | Can view/add/change subject`",
    )


def _patrol_config(
    api: ERPatrolAPI, stats: PatrolDatamodelStats, checklist: list
) -> dict:
    """The patrol configuration, or {} when the site hides or forbids it."""
    try:
        return api.get_patrol_config() or {}
    except ERForbidden as e:
        if not any(x.startswith("patrol config:") for x in stats.errors):
            stats.errors.append(f"patrol config: {e}")
        _add_once(
            checklist,
            _permission_item(
                "permission_patrol_config",
                "Grant the operator permission to read the patrol configuration",
                "`activity | patrol | Can view patrol` (the configuration needs it)",
            ),
        )
        return {}


def _uuid_key(u) -> str | None:
    """Compare uuids written with or without hyphens, in either case."""
    return u.replace("-", "").lower() if isinstance(u, str) and u else None


def unique_path(directory: Path, prefix: str, suffix: str) -> Path:
    """`<prefix>-<UTC time to the microsecond><suffix>` in `directory`, never
    an existing file, so two runs in the same instant keep both."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    path = directory / f"{prefix}-{stamp}{suffix}"
    n = 1
    while path.exists():
        n += 1
        path = directory / f"{prefix}-{stamp}-{n}{suffix}"
    return path


def write_run_record(
    result: PatrolDatamodelResult, bundle: Bundle, endpoint: str, directory: Path
) -> Path | None:
    """Write what this run changed, in order, with the values it replaced:
    the record docs/cli-reference/patrol-datamodel.md undoes a run from.
    Nothing is written when the run changed nothing."""
    if not result.stats.changes:
        return None
    path = unique_path(directory, "patrol-datamodel-run", ".json")
    doc = {
        "run_at": datetime.now(timezone.utc).isoformat(),
        "earthranger": endpoint,
        "conservation_area": bundle.manifest.conservation_area,
        "changes": result.stats.changes,
    }
    path.write_text(json.dumps(doc, indent=1) + "\n", encoding="utf-8")
    return path


def _legacy_by_employee_id(bundle: Bundle, existing: list) -> dict:
    """A legacy subject carries `smart_employee_id` but no `smart_member_id`.
    SMART employee ids are not unique (one id can belong to several people
    across conservation areas), so a legacy subject is matched only when it
    records this bundle's conservation area (`smart_ca_uuid`, or `ca_uuid` as
    the SMART Connect sync wrote it) and the id is unique in the bundle and
    among those subjects. One with no area recorded is never claimed, and a
    subject that already has a `smart_member_id` is never re-claimed by id."""
    from collections import Counter

    def area(s):
        a = s.get("additional") or {}
        return _uuid_key(a.get("smart_ca_uuid") or a.get("ca_uuid"))

    legacy = [
        s
        for s in existing
        if (s.get("additional") or {}).get("smart_employee_id")
        and not (s.get("additional") or {}).get("smart_member_id")
        and area(s) == _uuid_key(bundle.ca_uuid)
    ]
    on_site = Counter(s["additional"]["smart_employee_id"] for s in legacy)
    in_bundle = Counter(e.id for e in bundle.reference.employees if e.id)
    return {
        s["additional"]["smart_employee_id"]: s
        for s in legacy
        if on_site[s["additional"]["smart_employee_id"]] == 1
        and in_bundle[s["additional"]["smart_employee_id"]] == 1
    }


def _sync_subjects(
    api: ERPatrolAPI, bundle: Bundle, stats: PatrolDatamodelStats, checklist: list
) -> dict:
    try:
        existing = api.list_subjects()
    except ERForbidden as e:
        stats.errors.append(f"subjects: {e}")
        _add_once(checklist, _subjects_permission_item())
        return {}

    def additional(s):
        return s.get("additional") or {}

    by_member = {
        additional(s)["smart_member_id"]: s
        for s in existing
        if additional(s).get("smart_member_id")
    }
    by_employee = _legacy_by_employee_id(bundle, existing)
    stats.logins_skipped = [
        " ".join(p for p in (e.given_name, e.family_name) if p) or e.id or e.uuid
        for e in login_only_employees(bundle)
    ]
    ids: dict = {}
    for plan in build_subjects(bundle):
        match = by_member.get(plan.smart_member_id)
        if match is None and plan.smart_employee_id:
            match = by_employee.get(plan.smart_employee_id)
        payload = _subject_payload(plan)
        try:
            if match is None:
                created = api.create_subject(payload)
                stats.subjects_created += 1
                stats.changes.append(
                    {"kind": "subject", "action": "created", "id": created.get("id")}
                )
                ids[plan.smart_member_id] = created.get("id")
                continue
            ids[plan.smart_member_id] = match["id"]
            patch: dict = {}
            if match.get("name") != plan.name:
                patch["name"] = plan.name
            if bool(match.get("is_active", True)) != plan.is_active:
                patch["is_active"] = plan.is_active
            current_additional = dict(additional(match))
            if any(
                current_additional.get(k) != payload["additional"][k]
                for k in IDENTITY_KEYS
            ):
                current_additional.update(payload["additional"])
                patch["additional"] = current_additional
            if patch:
                api.patch_subject(match["id"], patch)
                stats.subjects_updated += 1
                stats.changes.append(
                    {
                        "kind": "subject",
                        "action": "updated",
                        "id": match["id"],
                        "before": {
                            k: match.get(k)
                            if k != "additional"
                            else dict(additional(match))
                            for k in patch
                        },
                    }
                )
            else:
                stats.subjects_unchanged += 1
        except ERForbidden as e:
            stats.errors.append(f"subject {plan.name}: {e}")
            _add_once(checklist, _subjects_permission_item())
    _deactivate_unplanned_subjects(api, bundle, stats, checklist, existing, ids)
    return ids


def _deactivate_unplanned_subjects(
    api: ERPatrolAPI,
    bundle: Bundle,
    stats: PatrolDatamodelStats,
    checklist: list,
    existing: list,
    ids: dict,
) -> None:
    """A subject an earlier run made for this conservation area that the plan
    no longer has (a SMART login that never patrolled, say) is deactivated,
    never deleted, the way orphan choices are."""
    planned = set(ids.values())
    for s in existing:
        a = s.get("additional") or {}
        if (
            a.get("smart_ca_uuid") != bundle.ca_uuid
            or not a.get("smart_member_id")
            or s.get("id") in planned
            or not s.get("is_active", True)
        ):
            continue
        try:
            api.patch_subject(s["id"], {"is_active": False})
            stats.subjects_deactivated += 1
            stats.changes.append(
                {
                    "kind": "subject",
                    "action": "updated",
                    "id": s["id"],
                    "before": {"is_active": True},
                }
            )
        except ERForbidden as e:
            stats.errors.append(f"subject {s.get('name')}: {e}")
            _add_once(checklist, _subjects_permission_item())


def _sync_roster(
    api: ERPatrolAPI,
    stats: PatrolDatamodelStats,
    checklist: list,
    ids: dict,
    roster_group: str | None,
) -> None:
    group = None
    try:
        if roster_group:
            group = api.find_subject_group(roster_group)
            if group is None:
                stats.errors.append(f"roster group {roster_group!r} not found")
        else:
            members = _patrol_config(api, stats, checklist).get("members") or []
            if members:
                group = {"id": members[0]["id"], "name": members[0].get("name", "")}
    except ERForbidden as e:
        stats.errors.append(f"subject groups: {e}")
        _add_once(checklist, _subjects_permission_item())
        return
    want = {i for i in ids.values() if i and i != "dry-run"}
    if group is None:
        checklist.append(
            ChecklistItem(
                "roster_group",
                "Put the ranger subjects in the patrol roster subject group",
                [
                    "Django admin > Patrol configuration > member subject groups: "
                    "choose or create the group rangers are picked from "
                    "(for example `Rangers`).",
                    f"Add the {len(ids)} subjects this run created or matched to "
                    "that group, or re-run with `--roster-group <name>` to let the "
                    "tool do it.",
                ],
            )
        )
        return
    try:
        missing = sorted(want - api.group_subject_ids(group["id"]))
        if not missing:
            return
        api.add_subjects_to_group(group["id"], missing)
        stats.subjects_grouped = len(missing)
        stats.changes.append(
            {
                "kind": "roster",
                "action": "added",
                "group_id": group["id"],
                "group": group.get("name"),
                "subject_ids": list(missing),
            }
        )
    except ERForbidden as e:
        stats.errors.append(f"subject group {group['name']}: {e}")
        checklist.append(
            ChecklistItem(
                "permission_subject_group",
                f"Add the subjects to the `{group['name']}` subject group",
                [
                    "The operator cannot change subject-group membership; an admin "
                    "adds the subjects in Django admin > Subject groups."
                ],
            )
        )


def _team_items(
    bundle: Bundle, api: ERPatrolAPI, stats: PatrolDatamodelStats, checklist: list
) -> None:
    reqs = build_team_requirements(bundle)
    if not reqs:
        return
    teams = _patrol_config(api, stats, checklist).get("teams") or []
    existing = {
        (t.get(k) or "").strip().lower() for t in teams for k in ("display", "value")
    }
    for req in reqs:
        if req.name.strip().lower() in existing:
            continue
        plural = "s" if req.referenced_by_patrols != 1 else ""
        checklist.append(
            ChecklistItem(
                "team",
                f"Create the patrol team {req.name}",
                [
                    "Django admin > Patrol configuration > Teams > add a team with "
                    f"display `{req.name}`.",
                    "Members (from SMART): "
                    + (", ".join(req.member_names) or "none recorded")
                    + ".",
                    f"Referenced by {req.referenced_by_patrols} patrol{plural} in the "
                    "bundle; the import resolves the team by this name.",
                ],
            )
        )


def _sync_choices(er_client, bundle: Bundle, stats: PatrolDatamodelStats) -> None:
    # The patrol choice fields are site-wide: a second conservation area's
    # bundle or an admin may own options in them, and a choice records no
    # owner. Only options this bundle names are changed; the rest are counted.
    stats.choices = upsert_choices(
        er_client=er_client,
        choice_sets=build_patrol_choice_sets(bundle),
        deactivate_orphans=False,
    )
    stats.changes += [{"kind": "choice", **c} for c in stats.choices.changes]
    if stats.choices.errored:
        stats.errors.append(
            f"{stats.choices.errored} choice write(s) failed; see the log above"
        )


def _station_items(
    bundle: Bundle, stats: PatrolDatamodelStats, checklist: list
) -> None:
    unnamed = unnamed_stations(bundle)
    used = [u for u, in_use in unnamed if in_use]
    stats.stations_placeholder = len(unnamed)
    stats.stations_inactive = len(unnamed) - len(used)
    if used:
        checklist.append(
            ChecklistItem(
                "station_names",
                "Name the stations SMART left unnamed",
                [
                    "SMART has no name for these stations in any language, so they "
                    "were created with a placeholder: "
                    + ", ".join(f"`Unnamed station {u[:8]}`" for u in used)
                    + ".",
                    "Name each one in SMART and re-run, or rename it in Django "
                    "admin > Choices (field `patrol_station`); a later run puts the "
                    "placeholder back until SMART has a name.",
                ],
            )
        )


def run_patrol_datamodel(
    api: ERPatrolAPI,
    er_client,
    bundle: Bundle,
    *,
    replace_segment_schema: bool = False,
    roster_group: str | None = None,
    choices_base_url: str = "/api/v2.0/schemas",
    schema_backup_dir: Path | None = None,
) -> PatrolDatamodelResult:
    stats = PatrolDatamodelStats()
    checklist: list = []

    def phase(name, fn, *args, default=None, **kwargs):
        """Run one step; an unexpected failure is recorded and the next step runs."""
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            logger.exception("patrol-datamodel step %s failed", name)
            stats.errors.append(f"{name}: {type(e).__name__}: {e}")
            return default

    phase("patrol types", _sync_patrol_types, api, bundle, stats, checklist)
    phase(
        "universal patrol fields",
        _sync_segment_schema,
        api,
        bundle,
        stats,
        checklist,
        replace=replace_segment_schema,
        choices_base_url=choices_base_url,
        backup_dir=schema_backup_dir,
    )
    phase("choices", _sync_choices, er_client, bundle, stats)
    phase("stations", _station_items, bundle, stats, checklist)
    ids = phase("subjects", _sync_subjects, api, bundle, stats, checklist, default={})
    phase("roster", _sync_roster, api, stats, checklist, ids, roster_group)
    phase("teams", _team_items, bundle, api, stats, checklist)
    checklist.sort(
        key=lambda c: (
            CHECKLIST_ORDER.index(c.code)
            if c.code in CHECKLIST_ORDER
            else len(CHECKLIST_ORDER)
        )
    )
    return PatrolDatamodelResult(stats=stats, checklist=checklist, subject_ids=ids)


def format_summary(result: PatrolDatamodelResult) -> str:
    s = result.stats
    if s.schema_written:
        schema = "written"
    elif s.schema_unchanged:
        schema = "unchanged"
    else:
        schema = f"skipped ({s.schema_skipped})"
    ch = s.choices or ChoicesStats()
    lines = [
        "Patrol datamodel summary:",
        f"  patrol types: {s.types_created} created, {s.types_updated} updated, "
        f"{s.types_unchanged} unchanged",
        f"  universal patrol fields: {schema}"
        + (
            f" (previous document saved to {s.schema_backup})"
            if s.schema_backup
            else ""
        ),
        f"  choices: {ch.created} created, {ch.updated} updated, {ch.unchanged} "
        f"unchanged, {ch.deactivated} deactivated, {ch.errored} errored, "
        f"{ch.kept} from other sources left as they are",
        f"  subjects: {s.subjects_created} created, {s.subjects_updated} updated, "
        f"{s.subjects_unchanged} unchanged, {s.subjects_deactivated} deactivated, "
        f"{s.subjects_grouped} added to the roster group",
    ]
    if s.logins_skipped:
        lines.append(
            "  SMART logins that never patrolled, not made subjects: "
            + ", ".join(s.logins_skipped)
        )
    if s.stations_placeholder:
        lines.append(
            f"  stations with no name in SMART: {s.stations_placeholder} given a "
            f"placeholder name, {s.stations_inactive} of them inactive (no patrol "
            "uses them)"
        )
    lines += [
        f"  checklist items: {len(result.checklist)}",
        f"  errors: {len(s.errors)}",
    ]
    lines += [f"    - {e}" for e in s.errors]
    return "\n".join(lines)


def render_checklist(result: PatrolDatamodelResult, bundle: Bundle) -> str:
    ca = bundle.manifest.conservation_area
    out = [
        f"# Admin checklist for {ca.get('name')} ({ca.get('id')})",
        "",
        "Steps `er-smart-sync patrol-datamodel` could not do through the API. "
        "Do them in order, then run `er-smart-sync patrol-preflight`.",
        "",
    ]
    if not result.checklist:
        out.append("Nothing to do: every step completed through the API.")
    for n, item in enumerate(result.checklist, 1):
        out.append(f"## {n}. {item.title}")
        out.append("")
        out += [f"- {s}" for s in item.steps]
        out.append("")
    return "\n".join(out)
