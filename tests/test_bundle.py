from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from er_smart_sync.bundle import Bundle, BundleError, load_bundle

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
    m["bundle_schema_version"] = 2
    (tmp_path / "b" / "manifest.json").write_text(json.dumps(m))
    with pytest.raises(BundleError, match="bundle_schema_version 2"):
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
