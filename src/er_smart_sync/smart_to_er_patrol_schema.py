"""Pure builders from a migration bundle to EarthRanger patrol configuration
plans: patrol types (one per SMART transport), choice sets for the universal
patrol fields, ranger subjects (one per SMART employee), and the teams an
admin must create by hand. No I/O. The universal segment schema builders
live further down in this module.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass

from smartconnect.choices import sanitize_choice_value

from .bundle import Bundle
from .choices import ChoiceOption, ChoiceSet
from .smart_to_er_v2 import SCALAR_JSON, SCALAR_UI, _build_choice_property_pair

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
        # identity is the SMART key, or the uuid for key-less rows (stations): never
        # the label, which changes with the language
        if it.key:
            value = sanitize_choice_value(it.key)
        elif it.uuid:
            value = it.uuid.replace("-", "")
        else:
            continue  # nothing to identify the option by
        if value in seen:
            continue
        seen.add(value)
        display = bundle.label(it) or it.key or it.uuid
        out.append(ChoiceOption(value=value, display=display, is_active=it.active))
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


# ── universal segment schema ────────────────────────────────────

CANONICAL_FIELDS = (
    "objective",
    "mandate",
    "station",
    "patrol_class",
    "armed",
    "comment",
)
SEGMENT_SECTION = "section-1"
SEGMENT_SECTION_LABEL = "Patrol details"


def _scalar(smart_type: str, title: str) -> tuple:
    # das's segment-type meta-schema requires `deprecated` on every property
    # (the PUT is a 400 without it; verified against gundi-dev on 2026-10-13)
    json_prop = {
        **copy.deepcopy(SCALAR_JSON[smart_type]),
        "title": title,
        "deprecated": False,
    }
    ui = {**copy.deepcopy(SCALAR_UI[smart_type]), "parent": SEGMENT_SECTION}
    return json_prop, ui


def _choice(
    title: str, field_name: str, choices_base_url: str, is_multiple: bool = False
) -> tuple:
    json_prop, ui = _build_choice_property_pair(
        smart_type="MLIST" if is_multiple else "LIST",
        display=title,
        is_multiple=is_multiple,
        field_name=field_name,
        choices_base_url=choices_base_url,
    )
    ui["parent"] = SEGMENT_SECTION
    # das drops ui.fields[*].choices when it stores the schema (normalize_v2_schema);
    # emitting it would make every run see a difference and PUT again
    ui.pop("choices", None)
    return json_prop, ui


def build_segment_schema(
    bundle: Bundle, *, choices_base_url: str = "/api/v2.0/schemas"
) -> dict:
    """The universal patrol fields for this CA, as the das schema envelope."""
    pairs: dict = {}
    pairs["objective"] = _scalar("TEXT", "Objective")
    pairs["mandate"] = _choice("Mandate", CHOICE_FIELD_MANDATE, choices_base_url)
    pairs["station"] = _choice("Station", CHOICE_FIELD_STATION, choices_base_url)
    pairs["patrol_class"] = _choice(
        "Patrol class", CHOICE_FIELD_CLASS, choices_base_url
    )
    pairs["armed"] = _scalar("BOOLEAN", "Armed")
    comment_json, comment_ui = _scalar("TEXT", "Comment")
    comment_ui["inputType"] = "LONG_TEXT"
    pairs["comment"] = (comment_json, comment_ui)
    for attr in bundle.reference.patrol_attributes:
        if not attr.active:
            continue
        name = f"smart_{sanitize_choice_value(attr.key)}"
        t = attr.type.strip()
        title = bundle.label(attr) or attr.key
        if t in ("LIST", "MLIST", "TREE"):
            pairs[name] = _choice(
                title,
                custom_choice_field(attr.key),
                choices_base_url,
                is_multiple=(t == "MLIST"),
            )
        elif t in SCALAR_JSON:
            pairs[name] = _scalar(t, title)
        else:
            pairs[name] = _scalar("TEXT", title)
    return {
        "json": {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "unevaluatedProperties": False,
            "properties": {k: v[0] for k, v in pairs.items()},
            "required": [],
        },
        "ui": {
            "fields": {k: v[1] for k, v in pairs.items()},
            "headers": {},
            "order": [SEGMENT_SECTION],
            "sections": {
                SEGMENT_SECTION: {
                    "label": SEGMENT_SECTION_LABEL,
                    "columns": 1,
                    "isActive": True,
                    "leftColumn": [{"name": k, "type": "field"} for k in pairs],
                    "rightColumn": [],
                }
            },
        },
    }


def merge_segment_schema(current: dict | None, ours: dict) -> dict:
    """Keep what an admin added by hand; our keys win where they overlap.
    Returns a document equal to `current` when nothing needs to change."""
    if not current or not (current.get("json") or {}).get("properties"):
        return copy.deepcopy(ours)
    merged = copy.deepcopy(current)
    merged["json"].setdefault("$schema", ours["json"]["$schema"])
    merged["json"].setdefault("type", "object")
    merged["json"].setdefault("required", [])
    merged["json"].setdefault("unevaluatedProperties", False)
    merged["json"]["properties"] = {
        **current["json"].get("properties", {}),
        **ours["json"]["properties"],
    }
    ui = merged.setdefault("ui", {})
    ui["fields"] = {
        **(current.get("ui", {}).get("fields") or {}),
        **ours["ui"]["fields"],
    }
    ui.setdefault("headers", {})
    sections = ui.setdefault("sections", {})
    if SEGMENT_SECTION not in sections:
        sections[SEGMENT_SECTION] = copy.deepcopy(
            ours["ui"]["sections"][SEGMENT_SECTION]
        )
    order = ui.setdefault("order", [])
    if SEGMENT_SECTION not in order:
        order.insert(0, SEGMENT_SECTION)
    placed = {
        f["name"]
        for sec in sections.values()
        for col in ("leftColumn", "rightColumn")
        for f in sec.get(col, [])
    }
    left = sections[SEGMENT_SECTION].setdefault("leftColumn", [])
    ours_names = [
        f["name"] for f in ours["ui"]["sections"][SEGMENT_SECTION]["leftColumn"]
    ]
    # the admin's order is kept; only our fields placed nowhere are appended
    seen: set = set()
    new_left = []
    missing = [{"name": n, "type": "field"} for n in ours_names if n not in placed]
    for entry in left + missing:
        if entry["name"] in seen:
            continue
        seen.add(entry["name"])
        new_left.append(entry)
    sections[SEGMENT_SECTION]["leftColumn"] = new_left
    return merged
