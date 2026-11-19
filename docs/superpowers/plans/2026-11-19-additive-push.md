# Additive Push (library) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A data model push from files can add to event types and choice lists that another conservation area also uses, without taking away what that area put there.

**Architecture:** `push_datamodel_files` gains `additive`. It sets a flag on the synchronizer that changes three things: the choices upsert keeps orphans and never deactivates, the event type update never deactivates and never moves an existing type to another category, and new event types join the category the set is already in. Everything else, and every push that does not ask for it, is unchanged.

**Tech Stack:** Python 3.12, Pydantic v1, pytest. Tests: `.venv/bin/python -m pytest -q` (597 pass before this change). Lint: `.venv/bin/ruff check src tests`.

**Spec:** `docs/superpowers/specs/2026-11-19-shared-event-type-sets-design.md` in `PADAS/er-smart-able` (local checkout `/Users/chrisdo/padas/er-smart-able`). Ticket GUNDI-5946. The app's half is that repository's `docs/superpowers/plans/2026-11-19-shared-event-type-sets-app.md`; this plan is released first.

## Global Constraints

- `additive` defaults to `False` everywhere. With it false, every existing test passes unchanged and every write is what it is today.
- Additive, choices: an option on the site that the plan lacks is left as it is; an active option is never made inactive; an inactive option the plan has active is activated; a new option is created as planned; display and order are written as today.
- Additive, event types: an active type is never made inactive; an inactive type the plan has active is activated; an existing type keeps its category; display and schema are written as today. A type the plan has inactive and the site has active is not touched at all (its schema is not compared).
- Additive, category: when at least one of the push's event type values is already on the site, new event types go to the category most of those existing types are in (ties: the lowest value), the computed category is not created, and `ca_identifier` is used only for the display of a category that has to be made. When none is on the site, the category is computed and created as today.
- `datamodel_stats["event_category"]` is the category value the push used, in both modes.
- Event types the site has that the plan lacks are left alone, as today.
- No token, host or real site name in tests, fixtures or docs.
- Commit messages end with `Co-Authored-By: Claude Fable 5.2 <noreply@anthropic.com>`.
- The release tag is the user's to choose; do not tag.

## Review Focus

1. **A second area whose list lacks an option of the first.** The option stays active; the second's own options are added. (Task 1)
2. **One area has a category retired, another has it active.** After either order of pushes the event type is active and has the active area's schema. (Task 2)
3. **A set whose types sit in two categories** (an administrator moved some). Existing types stay where they are; a new type joins the larger group. (Task 2)
4. **A dry run.** `DryRunERClient` records the same writes an additive run would make, and `event_category` is in the stats. (Task 2)
5. **`update-only` and `create-only` modes with `additive`.** The mode still decides whether a write happens; additive only narrows what is written. (Task 2)

---

### Task 1: Choices are only added to

**Files:**
- Modify: `src/er_smart_sync/choices.py`
- Test: `tests/test_choices.py`

**Interfaces:**
- Produces: `upsert_choices(*, er_client, choice_sets, deactivate_orphans: bool = True, keep_active: bool = False) -> ChoicesStats`. With `keep_active` true, `_maybe_patch_choice` drops a change of `is_active` from true to false (other changes in the same patch still go; if nothing is left the option counts as unchanged). `deactivate_orphans` is as it is today.

- [ ] **Step 1: Write the failing tests** in `tests/test_choices.py`, using that file's existing fake client:

```python
def test_keep_active_does_not_deactivate_an_option_the_plan_has_inactive():
    # site: option "lion" active; plan: "lion" inactive, same display and order
    # upsert_choices(..., keep_active=True): no PATCH; stats.unchanged == 1, stats.deactivated == 0

def test_keep_active_still_writes_the_display_of_an_option_it_will_not_deactivate():
    # site: "lion" active, display "Lion"; plan: inactive, display "African lion"
    # one PATCH with {"display": "African lion"} and no "is_active" key

def test_keep_active_activates_an_inactive_option_the_plan_has_active():
    # one PATCH carrying {"is_active": True}

def test_orphans_are_kept_and_active_options_stay_with_both_flags():
    # site: "lion", "tiger" active; plan: "lion" only
    # upsert_choices(..., deactivate_orphans=False, keep_active=True): no PATCH to "tiger"; stats.kept == 1

def test_without_keep_active_an_inactive_plan_still_deactivates():
    # the behaviour of today, pinned
```

- [ ] **Step 2: Run and see them fail**

Run: `.venv/bin/python -m pytest -q tests/test_choices.py -k "keep_active or both_flags"`
Expected: FAIL (`upsert_choices` has no `keep_active`)

- [ ] **Step 3: Implement** `keep_active` in `upsert_choices`, `_upsert_one_set` and `_maybe_patch_choice`.

