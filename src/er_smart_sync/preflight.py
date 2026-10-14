"""`patrol-preflight`: a read-only check of an ER site against a smart-able
bundle, run before `import-patrols`. Each check returns findings; a blocking
finding names the patrols it affects so the importer can skip just those.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field

from .bundle import Bundle
from .choices import _fetch_existing
from .smart_to_er_patrol_schema import (
    CHOICE_FIELD_CLASS,
    CHOICE_FIELD_MANDATE,
    CHOICE_FIELD_STATION,
    build_patrol_types,
    build_segment_schema,
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

REQUIRED_PERMISSIONS = {
    "patrol": ("add", "change", "view"),
    "patrolconfiguration": ("view",),
    "eventrelatedsegments": ("add",),
    "event": ("add", "change"),
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
    if not missing:
        return []
    return [
        Finding.of(
            "permissions",
            BLOCKING,
            f"the operator lacks {len(missing)} permission(s) the import needs",
            "Django admin > Users > the operator: grant each permission listed (app "
            "`activity`).",
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
