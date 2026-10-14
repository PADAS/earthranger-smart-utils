"""`patrol-preflight`: a read-only check of an ER site against a smart-able
bundle, run before `import-patrols`. Each check returns findings; a blocking
finding names the patrols it affects so the importer can skip just those.
"""

from __future__ import annotations

import logging
import re
from dataclasses import asdict, dataclass, field

from .bundle import Bundle
from .choices import _fetch_existing
from .smart_to_er_patrol_schema import (
    CHOICE_FIELD_CLASS,
    CHOICE_FIELD_MANDATE,
    CHOICE_FIELD_STATION,
    build_patrol_types,
    build_segment_schema,
    build_team_requirements,
    choice_value,
    custom_choice_field,
)

logger = logging.getLogger(__name__)

BLOCKING, WARNING = "blocking", "warning"
MAX_EXAMPLES = 10


@dataclass
class Finding:
    code: str
    level: str
    message: str
    fix: str
    count: int = 0
    examples: list = field(default_factory=list)
    affected_patrols: list = field(default_factory=list)

    @classmethod
    def of(cls, code, level, message, fix, items=(), affected=()) -> Finding:
        items = [str(i) for i in items]
        return cls(
            code,
            level,
            message,
            fix,
            count=len(items),
            examples=items[:MAX_EXAMPLES],
            affected_patrols=sorted(set(affected)),
        )

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class PreflightContext:
    api: object
    bundle: Bundle
    timezone: str | None = None
    event_type_prefix: str | None = None
    event_type_version: str = "v2"
    cache: dict = field(default_factory=dict)

    def once(self, key: str, fn):
        """Site reads shared by several checks are made once per run."""
        if key not in self.cache:
            self.cache[key] = fn()
        return self.cache[key]


@dataclass
class PreflightReport:
    findings: list
    context: dict

    @property
    def blocking(self) -> list:
        return [f for f in self.findings if f.level == BLOCKING]

    @property
    def warnings(self) -> list:
        return [f for f in self.findings if f.level == WARNING]

    @property
    def exit_code(self) -> int:
        return 2 if self.blocking else 0

    @property
    def blocked_patrols(self) -> set:
        return {p for f in self.blocking for p in f.affected_patrols}


# ── bundle-only checks ───────────────────────────────────────────


def check_leg_span(ctx: PreflightContext) -> list:
    legs = [
        (p, leg)
        for p in ctx.bundle.patrols
        for leg in p["legs"]
        if leg.get("placeholder_dates")
    ]
    if not legs:
        return []
    days = sum(len(leg.get("days") or []) for _, leg in legs)
    return [
        Finding.of(
            "leg_span",
            WARNING,
            f"{len(legs)} leg(s) start on SMART's 1970-01-01 placeholder or span more "
            "than a year "
            f"({days} leg days as stored)",
            "Nothing to do: import-patrols keeps only the leg days with a track or a "
            "waypoint. "
            "To correct the dates, fix the leg in SMART and re-export.",
            [f"{p['id']} leg {leg.get('id')}" for p, leg in legs],
            affected=[p["uuid"] for p, _ in legs],
        )
    ]


def check_time_coverage(ctx: PreflightContext) -> list:
    tc = ctx.bundle.manifest.time_coverage or {}
    n = int(tc.get("date_only") or 0)
    if not n:
        return []
    return [
        Finding(
            "time_coverage",
            WARNING,
            f"{n} of {tc.get('leg_days')} leg days have no track, start/end time or "
            "timed waypoint; "
            "their segments will cover the whole day",
            "Nothing to do; the imported segment records time_source: date_only. "
            f"Coverage: track {tc.get('track')}, start/end {tc.get('leg_day_times')}, "
            f"waypoints {tc.get('waypoints')}, date only {n}.",
            count=n,
        )
    ]


