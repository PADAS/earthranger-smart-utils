from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from er_smart_sync.bundle import Bundle, BundleError, load_bundle

from .bundle_v2 import line, to_v2, write_tracks

FIXTURE = Path(__file__).parent / "fixtures" / "smart_demo_bundle"


def test_loads_demo_bundle():
    b = load_bundle(FIXTURE)
    assert isinstance(b, Bundle)
    assert b.manifest.bundle_schema_version == 1
    assert b.manifest.conservation_area["id"] == "SMART"
    assert b.ca_uuid == b.manifest.conservation_area["uuid"]
    assert b.language == "en" and b.manifest.default_language == "en"
    assert [t.key for t in b.reference.transports] == ["foot", "vehicle"]
    assert b.reference.transports[0].extra["patrol_type"] == "GROUND"
    assert (
        len(b.reference.employees) == 7
        and b.reference.employees[0].uuid.count("-") == 4
    )
    assert len(b.patrols) == 5 and len(b.tracks) == 11
    assert b.member_uuids() <= {e.uuid for e in b.reference.employees}
    assert len(b.member_uuids()) == 6  # one demo employee never went on patrol
    # the demo legs use both transports
    assert b.leg_transport_uuids() == {t.uuid for t in b.reference.transports}
    # the demo patrols name a team that SMART's team table does not hold
    assert len(b.patrol_team_uuids()) == 1 and b.reference.teams == []


def test_label_fallback_order(tmp_path):
    shutil.copytree(FIXTURE, tmp_path / "b")
    ref = json.loads((tmp_path / "b" / "reference.json").read_text())
    ref["transports"][0]["labels"] = {"fr": "À pied"}  # no en, no default
    ref["transports"][0]["label"] = None
    ref["transports"][1]["labels"] = {"en": "Vehicle", "fr": "Véhicule"}
    (tmp_path / "b" / "reference.json").write_text(json.dumps(ref))
    m = json.loads((tmp_path / "b" / "manifest.json").read_text())
    m["languages"].append({"code": "fr", "default": False, "labels": 2, "missing": 468})
    (tmp_path / "b" / "manifest.json").write_text(json.dumps(m))
    b = load_bundle(tmp_path / "b", language="fr")
    assert b.label(b.reference.transports[1]) == "Véhicule"
    b_en = load_bundle(tmp_path / "b", language="en")
    assert b_en.label(b_en.reference.transports[1]) == "Vehicle"
    assert (
        b_en.label(b_en.reference.transports[0]) == "À pied"
    )  # only language present wins over nothing
    assert (
        b_en.label(b_en.reference.mandates[0])
        == b_en.reference.mandates[0].labels["en"]
    )


def test_unknown_language_lists_available():
    with pytest.raises(BundleError) as e:
        load_bundle(FIXTURE, language="xx")
    assert "xx" in str(e.value) and "en" in str(e.value) and "missing" in str(e.value)


def test_schema_violation_and_version(tmp_path):
    shutil.copytree(FIXTURE, tmp_path / "b")
    m = json.loads((tmp_path / "b" / "manifest.json").read_text())
    m["bundle_schema_version"] = 3
    (tmp_path / "b" / "manifest.json").write_text(json.dumps(m))
    with pytest.raises(BundleError, match="bundle_schema_version 3.*versions 1 and 2"):
        load_bundle(tmp_path / "b")
    m["bundle_schema_version"] = 1
    tz = m.pop("timezone")
    (tmp_path / "b" / "manifest.json").write_text(json.dumps(m))
    with pytest.raises(BundleError, match="manifest.json"):
        load_bundle(tmp_path / "b")
    m["timezone"] = tz
    (tmp_path / "b" / "manifest.json").write_text(json.dumps(m))
    (tmp_path / "b" / "tracks.json").unlink()
    with pytest.raises(BundleError, match="tracks.json"):
        load_bundle(tmp_path / "b")


@pytest.mark.parametrize("doc", ["[]", "null", "3"])
def test_manifest_that_is_not_an_object_is_a_bundle_error(tmp_path, doc):
    shutil.copytree(FIXTURE, tmp_path / "b")
    (tmp_path / "b" / "manifest.json").write_text(doc)
    with pytest.raises(BundleError, match="manifest.json"):
        load_bundle(tmp_path / "b")


def test_observed_category_hkeys():
    b = load_bundle(FIXTURE)
    hkeys = b.observed_category_hkeys()
    assert "animals.sign." in hkeys and "humanactivity.rocksminerals." in hkeys
    assert all(h.endswith(".") for h in hkeys)


