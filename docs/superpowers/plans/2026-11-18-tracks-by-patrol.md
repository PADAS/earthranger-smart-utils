# Tracks By Patrol (library) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `er-smart-sync` reads a bundle's tracks one patrol at a time, so importing does not hold every track in memory, and still reads the bundles it reads today.

**Architecture:** `Bundle.tracks` becomes a `TrackStore` with one interface over two layouts: version 1 (`tracks.json`, parsed whole as today) and version 2 (`tracks.jsonl` plus `tracks.index.json`, read by byte range on request). The import builds its by-day lookup per patrol from the store.

**Tech Stack:** Python 3.12, `jsonschema`, pytest. Tests: `.venv/bin/python -m pytest -q` (575 pass before this change).

**Spec:** `docs/superpowers/specs/2026-11-18-tracks-by-patrol-design.md` in `PADAS/er-smart-able` (branch `feat/bundle-tracks-by-patrol`; local checkout `/Users/chrisdo/padas/er-smart-able`). Ticket GUNDI-5944. The app's half is that repository's `docs/superpowers/plans/2026-11-18-tracks-by-patrol-app.md`; this plan is released first.

## Global Constraints

- `load_bundle` accepts `bundle_schema_version` 1 or 2 and refuses anything else, naming both.
- A version 1 bundle behaves exactly as today: same tracks, same leg times, same import calls, same errors.
- Version 2 layout: `tracks.jsonl` is UTF-8, one track object per line, each line ending `\n`, a patrol's lines contiguous. `tracks.index.json` is `{"patrols": {"<patrol uuid>": {"offset": int, "length": int, "tracks": int, "points": int}}}`; entries, in offset order, start at 0, each starts where the one before ends, and the last ends at the file's size.
- `load_bundle` does not read `tracks.jsonl`. It reads its size only.
- `TrackStore.for_patrol` is safe to call from several threads at once: each call opens the file itself and shares no file position.
- `bundle/schema.json` is the vendored copy of er-smart-able's `export/bundle.schema.json`; the two files must end byte-identical. The changes are given in Task 1 and repeated in the app's plan.
- No token, host or real site name in tests, fixtures or docs. Fixtures are the existing `smart_demo_bundle`.
- Commit messages end with `Co-Authored-By: Claude Fable 5.2 <noreply@anthropic.com>`.
- The release tag is the user's to choose; do not tag.

## Review Focus

1. **A patrol with no tracks.** It has no index entry; `for_patrol` returns `[]`, `points` and `count` return 0, and the patrol imports. (Task 1, Task 2)
2. **An index that lies:** a range past the end of the file, a gap, an overlap, a zero or negative length. Refused at `load_bundle` with a message naming `tracks.index.json`. (Task 1)
3. **A line under the wrong patrol, or a broken line.** `for_patrol` raises `BundleError` naming the patrol; the import fails that patrol and carries on with the rest. (Task 1, Task 2)
4. **Several workers.** `run_import` with `workers=4` on a version 2 bundle writes the same calls as with one. (Task 2)
5. **Non-ASCII in a track line** (the file is UTF-8 and offsets are bytes, not characters). (Task 1)

---

### Task 1: The track store and version 2 in `load_bundle`

**Files:**
- Modify: `src/er_smart_sync/bundle/__init__.py`, `src/er_smart_sync/bundle/schema.json`
- Create: `tests/bundle_v2.py` (a test helper, not a test file)
- Test: `tests/test_bundle.py`

**Interfaces:**
- Produces, in `er_smart_sync.bundle`:
  - `SUPPORTED_SCHEMA_VERSIONS = (1, 2)` (replaces `SUPPORTED_SCHEMA_VERSION`)
  - `class TrackStore` with
    - `for_patrol(patrol_uuid: str) -> list[dict]`
    - `points(patrol_uuid: str) -> int`, `count(patrol_uuid: str) -> int`
    - `patrol_uuids() -> set[str]`
    - `__iter__` yielding every track, a patrol at a time in file order (version 1: list order), and `__len__` giving the number of tracks, so `for t in bundle.tracks` and `len(bundle.tracks)` keep working without holding the file
    - `TrackStore.from_list(tracks: list) -> TrackStore` (version 1)
    - `TrackStore.from_files(jsonl: Path, index: dict, schema: dict) -> TrackStore` (version 2; `index` is the parsed, schema-checked `tracks.index.json`; raises `BundleError` if the ranges do not tile the file)
  - `Bundle.tracks: TrackStore`