def check_timezone(ctx: PreflightContext) -> list:
    tz = ctx.bundle.manifest.timezone or {}
    name, source = tz.get("name"), tz.get("source")
    ca_id = ctx.bundle.manifest.conservation_area.get("id", "")
    if ctx.timezone and ctx.timezone != name:
        return [
            Finding(
                "timezone",
                BLOCKING,
                f"timezone {ctx.timezone!r} is configured but the bundle was written "
                f"for {name!r} ({source})",
                "Re-export the bundle with the right zone: "
                f"SMART_ABLE_TIMEZONE={ctx.timezone} ./smart-able export {ca_id}",
            )
        ]
    if not ctx.timezone and source == "inferred":
        return [
            Finding(
                "timezone",
                WARNING,
                f"the bundle's timezone {name} was inferred from waypoint positions",
                f"Confirm it by setting smart.timezone: {name} in sync.yaml (or "
                f"--timezone {name}); "
                "if it is wrong, re-export with SMART_ABLE_TIMEZONE set.",
            )
        ]
    return []


def check_language(ctx: PreflightContext) -> list:
    b = ctx.bundle
    lang = next((x for x in b.manifest.languages if x["code"] == b.language), None)
    if not lang or not lang.get("missing"):
        return []
    return [
        Finding(
            "language",
            WARNING,
            f"{lang['missing']} element(s) have no label in {b.language!r}; "
            f"they fall back to {b.manifest.default_language!r} or any language with "
            "one",
            "Pick the best-covered language in smart-able's Data Model tab and pass it "
            "as --smart-language.",
            count=int(lang["missing"]),
        )
    ]


def check_bundle_warnings(ctx: PreflightContext) -> list:
    warnings = list(ctx.bundle.manifest.warnings or [])
    if not warnings:
        return []
    return [
        Finding.of(
            "bundle_warnings",
            WARNING,
            f"smart-able recorded {len(warnings)} warning(s) when it wrote the bundle",
            "Read them in the bundle's manifest.json; smart-able's quirks.html "
            "explains each kind.",
            warnings,
        )
    ]


ALL_CHECKS: list = [
    check_leg_span,
    check_time_coverage,
    check_timezone,
    check_language,
    check_bundle_warnings,
]


def run_preflight(
    api,
    bundle: Bundle,
    *,
    timezone=None,
    event_type_prefix=None,
    event_type_version="v2",
    checks=None,
) -> PreflightReport:
    ctx = PreflightContext(
        api=api,
        bundle=bundle,
        timezone=timezone,
        event_type_prefix=event_type_prefix,
        event_type_version=event_type_version,
    )
    findings: list = []
    for check in checks if checks is not None else ALL_CHECKS:
        try:
            findings.extend(check(ctx))
        except Exception as e:  # one check's failure must not hide the others
            logger.exception("preflight check %s failed", check.__name__)
            findings.append(
                Finding(
                    "check_failed",
                    BLOCKING,
                    f"{check.__name__} could not run: {type(e).__name__}: {e}",
                    "Fix the cause (often access or connectivity) and run "
                    "patrol-preflight again.",
                )
            )
    findings.sort(key=lambda f: (f.level != BLOCKING,))
    ca = bundle.manifest.conservation_area
    return PreflightReport(
        findings=findings,
        context={
            "conservation_area": {
                "id": ca.get("id"),
                "name": ca.get("name"),
                "uuid": ca.get("uuid"),
            },
            "patrols": len(bundle.patrols),
            "language": bundle.language,
            "timezone": bundle.manifest.timezone,
        },
    )


# ── site checks ─────────────────────────────────────────────────

# What das enforces, as user/me reports it: the patrol endpoints and the patrol
# configuration need activity.*_patrol; linking an event to a segment needs
# eventrelatedsegments. Creating an event is checked per event category
# (activity.<category>_create, shown as "<category>": ["add"]); see
# check_permissions.
REQUIRED_PERMISSIONS = {
    "patrol": ("add", "change", "view"),
    "eventrelatedsegments": ("add",),
}


