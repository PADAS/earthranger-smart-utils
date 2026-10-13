"""`patrol-datamodel`: from a smart-able bundle, create or update the ER
patrol types, the universal segment schema and its choices, and the ranger
subjects, then list what the admin still has to do by hand. Every step is
idempotent; a failed step is recorded and the next one runs.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from .bundle import Bundle
from .choices import ChoicesStats, upsert_choices
from .er_patrol_api import ERForbidden, ERPatrolAPI
from .smart_to_er_patrol_schema import (
    build_patrol_choice_sets,
    build_patrol_types,
    build_segment_schema,
    build_subjects,
    build_team_requirements,
    merge_segment_schema,
)

logger = logging.getLogger(__name__)

SUBJECT_SUBTYPE = "ranger"
IDENTITY_KEYS = ("smart_member_id", "smart_employee_id", "smart_ca_uuid")
CHECKLIST_ORDER = (
    "enable_patrol_schemas",
    "permission_patrol_types",
    "permission_segment_schema",
    "permission_subjects",
    "permission_subject_group",
    "team",
    "roster_group",
)


@dataclass
class PatrolDatamodelStats:
    types_created: int = 0
    types_updated: int = 0
    types_unchanged: int = 0
    schema_written: bool = False
    schema_unchanged: bool = False
    schema_skipped: str | None = None
    choices: ChoicesStats | None = None
    subjects_created: int = 0
    subjects_updated: int = 0
    subjects_unchanged: int = 0
    subjects_grouped: int = 0
    errors: list = field(default_factory=list)


@dataclass
class ChecklistItem:
    code: str
    title: str
    steps: list


@dataclass
class PatrolDatamodelResult:
    stats: PatrolDatamodelStats
    checklist: list
    subject_ids: dict


def _add_once(checklist: list, item: ChecklistItem) -> None:
    if not any(c.code == item.code for c in checklist):
        checklist.append(item)


def _sync_patrol_types(
    api: ERPatrolAPI, bundle: Bundle, stats: PatrolDatamodelStats, checklist: list
) -> None:
    for plan in build_patrol_types(bundle):
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
            else:
                stats.types_unchanged += 1
        except ERForbidden as e:
            stats.errors.append(f"patrol type {plan.value}: {e}")
            _add_once(
                checklist,
                ChecklistItem(
                    "permission_patrol_types",
                    "Grant the operator permission to manage patrol types",
                    [
                        "Django admin > Users > the operator > add "
                        "`activity | patrol type | Can add/change patrol type`.",
                        "Re-run `er-smart-sync patrol-datamodel`.",
                    ],
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
        api.put_segment_schema(target)
        stats.schema_written = True
    except ERForbidden as e:
        stats.errors.append(f"segment schema: {e}")
        stats.schema_skipped = "forbidden"
        checklist.append(
            ChecklistItem(
                "permission_segment_schema",
                "Grant the operator permission to write the universal patrol fields",
                [
                    "Django admin > Users > the operator > add "
                    "`activity | segment type | Can change segment type` "
                    "(superuser today).",
                    "Re-run `er-smart-sync patrol-datamodel`.",
                ],
            )
        )


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


def _sync_subjects(
    api: ERPatrolAPI, bundle: Bundle, stats: PatrolDatamodelStats, checklist: list
) -> dict:
    existing = api.list_subjects()

    def additional(s):
        return s.get("additional") or {}

    by_member = {
        additional(s)["smart_member_id"]: s
        for s in existing
        if additional(s).get("smart_member_id")
    }
    by_employee = {
        additional(s)["smart_employee_id"]: s
        for s in existing
        if additional(s).get("smart_employee_id")
    }
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
            else:
                stats.subjects_unchanged += 1
        except ERForbidden as e:
            stats.errors.append(f"subject {plan.name}: {e}")
            _add_once(
                checklist,
                ChecklistItem(
                    "permission_subjects",
                    "Grant the operator permission to manage subjects",
                    [
                        "Django admin > Users > the operator > add "
                        "`observations | subject | Can add/change subject`.",
                        "Re-run `er-smart-sync patrol-datamodel`.",
                    ],
                ),
            )
    return ids


def _sync_roster(
    api: ERPatrolAPI,
    stats: PatrolDatamodelStats,
    checklist: list,
    ids: dict,
    roster_group: str | None,
) -> None:
    group = None
    if roster_group:
        group = api.find_subject_group(roster_group)
        if group is None:
            stats.errors.append(f"roster group {roster_group!r} not found")
    else:
        members = (api.get_patrol_config() or {}).get("members") or []
        if members:
            group = {"id": members[0]["id"], "name": members[0].get("name", "")}
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
    missing = sorted(want - api.group_subject_ids(group["id"]))
    if not missing:
        return
    try:
        api.add_subjects_to_group(group["id"], missing)
        stats.subjects_grouped = len(missing)
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


def _team_items(bundle: Bundle, api: ERPatrolAPI, checklist: list) -> None:
    reqs = build_team_requirements(bundle)
    if not reqs:
        return
    teams = ((api.get_patrol_config() or {}).get("teams")) or []
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


def run_patrol_datamodel(
    api: ERPatrolAPI,
    er_client,
    bundle: Bundle,
    *,
    replace_segment_schema: bool = False,
    roster_group: str | None = None,
    choices_base_url: str = "/api/v2.0/schemas",
) -> PatrolDatamodelResult:
    stats = PatrolDatamodelStats()
    checklist: list = []
    _sync_patrol_types(api, bundle, stats, checklist)
    _sync_segment_schema(
        api,
        bundle,
        stats,
        checklist,
        replace=replace_segment_schema,
        choices_base_url=choices_base_url,
    )
    stats.choices = upsert_choices(
        er_client=er_client, choice_sets=build_patrol_choice_sets(bundle)
    )
    ids = _sync_subjects(api, bundle, stats, checklist)
    _sync_roster(api, stats, checklist, ids, roster_group)
    _team_items(bundle, api, checklist)
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
        f"  universal patrol fields: {schema}",
        f"  choices: {ch.created} created, {ch.updated} updated, {ch.unchanged} "
        f"unchanged, {ch.deactivated} deactivated, {ch.errored} errored",
        f"  subjects: {s.subjects_created} created, {s.subjects_updated} updated, "
        f"{s.subjects_unchanged} unchanged, {s.subjects_grouped} added to the roster "
        "group",
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
