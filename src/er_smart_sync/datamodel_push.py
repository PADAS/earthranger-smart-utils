"""Push a SMART data model (and a configurable model) from XML files into
EarthRanger, as `er-smart-sync datamodel --from-file` does, for callers that
are not the CLI."""

from __future__ import annotations

import uuid as _uuid

from .bundle import BundleError, load_bundle
from .config import EarthRangerConfig, SmartConnectConfig, SyncConfig
from .synchronizer import ERSmartSynchronizer

FILE_BASED_CA_UUID = "smart-ca-import"
DEFAULT_FILE_CM_UUID = "00000000-0000-0000-0000-000000000000"


class PushArgumentError(ValueError):
    """The arguments to `push_datamodel_files` do not go together. Raised
    before anything is written, so a caller can show it as a usage error
    and know nothing on the site changed."""


def push_datamodel_files(
    er_client,
    *,
    endpoint: str,
    datamodel_file: str,
    ca_identifier: str,
    cm_file: str | None = None,
    cm_uuid: str | None = None,
    include_base_datamodel: bool = False,
    language: str = "en",
    bundle_dir: str | None = None,
    event_type_version: str = "v2",
    mode: str = "both",
    skip_choices: bool = False,
    synchronizer: ERSmartSynchronizer | None = None,
) -> dict:
    """Push the data model in `datamodel_file` (and the configurable model in
    `cm_file`, if given) through `er_client`. Returns the synchronizer's
    datamodel_stats. Raises ValueError for invalid argument combinations.

    `synchronizer` lets a caller (the CLI) supply a ready-made synchronizer, in
    which case `er_client` and the config-derived arguments are not used to
    build one."""
    if cm_uuid and not cm_file:
        raise PushArgumentError("cm_uuid needs cm_file")
    if include_base_datamodel and not cm_file:
        raise PushArgumentError("include_base_datamodel needs cm_file")
    if cm_file:
        try:
            _uuid.UUID(cm_uuid or DEFAULT_FILE_CM_UUID)
        except ValueError:
            raise PushArgumentError(f"cm_uuid {cm_uuid!r} is not a UUID") from None
    observed: set = set()
    if bundle_dir:
        if event_type_version != "v2":
            raise PushArgumentError("bundle_dir needs v2 event types")
        try:
            observed = load_bundle(bundle_dir).observed_category_hkeys()
        except BundleError as e:
            raise PushArgumentError(f"bundle: {e}") from None

    from smartconnect import ConfigurableDataModel, SmartClient

    sclient = SmartClient(api="https://tempuri.org/", username="", password="", use_language_code=language)
    dm = sclient.load_datamodel(filename=datamodel_file)
    cm = None
    if cm_file:
        cm = ConfigurableDataModel(use_language_code=language, cm_uuid=cm_uuid or DEFAULT_FILE_CM_UUID)
        with open(cm_file) as f:
            cm.load(f.read())

    sync = synchronizer
    if sync is None:
        config = SyncConfig(
            smart=SmartConnectConfig(endpoint="", login="", password="", use_language_code=language),
            earthranger=EarthRangerConfig(id="library", endpoint=endpoint, token="unused",
                                          event_type_version=event_type_version),
        )
        sync = ERSmartSynchronizer(config=config, er_client=er_client)
    sync.sync_mode = mode
    sync.skip_choices = skip_choices
    sync.observed_categories = observed
    # Without a CM: push the base data model. With a CM and include_base_datamodel:
    # the base model as its own category first, then the CM as a second one.
    # With a CM alone: push only the CM.
    if cm and include_base_datamodel:
        sync.push_smart_ca_datamodel_to_earthranger(dm=dm, smart_ca_uuid=FILE_BASED_CA_UUID,
                                                    ca_identifier=ca_identifier, cm=None)
    sync.push_smart_ca_datamodel_to_earthranger(dm=dm, smart_ca_uuid=FILE_BASED_CA_UUID,
                                                ca_identifier=ca_identifier, cm=cm)
    return dict(sync.datamodel_stats)
