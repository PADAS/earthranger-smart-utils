# CM-variant Event Types Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give operators a per-sync choice — `--cm-variant-mode {split,consolidate}` — for how SMART CM variant groups (multiple CM nodes sharing one `hkeyPath`) map to EarthRanger v2 event types, replacing the lossy downstream dedup with two faithful representations.

**Architecture:** The v2 builder (`smart_to_er_v2.build_event_types_v2`) groups categories by `hkeyPath`. Singleton groups are unchanged. Variant groups (>1) apply the mode: **split** emits one event type per CM node with a stable disambiguated slug; **consolidate** emits one event type with a discriminator choice field and per-variant conditional sections (using ER's v2 conditional-section feature). smartconnect-client exposes the CM node `id` for split's stable slugs.

**Tech Stack:** Python 3.10+, Pydantic v1, pytest (`uv run --extra dev pytest`), smartconnect-client (sibling repo at `/Users/chrisdo/padas/smartconnect-client-pypi`), ER v2 event-type meta-schema (`/Users/chrisdo/padas/das/das/activity/schemas/eventtype_meta_schemas.py`).

**Reference spec:** `docs/superpowers/specs/2026-05-26-cm-variant-event-types-design.md`

**Resolved during planning:** ER's `EventType.value` is `varchar(255)` (`das/activity/models.py:330`), charset `^[A-Za-z0-9-_]*$`. Split slugs (~140 chars worst case) fit comfortably — **no value-shortening helper is needed**.

---

## File Structure

| File | Repo | Responsibility | Change |
|---|---|---|---|
| `smartconnect/models.py` | smartconnect-client | CM/DM parsing → dicts → Pydantic | Add `Category.id`; populate in `generate_node_paths` |
| `pyproject.toml` | er-smart-sync | deps | Bump `smartconnect-client>=1.11.2` |
| `src/er_smart_sync/config.py` | er-smart-sync | config models | Add `cm_variant_mode` field |
| `src/er_smart_sync/cli.py` | er-smart-sync | CLI | Add `--cm-variant-mode` flag (datamodel subcommand) |
| `src/er_smart_sync/smart_to_er_v2.py` | er-smart-sync | v2 builder | Grouping, split disambiguator, consolidate builder |
| `src/er_smart_sync/choices.py` | er-smart-sync | choice records | Emit discriminator ChoiceSet for consolidate groups |
| `src/er_smart_sync/synchronizer.py` | er-smart-sync | orchestration | Read `_cm_variant_mode`, thread to builder + choices |

---

## PHASE A — smartconnect-client (ships first, separate repo)

> Work in `/Users/chrisdo/padas/smartconnect-client-pypi`. This repo uses its own venv; run its existing test command (`pytest` from that repo root).

### Task 1: Expose CM node `id` on Category

**Files:**
- Modify: `smartconnect/models.py` (Category model ~18-27; `generate_node_paths` ~454-475)
- Test: `tests/` (follow the repo's existing test layout for models)

- [ ] **Step 1: Write the failing test**

Add to the smartconnect-client test suite (mirror an existing CM-parsing test's fixture style):

```python
def test_generate_node_paths_includes_node_id():
    from smartconnect.models import ConfigurableDataModel
    cdm = ConfigurableDataModel(use_language_code="en", cm_uuid="cm-1")
    cdm.load(MINIMAL_CM_XML_WITH_NODE_ID)  # a <node> carrying id="abc-123"
    cats = cdm.export_as_dict()["categories"]
    assert any(c.get("id") == "abc-123" for c in cats)


def test_category_model_accepts_optional_id():
    from smartconnect.models import Category
    assert Category(path="p", display="D").id is None
    assert Category(path="p", display="D", id="x").id == "x"
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/ -k "node_id or optional_id" -v`
Expected: FAIL — `Category` has no `id`; dicts lack `id` key.

- [ ] **Step 3: Add `id` to the Category model**

In `smartconnect/models.py`, `Category` (~line 18):

```python
class Category(BaseModel):
    path: str
    hkeyPath: Optional[str]
    display: str
    is_multiple: Optional[bool] = Field(alias="ismultiple", default=False)
    is_active: Optional[bool] = Field(alias="isactive", default=True)
    attributes: Optional[List[CategoryAttribute]]
    id: Optional[str] = None

    class Config:
        allow_population_by_field_name = True
```

- [ ] **Step 4: Populate `id` in `generate_node_paths`**

In both yield branches (~lines 459 and 468), add the `id` key. Guard against a node without an `id` attribute:

```python
yield {
    'path': f'{prefix}.{subcat["categoryKey"]}',
    'hkeyPath': subcat['categoryHkey'].rstrip('.'),
    'attributes': list(self.generate_category_attributes(subcat)),
    'display': self.resolve_display(subcat.name, language_code=self.use_language_code),
    'id': getattr(subcat, 'id', None) or (subcat['id'] if 'id' in dir(subcat) else None),
}
```

Note: `untangle` exposes XML attributes via `subcat['id']` and raises `AttributeError`/`KeyError` if absent. Use a small helper to be safe:

```python
def _node_id(subcat):
    try:
        return subcat['id']
    except (KeyError, AttributeError, IndexError):
        return None
```

Then `'id': _node_id(subcat)` in both branches.

- [ ] **Step 5: Run to verify pass**

Run: `pytest tests/ -k "node_id or optional_id" -v`
Expected: PASS.

- [ ] **Step 6: Run full smartconnect-client suite (no regressions)**

Run: `pytest`
Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add smartconnect/models.py tests/
git commit -m "feat: expose CM node id on Category for downstream variant slugs"
```

### Task 2: Release smartconnect-client 1.11.2

- [ ] **Step 1:** Bump the version per that repo's release process (it publishes to PyPI on tag push, mirroring er-smart-sync). Tag `v1.11.2`, push, confirm PyPI has `smartconnect-client==1.11.2`.

> **Blocking dependency:** Phase B Task 3 pins to this version. Do not start Phase B until 1.11.2 is on PyPI (or install the sibling source editable into the er-smart-sync venv for local dev: `uv pip install -e /Users/chrisdo/padas/smartconnect-client-pypi`).

---

## PHASE B — er-smart-sync: config + split mode

> Work in `/Users/chrisdo/padas/earthranger-smart-utils`. Test command: `uv run --extra dev pytest`.

### Task 3: Bump dependency floor + add config field

**Files:**
- Modify: `pyproject.toml` (dependencies)
- Modify: `src/er_smart_sync/config.py:56` (EarthRangerConfig)
- Test: `tests/test_config.py`

- [ ] **Step 1: Write the failing test**

```python
def test_cm_variant_mode_defaults_to_split(er_config):
    assert er_config.cm_variant_mode == "split"


def test_cm_variant_mode_rejects_unknown(smart_config):
    import pydantic
    from er_smart_sync.config import EarthRangerConfig, SyncConfig
    with pytest.raises(pydantic.ValidationError):
        SyncConfig(
            smart=smart_config,
            earthranger=EarthRangerConfig(endpoint="https://x", token="t", cm_variant_mode="nonsense"),
        )
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run --extra dev pytest tests/test_config.py -k cm_variant_mode -v`
Expected: FAIL — no `cm_variant_mode` field.

- [ ] **Step 3: Add the field**

In `config.py`, `EarthRangerConfig`, beside `event_type_version` (~line 56):

```python
cm_variant_mode: Literal["split", "consolidate"] = "split"
```

- [ ] **Step 4: Bump the dependency floor**

In `pyproject.toml` dependencies, change `"smartconnect-client>=1.11.0,<2.0"` to `"smartconnect-client>=1.11.2,<2.0"`.

- [ ] **Step 5: Run to verify pass**

Run: `uv run --extra dev pytest tests/test_config.py -k cm_variant_mode -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml src/er_smart_sync/config.py tests/test_config.py
git commit -m "feat(config): add cm_variant_mode (default split); require smartconnect-client>=1.11.2"
```

### Task 4: Variant-group detection helper

**Files:**
- Modify: `src/er_smart_sync/smart_to_er_v2.py` (add helper near top, after imports)
- Test: `tests/test_smart_to_er_v2.py`

- [ ] **Step 1: Write the failing test**

```python
def test_group_by_hkey_singletons_and_groups():
    from smartconnect.models import Category
    from er_smart_sync.smart_to_er_v2 import _group_by_hkey

    cats = [
        Category(path="a", hkeyPath="x", display="A", id="1"),
        Category(path="b", hkeyPath="y", display="B1", id="2"),
        Category(path="c", hkeyPath="y", display="B2", id="3"),
    ]
    groups = _group_by_hkey(cats)
    assert list(groups.keys()) == ["x", "y"]          # insertion order preserved
    assert len(groups["x"]) == 1
    assert [c.id for c in groups["y"]] == ["2", "3"]   # member order preserved
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run --extra dev pytest tests/test_smart_to_er_v2.py -k group_by_hkey -v`
Expected: FAIL — `_group_by_hkey` undefined.

- [ ] **Step 3: Implement the helper**

In `smart_to_er_v2.py`, after the existing imports:

```python
def _group_by_hkey(cats: list[Category]) -> dict[str, list[Category]]:
    """Group categories by hkeyPath (falls back to path). Preserves first-seen
    order of both keys and members so builder output is deterministic."""
    groups: dict[str, list[Category]] = {}
    for cat in cats:
        key = cat.hkeyPath or cat.path or ""
        groups.setdefault(key, []).append(cat)
    return groups
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run --extra dev pytest tests/test_smart_to_er_v2.py -k group_by_hkey -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/er_smart_sync/smart_to_er_v2.py tests/test_smart_to_er_v2.py
git commit -m "feat(v2): add _group_by_hkey variant-group detection helper"
```

### Task 5: Split disambiguator helper

**Files:**
- Modify: `src/er_smart_sync/smart_to_er_v2.py` (add helper; ensure `hashlib` and `sanitize_choice_value` imports)
- Test: `tests/test_smart_to_er_v2.py`

- [ ] **Step 1: Write the failing test**

```python
def test_variant_disambiguator_is_stable_and_readable():
    from smartconnect.models import Category
    from er_smart_sync.smart_to_er_v2 import _variant_disambiguator

    cat = Category(path="c", hkeyPath="animals.carcass", display="Large Predator Carcass", id="node-1")
    out = _variant_disambiguator(cat)
    assert out.startswith("large_predator_carcass_")
    assert _variant_disambiguator(cat) == out               # deterministic
    # 8-hex node-id suffix
    assert len(out.rsplit("_", 1)[-1]) == 8


def test_variant_disambiguator_missing_id_falls_back(caplog):
    from smartconnect.models import Category
    from er_smart_sync.smart_to_er_v2 import _variant_disambiguator

    cat = Category(path="c", hkeyPath="animals.carcass", display="Large Predator Carcass", id=None)
    with caplog.at_level("WARNING"):
        out = _variant_disambiguator(cat)
    assert out == "large_predator_carcass"
    assert any("no id" in r.message.lower() for r in caplog.records)
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run --extra dev pytest tests/test_smart_to_er_v2.py -k variant_disambiguator -v`
Expected: FAIL — undefined.

- [ ] **Step 3: Implement**

Ensure top-of-file imports include `import hashlib` and `from .choices import derive_choice_field, sanitize_choice_value` (the module already imports `derive_choice_field`; add `sanitize_choice_value` to that import). Then:

```python
def _variant_disambiguator(cat: Category) -> str:
    """Per-variant slug suffix: sanitized display + 8-hex node-id hash.
    Falls back to display-only (with a warning) when the CM node has no id."""
    base = sanitize_choice_value(cat.display)
    if cat.id:
        digest = hashlib.sha256(cat.id.encode("utf-8")).hexdigest()[:8]
        return f"{base}_{digest}"
    logger.warning(
        "CM node %r has no id; split slug uses display only and may collide",
        cat.display,
        extra=dict(display=cat.display, hkey=cat.hkeyPath),
    )
    return base
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run --extra dev pytest tests/test_smart_to_er_v2.py -k variant_disambiguator -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/er_smart_sync/smart_to_er_v2.py tests/test_smart_to_er_v2.py
git commit -m "feat(v2): add _variant_disambiguator for split-mode slugs"
```

### Task 6: `_build_one` accepts `value_disambiguator`

**Files:**
- Modify: `src/er_smart_sync/smart_to_er_v2.py:106-138` (`_build_one` signature + value assembly)
- Test: `tests/test_smart_to_er_v2.py`

- [ ] **Step 1: Write the failing test**

```python
def test_build_one_appends_value_disambiguator():
    from smartconnect.models import Attribute, Category
    from er_smart_sync.smart_to_er_v2 import _build_one

    cat = Category(path="carcass", hkeyPath="animals.carcass", display="Large Predator Carcass",
                   id="n1", attributes=[{"key": "age"}])
    attrs = [Attribute(key="age", type="NUMERIC", display="Age")]
    et = _build_one(
        cat=cat, cats=[cat], cat_paths=["carcass"], attributes=attrs,
        attribute_configs=None, ca_uuid="ca1", cm={"cm_uuid": "cm1"},
        value_disambiguator="large_predator_carcass_a1b2c3d4",
    )
    assert et is not None
    assert et.value == "ca1_cm1_animals_carcass_large_predator_carcass_a1b2c3d4"
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run --extra dev pytest tests/test_smart_to_er_v2.py -k append_value_disambiguator -v`
Expected: FAIL — `_build_one` has no `value_disambiguator` kwarg.

- [ ] **Step 3: Implement**

Add the kwarg (default `None`) to `_build_one`'s signature and apply it after the base value is assembled (smart_to_er_v2.py ~128-132):

```python
def _build_one(
    *,
    cat: Category,
    cats: list[Category],
    cat_paths: list[str],
    attributes: list[Attribute],
    attribute_configs: list | None,
    ca_uuid: str,
    cm: dict | None,
    choices_base_url: str = "/api/v2.0/schemas",
    value_disambiguator: str | None = None,
) -> ERV2EventType | None:
    ...
    if cm:
        value = f"{ca_uuid}_{cm['cm_uuid']}_{value_suffix}"
    else:
        value = f"{ca_uuid}_{value_suffix}"
    if value_disambiguator:
        value = f"{value}_{value_disambiguator}"
    value = value.lower()
```

- [ ] **Step 4: Run to verify pass + full v2 suite (no regression to singleton path)**

Run: `uv run --extra dev pytest tests/test_smart_to_er_v2.py -v`
Expected: PASS, including all pre-existing tests.

- [ ] **Step 5: Commit**

```bash
git add src/er_smart_sync/smart_to_er_v2.py tests/test_smart_to_er_v2.py
git commit -m "feat(v2): _build_one accepts value_disambiguator for split slugs"
```

### Task 7: Wire grouping + split into `build_event_types_v2`

**Files:**
- Modify: `src/er_smart_sync/smart_to_er_v2.py:72-103` (`build_event_types_v2`)
- Test: `tests/test_smart_to_er_v2.py`

- [ ] **Step 1: Write the failing test**

```python
def test_build_event_types_v2_split_emits_one_per_variant():
    from er_smart_sync.smart_to_er_v2 import build_event_types_v2

    cm = {
        "cm_uuid": "cm1",
        "categories": [
            {"path": "carcass.lp", "hkeyPath": "animals.carcass", "display": "Large Predator Carcass",
             "id": "n1", "attributes": [{"key": "age"}]},
            {"path": "carcass.sp", "hkeyPath": "animals.carcass", "display": "Small Predator Carcass",
             "id": "n2", "attributes": [{"key": "age"}]},
        ],
        "attributes": [],
    }
    dm = {"attributes": [{"key": "age", "type": "NUMERIC", "display": "Age"}]}
    ets = build_event_types_v2(dm=dm, cm=cm, ca_uuid="ca1", ca_identifier="CA", cm_variant_mode="split")
    values = sorted(e.value for e in ets)
    assert len(values) == 2
    assert all(v.startswith("ca1_cm1_animals_carcass_") for v in values)
    assert values[0] != values[1]


def test_build_event_types_v2_singleton_unchanged():
    from er_smart_sync.smart_to_er_v2 import build_event_types_v2
    cm = {
        "cm_uuid": "cm1",
        "categories": [
            {"path": "incident", "hkeyPath": "incidents.report", "display": "Report",
             "id": "n9", "attributes": [{"key": "age"}]},
        ],
        "attributes": [],
    }
    dm = {"attributes": [{"key": "age", "type": "NUMERIC", "display": "Age"}]}
    ets = build_event_types_v2(dm=dm, cm=cm, ca_uuid="ca1", ca_identifier="CA", cm_variant_mode="split")
    assert len(ets) == 1
    assert ets[0].value == "ca1_cm1_incidents_report"   # no disambiguator for singletons
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run --extra dev pytest tests/test_smart_to_er_v2.py -k "split_emits or singleton_unchanged" -v`
Expected: FAIL — `build_event_types_v2` has no `cm_variant_mode` kwarg / doesn't group.

- [ ] **Step 3: Implement grouping + split dispatch**

Replace the per-cat loop in `build_event_types_v2` (smart_to_er_v2.py:89-102):

```python
def build_event_types_v2(
    *,
    dm: dict,
    cm: dict | None = None,
    ca_uuid: str,
    ca_identifier: str,
    choices_base_url: str = "/api/v2.0/schemas",
    cm_variant_mode: str = "split",
) -> list[ERV2EventType]:
    del ca_identifier
    source = cm if cm else dm
    cats = parse_obj_as(list[Category], source.get("categories") or [])
    cat_paths = [cat.path for cat in cats]
    attributes = parse_obj_as(list[Attribute], dm.get("attributes") or [])
    attribute_configs = cm.get("attributes") if cm else None

    common = dict(
        cats=cats, cat_paths=cat_paths, attributes=attributes,
        attribute_configs=attribute_configs, ca_uuid=ca_uuid, cm=cm,
        choices_base_url=choices_base_url,
    )

    event_types: list[ERV2EventType] = []
    for _hkey, group in _group_by_hkey(cats).items():
        if len(group) == 1:
            et = _build_one(cat=group[0], **common)
            if et is not None:
                event_types.append(et)
        elif cm_variant_mode == "consolidate":
            et = _build_consolidated(group=group, **common)
            if et is not None:
                event_types.append(et)
        else:  # split
            for cat in group:
                et = _build_one(cat=cat, value_disambiguator=_variant_disambiguator(cat), **common)
                if et is not None:
                    event_types.append(et)
    return event_types
```

> `_build_consolidated` doesn't exist yet (Task 9). Until then, add a temporary stub that raises `NotImplementedError`, OR implement Task 9 before exercising consolidate. To keep this task green, the two tests above use split mode only. Add the stub:
>
> ```python
> def _build_consolidated(*, group, **kwargs):
>     raise NotImplementedError("consolidate mode lands in Task 9")
> ```

- [ ] **Step 4: Run to verify pass + full v2 suite**

Run: `uv run --extra dev pytest tests/test_smart_to_er_v2.py -v`
Expected: PASS (all, including pre-existing singleton/no-cm tests).

- [ ] **Step 5: Commit**

```bash
git add src/er_smart_sync/smart_to_er_v2.py tests/test_smart_to_er_v2.py
git commit -m "feat(v2): group by hkeyPath; split mode emits one event type per variant"
```

### Task 8: Synchronizer + CLI thread the mode through

**Files:**
- Modify: `src/er_smart_sync/synchronizer.py:210` (read `_cm_variant_mode`), `:390` (pass to builder)
- Modify: `src/er_smart_sync/cli.py` (add `--cm-variant-mode` to datamodel subcommand, ~line 310 flag block and ~line 365 override block)
- Test: `tests/test_synchronizer.py`, `tests/test_cli.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_synchronizer.py
def test_synchronizer_passes_cm_variant_mode_to_builder(sync_config_v2, mock_er_client):
    from unittest.mock import patch, MagicMock
    sync_config_v2.earthranger.cm_variant_mode = "consolidate"
    sync = ERSmartSynchronizer(config=sync_config_v2, er_client=mock_er_client, smart_client=MagicMock())
    dm = MagicMock(); dm.export_as_dict.return_value = {"categories": [], "attributes": []}
    mock_er_client.get_event_categories.return_value = [{"id": "c", "value": "test", "display": "T"}]
    mock_er_client.get_event_types.return_value = []
    with patch("er_smart_sync.synchronizer.build_event_types_v2", return_value=[]) as mock_build, \
         patch("er_smart_sync.synchronizer.build_choice_sets", return_value=[]), \
         patch("er_smart_sync.synchronizer.upsert_choices",
               return_value=__import__("er_smart_sync.choices", fromlist=["ChoicesStats"]).ChoicesStats()):
        sync.push_smart_ca_datamodel_to_earthranger(dm=dm, smart_ca_uuid="u", ca_identifier="TEST")
    assert mock_build.call_args.kwargs["cm_variant_mode"] == "consolidate"
```

```python
# tests/test_cli.py — mirror an existing --event-type-version override test
def test_cm_variant_mode_flag_overrides_config(...):
    # invoke datamodel with --cm-variant-mode consolidate and assert
    # the constructed synchronizer's config.earthranger.cm_variant_mode == "consolidate"
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run --extra dev pytest tests/test_synchronizer.py -k cm_variant_mode tests/test_cli.py -k cm_variant_mode -v`
Expected: FAIL.

- [ ] **Step 3: Implement synchronizer read + pass**

`synchronizer.py` ~line 210 (beside `_event_type_version`):

```python
self._cm_variant_mode: str = config.earthranger.cm_variant_mode
```

`synchronizer.py` ~line 390, in the `build_event_types_v2(...)` call, add:

```python
event_types = build_event_types_v2(
    dm=dm_dict,
    cm=cdm_dict,
    ca_uuid=smart_ca_uuid,
    ca_identifier=ca_identifier,
    choices_base_url=self.config.earthranger.choices_base_url,
    cm_variant_mode=self._cm_variant_mode,
)
```

- [ ] **Step 4: Implement CLI flag**

In `cli.py` datamodel subcommand, mirror `--event-type-version` (~line 310):

```python
@click.option(
    "--cm-variant-mode",
    type=click.Choice(["split", "consolidate"]),
    default=None,
    help="How to map CM variant groups (categories sharing an hkeyPath) to "
         "ER event types: 'split' (one per variant, default) or 'consolidate' "
         "(one event type + variant selector + conditional sections). v2 only.",
)
```

Add `cm_variant_mode` to the function params, and in the override block (~line 365):

```python
if cm_variant_mode:
    config.earthranger.cm_variant_mode = cm_variant_mode
```

- [ ] **Step 5: Run to verify pass**

Run: `uv run --extra dev pytest tests/test_synchronizer.py -k cm_variant_mode tests/test_cli.py -k cm_variant_mode -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/er_smart_sync/synchronizer.py src/er_smart_sync/cli.py tests/test_synchronizer.py tests/test_cli.py
git commit -m "feat: thread cm_variant_mode from CLI/config through synchronizer to v2 builder"
```

> **Milestone:** Phases A+B deliver a complete, shippable split-mode feature. Consolidate (Phase C) can be a follow-up release if desired.

---

## PHASE C — er-smart-sync: consolidate mode

### Task 9: Consolidate builder (`_build_consolidated`)

**Files:**
- Modify: `src/er_smart_sync/smart_to_er_v2.py` (replace the Task-7 stub)
- Test: `tests/test_smart_to_er_v2.py`

- [ ] **Step 1: Write the failing test**

```python
def test_build_consolidated_emits_discriminator_and_conditional_sections():
    from er_smart_sync.smart_to_er_v2 import build_event_types_v2

    cm = {
        "cm_uuid": "cm1",
        "categories": [
            {"path": "carcass.lp", "hkeyPath": "animals.carcass", "display": "Large Predator Carcass",
             "id": "n1", "attributes": [{"key": "age"}]},
            {"path": "carcass.sp", "hkeyPath": "animals.carcass", "display": "Small Predator Carcass",
             "id": "n2", "attributes": [{"key": "lc"}]},
        ],
        "attributes": [],
    }
    dm = {"attributes": [
        {"key": "age", "type": "NUMERIC", "display": "Age"},
        {"key": "lc", "type": "NUMERIC", "display": "Large Carnivore"},
    ]}
    ets = build_event_types_v2(dm=dm, cm=cm, ca_uuid="ca1", ca_identifier="CA", cm_variant_mode="consolidate")
    assert len(ets) == 1
    et = ets[0]
    assert et.value == "ca1_cm1_animals_carcass"
    assert et.display == "Carcass"
    schema = et.event_schema
    ui = schema["ui"]
    # one section per variant + the discriminator section
    assert len(ui["sections"]) == 3
    assert ui["order"][0] == "section-1"
    # discriminator field present and required
    disc = next(k for k in schema["json"]["properties"] if k.endswith("_variant"))
    assert disc in schema["json"]["required"]
    # each variant section carries an IS_EXACTLY condition on the discriminator
    variant_sections = [s for sid, s in ui["sections"].items() if sid != "section-1"]
    for s in variant_sections:
        cond = s["conditions"][0]
        assert cond["operator"] == "IS_EXACTLY"
        assert cond["field"] == disc
        assert cond["id"].startswith("condition-")
    # discriminator field lists the variant sections as conditionalDependents
    dep = ui["fields"][disc]["conditionalDependents"]
    assert set(dep) == {sid for sid in ui["sections"] if sid != "section-1"}
    # variant attribute fields are re-parented to their own section (not the
    # default section-1), so conditional visibility actually hides them.
    assert ui["fields"]["age"]["parent"] != "section-1"
    assert ui["fields"]["lc"]["parent"] != "section-1"
    assert ui["fields"]["age"]["parent"] != ui["fields"]["lc"]["parent"]
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run --extra dev pytest tests/test_smart_to_er_v2.py -k build_consolidated -v`
Expected: FAIL — stub raises `NotImplementedError`.

- [ ] **Step 3: Implement `_build_consolidated`**

Replace the stub. Reuse `_build_field_blocks` (existing, smart_to_er_v2.py:191) for each variant's attributes and `_build_choice_property_pair` (existing, :287) for the discriminator. The discriminator's choice options resolve at query time from the ChoiceSet emitted in Task 10.

```python
def _build_consolidated(
    *,
    group: list[Category],
    cats: list[Category],
    cat_paths: list[str],
    attributes: list[Attribute],
    attribute_configs: list | None,
    ca_uuid: str,
    cm: dict | None,
    choices_base_url: str = "/api/v2.0/schemas",
) -> ERV2EventType | None:
    rep = group[0]
    hkey = rep.hkeyPath or rep.path or ""
    # Use event_type_value_for (choices.py) so the consolidated value matches
    # the discriminator ChoiceSet's value exactly (Task 10) — same field name.
    value = event_type_value_for(category_path=hkey, ca_uuid=ca_uuid, cm=cm)
    display = hkey.split(".")[-1].replace("_", " ").title()

    discriminator = derive_choice_field(value, "variant")

    properties: dict = {}
    ui_fields: dict = {}
    sections: dict = {}
    order: list[str] = ["section-1"]

    # Discriminator: a single-select CHOICE_LIST. _build_choice_property_pair
    # derives the field name internally as derive_choice_field(value, "variant")
    # — identical to `discriminator` above — and sets parent="section-1", which
    # is exactly where the discriminator lives. Its options resolve at query
    # time from the ChoiceSet emitted in Task 10. (Verified signature:
    # smart_type/display/is_multiple/attr_key/choices_base_url/event_type_value.)
    disc_prop, disc_ui = _build_choice_property_pair(
        smart_type="LIST",
        display="Variant",
        is_multiple=False,
        attr_key="variant",
        choices_base_url=choices_base_url,
        event_type_value=value,
    )
    properties[discriminator] = disc_prop

    variant_section_ids: list[str] = []
    for i, cat in enumerate(group, start=2):
        section_id = f"section-{i}"
        variant_section_ids.append(section_id)
        order.append(section_id)
        leaf_attributes = list(cat.attributes or [])
        props, fields, field_order = _build_field_blocks(
            attributes=attributes,
            leaf_attributes=leaf_attributes,
            is_multiple=bool(cat.is_multiple),
            attribute_configs=attribute_configs,
            choices_base_url=choices_base_url,
            event_type_value=value,
        )
        # _build_field_blocks tags every ui field parent="section-1" (the
        # single-section default). Re-parent this variant's fields to their
        # own section so rjsf renders them under the conditional section, not
        # the always-visible one.
        for fname in fields:
            fields[fname]["parent"] = section_id
        properties.update(props)
        ui_fields.update(fields)
        sections[section_id] = {
            "label": cat.display,
            "columns": 1,
            "isActive": True,
            "leftColumn": [{"name": k, "type": "field"} for k in field_order],
            "rightColumn": [],
            "conditions": [{
                "field": discriminator,
                "id": f"condition-{i}",
                "operator": "IS_EXACTLY",
                "value": sanitize_choice_value(cat.display),
            }],
        }

    # Discriminator UI field; always-visible section-1; depends on variant sections.
    disc_ui["conditionalDependents"] = variant_section_ids
    ui_fields[discriminator] = disc_ui
    sections["section-1"] = {
        "label": display,
        "columns": 1,
        "isActive": True,
        "leftColumn": [{"name": discriminator, "type": "field"}],
        "rightColumn": [],
        "conditions": [],
    }

    if not properties:
        return None

    et = ERV2EventType(value=value, display=display, is_active=True)
    et.event_schema = {
        "json": {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "unevaluatedProperties": False,
            "properties": properties,
            "required": [discriminator],
        },
        "ui": {
            "fields": ui_fields,
            "headers": {},
            "order": order,
            "sections": sections,
        },
    }
    return et
```

> **Imports:** extend the choices import to `from .choices import derive_choice_field, sanitize_choice_value, event_type_value_for` (Task 5 added the first two; add `event_type_value_for` here). `_build_consolidated` otherwise uses only same-module helpers `_build_choice_property_pair` and `_build_field_blocks`. The discriminator field name from `_build_choice_property_pair` equals `derive_choice_field(value, "variant")` by construction, so it matches `discriminator` and the Task-10 ChoiceSet field exactly.

- [ ] **Step 4: Run to verify pass**

Run: `uv run --extra dev pytest tests/test_smart_to_er_v2.py -k build_consolidated -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/er_smart_sync/smart_to_er_v2.py tests/test_smart_to_er_v2.py
git commit -m "feat(v2): consolidate mode builds discriminator + conditional sections"
```

### Task 10: `build_choice_sets` emits the discriminator ChoiceSet

**Files:**
- Modify: `src/er_smart_sync/choices.py` (`build_choice_sets` ~line 130; add grouping + mode param)
- Modify: `src/er_smart_sync/synchronizer.py:360` (pass `cm_variant_mode` to `build_choice_sets`)
- Test: `tests/test_choices.py`

- [ ] **Step 1: Write the failing test**

```python
def test_build_choice_sets_consolidate_emits_discriminator():
    from er_smart_sync.choices import build_choice_sets, derive_choice_field

    cm = {
        "cm_uuid": "cm1",
        "categories": [
            {"path": "carcass.lp", "hkeyPath": "animals.carcass", "display": "Large Predator Carcass",
             "id": "n1", "attributes": []},
            {"path": "carcass.sp", "hkeyPath": "animals.carcass", "display": "Small Predator Carcass",
             "id": "n2", "attributes": []},
        ],
        "attributes": [],
    }
    dm = {"attributes": []}
    sets = build_choice_sets(dm=dm, cm=cm, ca_uuid="ca1", cm_variant_mode="consolidate")
    value = "ca1_cm1_animals_carcass"
    disc_field = derive_choice_field(value, "variant")
    disc = next((s for s in sets if s.field == disc_field), None)
    assert disc is not None
    assert {o.value for o in disc.options} == {"large_predator_carcass", "small_predator_carcass"}


def test_build_choice_sets_split_emits_no_discriminator():
    from er_smart_sync.choices import build_choice_sets
    cm = {
        "cm_uuid": "cm1",
        "categories": [
            {"path": "carcass.lp", "hkeyPath": "animals.carcass", "display": "Large Predator Carcass",
             "id": "n1", "attributes": []},
            {"path": "carcass.sp", "hkeyPath": "animals.carcass", "display": "Small Predator Carcass",
             "id": "n2", "attributes": []},
        ],
        "attributes": [],
    }
    sets = build_choice_sets(dm={"attributes": []}, cm=cm, ca_uuid="ca1", cm_variant_mode="split")
    assert all(not s.field.endswith("_variant") for s in sets)
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run --extra dev pytest tests/test_choices.py -k "consolidate_emits or split_emits_no" -v`
Expected: FAIL — `build_choice_sets` has no `cm_variant_mode` kwarg / no discriminator.

- [ ] **Step 3: Implement**

Add `cm_variant_mode: str = "split"` to `build_choice_sets` (signature at choices.py:194). The function accumulates into `result: list[ChoiceSet]` (line 211) and returns it (line 271). Just before `return result`, append discriminator sets for consolidate variant groups. Reuse the existing `event_type_value_for` helper (choices.py:136) for the consolidated value — DRY with the builder's scheme — and the existing `derive_choice_field`, `sanitize_choice_value`, `_shorten_value`, `_shorten_display`, `ChoiceOption`, `ChoiceSet`:

```python
    if cm and cm_variant_mode == "consolidate":
        # Group CM categories by hkeyPath; each variant group (>1) gets a
        # discriminator ChoiceSet whose options are the variant displays.
        groups: dict[str, list[dict]] = {}
        for c in (cm.get("categories") or []):
            key = c.get("hkeyPath") or c.get("path") or ""
            groups.setdefault(key, []).append(c)
        for hkey, members in groups.items():
            if len(members) < 2:
                continue
            value = event_type_value_for(category_path=hkey, ca_uuid=ca_uuid, cm=cm)
            field = derive_choice_field(value, "variant")
            options = tuple(
                ChoiceOption(
                    value=_shorten_value(sanitize_choice_value(m.get("display", ""))),
                    display=_shorten_display(m.get("display", "")),
                    is_active=True,
                )
                for m in members
            )
            result.append(ChoiceSet(field=field, options=options))

    return result
```

Then in `synchronizer.py:360`, pass the mode:

```python
choice_sets = build_choice_sets(
    dm=dm_dict,
    cm=cdm_dict,
    ca_uuid=smart_ca_uuid,
    cm_variant_mode=self._cm_variant_mode,
)
```

- [ ] **Step 4: Run to verify pass + full choices suite**

Run: `uv run --extra dev pytest tests/test_choices.py -v`
Expected: PASS (including pre-existing).

- [ ] **Step 5: Commit**

```bash
git add src/er_smart_sync/choices.py src/er_smart_sync/synchronizer.py tests/test_choices.py
git commit -m "feat(choices): emit discriminator ChoiceSet for consolidate variant groups"
```

### Task 11: Consolidate schema validates against ER's v2 meta-schema

**Files:**
- Test: `tests/test_smart_to_er_v2.py`

This is the integration safety net: it proves the emitted consolidate schema is actually accepted by ER, catching any shape drift in Task 9's conditions / sections / discriminator.

- [ ] **Step 1: Write the failing/guard test**

The ER meta-schema lives in the `das` repo, not a dependency here. Vendor a copy of the relevant validator OR validate structurally against the documented constraints. Preferred: a structural assertion test that encodes the meta-schema's hard requirements (so it runs without the das repo):

```python
import re

def test_consolidate_schema_matches_meta_schema_constraints():
    from er_smart_sync.smart_to_er_v2 import build_event_types_v2

    cm = {  # same 2-variant carcass CM as Task 9
        "cm_uuid": "cm1",
        "categories": [
            {"path": "carcass.lp", "hkeyPath": "animals.carcass", "display": "Large Predator Carcass",
             "id": "n1", "attributes": [{"key": "age"}]},
            {"path": "carcass.sp", "hkeyPath": "animals.carcass", "display": "Small Predator Carcass",
             "id": "n2", "attributes": [{"key": "age"}]},
        ],
        "attributes": [],
    }
    dm = {"attributes": [{"key": "age", "type": "NUMERIC", "display": "Age"}]}
    et = build_event_types_v2(dm=dm, cm=cm, ca_uuid="ca1", ca_identifier="CA",
                              cm_variant_mode="consolidate")[0]
    ui = et.event_schema["ui"]

    section_id = re.compile(r"^section-[A-Za-z0-9_-]+$")
    condition_id = re.compile(r"^condition-.+$")

    # every order entry is a real section
    assert set(ui["order"]) == set(ui["sections"].keys())
    for sid, section in ui["sections"].items():
        assert section_id.match(sid), sid
        for cond in section.get("conditions", []):
            assert condition_id.match(cond["id"])
            assert cond["operator"] in {
                "CONTAINS", "IS_EMPTY", "IS_NOT_EMPTY", "IS_EXACTLY",
                "IS_CONTAINED_BY", "IS_NOT_CONTAINED_BY",
            }
            assert cond["field"] in et.event_schema["json"]["properties"]
    # conditionalDependents reference real sections
    for fname, field in ui["fields"].items():
        for dep in field.get("conditionalDependents", []):
            assert dep in ui["sections"], dep
```

> **Stronger option (optional):** if the das repo is checked out locally, add a separately-marked test that imports `eventtype_v2_schema` from `das.activity.schemas.eventtype_meta_schemas` and runs `jsonschema.validate(et.event_schema, ...)`. Mark it `@pytest.mark.skipif` on import failure so CI without `das` still passes. The structural test above is the always-on guard.

- [ ] **Step 2: Run to verify it passes (or surfaces a real shape bug)**

Run: `uv run --extra dev pytest tests/test_smart_to_er_v2.py -k meta_schema_constraints -v`
Expected: PASS. If it fails, fix Task 9's shapes until it passes.

- [ ] **Step 3: Commit**

```bash
git add tests/test_smart_to_er_v2.py
git commit -m "test(v2): guard consolidate schema against ER meta-schema constraints"
```

---

## PHASE D — verification & docs

### Task 12: End-to-end dry-run + docs

**Files:**
- Modify: `docs/concepts/choices.md` and/or a new `docs/concepts/cm-variants.md`
- Modify: `USAGE.md` (document `--cm-variant-mode`)

- [ ] **Step 1: Dry-run split against Botswana**

Run:
```bash
.venv/bin/er-smart-sync --dry-run datamodel --config sync.yaml \
  --from-file ./datamodel.xml --cm-from-file ./Botswana_Guardians.xml \
  --ca-identifier CA --event-type-version v2 --cm-variant-mode split 2>&1 \
  | grep -E "event types|Builder emitted"
```
Expected: the carcass/snared/stuck-between-fence/spoor groups each yield multiple distinct event types; no "Builder emitted N duplicate value" warnings (split produces unique slugs, so the dedup never fires).

- [ ] **Step 2: Dry-run consolidate against Botswana**

Run the same with `--cm-variant-mode consolidate`.
Expected: each variant group yields ONE event type; choices phase emits a `*_variant` discriminator ChoiceSet per group.

- [ ] **Step 3: Document the modes**

Add a "CM variant modes" section to the docs explaining split vs consolidate, the default (split), the v2-only constraint, and the rename-orphaning caveat. Cross-reference from `docs/concepts/choices.md`.

- [ ] **Step 4: Build docs**

Run: `uv run --extra docs mkdocs build --strict`
Expected: clean build.

- [ ] **Step 5: Full suite + commit**

Run: `uv run --extra dev pytest`
Expected: all pass.

```bash
git add docs/ USAGE.md
git commit -m "docs: document --cm-variant-mode split vs consolidate"
```

---

## Self-Review Notes

- **Spec coverage:** smartconnect-client id (Task 1), config+CLI (Tasks 3,8), grouping (Task 4), split slugs (Tasks 5-7), consolidate builder (Task 9), discriminator choices (Task 10), meta-schema validation (Task 11), e2e+docs (Task 12). Value-length verification resolved at plan time (255, no shortening). All spec sections covered.
- **Ordering dependency:** Phase A ships to PyPI before Phase B's dependency bump (or use editable local install for dev).
- **Milestone:** Phases A+B = shippable split-only release; Phase C adds consolidate.
- **Integration safety net:** Task 11 guards consolidate's schema shape so Task 9 drift is caught without a live ER.