def flag_on(ctx: PreflightContext) -> bool:
    return bool(ctx.once("flag", ctx.api.patrol_schemas_enabled))


def check_feature_flag(ctx: PreflightContext) -> list:
    if flag_on(ctx):
        return []
    return [
        Finding.of(
            "feature_flag",
            BLOCKING,
            "the patrol_schemas preview feature is off on this site, so it has no "
            "patrol "
            "legs, "
            "universal patrol fields, teams or rosters to import into",
            "Django admin > Preview features > patrol_schemas > enable it for this "
            "tenant "
            "(ask ER Core if the page is not available), then run patrol-datamodel and "
            "patrol-preflight again.",
            affected=[p["uuid"] for p in ctx.bundle.patrols],
        )
    ]


def check_permissions(ctx: PreflightContext) -> list:
    me = ctx.once("me", ctx.api.get_me)
    if me.get("is_superuser"):
        return []
    have = me.get("permissions") or {}
    missing = [
        f"{model}: {action}"
        for model, actions in REQUIRED_PERMISSIONS.items()
        for action in actions
        if action not in (have.get(model) or [])
    ]
    missing += [
        f"{cat}: add (event category)"
        for cat in sorted(_event_categories(ctx))
        if "add" not in (have.get(cat) or [])
    ]
    if not missing:
        return []
    return [
        Finding.of(
            "permissions",
            BLOCKING,
            f"the operator lacks {len(missing)} permission(s) the import needs",
            "Django admin > Users > the operator: grant each permission listed (app "
            "`activity`; an event category's add is its <category>_create "
            "permission, usually granted through a permission set).",
            missing,
            affected=[p["uuid"] for p in ctx.bundle.patrols],
        )
    ]


def check_patrol_types(ctx: PreflightContext) -> list:
    if not flag_on(ctx):
        return []
    by_transport = {p.transport_uuid: p for p in build_patrol_types(ctx.bundle)}
    problems, affected = {}, set()
    for p in ctx.bundle.patrols:
        for leg in p["legs"]:
            plan = by_transport.get(leg.get("transport_uuid"))
            if plan is None:
                continue
            if plan.value not in problems:
                existing = ctx.api.get_patrol_type(plan.value)
                problems[plan.value] = (
                    "missing"
                    if existing is None
                    else "inactive"
                    if not existing.get("is_active", True)
                    else None
                )
            if problems[plan.value]:
                affected.add(p["uuid"])
    bad = [f"{value} ({why})" for value, why in sorted(problems.items()) if why]
    if not bad:
        return []
    return [
        Finding.of(
            "patrol_types",
            BLOCKING,
            f"{len(bad)} patrol type(s) the legs need are missing or inactive",
            "Run patrol-datamodel; activate an inactive type in EarthRanger admin > "
            "Patrol "
            "types.",
            bad,
            affected=affected,
        )
    ]


def check_segment_schema(ctx: PreflightContext) -> list:
    if not flag_on(ctx):
        return []
    site = (ctx.api.get_segment_schema() or {}).get("json", {}).get("properties", {})
    ours = build_segment_schema(ctx.bundle)["json"]["properties"]
    bad = []
    for name in ours:
        if name not in site:
            bad.append(f"{name} (missing)")
        elif site[name].get("deprecated"):
            bad.append(f"{name} (deprecated)")
    if not bad:
        return []
    return [
        Finding.of(
            "segment_schema",
            BLOCKING,
            f"{len(bad)} universal patrol field(s) are missing or deprecated on the "
            "site",
            "Run patrol-datamodel; if an admin deprecated a field on purpose, restore "
            "it "
            "in the patrol form builder.",
            bad,
            affected=[p["uuid"] for p in ctx.bundle.patrols],
        )
    ]


def _choice_records(ctx: PreflightContext, field: str) -> list:
    return ctx.once(
        f"choices:{field}", lambda: _fetch_existing(er_client=ctx.api.er, field=field)
    )