- [ ] **Step 4: Run the whole suite**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/er_smart_sync/choices.py tests/test_choices.py
git commit -m "choices: an upsert that never deactivates"
```

---

### Task 2: The additive push

**Files:**
- Modify: `src/er_smart_sync/synchronizer.py` (`push_smart_ca_datamodel_to_earthranger`, `create_or_update_er_event_types`), `src/er_smart_sync/datamodel_push.py`, `src/er_smart_sync/cli.py` (`datamodel`)
- Create: `tests/fake_site.py` (a test helper, not a test file)
- Test: `tests/test_datamodel_push.py`, `tests/test_cli.py`

**Interfaces:**
- Consumes: `upsert_choices(..., deactivate_orphans, keep_active)` from Task 1.
- Produces:
  - `ERSmartSynchronizer.additive: bool = False`, an attribute set like `sync_mode`.
  - `push_datamodel_files(er_client, *, ..., additive: bool = False) -> dict`; the returned stats carry `"event_category": str`.
  - `er-smart-sync datamodel --from-file ... --additive`; refused without `--from-file` with the message `--additive requires --from-file`.
  - `tests/fake_site.py`: `class FakeSite`, a stand-in for the ER client that keeps event categories, event types (version 2) and choices in memory and answers the reads and writes the push makes; `site.event_types() -> dict[value, dict]`, `site.choices(field) -> dict[value, dict]`, `site.categories() -> set[str]`. Find the exact client methods by running the existing push test against it. The app's plan builds its own copy; keep it small.

- [ ] **Step 1: Write the failing tests** in `tests/test_datamodel_push.py`. Two data models, written as XML strings beside the existing `DATAMODEL_XML`: model A as it is, model B the same with the option `lion` replaced by `tiger`, a second category `incidents.snaring`, and `incidents.poaching` marked `isactive="false"`.

```python
def push(site, tmp_path, xml, ca_identifier, **kw):
    # writes xml to a file and calls push_datamodel_files(site, endpoint="https://example.pamdas.org/api/v1.0",
    # datamodel_file=..., ca_identifier=ca_identifier, **kw); returns the stats

def test_an_additive_push_of_a_second_model_takes_nothing_away(tmp_path):
    site = FakeSite()
    push(site, tmp_path, DATAMODEL_XML, "smart-a", additive=True)
    first = site.event_types()["smart-ca-import_incidents_poaching"]
    stats = push(site, tmp_path, MODEL_B_XML, "smart-b", additive=True)
    field = next(f for f in site.choice_fields() if f.endswith("species"))
    assert {v for v, c in site.choices(field).items() if c["is_active"]} == {"lion", "tiger"}
    poaching = site.event_types()["smart-ca-import_incidents_poaching"]
    assert poaching["is_active"] and poaching["category"] == first["category"]
    assert poaching["schema"] == first["schema"]            # B has it inactive: not touched
    assert site.event_types()["smart-ca-import_incidents_snaring"]["category"] == first["category"]
    assert site.categories() == {first["category"]} and stats["event_category"] == first["category"]

def test_the_same_two_pushes_without_additive_behave_as_before(tmp_path):
    # after B: "lion" inactive, poaching inactive, poaching's category is B's, both categories exist

def test_an_additive_push_activates_what_the_site_has_inactive(tmp_path):
    # push B then A, both additive: poaching ends active with A's schema; "lion" and "tiger" both active

def test_a_new_type_joins_the_larger_group_of_a_set_in_two_categories(tmp_path):
    # push a three-category model additively, move one type to category "other" on the fake, push a model
    # with a fourth category additively: the moved type is still in "other", the new one is in the first category

def test_an_additive_dry_run_plans_the_same_writes(tmp_path):
    # DryRunERClient(site) for the second push: site unchanged; the recorded calls are the writes the real
    # additive push makes on a copy of the site; stats["event_category"] is the first push's category

@pytest.mark.parametrize("mode", ["update-only", "create-only"])
def test_additive_respects_the_mode(tmp_path, mode):
    # update-only: no new event type is created; create-only: no existing type is patched
```

In `tests/test_cli.py`: `--additive` without `--from-file` exits non-zero with `--additive requires --from-file`; with it, `push_datamodel_files` (or the synchronizer) receives `additive=True`.

- [ ] **Step 2: Run and see them fail**

Run: `.venv/bin/python -m pytest -q tests/test_datamodel_push.py`
Expected: FAIL (`push_datamodel_files` has no `additive`)

- [ ] **Step 3: Implement.** In `push_smart_ca_datamodel_to_earthranger`: pass `deactivate_orphans=not self.additive, keep_active=self.additive` to `upsert_choices`; when additive, find the existing set's category before the category is looked up or created, and use it. In `create_or_update_er_event_types`: when additive and the type exists, give the planned type the existing category; skip it as unchanged when it is planned inactive and exists active. Set `datamodel_stats["event_category"]`. Check every consumer of `datamodel_stats` for code that assumes integer values (`grep -n datamodel_stats src`) and fix any that sums or formats them.

- [ ] **Step 4: Run the whole suite and lint**

Run: `.venv/bin/python -m pytest -q && .venv/bin/ruff check src tests`
Expected: PASS, no findings

- [ ] **Step 5: Commit**

```bash
git add src/er_smart_sync tests/fake_site.py tests/test_datamodel_push.py tests/test_cli.py
git commit -m "datamodel: an additive push for sets several conservation areas share"
```

---

### Task 3: Say so

**Files:**
- Modify: `docs/changelog.md`, `docs/cli-reference/datamodel.md`, `USAGE.md`

- [ ] **Step 1: Add to the "Unreleased" section of `docs/changelog.md`**, in that file's style: `push_datamodel_files(additive=True)` and `datamodel --from-file --additive`; what additive changes (the three rules of Global Constraints, in a sentence each); that the stats now carry `event_category`; that the default is unchanged.

- [ ] **Step 2: Document `--additive`** in `docs/cli-reference/datamodel.md` and in `USAGE.md` under "Pushing a SMART data model into EarthRanger", with the case it is for: several conservation areas with one data model on one site.

- [ ] **Step 3: Run the suite and commit**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS

```bash
git add docs/changelog.md docs/cli-reference/datamodel.md USAGE.md
git commit -m "Changelog and docs: the additive push"
```
