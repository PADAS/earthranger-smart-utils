"""Test helper: write a version 1 bundle directory as version 2, the way
`smart-able export` writes it (tracks.jsonl, one track per line with a
patrol's lines together, and tracks.index.json with each patrol's byte range)."""

import json
import shutil
from pathlib import Path


def line(track: dict) -> bytes:
    return (json.dumps(track, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")


def write_tracks(tracks: list, dst: Path) -> dict:
    index: dict = {}
    at = 0
    with open(dst / "tracks.jsonl", "wb") as f:
        # a stable sort: the tracks of one leg day keep the order they came in
        for t in sorted(tracks, key=lambda t: (t["patrol_uuid"], t["leg_day_uuid"])):
            raw = line(t)
            f.write(raw)
            e = index.setdefault(t["patrol_uuid"], {"offset": at, "length": 0, "tracks": 0, "points": 0})
            e["length"] += len(raw)
            e["tracks"] += 1
            e["points"] += sum(len(p["coordinates"]) for p in t["parts"])
            at += len(raw)
    (dst / "tracks.index.json").write_text(json.dumps({"patrols": index}, indent=1) + "\n", encoding="utf-8")
    return index


def to_v2(src: Path, dst: Path) -> Path:
    shutil.copytree(src, dst)
    tracks = json.loads((dst / "tracks.json").read_text(encoding="utf-8"))["tracks"]
    (dst / "tracks.json").unlink()
    manifest = json.loads((dst / "manifest.json").read_text(encoding="utf-8"))
    manifest["bundle_schema_version"] = 2
    (dst / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    write_tracks(tracks, dst)
    return dst
