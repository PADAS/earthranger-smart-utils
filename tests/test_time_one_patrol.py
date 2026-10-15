import importlib.util
import json
from pathlib import Path

SCRIPT = Path(__file__).parent.parent / "scripts" / "time_one_patrol.py"
spec = importlib.util.spec_from_file_location("time_one_patrol", SCRIPT)
time_one_patrol = importlib.util.module_from_spec(spec)
spec.loader.exec_module(time_one_patrol)


def test_pending_retirements_counts_reactivated_rangers_across_sites(tmp_path):
    led = tmp_path / "ledger.json"
    led.write_text(
        json.dumps(
            {
                "a|ca1": {"patrols": {}, "reactivated": ["s1", "s2"]},
                "b|ca2": {"patrols": {}, "reactivated": ["s3"]},
            }
        )
    )
    assert time_one_patrol.pending_retirements(str(led)) == 3


def test_pending_retirements_is_zero_when_all_were_retired_or_no_ledger(tmp_path):
    led = tmp_path / "ledger.json"
    led.write_text(json.dumps({"a|ca": {"patrols": {}, "reactivated": []}}))
    assert time_one_patrol.pending_retirements(str(led)) == 0
    assert time_one_patrol.pending_retirements(str(tmp_path / "missing.json")) == 0


def test_an_unreadable_ledger_counts_as_pending(tmp_path):
    led = tmp_path / "ledger.json"
    led.write_text("{not json")
    assert time_one_patrol.pending_retirements(str(led)) == 1