# ---- version 2: tracks.jsonl and tracks.index.json, read a patrol at a time
def _key(t):
    return t["uuid"]


def _index(d: Path) -> dict:
    return json.loads((d / "tracks.index.json").read_text())["patrols"]


def _set_index(d: Path, index: dict):
    (d / "tracks.index.json").write_text(json.dumps({"patrols": index}))


def _filler(n: int, points: int = 400) -> list:
    """Valid tracks of patrols that patrols.json does not hold."""
    coords = [[16.26 + i / 1e6, 2.92 + i / 1e6] for i in range(points)]
    return [
        {"uuid": f"00000000-0000-4000-8000-{i:012x}", "leg_day_uuid": f"00000000-0000-4000-8001-{i:012x}",
         "leg_uuid": f"00000000-0000-4000-8002-{i:012x}", "patrol_uuid": f"ffffffff-0000-4000-8003-{i:012x}",
         "distance_km": 1.0, "start": None, "end": None, "parts": [{"coordinates": coords}]}
        for i in range(n)
    ]


def pad(d: Path, megabytes: int) -> int:
    """Grow a version 2 bundle's tracks.jsonl by about that much with other patrols' tracks. Returns its size."""
    tracks = list(load_bundle(d).tracks)
    per = len(line(_filler(1)[0]))
    write_tracks(tracks + _filler(megabytes * 1048576 // per + 1), d)
    return (d / "tracks.jsonl").stat().st_size


def test_v2_gives_the_same_tracks_as_v1(tmp_path):
    v1, v2 = load_bundle(FIXTURE), load_bundle(to_v2(FIXTURE, tmp_path / "v2"))
    assert v2.manifest.bundle_schema_version == 2 and len(v2.tracks) == len(v1.tracks) == 11
    assert not (tmp_path / "v2" / "tracks.json").exists()
    for p in v1.patrols:
        u = p["uuid"]
        assert sorted(v2.tracks.for_patrol(u), key=_key) == sorted(v1.tracks.for_patrol(u), key=_key)
        points = sum(len(part["coordinates"]) for t in v1.tracks.for_patrol(u) for part in t["parts"])
        assert v2.tracks.points(u) == v1.tracks.points(u) == points
        assert v2.tracks.count(u) == v1.tracks.count(u) == len(v1.tracks.for_patrol(u))
    assert v2.tracks.patrol_uuids() == v1.tracks.patrol_uuids() == {t["patrol_uuid"] for t in v1.tracks}
    assert sorted(v2.tracks, key=_key) == sorted(v1.tracks, key=_key)
    assert sum(v1.tracks.points(u) for u in v1.tracks.patrol_uuids()) > 0


@pytest.mark.parametrize("version", [1, 2])
def test_a_patrol_without_tracks_has_none(tmp_path, version):
    b = load_bundle(FIXTURE if version == 1 else to_v2(FIXTURE, tmp_path / "v2"))
    nobody = "12345678-1234-4234-8234-123456789abc"
    assert b.tracks.for_patrol(nobody) == [] and b.tracks.points(nobody) == 0 and b.tracks.count(nobody) == 0


def test_for_patrol_reads_only_its_own_bytes(tmp_path):
    d = to_v2(FIXTURE, tmp_path / "v2")
    want = {u: load_bundle(d).tracks.for_patrol(u) for u in _index(d)}
    raw = (d / "tracks.jsonl").read_bytes()
    for u, e in _index(d).items():
        spoiled = b"\xff" * e["offset"] + raw[e["offset"]:e["offset"] + e["length"]] + b"\xff" * (len(raw) - e["offset"] - e["length"])
        (d / "tracks.jsonl").write_bytes(spoiled)
        assert load_bundle(d).tracks.for_patrol(u) == want[u]   # and loading reads none of it


def test_load_bundle_does_not_read_the_tracks(tmp_path):
    import tracemalloc

    def peak(d):
        tracemalloc.start()
        b = load_bundle(d)
        got = b.tracks.for_patrol(b.patrols[0]["uuid"])
        _, p = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        return p, got

    d = to_v2(FIXTURE, tmp_path / "v2")
    small, got = peak(d)
    size = pad(d, 32)
    big, again = peak(d)
    assert size > 32 * 1048576 and again == got
    # the index of the filler patrols is all that grew: far less than the file
    assert big - small < 8 * 1048576, f"{(big - small) / 1048576:.1f} MiB more for a {size / 1048576:.0f} MiB tracks.jsonl"


def test_offsets_are_bytes(tmp_path):
    d = to_v2(FIXTURE, tmp_path / "v2")
    tracks = list(load_bundle(d).tracks)
    first = min(t["patrol_uuid"] for t in tracks)
    for t in tracks:
        if t["patrol_uuid"] == first:
            t["note"] = "é—ñ"   # two and three bytes each; the schema allows an extra field
    write_tracks(tracks, d)
    b = load_bundle(d)
    assert sorted(b.tracks, key=_key) == sorted(tracks, key=_key)
    assert all(t["note"] == "é—ñ" for t in b.tracks.for_patrol(first))


def test_v2_without_tracks_jsonl_is_refused(tmp_path):
    d = to_v2(FIXTURE, tmp_path / "v2")
    (d / "tracks.jsonl").unlink()
    with pytest.raises(BundleError, match="tracks.jsonl"):
        load_bundle(d)
    d2 = to_v2(FIXTURE, tmp_path / "v2b")
    (d2 / "tracks.index.json").unlink()
    with pytest.raises(BundleError, match="tracks.index.json: missing"):
        load_bundle(d2)


def test_a_v2_bundle_with_no_tracks_loads(tmp_path):
    d = to_v2(FIXTURE, tmp_path / "v2")
    write_tracks([], d)
    b = load_bundle(d)
    assert (d / "tracks.jsonl").stat().st_size == 0 and len(b.tracks) == 0 and list(b.tracks) == []


@pytest.mark.parametrize("damage", ["past_the_end", "gap", "overlap", "short_of_the_end", "no_length"])
def test_an_index_that_does_not_cover_the_file_is_refused(tmp_path, damage):
    d = to_v2(FIXTURE, tmp_path / "v2")
    index = _index(d)
    by_offset = sorted(index.values(), key=lambda e: e["offset"])
    first, last = by_offset[0], by_offset[-1]
    if damage == "past_the_end":
        last["length"] += 10
    elif damage == "gap":
        by_offset[1]["offset"] += 1
    elif damage == "overlap":
        by_offset[1]["offset"] -= 1
    elif damage == "short_of_the_end":
        last["length"] -= 1
    else:
        first["length"] = 0
    _set_index(d, index)
    with pytest.raises(BundleError, match="tracks.index.json"):
        load_bundle(d)


def test_a_bad_line_fails_its_patrol_only(tmp_path):
    d = to_v2(FIXTURE, tmp_path / "v2")
    index = _index(d)
    bad, good = sorted(index, key=lambda u: index[u]["offset"])[:2]
    raw = bytearray((d / "tracks.jsonl").read_bytes())
    raw[index[bad]["offset"]] = ord("[")   # same length, no longer an object
    (d / "tracks.jsonl").write_bytes(bytes(raw))
    b = load_bundle(d)
    with pytest.raises(BundleError, match=f"tracks.jsonl: patrol {bad}: line 1"):
        b.tracks.for_patrol(bad)
    assert len(b.tracks.for_patrol(good)) == index[good]["tracks"]


def test_a_line_under_the_wrong_patrol_or_off_schema_is_refused(tmp_path):
    d = to_v2(FIXTURE, tmp_path / "v2")
    index = _index(d)
    a, b_ = sorted(index, key=lambda u: index[u]["offset"])[:2]
    index[a], index[b_] = index[b_], index[a]   # each now points at the other's lines
    _set_index(d, index)
    with pytest.raises(BundleError, match=f"patrol {a}: line 1: belongs to patrol {b_}"):
        load_bundle(d).tracks.for_patrol(a)
    d2 = to_v2(FIXTURE, tmp_path / "v2b")
    tracks = list(load_bundle(d2).tracks)
    tracks[0]["distance_km"] = "far"
    write_tracks(tracks, d2)
    with pytest.raises(BundleError, match="line 1: distance_km: 'far'"):
        load_bundle(d2).tracks.for_patrol(tracks[0]["patrol_uuid"])
    d3 = to_v2(FIXTURE, tmp_path / "v2c")
    index = _index(d3)
    index[a]["tracks"] += 1
    _set_index(d3, index)
    with pytest.raises(BundleError, match="where the index says"):
        load_bundle(d3).tracks.for_patrol(a)


def test_for_patrol_from_many_threads(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    b = load_bundle(to_v2(FIXTURE, tmp_path / "v2"))
    uuids = sorted(b.tracks.patrol_uuids())
    want = {u: b.tracks.for_patrol(u) for u in uuids}
    with ThreadPoolExecutor(16) as pool:
        got = list(pool.map(lambda u: (u, b.tracks.for_patrol(u)), uuids * 50))
    assert all(tracks == want[u] for u, tracks in got)
