"""`patrol-preflight`: a read-only check of an ER site against a smart-able
bundle, run before `import-patrols`. Each check returns findings; a blocking
finding names the patrols it affects so the importer can skip just those.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field

from .bundle import Bundle

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
            f"{len(legs)} leg(s) start on SMART's 1970-01-01 placeholder or span more than a year "
            f"({days} leg days as stored)",
            "Nothing to do: import-patrols keeps only the leg days with a track or a waypoint. "
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
            f"{n} of {tc.get('leg_days')} leg days have no track, start/end time or timed waypoint; "
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
                f"timezone {ctx.timezone!r} is configured but the bundle was written for {name!r} ({source})",
                f"Re-export the bundle with the right zone: SMART_ABLE_TIMEZONE={ctx.timezone} ./smart-able export {ca_id}",
            )
        ]
    if not ctx.timezone and source == "inferred":
        return [
            Finding(
                "timezone",
                WARNING,
                f"the bundle's timezone {name} was inferred from waypoint positions",
                f"Confirm it by setting smart.timezone: {name} in sync.yaml (or --timezone {name}); "
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
            f"they fall back to {b.manifest.default_language!r} or any language with one",
            "Pick the best-covered language in smart-able's Data Model tab and pass it as --smart-language.",
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
            "Read them in the bundle's manifest.json; smart-able's quirks.html explains each kind.",
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
                    "Fix the cause (often access or connectivity) and run patrol-preflight again.",
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
