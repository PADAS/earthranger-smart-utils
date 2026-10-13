"""The smart-able migration bundle: four JSON files produced by
`smart-able export` (manifest, reference, patrols, tracks). This module
loads them, validates them against the vendored schema, and resolves labels
in the chosen language. It knows nothing about EarthRanger.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import jsonschema

SUPPORTED_SCHEMA_VERSION = 1
SCHEMA_PATH = Path(__file__).with_name("schema.json")
FILES = ("manifest", "reference", "patrols", "tracks")


class BundleError(Exception):
    """The bundle is missing, malformed, or asks for something unsupported."""


@dataclass
class Manifest:
    bundle_schema_version: int
    generated_at: str
    source: dict
    conservation_area: dict
    timezone: dict
    default_language: str
    languages: list
    counts: dict
    time_coverage: dict
    warnings: list


@dataclass
class Labelled:
    uuid: str | None
    key: str | None
    labels: dict
    label: str | None
    active: bool = True
    extra: dict = field(default_factory=dict)


@dataclass
class PatrolAttribute:
    uuid: str
    key: str
    labels: dict
    label: str | None
    type: str
    active: bool
    options: list


@dataclass
class Employee:
    uuid: str
    id: str | None
    given_name: str | None
    family_name: str | None
    agency_uuid: str | None
    rank_uuid: str | None
    employment_start: str | None
    employment_end: str | None
    active: bool
    # also a SMART Desktop login; False in bundles written before the field existed
    has_smart_login: bool = False


@dataclass
class Reference:
    patrol_types: list
    transports: list
    mandates: list
    stations: list
    patrol_attributes: list
    agencies: list
    ranks: list
    teams: list
    employees: list


def _labelled(d: dict, *extra_keys: str) -> Labelled:
    return Labelled(
        uuid=d.get("uuid"),
        key=d.get("key"),
        labels=d.get("labels") or {},
        label=d.get("label"),
        active=bool(d.get("active", True)),
        extra={k: d.get(k) for k in extra_keys},
    )


@dataclass
class Bundle:
    path: Path
    manifest: Manifest
    reference: Reference
    patrols: list
    tracks: list
    language: str

    @property
    def ca_uuid(self) -> str:
        return self.manifest.conservation_area["uuid"]

    def label(self, obj: Any) -> str:
        """Display text: chosen language, bundle default, then any; the key last."""
        if isinstance(obj, dict):
            labels, plain = obj.get("labels"), obj.get("label")
            key = obj.get("key")
        else:
            labels, plain, key = (
                getattr(obj, "labels", None),
                getattr(obj, "label", None),
                getattr(obj, "key", None),
            )
        labels = labels or {}
        for code in (self.language, self.manifest.default_language):
            if labels.get(code):
                return labels[code]
        if plain:
            return plain
        for v in labels.values():
            if v:
                return v
        return key or ""

    def member_uuids(self) -> set:
        return {
            m["employee_uuid"]
            for p in self.patrols
            for leg in p["legs"]
            for m in leg["members"]
        }

    def leg_transport_uuids(self) -> set:
        return {
            leg["transport_uuid"]
            for p in self.patrols
            for leg in p["legs"]
            if leg.get("transport_uuid")
        }

    def patrol_team_uuids(self) -> set:
        return {p["team_uuid"] for p in self.patrols if p.get("team_uuid")}


def _read(path: Path, name: str, schema: dict) -> Any:
    f = path / f"{name}.json"
    if not f.exists():
        names = ", ".join(n + ".json" for n in FILES)
        raise BundleError(f"{f}: missing; a bundle has {names}")
    try:
        doc = json.loads(f.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise BundleError(f"{name}.json: not valid JSON ({e})") from None
    validator = jsonschema.Draft202012Validator(
        {"$ref": f"#/$defs/{name}", "$defs": schema["$defs"]}
    )
    errors = sorted(validator.iter_errors(doc), key=lambda e: list(e.path))
    if errors:
        e = errors[0]
        where = "/".join(str(p) for p in e.path) or "(root)"
        more = f" (+{len(errors) - 1} more)" if len(errors) > 1 else ""
        raise BundleError(f"{name}.json: {where}: {e.message}{more}")
    return doc


def load_bundle(path: Path, *, language: str | None = None) -> Bundle:
    path = Path(path)
    if not path.is_dir():
        raise BundleError(f"{path}: not a directory")
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    manifest_file = path / "manifest.json"
    if manifest_file.exists():
        try:
            doc = json.loads(manifest_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            doc = None  # _read reports the parse error
        # a manifest that is not an object is left for _read's schema check
        version = (
            doc.get("bundle_schema_version")
            if isinstance(doc, dict)
            else SUPPORTED_SCHEMA_VERSION
        )
        if version != SUPPORTED_SCHEMA_VERSION:
            raise BundleError(
                f"manifest.json: bundle_schema_version {version!r} is not supported; "
                f"this er-smart-sync reads version {SUPPORTED_SCHEMA_VERSION}"
            )
    docs = {name: _read(path, name, schema) for name in FILES}
    m = docs["manifest"]
    manifest = Manifest(**{k: m[k] for k in Manifest.__dataclass_fields__})
    codes = [lang["code"] for lang in manifest.languages]
    lang = language or manifest.default_language
    if lang not in codes:
        coverage = ", ".join(
            f"{x['code']} ({x['labels']} labels, {x['missing']} missing)"
            for x in manifest.languages
        )
        raise BundleError(
            f"language {lang!r} is not in the bundle; available: {coverage}"
        )
    r = docs["reference"]
    reference = Reference(
        patrol_types=r["patrol_types"],
        transports=[_labelled(t, "patrol_type") for t in r["transports"]],
        mandates=[_labelled(x) for x in r["mandates"]],
        stations=[_labelled(x) for x in r["stations"]],
        patrol_attributes=[
            PatrolAttribute(
                uuid=a["uuid"],
                key=a["key"],
                labels=a.get("labels") or {},
                label=a.get("label"),
                type=a["type"],
                active=bool(a["active"]),
                options=[_labelled(o) for o in a["options"]],
            )
            for a in r["patrol_attributes"]
        ],
        agencies=r["agencies"],
        ranks=[_labelled(x, "agency_uuid") for x in r["ranks"]],
        teams=[_labelled(x, "member_uuids") for x in r["teams"]],
        employees=[
            Employee(
                **{
                    k: e.get(k)
                    for k in Employee.__dataclass_fields__
                    if k != "has_smart_login"
                },
                has_smart_login=bool(e.get("has_smart_login", False)),
            )
            for e in r["employees"]
        ],
    )
    return Bundle(
        path=path,
        manifest=manifest,
        reference=reference,
        patrols=docs["patrols"]["patrols"],
        tracks=docs["tracks"]["tracks"],
        language=lang,
    )