def _used_choices(bundle: Bundle) -> dict:
    """field -> {value: set of patrol uuids} for every option a patrol uses."""
    ref = bundle.reference
    by_uuid = {x.uuid: x for x in ref.mandates + ref.stations if x.uuid}
    used: dict = {}

    def add(field, value, patrol):
        if value:
            used.setdefault(field, {}).setdefault(value, set()).add(patrol)

    attrs = {a.uuid: a for a in ref.patrol_attributes}
    for p in bundle.patrols:
        if p.get("station_uuid") in by_uuid:
            add(
                CHOICE_FIELD_STATION,
                choice_value(by_uuid[p["station_uuid"]]),
                p["uuid"],
            )
        if p.get("patrol_type"):
            add(CHOICE_FIELD_CLASS, p["patrol_type"].lower(), p["uuid"])
        for leg in p["legs"]:
            if leg.get("mandate_uuid") in by_uuid:
                add(
                    CHOICE_FIELD_MANDATE,
                    choice_value(by_uuid[leg["mandate_uuid"]]),
                    p["uuid"],
                )
        for a in p.get("attributes") or []:
            attr = attrs.get(a.get("attribute_uuid"))
            if attr and attr.type.strip() in ("LIST", "MLIST", "TREE"):
                values = (
                    a["value"] if isinstance(a.get("value"), list) else [a.get("value")]
                )
                for v in values:
                    opt = next((o for o in attr.options if o.key == v), None)
                    add(
                        custom_choice_field(attr.key),
                        choice_value(opt) if opt else None,
                        p["uuid"],
                    )
    return used


def check_choices(ctx: PreflightContext) -> list:
    if not flag_on(ctx):
        return []
    bad, affected = [], set()
    for field_name, values in sorted(_used_choices(ctx.bundle).items()):
        records = {r["value"]: r for r in _choice_records(ctx, field_name)}
        for value, patrols in sorted(values.items()):
            r = records.get(value)
            if r is None or not r.get("is_active", True):
                bad.append(
                    f"{field_name}: {value} ({'missing' if r is None else 'inactive'})"
                )
                affected |= patrols
    if not bad:
        return []
    return [
        Finding.of(
            "choices",
            WARNING,
            f"{len(bad)} option(s) the patrols use are missing or inactive; those "
            "fields "
            "would be stored "
            "without a matching dropdown entry",
            "Run patrol-datamodel; re-activate an option an admin switched off in "
            "Django "
            "admin > Choices.",
            bad,
            affected=affected,
        )
    ]


ALL_CHECKS = [
    check_feature_flag,
    check_permissions,
    check_patrol_types,
    check_segment_schema,
    check_choices,
    check_leg_span,
    check_time_coverage,
    check_timezone,
    check_language,
    check_bundle_warnings,
]


def subjects_by_member(ctx: PreflightContext) -> dict:
    def build():
        out = {}
        for s in ctx.api.list_subjects():
            mid = (s.get("additional") or {}).get("smart_member_id")
            if mid:
                out[str(mid)] = s
        return out

    return ctx.once("subjects", build)


def _member_patrols(bundle: Bundle) -> dict:
    out: dict = {}
    for p in bundle.patrols:
        for leg in p["legs"]:
            for m in leg["members"]:
                out.setdefault(m["employee_uuid"], set()).add(p["uuid"])
    return out


def _resolved(ctx):
    return ctx.once("resolved", ctx.api.get_patrol_config_resolved) or {}


