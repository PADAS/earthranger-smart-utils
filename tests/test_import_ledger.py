import json

from er_smart_sync.import_ledger import ImportLedger


def test_ledger_records_stages_and_ids(tmp_path):
    p = tmp_path / "l.json"
    led = ImportLedger(p, "site|ca")
    led.done("p1", "patrol", patrol_id="x")
    led.add_id("p1", "attached", "w1")
    again = ImportLedger(p, "site|ca")
    assert again.stages("p1") == {"patrol"}
    assert again.ids("p1") == {"patrol_id": "x", "attached": ["w1"]}
    assert ImportLedger(p, "other|ca").stages("p1") == set()


def test_reactivated_round_trip(tmp_path):
    p = tmp_path / "l.json"
    led = ImportLedger(p, "s")
    led.set_reactivated(["a", "b"])
    assert ImportLedger(p, "s").reactivated() == ["a", "b"]


def test_read_only_never_writes(tmp_path):
    p = tmp_path / "l.json"
    led = ImportLedger(p, "s", read_only=True)
    led.done("p1", "patrol")
    assert not p.exists() and led.stages("p1") == {"patrol"}


def test_existing_file_other_sites_survive(tmp_path):
    p = tmp_path / "l.json"
    p.write_text(json.dumps({"keep": {"patrols": {}, "reactivated": []}}))
    ImportLedger(p, "s").done("p1", "patrol")
    assert "keep" in json.loads(p.read_text())


def test_ledger_survives_concurrent_writers(tmp_path):
    import threading

    led = ImportLedger(tmp_path / "l.json", "site|ca")

    def work(i):
        for s in ("patrol", "events", "tracks"):
            led.done(f"p{i}", s, patrol_id=f"id{i}")
            led.add_id(f"p{i}", "attached", f"w{i}")

    threads = [threading.Thread(target=work, args=(i,)) for i in range(16)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    again = ImportLedger(tmp_path / "l.json", "site|ca")
    for i in range(16):
        assert again.stages(f"p{i}") == {"patrol", "events", "tracks"}
        assert again.ids(f"p{i}") == {"patrol_id": f"id{i}", "attached": [f"w{i}"]}
