"""Pure builders from a migration bundle to EarthRanger patrol configuration
plans: patrol types (one per SMART transport), choice sets for the universal
patrol fields, ranger subjects (one per SMART employee), and the teams an
admin must create by hand. No I/O. The universal segment schema builders
live further down in this module.
"""

from __future__ import annotations

import copy
import hashlib
from dataclasses import dataclass, replace

from smartconnect.choices import sanitize_choice_value

from .bundle import Bundle
from .choices import ChoiceOption, ChoiceSet
from .smart_to_er_v2 import SCALAR_JSON, SCALAR_UI, _build_choice_property_pair

CHOICE_FIELD_MANDATE = "patrol_mandate"
CHOICE_FIELD_STATION = "patrol_station"
CHOICE_FIELD_CLASS = "patrol_class"
PATROL_CLASSES = ("GROUND", "AIR", "MARINE", "MIXED")
PATROL_TYPE_VALUE_MAX = 50  # PatrolType.value is a CharField(max_length=50)
# universal patrol fields named so are this tool's, one per SMART custom attribute
CUSTOM_FIELD_PREFIX = "smart_"


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


CHOICE_FIELD_MAX = 40  # Choice.field is a CharField(max_length=40)


def custom_choice_field(attribute_key: str) -> str:
    """`patrol_<key>`; a key too long for ER's 40-character limit keeps its
    first characters and ends in a hash of the whole key, so it stays unique."""
    field = f"patrol_{sanitize_choice_value(attribute_key)}"
    if len(field) <= CHOICE_FIELD_MAX:
        return field
    digest = hashlib.sha256(attribute_key.encode()).hexdigest()[:8]
    return f"{field[: CHOICE_FIELD_MAX - 9]}_{digest}"


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


def choice_value(item) -> str | None:
    """The Choice value for a SMART list item: its key, sanitized, or for
    key-less rows (stations) its uuid without hyphens. Never the label."""
    if item.key:
        return sanitize_choice_value(item.key)
    if item.uuid:
        return item.uuid.replace("-", "")
    return None


def _options(bundle: Bundle, items: list, unlabelled=None) -> tuple:
    """Choice options keyed by SMART key (or uuid). An item with no label in
    any language shows what `unlabelled(item)` returns, else its key or uuid."""
    out, seen = [], set()
    for it in items:
        # identity is the SMART key, or the uuid for key-less rows (stations): never
        # the label, which changes with the language
        value = choice_value(it)
        if value is None:
            continue  # nothing to identify the option by
        if value in seen:
            continue
        seen.add(value)
        display = bundle.label(it)
        if not display and unlabelled is not None:
            display = unlabelled(it)
        display = display or it.key or it.uuid
        out.append(ChoiceOption(value=value, display=display, is_active=it.active))
    return tuple(out)


def _station_options(bundle: Bundle) -> tuple:
    """SMART lets a station exist with no name in any language. It gets a
    readable placeholder instead of its uuid; one no patrol uses is planned
    inactive, so it is not offered but stays this bundle's to manage."""
    unused = {st.uuid.replace("-", "") for st, in_use in _unnamed(bundle) if not in_use}
    return tuple(
        replace(o, is_active=False) if o.value in unused else o
        for o in _options(
            bundle,
            bundle.reference.stations,
            unlabelled=lambda st: f"Unnamed station {st.uuid[:8]}",
        )
    )


def _unnamed(bundle: Bundle) -> list:
    used = {p.get("station_uuid") for p in bundle.patrols}
    return [
        (st, st.uuid in used)
        for st in bundle.reference.stations
        if st.uuid and not bundle.label(st)
    ]


def unnamed_stations(bundle: Bundle) -> list:
    """(uuid, used by a patrol) for each station with no name in any language."""
    return [(st.uuid, in_use) for st, in_use in _unnamed(bundle)]


def build_patrol_choice_sets(bundle: Bundle) -> list:
    ref = bundle.reference
    sets = [
        ChoiceSet(field=CHOICE_FIELD_MANDATE, options=_options(bundle, ref.mandates)),
        ChoiceSet(field=CHOICE_FIELD_STATION, options=_station_options(bundle)),
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


def login_only_employees(bundle: Bundle) -> list:
    """Employees that are SMART Desktop logins and never went on a patrol, such
    as the `smart` admin every SMART install creates. They are accounts, not
    rangers. A login that is a leg member stays a subject: sites record shared
    admin logins as patrol members, and the patrol import needs them."""
    members = bundle.member_uuids()
    return [
        e
        for e in bundle.reference.employees
        if e.has_smart_login and e.uuid not in members
    ]


def build_subjects(bundle: Bundle) -> list:
    """One subject per employee that has a leg membership or an active employment,
    except SMART logins that never patrolled (login_only_employees). A member
    missing from the employee list (deleted staff) gets a subject named by its
    uuid."""
    members = bundle.member_uuids()
    known = {e.uuid for e in bundle.reference.employees}
    accounts = {e.uuid for e in login_only_employees(bundle)}
    out = []
    for e in bundle.reference.employees:
        if not (e.active or e.uuid in members) or e.uuid in accounts:
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
        name = f"{CUSTOM_FIELD_PREFIX}{sanitize_choice_value(attr.key)}"
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
    # a `smart_<key>` field whose custom attribute is now inactive or gone is
    # ours but no longer planned: deprecate it and take it off the form, but
    # keep its definition so segments that already carry a value still validate
    stale = {
        k
        for k in merged["json"]["properties"]
        if k.startswith(CUSTOM_FIELD_PREFIX) and k not in ours["json"]["properties"]
    }
    for k in stale:
        merged["json"]["properties"][k] = {
            **merged["json"]["properties"][k],
            "deprecated": True,
        }
    # das's meta-schema requires `deprecated` on every property, and an admin
    # field saved before it did may lack it
    for k, prop in merged["json"]["properties"].items():
        if isinstance(prop, dict) and "deprecated" not in prop:
            merged["json"]["properties"][k] = {**prop, "deprecated": False}
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
    for sec in sections.values():
        for col in ("leftColumn", "rightColumn"):
            if any(f.get("name") in stale for f in sec.get(col, [])):
                sec[col] = [f for f in sec[col] if f.get("name") not in stale]
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
    # a managed field an admin moved to another section stays there; its
    # `parent` must name the section that holds it
    for sec_name, sec in sections.items():
        for col in ("leftColumn", "rightColumn"):
            for f in sec.get(col, []):
                if f.get("name") in ours_names and f["name"] in ui["fields"]:
                    ui["fields"][f["name"]] = {
                        **ui["fields"][f["name"]],
                        "parent": sec_name,
                    }
    return merged