def check_subjects(ctx: PreflightContext) -> list:
    if not flag_on(ctx):
        return []
    subjects = subjects_by_member(ctx)
    on_roster = {m["id"] for m in _resolved(ctx).get("members") or []}
    bad, affected = [], set()
    for member, patrols in sorted(_member_patrols(ctx.bundle).items()):
        s = subjects.get(member)
        if s is None:
            bad.append(f"{member}: no subject with this smart_member_id")
        elif s.get("is_active", True) and s["id"] not in on_roster:
            bad.append(
                f"{s['id']} ({s.get('name')}): active but not on the patrol member "
                "roster"
            )
        else:
            continue
        affected |= patrols
    if not bad:
        return []
    return [
        Finding.of(
            "subjects",
            BLOCKING,
            f"{len(bad)} patrol member(s) cannot be put on a segment",
            "Run patrol-datamodel (it creates the subjects and, with --roster-group, "
            "adds "
            "them to the roster); "
            "otherwise add them in Django admin > Subject groups to a group listed "
            "under "
            "Patrol configuration > members.",
            bad,
            affected=affected,
        )
    ]


def check_inactive_members(ctx: PreflightContext) -> list:
    if not flag_on(ctx):
        return []
    subjects = subjects_by_member(ctx)
    configured = [
        g["id"]
        for g in (ctx.once("config", ctx.api.get_patrol_config) or {}).get("members")
        or []
    ]
    in_groups = (
        set().union(*[ctx.api.group_subject_ids(g) for g in configured])
        if configured
        else set()
    )
    retired, stranded, affected, stranded_affected = [], [], set(), set()
    for member, patrols in sorted(_member_patrols(ctx.bundle).items()):
        s = subjects.get(member)
        if s is None or s.get("is_active", True):
            continue
        label = f"{s['id']} ({s.get('name')})"
        retired.append(label)
        affected |= patrols
        if s["id"] not in in_groups:
            stranded.append(label)
            stranded_affected |= patrols
    out = []
    if retired:
        out.append(
            Finding.of(
                "inactive_members",
                WARNING,
                f"{len(retired)} patrol member(s) are retired (inactive subjects)",
                "Nothing to do: das accepts only active members on a segment, so "
                "import-patrols reactivates "
                "these subjects for the run and retires them again when it finishes "
                "(recorded in its ledger).",
                retired,
                affected=affected,
            )
        )
    if stranded:
        out.append(
            Finding.of(
                "inactive_members",
                BLOCKING,
                f"{len(stranded)} retired member(s) are in no configured member group, "
                "so "
                "reactivating them "
                "would still leave them off the roster",
                "Add them to a group listed under Patrol configuration > members "
                "(Django "
                "admin > Subject groups).",
                stranded,
                affected=stranded_affected,
            )
        )
    return out


def check_teams(ctx: PreflightContext) -> list:
    if not flag_on(ctx):
        return []
    # The configuration lists every team with is_active; /resolved drops the
    # inactive ones, which would report them as missing.
    teams = (ctx.once("config", ctx.api.get_patrol_config) or {}).get("teams") or []
    names = {}
    for t in teams:
        for k in ("display", "value"):
            if t.get(k):
                names[t[k].strip().lower()] = t
    team_patrols: dict = {}
    for p in ctx.bundle.patrols:
        if p.get("team_uuid"):
            team_patrols.setdefault(p["team_uuid"], set()).add(p["uuid"])
    bad, affected = [], set()
    for req in build_team_requirements(ctx.bundle):
        t = names.get(req.name.strip().lower())
        if t is None or not t.get("is_active", True):
            bad.append(
                f"{req.name} ({'missing' if t is None else 'inactive'}, "
                f"{req.referenced_by_patrols} patrols)"
            )
            affected |= team_patrols.get(req.uuid, set())
    if not bad:
        return []
    return [
        Finding.of(
            "teams",
            BLOCKING,
            f"{len(bad)} patrol team(s) are missing or inactive",
            "Django admin > Patrol configuration > Teams: add (or activate) a team "
            "whose "
            "display is the name shown; "
            "patrol-datamodel's checklist lists their SMART members.",
            bad,
            affected=affected,
        )
    ]


