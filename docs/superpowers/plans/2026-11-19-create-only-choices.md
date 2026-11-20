# Create-only Choices and Event Type Differences Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A `create-only` push writes nothing to what the site already has, dropdown options included, and every push reports which of the site's event types differ from the model and how.

**Architecture:** `sync_mode == "create-only"` already skips updates to existing event types. The choices upsert gains the same rule. Where the synchronizer finds that an existing version 2 event type needs an update, it records what differs before it decides to patch or skip; the list travels in the stats.

**Tech Stack:** Python 3.12, pytest. Suite: `.venv/bin/python -m pytest -q` (618 passed before this change).

**Spec:** `docs/superpowers/specs/2026-11-19-keep-site-event-types-design.md` in `PADAS/er-smart-able` (local checkout `/Users/chrisdo/padas/er-smart-able`). Ticket GUNDI-5949.

## Global Constraints

- Default behaviour (`sync_mode == "both"`) is unchanged, apart from the new stats keys.
- `create_only` on the choices upsert: an existing option gets no write of any field; no orphan is deactivated; a missing option is created.
- Differences are recorded for version 2 event types only, in every mode, and are the same list whether the push patches or skips.
- A difference: `{"value": str, "display": str (the site's), "name": bool, "active": bool, "site_only": [str], "smart_only": [str], "changed": [str], "other": bool}`. Field keys are the keys of the schema's `json.properties`, each list sorted.
- No new argument on `push_datamodel_files`; `mode="create-only"` selects all of this.
- Untracked files in this checkout (`Botswana_Guardians.xml`, `datamodel.xml`, `t1.sh`, `sync*.yaml`) stay out of commits. No token, customer host or real ranger in tests or docs.
- Do not tag or choose the release version; the user does.
- Commit messages end with `Co-Authored-By: Claude Fable 5.2 <noreply@anthropic.com>`.

## Review Focus

