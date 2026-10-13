"""Pure builders from a migration bundle to EarthRanger patrol configuration
plans: patrol types (one per SMART transport), choice sets for the universal
patrol fields, ranger subjects (one per SMART employee), and the teams an
admin must create by hand. No I/O. The universal segment schema builders
live further down in this module.
"""

from __future__ import annotations

from dataclasses import dataclass

from smartconnect.choices import sanitize_choice_value

from .bundle import Bundle
from .choices import ChoiceOption, ChoiceSet

CHOICE_FIELD_MANDATE = "patrol_mandate"
CHOICE_FIELD_STATION = "patrol_station"
CHOICE_FIELD_CLASS = "patrol_class"
PATROL_CLASSES = ("GROUND", "AIR", "MARINE", "MIXED")
PATROL_TYPE_VALUE_MAX = 50  # PatrolType.value is a CharField(max_length=50)


@dataclass(frozen=True)
class PatrolTypePlan:
    value: str
    display: str
    is_active: bool
    transport_uuid: str


@dataclass(frozen=True)
class SubjectPlan:
    name: str
    smart_member_id: str
    smart_employee_id: str | None
    smart_ca_uuid: str
    is_active: bool


@dataclass(frozen=True)
class TeamRequirement:
    uuid: str
    name: str
    member_names: list
    referenced_by_patrols: int


def custom_choice_field(attribute_key: str) -> str:
    return f"patrol_{sanitize_choice_value(attribute_key)}"


def patrol_type_value(transport_key: str, taken: set) -> str:
    """`patrol_<key>`, unique within `taken` and within ER's 50-character limit."""
    base = f"patrol_{sanitize_choice_value(transport_key)}"[:PATROL_TYPE_VALUE_MAX]
    value, n = base, 1
    while value in taken:
        n += 1
        suffix = f"_{n}"
        value = base[: PATROL_TYPE_VALUE_MAX - len(suffix)] + suffix
    taken.add(value)
    return value


def build_patrol_types(bundle: Bundle) -> list:
    taken: set = set()
    return [
        PatrolTypePlan(
            value=patrol_type_value(t.key or t.uuid or "", taken),
            display=bundle.label(t),
            is_active=t.active,
            transport_uuid=t.uuid,
        )
        for t in bundle.reference.transports
    ]


def _options(bundle: Bundle, items: list) -> tuple:
    out, seen = [], set()
    for it in items:
        key = it.key or bundle.label(it)
        if not key:
            continue  # nothing to identify the option by
        value = sanitize_choice_value(key)
        if value in seen:
            continue
        seen.add(value)
        out.append(
            ChoiceOption(
                value=value, display=bundle.label(it) or key, is_active=it.active
            )
        )
    return tuple(out)


def build_patrol_choice_sets(bundle: Bundle) -> list:
    ref = bundle.reference
    sets = [
        ChoiceSet(field=CHOICE_FIELD_MANDATE, options=_options(bundle, ref.mandates)),
        ChoiceSet(field=CHOICE_FIELD_STATION, options=_options(bundle, ref.stations)),
    ]
    present = {pt["key"]: bool(pt.get("active", True)) for pt in ref.patrol_types}
    classes = tuple(
        ChoiceOption(
            value=c.lower(), display=c.title(), is_active=present.get(c, False)
        )
        for c in sorted(set(PATROL_CLASSES) | set(present))
    )
    sets.append(ChoiceSet(field=CHOICE_FIELD_CLASS, options=classes))
    for attr in ref.patrol_attributes:
        if attr.type.strip() in ("LIST", "MLIST", "TREE"):
            sets.append(
                ChoiceSet(
                    field=custom_choice_field(attr.key),
                    options=_options(bundle, attr.options),
                )
            )
    return sets


def _employee_name(e) -> str:
    return " ".join(p for p in (e.given_name, e.family_name) if p).strip() or (
        e.id or e.uuid
    )


def build_subjects(bundle: Bundle) -> list:
    """One subject per employee that has a leg membership or an active employment.
    A member missing from the employee list (deleted staff) gets a subject
    named by its uuid."""
    members = bundle.member_uuids()
    known = {e.uuid for e in bundle.reference.employees}
    out = []
    for e in bundle.reference.employees:
        if not (e.active or e.uuid in members):
            continue
        out.append(
            SubjectPlan(
                name=_employee_name(e),
                smart_member_id=e.uuid,
                smart_employee_id=e.id,
                smart_ca_uuid=bundle.ca_uuid,
                is_active=e.active,
            )
        )
    for uuid in sorted(members - known):
        out.append(
            SubjectPlan(
                name=uuid,
                smart_member_id=uuid,
                smart_employee_id=None,
                smart_ca_uuid=bundle.ca_uuid,
                is_active=False,
            )
        )
    return out


def build_team_requirements(bundle: Bundle) -> list:
    """Every team a patrol references, in SMART's team table or not."""
    counts: dict = {}
    for p in bundle.patrols:
        if p.get("team_uuid"):
            counts[p["team_uuid"]] = counts.get(p["team_uuid"], 0) + 1
    names = {e.uuid: _employee_name(e) for e in bundle.reference.employees}
    teams = {t.uuid: t for t in bundle.reference.teams}
    out = []
    for uuid, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
        t = teams.get(uuid)
        if t is not None:
            out.append(
                TeamRequirement(
                    uuid=uuid,
                    name=bundle.label(t) or uuid,
                    member_names=[
                        names.get(u, u) for u in (t.extra.get("member_uuids") or [])
                    ],
                    referenced_by_patrols=n,
                )
            )
        else:
            out.append(
                TeamRequirement(
                    uuid=uuid,
                    name=f"SMART team {uuid[:8]}",
                    member_names=[],
                    referenced_by_patrols=n,
                )
            )
    return out