ALL_CHECKS = [
    check_feature_flag,
    check_permissions,
    check_patrol_types,
    check_segment_schema,
    check_choices,
    check_subjects,
    check_inactive_members,
    check_teams,
    check_leg_span,
    check_time_coverage,
    check_timezone,
    check_language,
    check_bundle_warnings,
]


def category_path(hkey: str) -> str:
    """The part of an event-type value that names the SMART category: the
    hkey without its trailing dot, dots as underscores, lower-case (the rule
    in choices.event_type_value_for)."""
    return hkey.strip(".").replace(".", "_").lower()


def _observed_categories(bundle: Bundle) -> dict:
    out: dict = {}
    for p in bundle.patrols:
        for leg in p["legs"]:
            for w in leg["waypoints"]:
                for g in w["observation_groups"]:
                    for o in g["observations"]:
                        if o.get("category_hkey"):
                            out.setdefault(o["category_hkey"], set()).add(p["uuid"])
    return out


_UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
# The prefixes the data model sync writes: smart-ca-import (--from-file) or the
# CA uuid (Connect), optionally followed by a configurable model uuid.
_KNOWN_PREFIX = rf"(?:smart-ca-import|{_UUID})(?:_{_UUID})?"
# A configurable model in split mode appends _<display slug>_<8 hex> per variant.
_VARIANT_TAIL = r"_.+_[0-9a-f]{8}"


def resolve_event_types(bundle: Bundle, event_types: list, prefix: str | None):
    """Map each observed category hkey to one active event type.

    Returns (chosen, ambiguous, unmatched, variants): chosen {hkey: value};
    ambiguous {hkey: [values]}; unmatched [hkey]; variants {hkey: [values]}
    for categories present only as split-mode configurable model variants.
    Only the value shapes the data model sync writes are accepted, so a
    longer path that happens to end the same way is never picked.
    """
    active = [
        e["value"].lower()
        for e in event_types
        if e.get("value") and e.get("is_active", True)
    ]
    head = re.escape(prefix.lower()) if prefix else _KNOWN_PREFIX
    chosen, ambiguous, unmatched, variants = {}, {}, [], {}
    for hkey in sorted(_observed_categories(bundle)):
        path = re.escape(category_path(hkey))
        exact = re.compile(rf"^{head}_{path}$")
        variant = re.compile(rf"^{head}_{path}{_VARIANT_TAIL}$")
        candidates = sorted(v for v in active if exact.match(v))
        if len(candidates) == 1:
            chosen[hkey] = candidates[0]
        elif candidates:
            ambiguous[hkey] = candidates
        else:
            split = sorted(v for v in active if variant.match(v))
            if split:
                variants[hkey] = split
            else:
                unmatched.append(hkey)
    return chosen, ambiguous, unmatched, variants


def _event_categories(ctx: PreflightContext) -> set:
    """The ER event categories of the event types the import will use."""
    ets = ctx.once(
        "event_types", lambda: ctx.api.list_event_types(ctx.event_type_version)
    )
    chosen, ambiguous, _, variants = resolve_event_types(
        ctx.bundle, ets, ctx.event_type_prefix
    )
    used = set(chosen.values())
    for vs in list(ambiguous.values()) + list(variants.values()):
        used |= set(vs)
    out = set()
    for e in ets:
        if (e.get("value") or "").lower() in used:
            cat = e.get("category")
            cat = cat.get("value") if isinstance(cat, dict) else cat
            if cat:
                out.add(cat)
    return out


