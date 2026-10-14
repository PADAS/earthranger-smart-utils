from __future__ import annotations

import json
import shutil
from pathlib import Path
from unittest.mock import MagicMock

from er_smart_sync.bundle import load_bundle
from er_smart_sync.preflight import (
    BLOCKING,
    MAX_EXAMPLES,
    WARNING,
    Finding,
    check_bundle_warnings,
    check_language,
    check_leg_span,
    check_time_coverage,
    check_timezone,
    run_preflight,
)

FIXTURE = Path(__file__).parent / "fixtures" / "smart_demo_bundle"


def _edited(tmp_path, edit):
    shutil.copytree(FIXTURE, tmp_path / "b")
    for name in ("manifest", "reference", "patrols"):
        f = tmp_path / "b" / f"{name}.json"
        doc = json.loads(f.read_text())
        edit(name, doc)
        f.write_text(json.dumps(doc))
    return load_bundle(tmp_path / "b")


def _codes(findings):
    return [(f.code, f.level) for f in findings]


def test_finding_of_caps_examples():
    f = Finding.of(
        "x", BLOCKING, "m", "fix", [str(i) for i in range(25)], affected=["p1", "p2"]
    )
    assert (
        f.count == 25
        and len(f.examples) == MAX_EXAMPLES
        and f.affected_patrols == ["p1", "p2"]
    )


def test_bundle_only_checks_on_the_demo(tmp_path):
    b = load_bundle(FIXTURE)
    report = run_preflight(
        MagicMock(),
        b,
        checks=[
            check_leg_span,
            check_time_coverage,
            check_timezone,
            check_language,
            check_bundle_warnings,
        ],
    )
    codes = _codes(report.findings)
    assert (
        "timezone",
        WARNING,
    ) in codes  # the demo's zone was inferred and none was asserted
    assert ("leg_span", WARNING) not in codes and (
        "time_coverage",
        WARNING,
    ) not in codes
    assert report.exit_code == 0


def test_timezone_assertion(tmp_path):
    b = load_bundle(FIXTURE)
    name = b.manifest.timezone["name"]
    assert check_timezone_codes(b, name) == []
    assert check_timezone_codes(b, "Europe/Paris") == [("timezone", BLOCKING)]


def check_timezone_codes(b, tz):
    return _codes(
        run_preflight(MagicMock(), b, timezone=tz, checks=[check_timezone]).findings
    )


def test_leg_span_time_coverage_language_and_warnings(tmp_path):
    def edit(name, doc):
        if name == "patrols":
            doc["patrols"][0]["legs"][0]["placeholder_dates"] = True
        if name == "manifest":
            doc["time_coverage"]["date_only"] = 3
            doc["languages"] = [
                {"code": "en", "default": True, "labels": 400, "missing": 70}
            ]
            doc["warnings"] = [
                f"attachment {i} is not in the extracted filestore" for i in range(30)
            ]

    b = _edited(tmp_path, edit)
    report = run_preflight(
        MagicMock(),
        b,
        checks=[
            check_leg_span,
            check_time_coverage,
            check_language,
            check_bundle_warnings,
        ],
    )
    by = {f.code: f for f in report.findings}
    assert by["leg_span"].level == WARNING and by["leg_span"].count == 1
    assert by["leg_span"].affected_patrols == [b.patrols[0]["uuid"]]
    assert by["time_coverage"].count == 3 and "whole day" in by["time_coverage"].message
    assert by["language"].count == 70 and "en" in by["language"].message
    assert (
        by["bundle_warnings"].count == 30
        and len(by["bundle_warnings"].examples) == MAX_EXAMPLES
    )


def test_a_failing_check_does_not_hide_the_others():
    b = load_bundle(FIXTURE)

    def boom(ctx):
        raise RuntimeError("ER said 500")

    report = run_preflight(MagicMock(), b, checks=[boom, check_timezone])
    codes = _codes(report.findings)
    assert ("check_failed", BLOCKING) in codes and ("timezone", WARNING) in codes
    failed = next(f for f in report.findings if f.code == "check_failed")
    assert "boom" in failed.message and "ER said 500" in failed.message
    assert report.exit_code == 2


def test_examples_are_capped_but_affected_is_complete(tmp_path):
    def edit(name, doc):
        if name == "patrols":
            for p in doc["patrols"]:
                for leg in p["legs"]:
                    leg["placeholder_dates"] = True

    b = _edited(tmp_path, edit)
    report = run_preflight(MagicMock(), b, checks=[check_leg_span])
    (f,) = report.findings
    assert f.count == sum(len(p["legs"]) for p in b.patrols)
    assert sorted(f.affected_patrols) == sorted(p["uuid"] for p in b.patrols)
    assert report.blocked_patrols == set()  # a warning never blocks a patrol
