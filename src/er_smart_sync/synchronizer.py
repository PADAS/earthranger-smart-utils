import json
import logging
import pathlib
import re
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone

from erclient import ERClient
from erclient.er_errors import (
    ERClientBadCredentials,
    ERClientNotFound,
    ERClientPermissionDenied,
)
from gundi_core.schemas import EREvent, ERObservation, ERPatrol, ERSubject
from gundi_core.schemas.v1 import StreamPrefixEnum
from packaging import version
from pydantic import parse_obj_as
from smartconnect import SmartClient
from smartconnect.er_sync_utils import (
    EREventType,
    er_event_type_schemas_equal,
    er_subjects_equal,
    get_subjects_from_patrol_data_model,
)

from .choices import build_choice_sets, upsert_choices
from .config import SyncConfig
from .defaults import (
    JsonFileStateStore,
    LocalFileStorage,
    NullPublisher,
    NullTracing,
)
from .smart_to_er import build_event_types
from .smart_to_er_v2 import ERV2EventType, build_event_types_v2
from .utils import unicode_to_ascii

logger = logging.getLogger(__name__)


# Exceptions that should never be retried — they reflect permanent client errors.
_NON_RETRIABLE_ER_ERRORS = (
    ERClientNotFound,
    ERClientBadCredentials,
    ERClientPermissionDenied,
)


def _retry(fn, *, max_attempts: int = 4, base_delay: float = 1.0, **kwargs):
    """Call fn(**kwargs) with exponential-backoff retries on transient errors.

    Retries any exception except those in _NON_RETRIABLE_ER_ERRORS. We retry
    broadly because erclient maps several transient failure modes (5xx, gateway
    errors, connection errors) onto distinct exceptions, and the cost of an
    unnecessary retry is bounded by max_attempts.
    """
    last_exc = None
    for attempt in range(1, max_attempts + 1):
        try:
            return fn(**kwargs)
        except _NON_RETRIABLE_ER_ERRORS:
            raise
        except Exception as e:
            last_exc = e
            if attempt == max_attempts:
                raise
            delay = base_delay * (2 ** (attempt - 1))
            logger.warning(
                "ER call failed (attempt %d/%d), retrying in %.1fs: %s",
                attempt,
                max_attempts,
                delay,
                e,
            )
            time.sleep(delay)
    raise last_exc  # pragma: no cover — unreachable