def check_event_types(ctx: PreflightContext) -> list:
    ets = ctx.once(
        "event_types", lambda: ctx.api.list_event_types(ctx.event_type_version)
    )
    chosen, ambiguous, unmatched, variants = resolve_event_types(
        ctx.bundle, ets, ctx.event_type_prefix
    )
    ctx.cache["event_types_map"] = chosen
    used = _observed_categories(ctx.bundle)
    out = []
    if unmatched:
        out.append(
            Finding.of(
                "event_types",
                BLOCKING,
                f"{len(unmatched)} observed SMART categor(ies) have no active event "
                "type "
                "on the site",
                "Each has one of these causes. (1) The data model was not pushed: "
                "er-smart-sync datamodel --from-file <ca>/datamodel.xml "
                "[--cm-from-file <ca>/configurable_model_<uuid>.xml] --ca-identifier "
                "<ID>. (2) It is a parent category, or one that is inactive in "
                "SMART: the sync creates event types only for active leaf "
                "categories, so these old observations have no type. (3) The "
                "operator lacks the category permission to read its event types: "
                "non-superusers only see event types of categories they can read. "
                "With --event-type-prefix set, also check it is the prefix that push "
                "used.",
                unmatched,
                affected=set().union(*[used[h] for h in unmatched]),
            )
        )
    if ambiguous:
        out.append(
            Finding.of(
                "event_types",
                WARNING,
                f"{len(ambiguous)} categor(ies) match more than one event type "
                "(several "
                "data model pushes on this site)",
                "Pass --event-type-prefix with the prefix of the push to import "
                "against: "
                "smart-ca-import for "
                "--from-file, smart-ca-import_<cm uuid> with a configurable model, or "
                "the "
                "SMART CA uuid for Connect.",
                [f"{h}: {', '.join(c)}" for h, c in sorted(ambiguous.items())],
                affected=set().union(*[used[h] for h in ambiguous]),
            )
        )
    if variants:
        out.append(
            Finding.of(
                "event_types",
                BLOCKING,
                f"{len(variants)} observed categor(ies) exist only as configurable "
                "model variants (one event type per form)",
                "import-patrols cannot yet tell which variant an observation "
                "belongs to. Re-push the data model with --cm-variant-mode "
                "consolidate so each category also has one event type, then pass "
                "--event-type-prefix for that push.",
                [f"{h}: {', '.join(v)}" for h, v in sorted(variants.items())],
                affected=set().union(*[used[h] for h in variants]),
            )
        )
    return out


ALL_CHECKS = [
    check_feature_flag,
    check_permissions,
    check_patrol_types,
    check_segment_schema,
    check_choices,
    check_subjects,
    check_inactive_members,
    check_teams,
    check_event_types,
    check_leg_span,
    check_time_coverage,
    check_timezone,
    check_language,
    check_bundle_warnings,
]


def _section(title: str, findings: list) -> list:
    if not findings:
        return []
    out = [f"{title} ({len(findings)})"]
    for f in findings:
        out.append(f"- [{f.code}] {f.message}" + (f" ({f.count})" if f.count else ""))
        out.append(f"    fix: {f.fix}")
        out += [f"    e.g. {e}" for e in f.examples]
        if f.count > len(f.examples) and f.examples:
            out.append(f"    … and {f.count - len(f.examples)} more (see --json)")
    out.append("")
    return out


def render_text(report: PreflightReport) -> str:
    ca = report.context.get("conservation_area") or {}
    lines = [
        f"Patrol preflight for {ca.get('name')} ({ca.get('id')}): "
        f"{report.context.get('patrols')} patrols, "
        f"{len(report.blocking)} blocking, {len(report.warnings)} warning(s)",
        "",
    ]
    lines += _section("BLOCKING", report.blocking)
    lines += _section("WARNING", report.warnings)
    if report.blocking:
        lines.append(
            f"{len(report.blocking)} blocking finding(s): import-patrols will refuse "
            "to run "
            f"(or skip the {len(report.blocked_patrols)} affected patrols "
            "with --allow-blocking)."
        )
    else:
        lines.append("All clear: no blocking findings.")
    return "\n".join(lines)


def render_json(report: PreflightReport) -> str:
    import json

    return json.dumps(
        {
            "context": report.context,
            "exit_code": report.exit_code,
            "blocking": [f.to_dict() for f in report.blocking],
            "warnings": [f.to_dict() for f in report.warnings],
        },
        indent=1,
        ensure_ascii=False,
    )