1. **`create-only` with `additive`** (what smart-able sends). An option inactive on the site and active in the model stays inactive; an inactive event type stays inactive. (Task 1)
2. **A site schema that came back as a JSON string**, or that has no `json.properties`. The difference is recorded with empty field lists and `other` true, not an exception. (Task 2)
3. **An event type skipped by the additive rule** (active on the site, retired in the model). No difference entry: the push does not consider writing it. (Task 2)
4. **The CLI's summary.** It prints counters; the differences list must not be dumped into it. (Task 2)
5. **A dry-run client** (smart-able's plan). The differences come from reads and comparison only, so a plan gets them. (Task 2)

---

### Task 1: The choices upsert honours create-only

**Files:**
- Modify: `src/er_smart_sync/choices.py` (`ChoicesStats`, `upsert_choices`, `_upsert_one_set`), `src/er_smart_sync/synchronizer.py` (the `upsert_choices` call near line 444, the stats' initial dict near line 275)
- Test: `tests/test_choices.py`, `tests/test_datamodel_push.py`

**Interfaces:**
- Produces: `upsert_choices(*, er_client, choice_sets, deactivate_orphans=True, keep_active=False, create_only=False) -> ChoicesStats`; `ChoicesStats.skipped_by_mode: int`; `datamodel_stats["choices_skipped_by_mode"]: int`. The synchronizer passes `create_only=self.sync_mode == "create-only"`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_choices.py, in the style of the keep_active tests there
def test_create_only_writes_nothing_to_an_existing_option(...):
    # site: field f has "a" (display "Site A", ordernum 5, inactive) and orphan "z" (active)
    # plan: "a" (display "A", active), "b"
    # upsert_choices(..., create_only=True)
    #  - no _patch call at all; "a" is as it was; "z" is still active
    #  - "b" was created
    #  - stats.created == 1, stats.skipped_by_mode == 1, stats.updated == 0, stats.deactivated == 0

def test_create_only_counts_an_equal_option_as_unchanged(...):
    # an existing option equal to the plan: unchanged == 1, skipped_by_mode == 0

# tests/test_datamodel_push.py, with FakeSite and the push() helper
def test_a_create_only_push_leaves_the_sites_options(tmp_path):
    # push once; on the site change one option's display and deactivate another;
    # push(..., mode="create-only", additive=True): both are as the site had them,
    # stats["choices_skipped_by_mode"] == 2, stats["choices_updated"] == 0
```

- [ ] **Step 2: Run and see them fail**

Run: `.venv/bin/python -m pytest -q tests/test_choices.py tests/test_datamodel_push.py -k create_only`
Expected: FAIL (`upsert_choices() got an unexpected keyword argument 'create_only'`)

- [ ] **Step 3: Implement.** In `_upsert_one_set`, with `create_only`: for an existing option compare as `_maybe_patch_choice` does and count `unchanged` or `skipped_by_mode` without a write; skip the orphan loop.

- [ ] **Step 4: Run the suite**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/er_smart_sync/choices.py src/er_smart_sync/synchronizer.py tests/test_choices.py tests/test_datamodel_push.py
git commit -m "Choices: create-only leaves the options the site has"
```

---

### Task 2: Report how the site's event types differ

**Files:**
- Modify: `src/er_smart_sync/synchronizer.py` (a module function beside `_summarize_schema_diff`; the event type loop near line 1300; the stats' initial dict), `src/er_smart_sync/cli.py` (only if its summary prints every stats key), `docs/changelog.md`, `docs/cli-reference/datamodel.md`, `USAGE.md` (where `--mode` is described)
- Test: `tests/test_datamodel_push.py`, `tests/test_cli.py` (only if the summary changes)

**Interfaces:**
- Produces: `event_type_difference(event_type, existing: dict) -> dict` in `synchronizer.py`, returning the shape in Global Constraints (it parses a string schema as `_event_type_needs_update` does). `datamodel_stats["event_type_differences"]: list[dict]`, initially `[]`, one entry per existing version 2 event type for which `_event_type_needs_update` is true, appended before the `create-only` check.
- `other` is true when the event types differ and none of `name`, `active`, `site_only`, `smart_only`, `changed` accounts for it (read-only flag, category, the schema outside `json.properties`), or when either schema has no `json.properties`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_datamodel_push.py
def _edit(site, value, fn):
    # apply fn to the stored event type's parsed schema and store it back

def test_differences_name_the_fields_on_each_side(tmp_path):
    # push once. On the site, in one event type: add property "site_note", delete one model property P,
    # change the title of another property Q, and set display to "Renamed".
    # stats = push(..., mode="create-only", additive=True)
    d = one entry of stats["event_type_differences"]
    # d["value"] is that event type; d["display"] == "Renamed"; d["name"] is True; d["active"] is False
    # d["site_only"] == ["site_note"]; d["smart_only"] == [P]; d["changed"] == [Q]; d["other"] is False
    # len(stats["event_type_differences"]) == 1: equal event types give no entry
    # the site's event type is unchanged (create-only)

def test_the_differences_are_the_same_when_the_push_updates(tmp_path):
    # the same site, cloned (FakeSite.clone): mode="both" gives an equal list, and patches

def test_a_schema_without_properties_is_a_difference_not_an_error(tmp_path):
    # the site's schema replaced by {} (and once by the string "{}"): one entry, field lists empty, other True

def test_a_type_the_additive_rule_skips_has_no_entry(tmp_path):
    # active on the site, retired in the model (push_observed as the additive tests use it): no entry

def test_a_reactivation_is_a_difference(tmp_path):
    # inactive on the site, active in the model: entry with active True;
    # under create-only the event type stays inactive
```

- [ ] **Step 2: Run and see them fail**

Run: `.venv/bin/python -m pytest -q tests/test_datamodel_push.py -k "difference or schema_without or additive_rule_skips or reactivation"`
Expected: FAIL (`KeyError: 'event_type_differences'`)

- [ ] **Step 3: Implement.** Check what prints `datamodel_stats` in `cli.py`; if it prints every key, print the count of differences, not the list.

- [ ] **Step 4: Write it down.** `docs/changelog.md` under "Unreleased": `--mode create-only` now leaves existing dropdown options alone (no display, order or active change, no orphan deactivated), where before it only left event types; the stats' `choices_skipped_by_mode` and `event_type_differences`. `docs/cli-reference/datamodel.md` and `USAGE.md`: the same sentence where `--mode` is described.

- [ ] **Step 5: Run the suite**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/er_smart_sync tests docs/changelog.md docs/cli-reference/datamodel.md USAGE.md
git commit -m "Datamodel push: report how the site's event types differ from the model"
```