- Produces, in `tests/bundle_v2.py`: `to_v2(src: Path, dst: Path) -> Path`, which copies a version 1 bundle directory to `dst` as version 2 (manifest version 2; `tracks.jsonl` ordered by `patrol_uuid`, then `leg_day_uuid`, then `uuid`, written with `json.dumps(t, ensure_ascii=False, separators=(",", ":")) + "\n"`; `tracks.index.json`; no `tracks.json`). Task 2 and the app's tests of the same shape rely on it.

Schema changes, exactly:
- `$defs.manifest.properties.bundle_schema_version` becomes `{"type": "integer", "enum": [1, 2]}`.
- Add `$defs.tracks_index`:

```json
{"type": "object", "required": ["patrols"], "additionalProperties": false,
 "properties": {"patrols": {"type": "object", "additionalProperties": {
   "type": "object", "required": ["offset", "length", "tracks", "points"], "additionalProperties": false,
   "properties": {"offset": {"type": "integer", "minimum": 0}, "length": {"type": "integer", "minimum": 1},
                  "tracks": {"type": "integer", "minimum": 1}, "points": {"type": "integer", "minimum": 0}}}}}}
```

- `$defs.track` and `$defs.tracks` are unchanged. The top-level description names the version 2 files.

`for_patrol` on version 2 reads `length` bytes at `offset`, splits on `\n`, parses each line and checks it against `$defs/track`. It raises `BundleError` (message starting `tracks.jsonl: patrol <uuid>:`) for a line that is not JSON, fails the schema, or carries another `patrol_uuid`, and if the number of lines is not the index's `tracks`.

- [ ] **Step 1: Write the helper `tests/bundle_v2.py`** (`to_v2` as specified).

- [ ] **Step 2: Write the failing tests** in `tests/test_bundle.py`:

```python
def test_v2_gives_the_same_tracks_as_v1(tmp_path):
    v1, v2 = load_bundle(FIXTURE), load_bundle(to_v2(FIXTURE, tmp_path / "v2"))
    assert v2.manifest.bundle_schema_version == 2 and len(v2.tracks) == len(v1.tracks) == 11
    key = lambda t: t["uuid"]
    for p in v1.patrols:
        assert sorted(v2.tracks.for_patrol(p["uuid"]), key=key) == sorted(v1.tracks.for_patrol(p["uuid"]), key=key)
        assert v2.tracks.points(p["uuid"]) == v1.tracks.points(p["uuid"]) == sum(
            len(part["coordinates"]) for t in v1.tracks.for_patrol(p["uuid"]) for part in t["parts"])
        assert v2.tracks.count(p["uuid"]) == len(v1.tracks.for_patrol(p["uuid"]))
    assert v2.tracks.patrol_uuids() == v1.tracks.patrol_uuids() == {t["patrol_uuid"] for t in v1.tracks}
    assert sorted(v2.tracks, key=key) == sorted(v1.tracks, key=key)

def test_a_patrol_without_tracks_has_none(tmp_path):        # an unknown uuid: [], 0, 0, on both versions
def test_for_patrol_reads_only_its_own_bytes(tmp_path):      # overwrite every byte outside one patrol's range with b"\xff"; that patrol still loads; load_bundle still succeeds
def test_load_bundle_does_not_read_the_tracks(tmp_path):     # tracks.jsonl is 32 MiB of valid filler lines for other patrols; tracemalloc peak across load_bundle plus one for_patrol is under 8 MiB above the peak for the unpadded bundle
def test_offsets_are_bytes(tmp_path):                        # a track line holding "é" in an extra string field before another patrol's run: both patrols load
def test_version_3_is_refused(tmp_path):                     # BundleError naming "1" and "2"
def test_v2_without_tracks_jsonl_is_refused(tmp_path):       # BundleError matching "tracks.jsonl"
@pytest.mark.parametrize("damage", ["past_the_end", "gap", "overlap", "short_of_the_end"])
def test_an_index_that_does_not_cover_the_file_is_refused(tmp_path, damage):   # BundleError matching "tracks.index.json"
def test_a_bad_line_fails_its_patrol_only(tmp_path):         # one patrol's first line made invalid JSON, same byte length: its for_patrol raises BundleError naming the patrol; another patrol's still loads
def test_a_line_under_the_wrong_patrol_is_refused(tmp_path)
def test_for_patrol_from_many_threads(tmp_path):             # 16 threads, each patrol 50 times: each result equals the single-threaded one
```