class ERSmartSynchronizer:
    """Synchronizes data between SMART Connect and EarthRanger.

    Supports three flows:
    1. Datamodel sync (SMART -> ER): Push event categories and event types
    2. Event sync (ER -> SMART): Forward events via message publishing
    3. Patrol sync (ER -> SMART): Forward patrols via message publishing
    """

    def __init__(
        self,
        config: SyncConfig,
        *,
        er_client: ERClient | None = None,
        smart_client: SmartClient | None = None,
        publisher=None,
        file_storage=None,
        state_store=None,
        tracing=None,
        observations_topic: str = "",
    ):
        self.config = config

        self.publisher = publisher or NullPublisher()
        self.file_storage = file_storage or LocalFileStorage()
        self.state_store = state_store or JsonFileStateStore()
        self.tracing = tracing or NullTracing()
        self.observations_topic = observations_topic
        self._er_event_categories_cache: list | None = None
        self._er_event_types_cache: list | None = None
        # Datamodel-sync mode: "both", "create-only", or "update-only".
        # Drives whether create_or_update_er_event_types skips creates or updates.
        self.sync_mode: str = "both"
        self.skip_choices: bool = False
        self._event_type_version: str = config.earthranger.event_type_version
        if self._event_type_version == "v2":
            logger.warning(
                "event_type_version='v2' selected, but the v2 builder is "
                "experimental and known to produce schemas that fail ER's v2 "
                "meta-schema validation (every CHOICE_LIST attribute, every "
                "field's missing `deprecated` flag, wrong UI types, etc.). "
                "Expect datamodel sync to fail. See "
                "docs/superpowers/specs/ for the current state of v2 work."
            )
        # Datamodel-sync run summary counters, populated by create_or_update_er_event_types.
        self.datamodel_stats: dict[str, int] = {
            "categories_created": 0,
            "categories_existing": 0,
            "event_types_created": 0,
            "event_types_updated": 0,
            "event_types_unchanged": 0,
            "event_types_skipped_by_mode": 0,
            "event_types_skipped_by_conflict": 0,
            "event_types_errored": 0,
            "choices_created": 0,
            "choices_updated": 0,
            "choices_unchanged": 0,
            "choices_deactivated": 0,
            "choices_errored": 0,
        }

        if er_client:
            self.er_client = er_client
        else:
            self.er_client = ERClient(
                service_root=config.earthranger.endpoint,
                username=config.earthranger.login,
                password=config.earthranger.password,
                token=config.earthranger.token,
                client_id=config.earthranger.client_id,
                provider_key=config.smart.provider_key,
            )

        if smart_client:
            self.smart_client = smart_client
        else:
            self.smart_client = SmartClient(
                api=config.smart.endpoint,
                username=config.smart.login,
                password=config.smart.password,
                version=config.smart.version,
                use_language_code=config.smart.use_language_code,
                read_timeout=300.0,
                connect_timeout=10.0,
            )

    # ──────────────────────────────────────────────
    # Flow 1: Datamodel sync (SMART -> EarthRanger)
    # ──────────────────────────────────────────────

    def synchronize_datamodel(self) -> None:
        """Synchronize SMART Connect data models to EarthRanger.

        Iterates through all conservation areas (CAs) and configurable models
        configured in ``self.config.smart`` and pushes their data models to the
        corresponding EarthRanger instance.
        """
        ca_uuids = self.config.smart.ca_uuids
        total = len(ca_uuids)
        # Snapshot ER state once per run. push_smart_ca_datamodel_to_earthranger
        # updates these in-place when it creates new categories so each CA sees
        # the prior CA's writes.
        self._er_event_categories_cache = self.er_client.get_event_categories()
        self._er_event_types_cache = self.er_client.get_event_types(
            include_inactive=True,
            include_schema=True,
            version=self._event_type_version,
        )
        try:
            for idx, ca_uuid in enumerate(ca_uuids, start=1):
                logger.info("Syncing CA %d/%d (%s)", idx, total, ca_uuid)
                ca = self.smart_client.get_conservation_area(ca_uuid=ca_uuid)

                self.push_smart_datamodel_to_earthranger(smart_ca_uuid=ca_uuid, ca=ca)

                configurable_model_list = (
                    self.config.smart.configurable_models_lists.get(ca_uuid)
                )
                if not configurable_model_list:
                    logger.info(
                        "No configurable models found for CA %s (%s)",
                        ca.label,
                        ca_uuid,
                    )
                    continue

                for cm in configurable_model_list:
                    cm_uuid = cm.get("uuid")
                    if cm.get("use_with_earth_ranger", True):
                        logger.info(
                            "Pushing Configurable Model %s (%s) to EarthRanger",
                            cm.get("name"),
                            cm_uuid,
                        )
                        self.push_smart_datamodel_to_earthranger(
                            smart_ca_uuid=ca_uuid, ca=ca, smart_cm_uuid=cm_uuid
                        )
                    else:
                        logger.info(
                            "Configurable Model %s (%s) is not enabled for use with EarthRanger",
                            cm.get("name"),
                            cm_uuid,
                        )
        finally:
            self._er_event_categories_cache = None
            self._er_event_types_cache = None
            logger.info(
                "Datamodel sync summary: %s",
                ", ".join(f"{k}={v}" for k, v in self.datamodel_stats.items()),
            )

    def push_smart_datamodel_to_earthranger(
        self, *, smart_ca_uuid=None, ca=None, smart_cm_uuid=None
    ) -> None:
        dm = self.smart_client.get_data_model(ca_uuid=smart_ca_uuid)

        if smart_cm_uuid:
            cm = self.smart_client.get_configurable_data_model(cm_uuid=smart_cm_uuid)
        else:
            cm = None

        return self.push_smart_ca_datamodel_to_earthranger(
            dm=dm, smart_ca_uuid=smart_ca_uuid, ca_label=ca.label, cm=cm
        )

    def push_smart_ca_datamodel_to_earthranger(
        self, *, dm=None, smart_ca_uuid: str | None = None, ca_label=None, cm=None
    ) -> None:
        if not dm:
            raise ValueError("dm is required")
        if not smart_ca_uuid:
            raise ValueError("smart_ca_uuid is required")

        dm_dict = dm.export_as_dict()
        cdm_dict = cm.export_as_dict() if cm else None

        ca_identifier = self.get_identifier_from_ca_label(ca_label)

        if self._event_type_version == "v2" and not self.skip_choices:
            choice_sets = build_choice_sets(
                dm=dm_dict,
                cm=cdm_dict,
                ca_uuid=smart_ca_uuid,
            )
            choices_stats = upsert_choices(
                er_client=self.er_client,
                choice_sets=choice_sets,
            )
            self.datamodel_stats["choices_created"] += choices_stats.created
            self.datamodel_stats["choices_updated"] += choices_stats.updated
            self.datamodel_stats["choices_unchanged"] += choices_stats.unchanged
            self.datamodel_stats["choices_deactivated"] += choices_stats.deactivated
            self.datamodel_stats["choices_errored"] += choices_stats.errored

            if choices_stats.errored > 0:
                logger.warning(
                    "Aborting event-type push for CA %s: %d choice "
                    "operations failed. Investigate the choice errors above "
                    "before re-running.",
                    smart_ca_uuid,
                    choices_stats.errored,
                    extra=dict(
                        ca_uuid=smart_ca_uuid,
                        choices_errored=choices_stats.errored,
                    ),
                )
                return

        if self._event_type_version == "v2":
            event_types = build_event_types_v2(
                dm=dm_dict,
                cm=cdm_dict,
                ca_uuid=smart_ca_uuid,
                ca_identifier=ca_identifier,
                choices_base_url=self.config.earthranger.choices_base_url,
            )
        else:
            event_types = build_event_types(
                dm=dm_dict,
                cm=cdm_dict,
                ca_uuid=smart_ca_uuid,
                ca_identifier=ca_identifier,
            )

        if self._er_event_categories_cache is not None:
            existing_event_categories = self._er_event_categories_cache
        else:
            existing_event_categories = self.er_client.get_event_categories()
        event_category_value = self.calculate_event_category_value(
            ca_label=ca_identifier,
            cm_label=getattr(cm, "_name", None),
        )

        cm_name = getattr(cm, "_name", None) if cm else None
        event_category_display = (
            f"{ca_identifier} {cm_name}" if cm_name else ca_identifier
        )
        event_category = next(
            (
                x
                for x in existing_event_categories
                if x.get("value") == event_category_value
            ),
            None,
        )

        if not event_category:
            if self.sync_mode == "update-only":
                logger.info(
                    "update-only mode: skipping creation of missing category %s",
                    event_category_value,
                )
                self.datamodel_stats["event_types_skipped_by_mode"] += len(event_types)
                return
            logger.info(
                "Event Category not found in destination ER, creating now ...",
                extra=dict(value=event_category_value, display=event_category_display),
            )

            event_category = dict(
                value=event_category_value, display=event_category_display
            )
            try:
                _retry(self.er_client.post_event_category, data=event_category)
                logger.info(
                    "Successfully created event category",
                    extra=dict(
                        value=event_category_value,
                        display=event_category_display,
                    ),
                )
                self.datamodel_stats["categories_created"] += 1
                if self._er_event_categories_cache is not None:
                    self._er_event_categories_cache.append(event_category)
            except Exception as e:
                error_message = str(e)
                if "duplicate key value violates unique constraint" in error_message:
                    logger.warning(
                        "Event category already exists; re-fetching and continuing",
                        extra=dict(
                            value=event_category_value,
                            display=event_category_display,
                            error=error_message,
                        ),
                    )
                    existing_event_categories = self.er_client.get_event_categories()
                    refetched = next(
                        (
                            x
                            for x in existing_event_categories
                            if x.get("value") == event_category_value
                        ),
                        None,
                    )
                    event_category = refetched or event_category
                    self.datamodel_stats["categories_existing"] += 1
                else:
                    logger.error(
                        "Failed to create event category for some unknown reason. Will raise an error and stop the task.",
                        extra=dict(
                            value=event_category_value,
                            display=event_category_display,
                            error=error_message,
                        ),
                    )
                    raise
        else:
            self.datamodel_stats["categories_existing"] += 1

        self.create_or_update_er_event_types(
            event_category=event_category, event_types=event_types
        )
        logger.info(
            f"Finished syncing {len(event_types)} event_types for event_category {event_category.get('display')}"
        )

    @staticmethod
    def calculate_event_category_value(
        ca_label: str, cm_label: str | None = None
    ) -> str:
        if not ca_label:
            raise ValueError("ca_label is required")

        translation = str.maketrans(
            {
                "[": "",
                "]": "",
                " ": "_",
                "-": "_",
                "/": "_",
                "(": "",
                ")": "",
                "'": "",
                '"': "",
                ".": "",
                ",": "",
                ":": "",
                ";": "",
                "&": "",
                "$": "",
                "#": "",
                "@": "",
                "!": "",
                "?": "",
                "%": "",
                "*": "",
            }
        )

        calculated_value = ca_label.translate(translation).lower()
        if cm_label:
            calculated_value += "_" + cm_label.translate(translation).lower()

        return unicode_to_ascii(calculated_value)

    @staticmethod
    def get_identifier_from_ca_label(ca_label: str | None = "") -> str:
        """Extract the bracketed identifier from a CA label.

        Expects a string like ``"Some Name [SONM]"`` and returns ``"SONM"``.
        Returns "" for None, empty, or unbracketed labels.
        """
        if not ca_label:
            logger.warning("Unable to get identifier from empty ca_label")
            return ""
        match = re.findall(r"\[(.*?)\]", ca_label)
        if match:
            return match[-1]
        logger.warning(f"Unable to get identifier from ca_label {ca_label}")
        return ""

    def _event_type_needs_update(
        self, event_type: EREventType | ERV2EventType, existing_er_event_type: dict
    ) -> bool:
        if event_type.is_active != existing_er_event_type.get(
            "is_active"
        ) or event_type.display != existing_er_event_type.get("display"):
            return True

        if event_type.is_active and event_type.event_schema:
            if self._event_type_version == "v2":
                new_schema = event_type.event_schema
                existing_schema = existing_er_event_type.get("schema") or {}
                if not isinstance(existing_schema, dict):
                    # Defensive: shouldn't happen on v2 endpoint but guard anyway.
                    existing_schema = {}
                if new_schema != existing_schema:
                    return True
            else:
                new_schema = json.loads(event_type.event_schema).get("schema")
                existing_schema = json.loads(
                    existing_er_event_type.get("schema", "{}")
                ).get("schema")
                if not er_event_type_schemas_equal(new_schema, existing_schema):
                    return True

        return False

    def _create_event_type(self, event_type: EREventType | ERV2EventType) -> str:
        """Create an event type; recover by patching on duplicate-value conflicts.

        ER enforces a unique constraint on `value` per tenant (across all
        categories). If our local view of existing event types missed one —
        e.g. because get_event_types didn't return everything — the POST will
        fail with a duplicate-key error. In that case, re-fetch and patch.

        Returns one of "created", "patched", "skipped", or "errored".
        """
        logger.info(
            "Creating ER event type %r (%s)",
            event_type.display,
            event_type.value,
            extra=dict(value=event_type.value, category=event_type.category),
        )
        try:
            _retry(
                self.er_client.post_event_type,
                event_type=event_type.dict(by_alias=True, exclude_none=True),
                version=self._event_type_version,
            )
            return "created"
        except Exception as e:
            if "duplicate key" in str(e) or "already exists" in str(e):
                if self._event_type_version == "v2":
                    logger.warning(
                        "Event type value %r already exists in this tenant "
                        "(possibly under v1). Skipping; run ER's "
                        "POST /api/v2.0/activity/eventtypes/migrate/ to "
                        "convert legacy v1 records before retrying.",
                        event_type.value,
                        extra=dict(value=event_type.value),
                    )
                    return "skipped"
                logger.warning(
                    "post_event_type hit existing record; patching instead",
                    extra=dict(value=event_type.value),
                )
                existing = self._find_existing_event_type(event_type.value)
                if existing is not None:
                    self._update_event_type(event_type, existing)
                    return "patched"
            logger.exception(
                "Error occurred during er_client.post_event_type",
                extra=dict(
                    event_type=event_type.dict(by_alias=True, exclude_none=True),
                    error=str(e),
                ),
            )
            return "errored"

    def _find_existing_event_type(self, value: str) -> dict | None:
        """Look up an event type by value, bypassing any cache.

        Used to recover from POST conflicts where the local cache of existing
        types is incomplete.
        """
        try:
            fresh = self.er_client.get_event_types(
                include_inactive=True,
                include_schema=True,
                version=self._event_type_version,
            )
        except Exception:
            logger.exception("Failed to refetch event types after conflict")
            return None
        if self._er_event_types_cache is not None:
            self._er_event_types_cache = fresh
        return next((x for x in fresh if x.get("value") == value), None)

    def _update_event_type(
        self, event_type: EREventType | ERV2EventType, existing_er_event_type: dict
    ) -> None:
        logger.info(
            "Updating ER event type %r (%s)",
            event_type.display,
            event_type.value,
            extra=dict(value=event_type.value),
        )
        event_type.id = existing_er_event_type.get("id")
        try:
            _retry(
                self.er_client.patch_event_type,
                event_type=event_type.dict(by_alias=True, exclude_none=True),
                version=self._event_type_version,
            )
        except Exception as e:
            logger.exception(
                "Error occurred during er_client.patch_event_type",
                extra=dict(
                    event_type=event_type.dict(by_alias=True, exclude_none=True),
                    error=str(e),
                ),
            )

    def create_or_update_er_event_types(
        self,
        *,
        event_category: dict | None = None,
        event_types: list[EREventType] | list[ERV2EventType] | None = None,
    ) -> None:
        if self._er_event_types_cache is not None:
            existing_event_types = self._er_event_types_cache
        else:
            existing_event_types = self.er_client.get_event_types(
                include_inactive=True,
                include_schema=True,
                version=self._event_type_version,
            )
        logger.debug(
            "Fetched %d existing event types from ER for category %s",
            len(existing_event_types) if existing_event_types else 0,
            event_category.get("value") if event_category else None,
        )

        for event_type in event_types or []:
            try:
                event_type.category = event_category.get("value")
                logger.debug(
                    "Checking event type %r (%s)",
                    event_type.display,
                    event_type.value,
                )

                # Match on value alone — ER's unique constraint on event type
                # value is per tenant, not per (value, category). Trying to
                # POST a value that already exists under a different category
                # would 500 on the unique constraint.
                existing_er_event_type = next(
                    (
                        x
                        for x in existing_event_types
                        if x.get("value") == event_type.value
                    ),
                    None,
                )

                if not existing_er_event_type:
                    if self.sync_mode == "update-only":
                        self.datamodel_stats["event_types_skipped_by_mode"] += 1
                        continue
                    outcome = self._create_event_type(event_type)
                    if outcome == "created":
                        self.datamodel_stats["event_types_created"] += 1
                    elif outcome == "patched":
                        # Fell back to patch because of a duplicate-value race on v1.
                        self.datamodel_stats["event_types_updated"] += 1
                    elif outcome == "skipped":
                        self.datamodel_stats["event_types_skipped_by_conflict"] += 1
                    else:
                        self.datamodel_stats["event_types_errored"] += 1
                elif self._event_type_needs_update(event_type, existing_er_event_type):
                    if self.sync_mode == "create-only":
                        self.datamodel_stats["event_types_skipped_by_mode"] += 1
                        continue
                    self._update_event_type(event_type, existing_er_event_type)
                    self.datamodel_stats["event_types_updated"] += 1
                else:
                    logger.debug(
                        "Unchanged: %r (%s)",
                        event_type.display,
                        event_type.value,
                    )
                    self.datamodel_stats["event_types_unchanged"] += 1
            except Exception as e:
                self.datamodel_stats["event_types_errored"] += 1
                logger.exception(
                    "Unexpected error occurred while syncing event type",
                    extra=dict(
                        event_type=event_type.dict(by_alias=True, exclude_none=True),
                        error=str(e),
                    ),
                )

    # ──────────────────────────────────────────────
    # Flow 2: Event sync (EarthRanger -> SMART)
    # ──────────────────────────────────────────────

    def synchronize_er_events(self) -> None:
        """Retrieve events from EarthRanger and publish them for routing to SMART.

        Checkpoints state after each successfully published event so a crash
        mid-loop doesn't force re-processing of already-published events.
        """
        integration_id = self.config.earthranger.id

        i_state = self.state_store.get_last_poll(integration_id)

        event_last_poll_at = i_state.event_last_poll_at or datetime.now(
            tz=timezone.utc
        ) - timedelta(days=7)
        current_time = datetime.now(tz=timezone.utc)

        events = parse_obj_as(
            list[EREvent],
            self.er_client.get_events(updated_since=event_last_poll_at),
        )
        # Process in ascending updated_at order so per-event checkpointing
        # advances monotonically.
        events.sort(key=lambda e: e.updated_at)

        total = len(events)
        logger.info(
            f"Read {total} events from {self.config.earthranger.endpoint} (id={integration_id})"
        )
        published = 0
        skipped_patrol = 0

        for idx, event in enumerate(events, start=1):
            if total >= 20 and idx % max(1, total // 10) == 0:
                logger.info(
                    "Event sync progress: %d/%d (%.0f%%)",
                    idx,
                    total,
                    100 * idx / total,
                )
            with self.tracing.start_span(
                "gundi_er_smart_sync.process_event", kind="producer"
            ) as current_span:
                if not event.patrols:
                    event.integration_id = integration_id
                    event.device_id = event.id
                    if version.parse(self.smart_client.version) < version.parse(
                        "7.5.3"
                    ):
                        try:
                            self.update_event_with_smart_data(event=event)
                        except Exception as e:
                            error_msg = f"Error patching event {event.serial_number} ({event.id}) with smart_observation_uuid, event not processed: {e}"
                            current_span.set_attribute("error", error_msg)
                            current_span.add_event(
                                "gundi_er_smart_sync.error_updating_event"
                            )
                            logger.exception(
                                error_msg,
                                extra=dict(
                                    event_id=event.id,
                                    event_title=event.title,
                                    event_serial_number=event.serial_number,
                                ),
                            )
                    self._download_files_parallel(
                        event.files,
                        context="event",
                        context_extras=dict(
                            event_serial_number=event.serial_number,
                            event_id=event.id,
                        ),
                    )
                    logger.info(
                        f"Publishing observation for event {event.serial_number}",
                        extra=dict(
                            event_id=event.id,
                            event_title=event.title,
                            event_serial_number=event.serial_number,
                        ),
                    )
                    with self.tracing.start_span(
                        "gundi_api.send_event_to_routing", kind="producer"
                    ) as subspan:
                        tracing_context = json.dumps(
                            self.tracing.build_context_headers(),
                            default=str,
                        )
                        self.publisher.publish(
                            topic=self.observations_topic,
                            data=json.loads(event.json()),
                            extra={
                                "observation_type": StreamPrefixEnum.earthranger_event.value,
                                "gundi_version": "v1",
                                "tracing_context": tracing_context,
                            },
                        )
                        subspan.add_event("gundi_er_smart_sync.event_sent_to_routing")
                        i_state.event_last_poll_at = event.updated_at
                        self.state_store.set_last_poll(integration_id, i_state)
                        published += 1
                else:
                    current_span.set_attribute("is_patrol_event", True)
                    current_span.add_event(
                        "gundi_er_smart_sync.skipped_event_associated_to_patrol"
                    )
                    logger.info(
                        f"Skipping event {event.serial_number} because it is associated to a patrol"
                    )
                    skipped_patrol += 1

        # Final checkpoint advances the high-water mark past any patrol-only
        # events at the tail of the window so they aren't re-fetched next run.
        i_state.event_last_poll_at = current_time
        self.state_store.set_last_poll(integration_id, i_state)
        logger.info(
            "Event sync summary: %d read, %d published, %d skipped (patrol)",
            total,
            published,
            skipped_patrol,
        )

    def update_event_with_smart_data(self, event) -> None:
        """Add a SMART observation UUID to an ER event if it doesn't have one."""
        if not event.event_details.get("smart_observation_uuid"):
            smart_observation_uuid = uuid.uuid1()
            event.event_details["smart_observation_uuid"] = str(smart_observation_uuid)
            payload = dict(event_details=event.event_details)
            _retry(
                self.er_client.patch_event,
                event_id=str(event.id),
                payload=payload,
            )

    def _download_files_parallel(
        self,
        files: list,
        *,
        context: str,
        context_extras: dict,
        max_workers: int = 4,
    ) -> None:
        """Download a list of files concurrently, logging individual failures."""
        if not files:
            return
        with ThreadPoolExecutor(
            max_workers=min(max_workers, max(1, len(files)))
        ) as pool:
            futures = {pool.submit(self.process_file, file=f): f for f in files}
            for fut in as_completed(futures):
                f = futures[fut]
                try:
                    fut.result()
                except Exception as e:
                    logger.error(
                        f"Failed to download {context} file",
                        extra=dict(
                            **context_extras,
                            file_name=f.get("filename"),
                            error=str(e),
                        ),
                    )

    def process_file(self, file: dict) -> str | None:
        """Download a file from EarthRanger and upload it to file storage."""
        file_extension = pathlib.Path(file.get("filename")).suffix
        file_name = file.get("id") + file_extension

        if self.file_storage.check_exists(file_name=file_name):
            logger.debug(f"File already exists in storage: {file_name}")
            return None

        url = file.get("url")
        response = self.er_client.get_file(url)

        if not response.ok:
            raise RuntimeError(
                f"Failed to download file from {url}: {response.status_code} - {response.reason}"
            )

        uri = self.file_storage.upload(response.content, file_name)
        logger.info(f"Successfully uploaded file to storage: {uri}")
        return uri

    # ──────────────────────────────────────────────
    # Flow 3: Patrol sync (EarthRanger -> SMART)
    # ──────────────────────────────────────────────

    def synchronize_er_patrols(self) -> None:
        """Retrieve patrols from EarthRanger and publish them for routing to SMART."""
        integration_id = self.config.earthranger.id

        i_state = self.state_store.get_last_poll(integration_id)

        lower = i_state.patrol_last_poll_at or datetime.now(
            tz=timezone.utc
        ) - timedelta(days=7)
        upper = datetime.now(tz=timezone.utc)

        FILTER_DATETIME_FORMAT = "%Y-%m-%dT%H:%M:%SZ"

        patrol_filter_spec = {
            "date_range": {
                "lower": lower.strftime(FILTER_DATETIME_FORMAT),
                "upper": upper.strftime(FILTER_DATETIME_FORMAT),
            }
        }
        patrols = parse_obj_as(
            list[ERPatrol],
            self.er_client.get_patrols(filter=json.dumps(patrol_filter_spec)),
        )
        logger.info(
            f"Read {len(patrols)} patrols from integration {self.config.earthranger.endpoint} (id={integration_id})",
            extra=dict(
                lower=lower.strftime(FILTER_DATETIME_FORMAT),
                upper=upper.strftime(FILTER_DATETIME_FORMAT),
            ),
        )

        if len(patrols) > 0:
            self.process_er_patrols(
                patrols=patrols,
                integration_id=integration_id,
                patrol_last_poll_at=lower,
                upper=upper,
            )

        i_state.patrol_last_poll_at = upper
        self.state_store.set_last_poll(integration_id, i_state)

    def _fetch_events_by_id(self, event_ids: list) -> dict[str, EREvent]:
        """Fetch full EREvents for the given ids in a single ER call.

        Falls back to per-id fetches if the batched call returns fewer events
        than expected, which can happen when the ER deployment doesn't accept
        comma-separated `event_ids` filtering.
        """
        if not event_ids:
            return {}

        unique_ids = list({str(eid) for eid in event_ids})
        try:
            events = parse_obj_as(
                list[EREvent],
                self.er_client.get_events(event_ids=",".join(unique_ids)),
            )
        except Exception:
            events = []

        by_id = {str(ev.id): ev for ev in events}
        missing = [eid for eid in unique_ids if eid not in by_id]
        for eid in missing:
            fallback = parse_obj_as(
                list[EREvent],
                self.er_client.get_events(event_ids=eid),
            )
            for ev in fallback:
                by_id[str(ev.id)] = ev
        return by_id

    @staticmethod
    def _patrol_skip_reason(patrol: ERPatrol) -> str | None:
        """Return a human-readable reason to skip the patrol, or None if it's valid.

        A patrol is skipped if any of its segments is missing required fields
        (start_location or leader). We skip the whole patrol because the
        downstream consumer expects a complete patrol object.
        """
        for segment in patrol.patrol_segments:
            if not segment.start_location:
                return "no_start_location"
            if not segment.leader:
                return "no_segment_leader"
        return None

    def process_er_patrols(
        self,
        *,
        patrols: list[ERPatrol] = None,
        integration_id: str = None,
        patrol_last_poll_at: datetime = None,
        upper: datetime = None,
    ) -> None:
        if not patrols:
            raise ValueError("patrols is required")
        if not integration_id:
            raise ValueError("integration_id is required")
        if not patrol_last_poll_at:
            raise ValueError("patrol_last_poll_at is required")
        if not upper:
            raise ValueError("upper is required")

        total = len(patrols)
        published = 0
        skipped = 0
        oversize = 0

        for idx, patrol in enumerate(patrols, start=1):
            if total >= 10 and idx % max(1, total // 10) == 0:
                logger.info(
                    "Patrol sync progress: %d/%d (%.0f%%)",
                    idx,
                    total,
                    100 * idx / total,
                )
            with self.tracing.start_span(
                "gundi_er_smart_sync.process_patrol", kind="producer"
            ) as current_span:
                logger.info(
                    "Beginning processing of ER patrol",
                    extra=dict(
                        patrol_id=patrol.id,
                        patrol_serial_num=patrol.serial_number,
                        patrol_title=patrol.title,
                    ),
                )
                patrol.integration_id = integration_id
                patrol.device_id = patrol.id

                extra_dict = dict(
                    patrol_id=patrol.id,
                    patrol_serial_num=patrol.serial_number,
                    patrol_title=patrol.title,
                )

                skip_reason = self._patrol_skip_reason(patrol)
                if skip_reason:
                    logger.info(
                        f"skipping processing, patrol contains {skip_reason}",
                        extra=extra_dict,
                    )
                    current_span.add_event(
                        f"gundi_er_smart_sync.skipped_patrol_{skip_reason}"
                    )
                    skipped += 1
                    continue

                events_updated_at = []
                updates = patrol.updates
                for seg in patrol.patrol_segments:
                    for update in seg.updates:
                        updates.append(update)
                    for event in seg.events:
                        events_updated_at.append(event.updated_at)
                max_update = max(events_updated_at + [u.time for u in updates])

                self._download_files_parallel(
                    patrol.files,
                    context="patrol",
                    context_extras=dict(
                        patrol_id=patrol.id,
                        patrol_serial_number=patrol.serial_number,
                    ),
                )

                segment_event_ids = [
                    seg_ev.id
                    for segment in patrol.patrol_segments
                    for seg_ev in segment.events
                ]
                events_by_id = self._fetch_events_by_id(segment_event_ids)

                all_event_files = []
                for segment in patrol.patrol_segments:
                    for segment_event in segment.events:
                        full = events_by_id.get(str(segment_event.id))
                        if not full:
                            continue
                        all_event_files.extend(full.files)
                        segment.event_details.append(full)
                self._download_files_parallel(
                    all_event_files,
                    context="event",
                    context_extras=dict(
                        patrol_id=patrol.id,
                        patrol_serial_number=patrol.serial_number,
                    ),
                )

                smart_needs_patch = version.parse(
                    self.smart_client.version
                ) < version.parse("7.5.3")
                for segment in patrol.patrol_segments:
                    if smart_needs_patch:
                        for event in segment.event_details:
                            try:
                                self.update_event_with_smart_data(event=event)
                            except Exception as e:
                                logger.exception(
                                    "Error patching event_type with smart_observation_uuid, event not processed",
                                    extra=dict(
                                        event_id=event.id,
                                        event_title=event.title,
                                        error=str(e),
                                    ),
                                )

                    segment.track_points = parse_obj_as(
                        list[ERObservation],
                        self.er_client.get_subject_observations(
                            subject_id=segment.leader.id,
                            start=patrol_last_poll_at,
                            end=upper,
                        ),
                    )

                    for track_point in segment.track_points:
                        track_point.observation_details = None

                if max_update < patrol_last_poll_at and not any(
                    len(seg.track_points) > 0 for seg in patrol.patrol_segments
                ):
                    logger.info(
                        "skipping processing, patrol doesn't have updates since last poll",
                        extra=extra_dict,
                    )
                    current_span.add_event(
                        "gundi_er_smart_sync.skipped_patrol_no_updates"
                    )
                    skipped += 1
                    continue

                logger.info(
                    "Publishing observation for ER Patrol",
                    extra=extra_dict,
                )
                with self.tracing.start_span(
                    "gundi_api.send_patrol_to_routing", kind="producer"
                ) as subspan:
                    tracing_context = json.dumps(
                        self.tracing.build_context_headers(),
                        default=str,
                    )

                    patrol_data = json.loads(patrol.json())

                    serialized_json = json.dumps(patrol_data)
                    message_size_bytes = len(serialized_json.encode("utf-8"))
                    message_size_mb = message_size_bytes / (1024 * 1024)

                    logger.info(
                        f"Patrol message size: {message_size_bytes:,} bytes ({message_size_mb:.2f} MB)",
                        extra={
                            "patrol_id": patrol.id,
                            "patrol_serial_number": patrol.serial_number,
                            "message_size_bytes": message_size_bytes,
                            "message_size_mb": round(message_size_mb, 2),
                        },
                    )

                    try:
                        self.publisher.publish(
                            topic=self.observations_topic,
                            data=patrol_data,
                            extra={
                                "observation_type": StreamPrefixEnum.earthranger_patrol.value,
                                "gundi_version": "v1",
                                "tracing_context": tracing_context,
                            },
                        )
                        subspan.add_event("gundi_er_smart_sync.patrol_sent_to_routing")
                        published += 1
                    except Exception:
                        logger.exception(
                            f"Patrol {patrol.id} ({patrol.serial_number}) message too large to publish. Will skip it and continue with next patrol.",
                            extra={
                                "patrol_id": patrol.id,
                                "patrol_serial_number": patrol.serial_number,
                                "message_size_bytes": message_size_bytes,
                                "message_size_mb": round(message_size_mb, 2),
                            },
                        )
                        current_span.add_event(
                            "gundi_er_smart_sync.patrol_message_too_large"
                        )
                        oversize += 1

        logger.info(
            "Patrol sync summary: %d read, %d published, %d skipped, %d oversized",
            total,
            published,
            skipped,
            oversize,
        )

    # ──────────────────────────────────────────────
    # Patrol datamodel sync
    # ──────────────────────────────────────────────

    def sync_patrol_datamodel(self) -> None:
        """Synchronize SMART patrol subjects (team members) to EarthRanger."""
        ca_uuids = self.config.smart.ca_uuids
        if not ca_uuids:
            raise ValueError("No conservation areas configured for this integration")
        smart_ca_uuid = ca_uuids[0]
        ca = self.smart_client.get_conservation_area(ca_uuid=smart_ca_uuid)

        patrol_data_model = self.smart_client.download_patrolmodel(
            ca_uuid=smart_ca_uuid
        )
        patrol_subjects = get_subjects_from_patrol_data_model(
            pm=patrol_data_model, ca_uuid=smart_ca_uuid
        )

        existing_subjects = parse_obj_as(list[ERSubject], self.er_client.get_subjects())
        for subject in patrol_subjects:
            smart_member_id = subject.additional.get("smart_member_id")
            existing_subject_match = next(
                (
                    ex_subject
                    for ex_subject in existing_subjects
                    if ex_subject.additional.get("smart_member_id") == smart_member_id
                ),
                None,
            )

            if existing_subject_match:
                subject.id = existing_subject_match.id
                if not er_subjects_equal(subject, existing_subject_match):
                    logger.info(
                        "Subject differs from existing but updates are not implemented",
                        extra=dict(
                            smart_member_id=smart_member_id,
                            subject_name=subject.name,
                        ),
                    )
            else:
                try:
                    ca_identifier = self.get_identifier_from_ca_label(ca.label)
                    if ca_identifier:
                        subject.name = f"{subject.name} ({ca_identifier})"
                    _retry(
                        self.er_client.post_subject,
                        subject=subject.dict(exclude_none=True),
                    )
                except Exception as e:
                    logger.exception(
                        "Error occurred while attempting to create ER subject",
                        extra=dict(
                            subject=subject.dict(exclude_none=True),
                            error=str(e),
                        ),
                    )
