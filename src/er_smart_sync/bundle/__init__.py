"""The smart-able migration bundle: the files produced by `smart-able
export` (manifest, reference, patrols, tracks). This module loads them,
validates them against the vendored schema, and resolves labels in the
chosen language. It knows nothing about EarthRanger.

Tracks are the bulk of a bundle: hundreds of megabytes for a large
conservation area. A version 2 bundle keeps them one per line in
tracks.jsonl, a patrol's lines together, with each patrol's byte range in
tracks.index.json, and they are read a patrol at a time (TrackStore), so
memory does not grow with the area. A version 1 bundle has them all in
tracks.json, which is read whole, as it always was.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import jsonschema

SUPPORTED_SCHEMA_VERSIONS = (1, 2)
SCHEMA_PATH = Path(__file__).with_name("schema.json")
FILES = ("manifest", "reference", "patrols", "tracks")  # version 1
FILES_V2 = ("manifest", "reference", "patrols", "tracks.index")
TRACKS_JSONL = "tracks.jsonl"


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
    # SMART's employee.gender as recorded (M or F); None in bundles written
    # before the field existed and where SMART has none
    sex: str | None = None


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


def _points(track: dict) -> int:
    return sum(len(part.get("coordinates") or []) for part in track.get("parts") or [])


class TrackStore:
    """A bundle's tracks, asked for a patrol at a time.

    Version 2 holds only the index and reads a patrol's lines from
    tracks.jsonl when asked; version 1 holds the list tracks.json parsed
    to. Iterating gives every track (a patrol at a time for version 2, so
    still without holding the file) and len() is the number of tracks.
    """

    def __init__(self):
        self._by_patrol: dict | None = None  # version 1: patrol uuid -> tracks
        self._all: list = []
        self._path: Path | None = None  # version 2: tracks.jsonl
        self._index: dict = {}
        self._validator = None

    @classmethod
    def from_list(cls, tracks: list, patrols: list | None = None) -> "TrackStore":
        """Version 1: every track, already parsed. A track is filed under the
        patrol that owns its leg day (as the import always found it, by leg
        day), and under its own patrol_uuid only if no patrol has that day."""
        owner = {
            day["uuid"]: p["uuid"]
            for p in patrols or []
            for leg in p.get("legs") or []
            for day in leg.get("days") or []
        }
        store = cls()
        store._all = tracks
        store._by_patrol = {}
        for t in tracks:
            patrol = owner.get(t["leg_day_uuid"], t["patrol_uuid"])
            store._by_patrol.setdefault(patrol, []).append(t)
        return store

    @classmethod
    def from_files(cls, jsonl: Path, index: dict, schema: dict) -> TrackStore:
        """index is tracks.index.json, already checked against the schema.
        Its ranges must cover tracks.jsonl exactly, in order, with no gap."""
        store = cls()
        store._path = Path(jsonl)
        store._index = index["patrols"]
        store._validator = jsonschema.Draft202012Validator(
            {"$ref": "#/$defs/track", "$defs": schema["$defs"]}
        )
        if not store._path.is_file():
            raise BundleError(
                f"{store._path}: missing; a version 2 bundle has {TRACKS_JSONL}"
            )
        size, at = store._path.stat().st_size, 0
        for uuid, e in sorted(store._index.items(), key=lambda kv: kv[1]["offset"]):
            if e["offset"] != at:
                what = "overlaps the one before" if e["offset"] < at else "leaves a gap"
                raise BundleError(
                    f"tracks.index.json: patrol {uuid}: offset {e['offset']} "
                    f"{what} (expected {at})"
                )
            at += e["length"]
        if at != size:
            raise BundleError(
                f"tracks.index.json: covers {at} bytes but {TRACKS_JSONL} is {size}"
            )
        return store

    def for_patrol(self, patrol_uuid: str) -> list:
        if self._by_patrol is not None:
            return self._by_patrol.get(patrol_uuid, [])
        e = self._index.get(patrol_uuid)
        if not e:
            return []
        where = f"{TRACKS_JSONL}: patrol {patrol_uuid}"
        try:
            with open(
                self._path, "rb"
            ) as f:  # its own handle: safe from several threads
                f.seek(e["offset"])
                raw = f.read(e["length"])
        except OSError as err:  # the file went away after the bundle was loaded
            raise BundleError(f"{where}: cannot be read ({err})") from None
        if len(raw) != e["length"] or not raw.endswith(b"\n"):
            raise BundleError(
                f"{where}: the file does not match its index (the patrol's "
                f"lines do not end where the index says)"
            )
        lines = raw[:-1].split(b"\n")
        if len(lines) != e["tracks"]:
            raise BundleError(
                f"{where}: {len(lines)} lines where the index says {e['tracks']}"
            )
        out = []
        for n, line in enumerate(lines, 1):
            try:
                t = json.loads(line)
            except ValueError as err:  # not JSON, or not UTF-8
                raise BundleError(
                    f"{where}: line {n}: not valid JSON ({err})"
                ) from None
            error = next(iter(self._validator.iter_errors(t)), None)
            if error:
                at = "/".join(str(p) for p in error.path) or "(root)"
                raise BundleError(f"{where}: line {n}: {at}: {error.message}")
            if t["patrol_uuid"] != patrol_uuid:
                raise BundleError(
                    f"{where}: line {n}: belongs to patrol {t['patrol_uuid']}"
                )
            out.append(t)
        return out

    def count(self, patrol_uuid: str) -> int:
        if self._by_patrol is not None:
            return len(self._by_patrol.get(patrol_uuid, []))
        return self._index.get(patrol_uuid, {}).get("tracks", 0)

    def points(self, patrol_uuid: str) -> int:
        """Track points of a patrol; from the index, without reading its tracks."""
        if self._by_patrol is not None:
            return sum(_points(t) for t in self._by_patrol.get(patrol_uuid, []))
        return self._index.get(patrol_uuid, {}).get("points", 0)

    def patrol_uuids(self) -> set:
        return set(self._index if self._by_patrol is None else self._by_patrol)

    def __iter__(self):
        if self._by_patrol is not None:
            yield from self._all
            return
        for uuid, _ in sorted(self._index.items(), key=lambda kv: kv[1]["offset"]):
            yield from self.for_patrol(uuid)

    def __len__(self) -> int:
        if self._by_patrol is not None:
            return len(self._all)
        return sum(e["tracks"] for e in self._index.values())


@dataclass
class Bundle:
    path: Path
    manifest: Manifest
    reference: Reference
    patrols: list
    tracks: TrackStore
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

    def observed_category_hkeys(self) -> set:
        """SMART category hkeys (with trailing dot) that observations use."""
        return {
            o["category_hkey"]
            for p in self.patrols
            for leg in p["legs"]
            for w in leg["waypoints"]
            for g in w["observation_groups"]
            for o in g["observations"]
            if o.get("category_hkey")
        }


def _read(path: Path, name: str, schema: dict, files: tuple = FILES) -> Any:
    f = path / f"{name}.json"
    if not f.exists():
        names = ", ".join(n + ".json" for n in files)
        raise BundleError(f"{f}: missing; a bundle has {names}")
    try:
        doc = json.loads(f.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise BundleError(f"{name}.json: not valid JSON ({e})") from None
    validator = jsonschema.Draft202012Validator(
        {"$ref": f"#/$defs/{name.replace('.', '_')}", "$defs": schema["$defs"]}
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
        version = doc.get("bundle_schema_version") if isinstance(doc, dict) else 1
        if version not in SUPPORTED_SCHEMA_VERSIONS:
            raise BundleError(
                f"manifest.json: bundle_schema_version {version!r} is not supported; "
                f"this er-smart-sync reads versions "
                f"{' and '.join(str(v) for v in SUPPORTED_SCHEMA_VERSIONS)}"
            )
    else:
        version = 1  # _read reports the missing manifest
    names = FILES_V2 if version == 2 else FILES
    docs = {name: _read(path, name, schema, names) for name in names}
    if version == 2:  # the tracks stay on disk; only their index is read
        tracks = TrackStore.from_files(
            path / TRACKS_JSONL, docs["tracks.index"], schema
        )
    else:
        tracks = TrackStore.from_list(
            docs["tracks"]["tracks"], docs["patrols"]["patrols"]
        )
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
                    if k not in ("has_smart_login", "sex")
                },
                has_smart_login=bool(e.get("has_smart_login", False)),
                sex=(e.get("sex") or "").strip() or None,
            )
            for e in r["employees"]
        ],
    )
    return Bundle(
        path=path,
        manifest=manifest,
        reference=reference,
        patrols=docs["patrols"]["patrols"],
        tracks=tracks,
        language=lang,
    )