Existing tests that use `b.tracks` as a list (`len(b.tracks) == 11`, `{t["leg_uuid"] for t in b.tracks}`) must pass unchanged.

- [ ] **Step 3: Run them and see them fail**

Run: `.venv/bin/python -m pytest -q tests/test_bundle.py`
Expected: FAIL (`TrackStore` has no `for_patrol`; version 2 refused)

- [ ] **Step 4: Implement** `TrackStore`, the version check, and the version 2 branch of `load_bundle`; update `schema.json`.

- [ ] **Step 5: Run the whole suite**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS, 575 plus the new tests

- [ ] **Step 6: Commit**

```bash
git add src/er_smart_sync/bundle tests/bundle_v2.py tests/test_bundle.py
git commit -m "bundle: version 2, tracks read a patrol at a time"
```

---

### Task 2: The import asks for tracks by patrol

**Files:**
- Modify: `src/er_smart_sync/patrol_import.py`
- Test: `tests/test_patrol_import.py`, `tests/test_smart_to_er_patrols.py`, `tests/test_patrol_times.py`

**Interfaces:**
- Consumes: `bundle.tracks.for_patrol(uuid)` from Task 1; `tracks_by_day(tracks: list) -> dict` (unchanged).
- Produces: `run_import` no longer builds one `by_day` for the bundle. `_import_one` builds `by_day = tracks_by_day(bundle.tracks.for_patrol(p["uuid"]))` inside its `try`, so a `BundleError` from a bad line is that patrol's error. `by_day` leaves `per_patrol` and `_import_one`'s parameters. `build_patrol` and `build_track_batches` keep their signatures.

- [ ] **Step 1: Write the failing tests** in `tests/test_patrol_import.py` (reuse that file's `FakeAPI` and `_run`):

```python
@pytest.mark.parametrize("workers", [1, 4])
def test_a_v2_bundle_imports_the_same_as_v1(tmp_path, workers):
    # run the import on the fixture and on to_v2(fixture) with fresh FakeAPIs and ledgers;
    # the recorded calls are equal as multisets (sorted by a stable key), and so are result.counts

def test_a_bad_track_line_fails_that_patrol_and_no_other(tmp_path):
    # damage one tracked patrol's first line in the v2 copy; run the import:
    # that patrol is in result.failed with "BundleError" in its error; every other patrol has all its stages in the ledger

def test_importing_does_not_hold_the_other_patrols_tracks(tmp_path):
    # pad the v2 copy's tracks.jsonl with 32 MiB of filler lines for patrols not in patrols.json (index extended to cover them);
    # tracemalloc peak across load_bundle and run_import is under 12 MiB above the same run without the padding
```

In `tests/test_smart_to_er_patrols.py` and `tests/test_patrol_times.py`, `tracks_by_day(b.tracks)` keeps working through `TrackStore.__iter__`; leave those calls as they are and confirm they pass.

- [ ] **Step 2: Run and see them fail**

Run: `.venv/bin/python -m pytest -q tests/test_patrol_import.py -k "v2 or bad_track or does_not_hold"`
Expected: FAIL (the bad line is not that patrol's error; the memory bound is exceeded)

- [ ] **Step 3: Implement** the change in `patrol_import.py`.

- [ ] **Step 4: Run the whole suite**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/er_smart_sync/patrol_import.py tests
git commit -m "import-patrols: tracks are read for the patrol being imported"
```

---

### Task 3: Say so

**Files:**
- Modify: `docs/changelog.md`, `USAGE.md`, `CLAUDE.md` (where they describe the bundle or `import-patrols`)

- [ ] **Step 1: Add an "Unreleased" entry to `docs/changelog.md`**, in that file's style, saying: bundles of version 2 are read (`tracks.jsonl` and `tracks.index.json`); tracks are read for one patrol at a time, so memory no longer grows with a conservation area's tracks; version 1 bundles are read as before; a bad track now fails its patrol where it used to refuse the bundle; `Bundle.tracks` is a `TrackStore` (iterable and sized as before, with `for_patrol`, `points`, `count`, `patrol_uuids`).

- [ ] **Step 2: Update any sentence in `USAGE.md` and `CLAUDE.md` that lists the bundle's files** to name both layouts. Run `grep -rn "tracks.json" --include='*.md' . | grep -v "docs/superpowers"` and fix each hit that describes the current layout.

- [ ] **Step 3: Run the suite and commit**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS

```bash
git add docs/changelog.md USAGE.md CLAUDE.md
git commit -m "Changelog and docs: bundle version 2"
```
