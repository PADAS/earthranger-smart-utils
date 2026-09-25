"""SMART → EarthRanger Choice records.

Owns the choices layer required by ER v2 event types:

- Pure helpers: ``sanitize_choice_value``, ``choice_scope_key``,
  ``derive_shared_choice_field``, ``derive_choice_field``,
  ``event_type_value_for``.
- Plan-record dataclasses: ``ChoiceOption``, ``ChoiceSet``, ``ChoicesStats``.
- DM walker: ``build_choice_sets``.
- Upsert algorithm: ``upsert_choices``.

See ``docs/superpowers/specs/2026-05-13-er-v2-choices-population-design.md``
for full design rationale.
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass

from pydantic import parse_obj_as
from smartconnect.models import Attribute, Category, CategoryAttribute

logger = logging.getLogger(__name__)


def _variant_disambiguator(cat: Category) -> str:
    """Per-variant slug suffix: sanitized display + 8-hex node-id hash.

    Shared by ``build_choice_sets`` (split mode) and ``smart_to_er_v2._build_one``
    (also split mode). Both must apply the identical suffix so the
    ``event_type_value`` used to derive ``Choice.field`` names matches the
    ``$ref`` URLs embedded in the v2 event-type schema.

    Falls back to display-only (with a warning) when the CM node has no id.
    """
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


def sanitize_choice_value(option_key: str) -> str:
    """Map a SMART option key to a ``^\\w+$`` string.

    SMART keys may contain ``.`` (TREE leaf paths), accents, apostrophes,
    spaces. The Choice DB column requires letters/digits/underscores only.

    This rule is **load-bearing**: changing it later requires backfilling
    historical event records that store the resolved value string.
    """
    sanitized = re.sub(r"[^A-Za-z0-9]+", "_", option_key).strip("_").lower()
    return sanitized or "_"


def derive_choice_field(event_type_value: str, attr_key: str) -> str:
    """Derive a per-event-type Choice.field name (``et{8hex}_{attr_key}``).

    Only the consolidate-mode variant discriminator still uses this scheme —
    its options are the variant identities, so there is nothing to share.
    Regular choice attributes use ``derive_shared_choice_field`` instead.
    Total length ≤ 40 chars (truncated if needed).
    """
    digest = hashlib.sha256(event_type_value.encode("utf-8")).hexdigest()[:8]
    sanitized = sanitize_choice_value(attr_key)
    field = f"et{digest}_{sanitized}"
    if len(field) > 40:
        field = field[:40]
    return field


def choice_scope_key(*, ca_uuid: str, cm: dict | None) -> str:
    """Scope identity for shared choice fields.

    Choice lists are shared across the event types built from one datamodel
    sync — never across CAs or across CMs, because each CM curates its own
    option subsets (see docs/concepts/choices.md).

    - Without CM: ``{ca_uuid}``
    - With CM:    ``{ca_uuid}_{cm_uuid}``
    """
    if cm:
        return f"{ca_uuid}_{cm['cm_uuid']}".lower()
    return ca_uuid.lower()


def derive_shared_choice_field(scope_key: str, attr_key: str) -> str:
    """Derive the shared Choice.field name for a SMART attribute.

    Returns ``dm{8hex}_{sanitized_attr_key}`` where the hash covers the
    (CA, CM) scope key — so every event type built from the same datamodel
    references one Choice list per attribute, while two CAs (or two CMs)
    that reuse an attribute key stay collision-free. The ``dm`` prefix
    distinguishes shared lists from legacy per-event-type ``et`` lists.
    Total length ≤ 40 chars (truncated if needed).
    """
    digest = hashlib.sha256(scope_key.encode("utf-8")).hexdigest()[:8]
    sanitized = sanitize_choice_value(attr_key)
    field = f"dm{digest}_{sanitized}"
    if len(field) > 40:
        field = field[:40]
    return field


# ER's Choice table caps both `value` and `display` at varchar(100). Deep
# SMART TREE leaves (whose key is a dotted concatenation of parent path
# components) routinely exceed this after sanitize_choice_value collapses
# dots to underscores. Truncate at build time so the upsert path's
# (field, value) lookup and display-drift comparison stay consistent.
_CHOICE_VALUE_DISPLAY_MAX = 100
_VALUE_HASH_LEN = 8
# Readable prefix kept before the "_{hash}" suffix. Derived so the total
# always equals _CHOICE_VALUE_DISPLAY_MAX: 100 - 1 (separator) - 8 (hash) = 91.
_VALUE_PREFIX_LEN = _CHOICE_VALUE_DISPLAY_MAX - 1 - _VALUE_HASH_LEN


def _shorten_value(sanitized: str) -> str:
    """Hash-suffix overlong values; mirrors derive_choice_field's scheme.

    Stable: same input always produces the same output. Two distinct
    inputs that share a 91-char prefix get different hash tails, so
    silent collisions are vanishingly rare. Logs at DEBUG because deep
    TREEs can produce many shortenings per sync; the design is documented
    in docs/concepts/choices.md and counts surface via datamodel_stats.
    """
    if len(sanitized) <= _CHOICE_VALUE_DISPLAY_MAX:
        return sanitized
    digest = hashlib.sha256(sanitized.encode("utf-8")).hexdigest()[:_VALUE_HASH_LEN]
    shortened = f"{sanitized[:_VALUE_PREFIX_LEN]}_{digest}"
    logger.debug(
        "Shortened choice value (len %d → %d) via hash-suffix: %r",
        len(sanitized),
        len(shortened),
        shortened,
    )
    return shortened


def _discriminator_option_value(display: str, node_id: str | None) -> str:
    """Stable option value for a consolidate-mode discriminator option.

    Identical scheme to split-mode disambiguation: sanitized display +
    8-hex node-id hash.  Guarantees uniqueness within a variant group
    even when two variant displays sanitize to the same string.

    Falls back to display-only when ``node_id`` is absent (same fallback
    as ``_variant_disambiguator``).
    """
    base = sanitize_choice_value(display)
    if node_id:
        digest = hashlib.sha256(node_id.encode("utf-8")).hexdigest()[:8]
        return _shorten_value(f"{base}_{digest}")
    logger.warning(
        "CM node %r has no id; discriminator option value uses display only"
        " and may collide",
        display,
        extra=dict(display=display),
    )
    return _shorten_value(base)


def _shorten_display(raw: str) -> str:
    """Cap display at 100 chars while preserving meaning when possible.

    Strategy stays centered on the leaf identifier when dots are present:
    - Dotted fallback (the SMART tree-path-as-display edge case): keep the
      last dotted segment. If that segment is itself still > 100 chars,
      truncate the segment (word-boundary/hard-cut) rather than falling
      back to the start of the full path — keeps the focus on the leaf.
    - Long natural-language label with whitespace: word-boundary truncate
      at the last whitespace before char 99 and append ``…``.
    - Pathological no-whitespace string: hard-cut at char 99 + ``…``.

    Logs at DEBUG for the same reason as ``_shorten_value``.
    """
    if len(raw) <= _CHOICE_VALUE_DISPLAY_MAX:
        return raw
    # Center the truncation on the leaf segment when this looks like a
    # dotted-path display. Even if the leaf itself is overlong, we want
    # the truncation to operate on the leaf — not the parent prefix.
    target = raw.rsplit(".", 1)[-1] if "." in raw else raw
    if target != raw and len(target) <= _CHOICE_VALUE_DISPLAY_MAX:
        logger.debug(
            "Shortened choice display (len %d → %d) via last-segment: %r",
            len(raw),
            len(target),
            target,
        )
        return target
    head = target[: _CHOICE_VALUE_DISPLAY_MAX - 1]
    if " " in head:
        head = head.rsplit(" ", 1)[0]
        strategy = "word-boundary"
    else:
        strategy = "hard-cut"
    shortened = f"{head}…"
    logger.debug(
        "Shortened choice display (len %d → %d) via %s%s: %r",
        len(raw),
        len(shortened),
        "last-segment+" if target != raw else "",
        strategy,
        shortened,
    )
    return shortened


def event_type_value_for(
    *,
    category_path: str,
    ca_uuid: str,
    cm: dict | None,
) -> str:
    """Compute the event-type ``value`` string.

    Mirrors the scheme used by ``smart_to_er_v2._build_one``:

    - Without CM: ``{ca_uuid}_{path_underscored}`` lowercased.
    - With CM:    ``{ca_uuid}_{cm_uuid}_{path_underscored}`` lowercased.

    Caller passes ``category_path`` already resolved (``cat.path`` when no CM,
    ``cat.hkeyPath`` when CM is present — same rule the existing builder
    uses).
    """
    path_underscored = category_path.replace(".", "_")
    if cm:
        value = f"{ca_uuid}_{cm['cm_uuid']}_{path_underscored}"
    else:
        value = f"{ca_uuid}_{path_underscored}"
    return value.lower()


@dataclass(frozen=True)
class ChoiceOption:
    """A single option in a choice set, with its activity flag."""

    value: str
    display: str
    is_active: bool = True


@dataclass(frozen=True)
class ChoiceSet:
    """The plan for one ER ``Choice.field`` worth of records."""

    field: str
    options: tuple[ChoiceOption, ...]


@dataclass
class ChoicesStats:
    """Per-run counters for the choices upsert phase."""

    created: int = 0
    updated: int = 0
    unchanged: int = 0
    deactivated: int = 0
    errored: int = 0


# Attribute types that bear choice options. Other types (TEXT, NUMERIC, etc.)
# never produce ChoiceSets.
_CHOICE_TYPES = {"LIST", "MLIST", "TREE"}


def build_choice_sets(
    *,
    dm: dict,
    cm: dict | None = None,
    ca_uuid: str,
    cm_variant_mode: str = "split",
) -> list[ChoiceSet]:
    """Walk a SMART data model and emit one shared ChoiceSet per choice attribute.

    Choice lists are shared across all event types built from one datamodel
    sync: the field name hashes the (CA, CM) scope, not the event type, so an
    attribute reused by many categories yields a single ChoiceSet that every
    event type's schema references (see ``derive_shared_choice_field``). Does
    not produce event types; only the choices plan.

    ``cm_variant_mode="consolidate"`` additionally emits one per-variant-group
    discriminator ChoiceSet (options = the variant displays); discriminators
    stay keyed per event type since their options are the variant identities.
    """
    source = cm if cm else dm
    cats = parse_obj_as(list[Category], source.get("categories") or [])
    cat_paths = [cat.path for cat in cats]
    attributes = parse_obj_as(list[Attribute], dm.get("attributes") or [])
    attribute_configs = cm.get("attributes") if cm else None
    scope_key = choice_scope_key(ca_uuid=ca_uuid, cm=cm)

    # Collect attribute keys referenced by active categories, in first-seen
    # order so output stays deterministic. With a CM every category is active;
    # without one, only active leaf categories count and they inherit parent
    # attributes.
    attr_keys: dict[str, None] = {}
    for cat in cats:
        leaf_attrs = list(cat.attributes or [])
        if not cm:
            is_leaf = _is_leaf_node(cat_paths, cat.path)
            if not (cat.is_active and is_leaf):
                continue
            leaf_attrs.extend(_inherited_attributes(cats, (cat.path or "").split(".")))
        for cat_attr in leaf_attrs:
            attr_keys.setdefault(cat_attr.key, None)

    result: list[ChoiceSet] = []
    for key in attr_keys:
        cs = _choice_set_for_attr(
            key=key,
            attributes=attributes,
            attribute_configs=attribute_configs,
            field=derive_shared_choice_field(scope_key, key),
        )
        if cs is not None:
            result.append(cs)

    if cm and cm_variant_mode == "consolidate":
        # Group CM categories by hkeyPath; each variant group (>1) gets a
        # discriminator ChoiceSet whose options are the variant displays.
        disc_groups: dict[str, list[dict]] = {}
        for c in cm.get("categories") or []:
            key = c.get("hkeyPath") or c.get("path") or ""
            disc_groups.setdefault(key, []).append(c)
        for disc_hkey, members in disc_groups.items():
            if len(members) < 2:
                continue
            value = event_type_value_for(
                category_path=disc_hkey, ca_uuid=ca_uuid, cm=cm
            )
            field = derive_choice_field(value, "variant")
            options = tuple(
                ChoiceOption(
                    value=_discriminator_option_value(
                        display=m.get("display", ""),
                        node_id=m.get("id"),
                    ),
                    display=_shorten_display(m.get("display", "")),
                    is_active=True,
                )
                for m in members
            )
            result.append(ChoiceSet(field=field, options=options))

    return result


def _choice_set_for_attr(
    *,
    key: str,
    attributes: list[Attribute],
    attribute_configs: list | None,
    field: str,
) -> ChoiceSet | None:
    """Build the ChoiceSet for one attribute key, or None if it bears no choices."""
    attribute = next((a for a in attributes if a.key == key), None)
    if attribute is None or attribute.type not in _CHOICE_TYPES:
        return None
    options = list(attribute.options or [])
    if not options:
        return None

    options_cfg = _options_config_for(attribute_configs, key)
    if options_cfg is not None:
        choice_options = _options_from_cm_config(options, options_cfg)
    else:
        if attribute.type == "TREE":
            options = _leaf_options(options)
        choice_options = tuple(
            ChoiceOption(
                value=_shorten_value(sanitize_choice_value(o.key)),
                display=_shorten_display(o.display),
                is_active=True,
            )
            for o in options
        )

    if not choice_options:
        return None
    return ChoiceSet(field=field, options=choice_options)


def _is_leaf_node(node_paths: list[str], cur_node: str) -> bool:
    prefix = f"{cur_node}."
    return not any(p.startswith(prefix) for p in node_paths)


def _inherited_attributes(
    cats: list[Category], path_components: list[str]
) -> list[CategoryAttribute]:
    inherited: list[CategoryAttribute] = []
    parent_path = ""
    for component in path_components[:-1]:
        parent_path = component if not parent_path else f"{parent_path}.{component}"
        parent_cat = next((c for c in cats if c.path == parent_path), None)
        if parent_cat:
            inherited.extend(parent_cat.attributes)
    return inherited


def _options_config_for(attribute_configs: list | None, key: str) -> list | None:
    if not attribute_configs:
        return None
    cfg = next((c for c in attribute_configs if c.get("key") == key), None)
    return cfg.get("options") if cfg else None


def _options_from_cm_config(
    options: list, options_config: list
) -> tuple[ChoiceOption, ...]:
    """Build ChoiceOptions in CM order. Options the CM marks isActive=False
    appear with is_active=False; options the CM omits entirely are dropped."""
    by_key = {o.key: o for o in options}
    result: list[ChoiceOption] = []
    for opt_cfg in options_config:
        key = opt_cfg.get("key")
        if not key:
            continue
        original = by_key.get(key)
        if not original:
            logger.warning("CM references unknown option key %s", key)
            continue
        result.append(
            ChoiceOption(
                value=_shorten_value(sanitize_choice_value(original.key)),
                display=_shorten_display(original.display),
                is_active=bool(opt_cfg.get("isActive")),
            )
        )
    return tuple(result)


def _leaf_options(options: list) -> list:
    """For TREE option sets: keep only leaves (no children)."""
    keys = [o.key for o in options]
    return [o for o in options if _is_leaf_node(keys, o.key)]


# ER's Choice DB model is shared across content types; for event types we
# always POST with model="activity.event" (the serializer default).
_CHOICE_MODEL = "activity.event"

# Path prefix for the choices REST endpoint. Versionless in ER.
_CHOICES_PATH = "choices"


def upsert_choices(
    *,
    er_client,
    choice_sets: list[ChoiceSet],
) -> ChoicesStats:
    """Upsert each ChoiceSet against ER's Choices API.

    Returns a ChoicesStats counter dataclass. Per-option HTTP failures are
    logged and counted (no raise) — per-set processing is independent, an
    error in one ChoiceSet does not block subsequent sets.

    Raises:
        ValueError: when the same ``ChoiceSet.field`` appears twice in
            ``choice_sets`` with different options. This is a builder bug;
            two ChoiceSets with the same field MUST have identical options.
    """
    stats = ChoicesStats()
    seen_fields: dict[str, ChoiceSet] = {}

    total = len(choice_sets)
    logger.info("Upserting %d choice set(s)", total)

    for idx, cs in enumerate(choice_sets, start=1):
        # Deduplicate: same field, identical options is fine; same field,
        # different options is a builder bug (raises ValueError, see docstring).
        if cs.field in seen_fields:
            if seen_fields[cs.field].options != cs.options:
                raise ValueError(
                    f"ChoiceSet field {cs.field!r} appears twice with "
                    f"different options; this is a builder bug."
                )
            logger.debug(
                "Choice set %d/%d field=%s (duplicate, skipping)",
                idx,
                total,
                cs.field,
            )
            continue
        seen_fields[cs.field] = cs

        logger.info(
            "Choice set %d/%d field=%s (%d options)",
            idx,
            total,
            cs.field,
            len(cs.options),
        )
        try:
            _upsert_one_set(er_client=er_client, cs=cs, stats=stats)
        except Exception:
            # Unexpected error escaping _upsert_one_set (per-option HTTP errors
            # are already caught and counted inside it). Count as one failed
            # set rather than len(cs.options): the per-option counters may
            # already reflect some succeeded options before the catastrophic
            # exception, so adding len(options) would over-count.
            logger.exception(
                "Failed to upsert ChoiceSet",
                extra=dict(field=cs.field),
            )
            stats.errored += 1

    logger.info(
        "Choices done: created=%d updated=%d unchanged=%d deactivated=%d errored=%d",
        stats.created,
        stats.updated,
        stats.unchanged,
        stats.deactivated,
        stats.errored,
    )
    return stats


def _upsert_one_set(*, er_client, cs: ChoiceSet, stats: ChoicesStats) -> None:
    existing = _fetch_existing(er_client=er_client, field=cs.field)
    existing_by_value: dict[str, dict] = {r["value"]: r for r in existing}
    planned_values: set[str] = set()

    for ordernum, planned in enumerate(cs.options):
        planned_values.add(planned.value)
        existing_record = existing_by_value.get(planned.value)
        if existing_record is None:
            _create_choice(
                er_client=er_client,
                cs_field=cs.field,
                option=planned,
                ordernum=ordernum,
                stats=stats,
            )
        else:
            _maybe_patch_choice(
                er_client=er_client,
                existing=existing_record,
                planned=planned,
                ordernum=ordernum,
                stats=stats,
            )

    # Orphan handling: active records not in the plan get soft-deactivated.
    for record in existing:
        if record["value"] in planned_values:
            continue
        if not record.get("is_active"):
            continue
        try:
            from .synchronizer import _retry  # local import avoids circular dependency

            _retry(
                er_client._patch,
                path=f"{_CHOICES_PATH}/{record['id']}",
                payload={"is_active": False},
            )
            stats.deactivated += 1
        except Exception as e:
            logger.exception(
                "Failed to deactivate orphan choice",
                extra=dict(id=record.get("id"), error=str(e)),
            )
            stats.errored += 1


def _fetch_existing(*, er_client, field: str) -> list[dict]:
    """List all existing Choice records for (model=activity.event, field=...).

    ER's choices endpoint uses django-filter's ``AllValuesMultipleFilter`` on
    ``field=``, which validates the value against the set of `field` values
    that already exist in the database. On a fresh tenant where no Choice
    has our derived field name yet, the filter returns 400 — we interpret
    that as "no existing records for this field" and return an empty list.

    Also passes ``max_retries=0`` to skip ERClient's default 5-retry loop;
    400s from missing-field filtering don't fix themselves on retry, and
    upserts have their own _retry wrapper for transient failures.
    """
    from erclient.er_errors import ERClientException

    try:
        page = er_client._get(
            path=_CHOICES_PATH,
            params={
                "model": _CHOICE_MODEL,
                "field": field,
                "include_inactive": True,
                "page_size": 200,
            },
            max_retries=0,
        )
    except ERClientException as e:
        if "is not one of the available choices" in str(e):
            return []
        raise

    results: list[dict] = []
    while True:
        if isinstance(page, dict) and "results" in page:
            results.extend(page["results"])
            next_url = page.get("next")
            if not next_url:
                break
            page = er_client._get(path=next_url, max_retries=0)
        elif isinstance(page, list):
            results.extend(page)
            break
        else:
            break
    return results


def _create_choice(
    *,
    er_client,
    cs_field: str,
    option: ChoiceOption,
    ordernum: int,
    stats: ChoicesStats,
) -> None:
    payload = {
        "model": _CHOICE_MODEL,
        "field": cs_field,
        "value": option.value,
        "display": option.display,
        "ordernum": ordernum,
        "is_active": option.is_active,
    }
    try:
        from .synchronizer import _retry  # local import avoids circular dependency

        _retry(
            er_client._post,
            path=_CHOICES_PATH,
            payload=payload,
        )
        stats.created += 1
    except Exception as e:
        # ER's choices table has a varchar(100) constraint on at least one
        # column. Surface field lengths so a "value too long" 500 names the
        # offender directly. Keep logger.exception for the traceback —
        # ERClientException wrapping details are often diagnostic. Include
        # error=str(e) in extra= to stay consistent with the PATCH and
        # deactivation handlers (lines 444 and 584) for log aggregators.
        logger.exception(
            "Failed to POST choice: field=%r (len=%d) value=%r (len=%d) "
            "display=%r (len=%d)",
            cs_field,
            len(cs_field or ""),
            option.value,
            len(option.value or ""),
            option.display,
            len(option.display or ""),
            extra=dict(
                field=cs_field,
                value=option.value,
                display=option.display,
                error=str(e),
            ),
        )
        stats.errored += 1


def _maybe_patch_choice(
    *,
    er_client,
    existing: dict,
    planned: ChoiceOption,
    ordernum: int,
    stats: ChoicesStats,
) -> None:
    changes: dict = {}
    if existing.get("display") != planned.display:
        changes["display"] = planned.display
    if existing.get("ordernum") != ordernum:
        changes["ordernum"] = ordernum
    if existing.get("is_active") != planned.is_active:
        changes["is_active"] = planned.is_active

    if not changes:
        stats.unchanged += 1
        return

    is_deactivation = (
        "is_active" in changes
        and existing.get("is_active") is True
        and planned.is_active is False
    )

    path = f"{_CHOICES_PATH}/{existing['id']}"
    try:
        from .synchronizer import _retry  # local import avoids circular dependency

        _retry(
            er_client._patch,
            path=path,
            payload=changes,
        )
    except Exception as e:
        logger.exception(
            "Failed to PATCH choice",
            extra=dict(id=existing.get("id"), error=str(e)),
        )
        stats.errored += 1
        return

    if is_deactivation:
        stats.deactivated += 1
    else:
        stats.updated += 1
