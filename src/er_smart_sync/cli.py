import logging
import re
import sys

import click
import yaml

from .choices import build_choice_sets, upsert_choices
from .config import EarthRangerConfig, SmartConnectConfig, SyncConfig
from .defaults import DryRunERClient, JsonFileStateStore, NullPublisher
from .synchronizer import ERSmartSynchronizer

logger = logging.getLogger(__name__)

# Default cm_uuid used when --cm-from-file is given but --cm-uuid is not.
# The on-server cm_uuid isn't always known in the file-based flow, but
# build_event_types stitches it into the event-type `value`. The zero UUID
# makes the resulting value stable and obviously-synthetic.
#
# When a SMART CA needs multiple configurable models loaded into the same
# ER site, the user must pass --cm-uuid <uuid> for each run so the generated
# event-type values don't collide.
_DEFAULT_FILE_CM_UUID = "00000000-0000-0000-0000-000000000000"

_CA_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9_-]{2,30}$")


def _validate_ca_identifier(ctx, param, value):
    """Click callback: enforce 2-30 chars (alphanumeric, hyphen, underscore) on --ca-identifier."""
    if value is None:
        return None
    if not _CA_IDENTIFIER_RE.match(value):
        raise click.BadParameter(
            f"{value!r}: must be 2-30 characters (A-Z, a-z, 0-9, hyphen, underscore)"
        )
    return value


def _resolve_cm_uuid(cm_uuid: str | None) -> str:
    """Return the user-provided cm_uuid or the synthetic zero UUID."""
    import uuid as _uuid

    if not cm_uuid:
        return _DEFAULT_FILE_CM_UUID
    try:
        _uuid.UUID(cm_uuid)
    except ValueError as e:
        raise click.UsageError(
            f"--cm-uuid must be a valid UUID, got {cm_uuid!r}"
        ) from e
    return cm_uuid


def _set_network_timeout(seconds: float = 120.0) -> None:
    """Bound every TCP read/connect so a stalled ER request can't hang the run.

    ERClient (sync) doesn't expose a constructor-level network timeout; it
    uses `requests.{get,post,patch}` directly without a `timeout=` kwarg.
    Setting socket.setdefaulttimeout enforces a process-wide ceiling on every
    blocking socket operation, after which requests raises and our _retry
    wrapper kicks in.
    """
    import socket
    socket.setdefaulttimeout(seconds)


def _setup_logging(verbose: bool) -> None:
    """Configure logging.

    -v enables DEBUG output for our own package (you see every event type
    that's checked, every published message, every state checkpoint) while
    keeping noisy underlying libraries (requests, urllib3, smartconnect's
    auth chatter) at WARNING so the output stays readable.
    """
    _set_network_timeout()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    if verbose:
        logging.getLogger("er_smart_sync").setLevel(logging.DEBUG)
        for noisy in ("urllib3", "requests", "smartconnect", "ERClient"):
            logging.getLogger(noisy).setLevel(logging.WARNING)


def _load_config_from_file(path: str) -> SyncConfig:
    with open(path) as f:
        data = yaml.safe_load(f)
    return SyncConfig(**data)


@click.group()
@click.option("--verbose", "-v", is_flag=True, help="Enable debug logging.")
@click.option(
    "--dry-run",
    is_flag=True,
    help="Log intended writes (event types, observations) without contacting ER or the message broker.",
)
@click.pass_context
def main(ctx, verbose, dry_run):
    """Synchronize SMART Connect data models, events, and patrols with EarthRanger."""
    _setup_logging(verbose)
    ctx.ensure_object(dict)
    ctx.obj["dry_run"] = dry_run


def _make_synchronizer(
    config: SyncConfig, *, ctx, **synchronizer_kwargs
) -> ERSmartSynchronizer:
    """Construct an ERSmartSynchronizer honoring the --dry-run flag.

    When dry-run is set, wrap the real ERClient with DryRunERClient so writes
    are logged but never executed, and force NullPublisher so messages don't
    leave the machine.
    """
    dry_run = bool(ctx.obj.get("dry_run")) if ctx.obj else False
    sync = ERSmartSynchronizer(config=config, **synchronizer_kwargs)
    if dry_run:
        sync.er_client = DryRunERClient(sync.er_client)
        sync.publisher = NullPublisher()
        click.echo(
            "Dry run mode: no writes will be sent to ER or to the broker.", err=True
        )
    return sync


# ── Shared options ──────────────────────────────────────────────


def smart_options(f):
    """SMART Connect connection options."""
    f = click.option("--smart-api", help="SMART Connect API URL")(f)
    f = click.option("--smart-username", help="SMART Connect username")(f)
    f = click.option("--smart-password", help="SMART Connect password")(f)
    f = click.option("--smart-version", default="7.0", help="SMART version")(f)
    f = click.option("--smart-language", default="en", help="Language code")(f)
    return f


def er_options(f):
    """EarthRanger connection options."""
    f = click.option("--er-endpoint", help="EarthRanger API URL")(f)
    f = click.option("--er-token", help="EarthRanger API token")(f)
    f = click.option("--er-username", default="", help="EarthRanger username")(f)
    f = click.option("--er-password", default="", help="EarthRanger password")(f)
    f = click.option(
        "--er-id", default="cli", help="Integration ID for state tracking"
    )(f)
    return f


def _build_config(
    *,
    config_file=None,
    smart_api=None,
    smart_username=None,
    smart_password=None,
    smart_version="7.0",
    smart_language="en",
    er_endpoint=None,
    er_token=None,
    er_username="",
    er_password="",
    er_id="cli",
    smart_ca_uuids=None,
) -> SyncConfig:
    if config_file:
        config = _load_config_from_file(config_file)
    else:
        if not er_endpoint:
            raise click.UsageError("Either --config or --er-endpoint is required.")
        if not er_token and not (er_username and er_password):
            raise click.UsageError(
                "EarthRanger auth requires either --er-token or both "
                "--er-username and --er-password."
            )
        config = SyncConfig(
            smart=SmartConnectConfig(
                endpoint=smart_api or "",
                login=smart_username or "",
                password=smart_password or "",
                version=smart_version,
                use_language_code=smart_language,
                ca_uuids=list(smart_ca_uuids) if smart_ca_uuids else [],
            ),
            earthranger=EarthRangerConfig(
                id=er_id,
                endpoint=er_endpoint,
                token=er_token or "",
                login=er_username,
                password=er_password,
            ),
        )

    _validate_config(config)
    return config


def _print_datamodel_summary(sync: ERSmartSynchronizer) -> None:
    """Pretty-print the datamodel sync stats to stdout."""
    stats = sync.datamodel_stats
    click.echo("")
    click.echo("Datamodel sync summary:")
    for k, v in stats.items():
        click.echo(f"  {k.replace('_', ' ')}: {v}")


def _validate_config(config: SyncConfig) -> None:
    er = config.earthranger
    if not er.endpoint:
        raise click.UsageError("earthranger.endpoint is required")
    if not er.token and not (er.login and er.password):
        raise click.UsageError(
            "earthranger requires a token, or both login and password"
        )


# ── datamodel subcommand ────────────────────────────────────────


@main.command()
@click.option(
    "--config", "config_file", type=click.Path(exists=True), help="YAML config file"
)
@smart_options
@er_options
@click.option(
    "--smart-ca-uuid", multiple=True, help="Conservation area UUID(s) to sync"
)
@click.option(
    "--from-file",
    "datamodel_file",
    type=click.Path(exists=True),
    help="Load data model from local XML file instead of SMART API",
)
@click.option(
    "--cm-from-file",
    "cm_file",
    type=click.Path(exists=True),
    help="Load configurable model from local XML file (used with --from-file)",
)
@click.option(
    "--cm-uuid",
    "cm_uuid",
    default=None,
    help="Configurable-model UUID. Required when loading multiple configurable models for the same SMART CA to avoid event-type value collisions. Defaults to the zero UUID.",
)
@click.option(
    "--include-base-datamodel",
    is_flag=True,
    default=False,
    help="Also push the base data model as its own ER event category in addition to the configurable model. No effect unless --cm-from-file is given.",
)
@click.option(
    "--ca-identifier",
    "ca_identifier",
    default=None,
    callback=_validate_ca_identifier,
    help=(
        "Short code (2-30 chars; letters, digits, hyphens, underscores) used "
        "as the ER event-category identifier. Required when --from-file is "
        "used. Ignored when syncing from the SMART API (the identifier is "
        "extracted from the CA label in that case)."
    ),
)
@click.option(
    "--mode",
    type=click.Choice(["both", "create-only", "update-only"]),
    default="both",
    help="Restrict to creating only new event types, updating only existing ones, or both.",
)
@click.option(
    "--event-type-version",
    type=click.Choice(["v1", "v2"]),
    default=None,
    help="EarthRanger event-type API version. Overrides --config or the default (v2).",
)
@click.option(
    "--skip-choices",
    "skip_choices",
    is_flag=True,
    default=False,
    help=(
        "Skip the choices upsert phase (v2 only). "
        "Use if you've already run `er-smart-sync choices` separately."
    ),
)
@click.pass_context
def datamodel(
    ctx,
    config_file,
    smart_api,
    smart_username,
    smart_password,
    smart_version,
    smart_language,
    er_endpoint,
    er_token,
    er_username,
    er_password,
    er_id,
    smart_ca_uuid,
    datamodel_file,
    cm_file,
    cm_uuid,
    include_base_datamodel,
    ca_identifier,
    mode,
    event_type_version,
    skip_choices,
):
    """Sync SMART data models to EarthRanger as event categories/types."""
    config = _build_config(
        config_file=config_file,
        smart_api=smart_api,
        smart_username=smart_username,
        smart_password=smart_password,
        smart_version=smart_version,
        smart_language=smart_language,
        er_endpoint=er_endpoint,
        er_token=er_token,
        er_username=er_username,
        er_password=er_password,
        er_id=er_id,
        smart_ca_uuids=smart_ca_uuid,
    )

    if event_type_version:
        config.earthranger.event_type_version = event_type_version

    if cm_file and not datamodel_file:
        raise click.UsageError("--cm-from-file requires --from-file")
    if cm_uuid and not cm_file:
        raise click.UsageError("--cm-uuid requires --cm-from-file")
    if include_base_datamodel and not cm_file:
        raise click.UsageError(
            "--include-base-datamodel only makes sense with --cm-from-file "
            "(without a configurable model, the base data model is always pushed)"
        )
    # Validate the UUID up front so a bad value fails fast, before we touch
    # any XML files.
    resolved_cm_uuid = _resolve_cm_uuid(cm_uuid) if cm_file else None

    if datamodel_file:
        # File-based sync: load XML, push directly to ER
        if not ca_identifier:
            raise click.UsageError(
                "--ca-identifier is required when --from-file is used"
            )

        from smartconnect import ConfigurableDataModel, SmartClient

        sclient = SmartClient(
            api="https://tempuri.org/",
            username="",
            password="",
            use_language_code=smart_language,
        )
        dm = sclient.load_datamodel(filename=datamodel_file)

        cm = None
        if cm_file:
            cm = ConfigurableDataModel(
                use_language_code=smart_language,
                cm_uuid=resolved_cm_uuid,
            )
            with open(cm_file) as f:
                cm.load(f.read())

        sync = _make_synchronizer(config, ctx=ctx)
        sync.sync_mode = mode
        sync.skip_choices = skip_choices

        # Without a CM: just push the base data model (single call).
        # With a CM + --include-base-datamodel: push the base DM as its own
        # ER category, then the CM as a second separate category.
        # With a CM but without the flag: push only the CM (default — keeps
        # behavior of the CM as a curated overlay).
        if cm and include_base_datamodel:
            sync.push_smart_ca_datamodel_to_earthranger(
                dm=dm,
                smart_ca_uuid="smart-ca-import",
                ca_identifier=ca_identifier,
                cm=None,
            )
        sync.push_smart_ca_datamodel_to_earthranger(
            dm=dm,
            smart_ca_uuid="smart-ca-import",
            ca_identifier=ca_identifier,
            cm=cm,
        )
    else:
        # API-based sync: fetch from SMART Connect
        if not smart_api:
            raise click.UsageError(
                "Either --from-file or --smart-api (with credentials) is required."
            )
        if ca_identifier:
            logger.warning(
                "--ca-identifier %r ignored: identifier will be extracted "
                "from the conservation-area label fetched from the SMART API.",
                ca_identifier,
            )
        sync = _make_synchronizer(config, ctx=ctx)
        sync.sync_mode = mode
        sync.skip_choices = skip_choices
        sync.synchronize_datamodel()

    _print_datamodel_summary(sync)


# ── choices subcommand ─────────────────────────────────────────


@main.command()
@click.option(
    "--config", "config_file", type=click.Path(exists=True), help="YAML config file"
)
@smart_options
@er_options
@click.option(
    "--smart-ca-uuid", multiple=True, help="Conservation area UUID(s) to sync"
)
@click.option(
    "--from-file",
    "datamodel_file",
    type=click.Path(exists=True),
    help="Load data model from local XML file instead of SMART API",
)
@click.option(
    "--cm-from-file",
    "cm_file",
    type=click.Path(exists=True),
    help="Load configurable model from local XML file (used with --from-file)",
)
@click.option(
    "--cm-uuid",
    "cm_uuid",
    default=None,
    help="Configurable-model UUID. Defaults to the zero UUID.",
)
@click.pass_context
def choices(
    ctx,
    config_file,
    smart_api,
    smart_username,
    smart_password,
    smart_version,
    smart_language,
    er_endpoint,
    er_token,
    er_username,
    er_password,
    er_id,
    smart_ca_uuid,
    datamodel_file,
    cm_file,
    cm_uuid,
):
    """Upsert SMART option sets as EarthRanger Choice records.

    Required before pushing v2 event types; v2 event-type schemas reference
    choices via $ref, and the referenced records must exist first.
    """
    config = _build_config(
        config_file=config_file,
        smart_api=smart_api,
        smart_username=smart_username,
        smart_password=smart_password,
        smart_version=smart_version,
        smart_language=smart_language,
        er_endpoint=er_endpoint,
        er_token=er_token,
        er_username=er_username,
        er_password=er_password,
        er_id=er_id,
        smart_ca_uuids=smart_ca_uuid,
    )

    if cm_file and not datamodel_file:
        raise click.UsageError("--cm-from-file requires --from-file")
    if cm_uuid and not cm_file:
        raise click.UsageError("--cm-uuid requires --cm-from-file")
    resolved_cm_uuid = _resolve_cm_uuid(cm_uuid) if cm_file else None

    sync = _make_synchronizer(config, ctx=ctx)

    if datamodel_file:
        from smartconnect import ConfigurableDataModel, SmartClient

        sclient = SmartClient(
            api="https://tempuri.org/",
            username="",
            password="",
            use_language_code=smart_language,
        )
        dm = sclient.load_datamodel(filename=datamodel_file)
        cm = None
        if cm_file:
            cm = ConfigurableDataModel(
                use_language_code=smart_language,
                cm_uuid=resolved_cm_uuid,
            )
            with open(cm_file) as f:
                cm.load(f.read())
        choice_sets = build_choice_sets(
            dm=dm.export_as_dict(),
            cm=cm.export_as_dict() if cm else None,
            ca_uuid="smart-ca-import",
        )
    else:
        raise click.UsageError(
            "API-based choices sync is not yet supported. "
            "Use --from-file with --cm-from-file for now."
        )

    stats = upsert_choices(er_client=sync.er_client, choice_sets=choice_sets)
    click.echo(
        f"Choices: created={stats.created} updated={stats.updated} "
        f"unchanged={stats.unchanged} deactivated={stats.deactivated} "
        f"errored={stats.errored}"
    )
    if stats.errored > 0:
        raise click.ClickException(f"{stats.errored} choice operations failed")


# ── events subcommand ───────────────────────────────────────────


@main.command()
@click.option(
    "--config", "config_file", type=click.Path(exists=True), help="YAML config file"
)
@smart_options
@er_options
@click.option("--smart-ca-uuid", multiple=True, help="Conservation area UUID(s)")
@click.option("--topic", default="", help="Pub/Sub topic for publishing events")
@click.option(
    "--state-file", default="/tmp/er-smart-sync-state.json", help="Path to state file"
)
@click.pass_context
def events(
    ctx,
    config_file,
    smart_api,
    smart_username,
    smart_password,
    smart_version,
    smart_language,
    er_endpoint,
    er_token,
    er_username,
    er_password,
    er_id,
    smart_ca_uuid,
    topic,
    state_file,
):
    """Sync EarthRanger events to SMART Connect."""
    config = _build_config(
        config_file=config_file,
        smart_api=smart_api,
        smart_username=smart_username,
        smart_password=smart_password,
        smart_version=smart_version,
        smart_language=smart_language,
        er_endpoint=er_endpoint,
        er_token=er_token,
        er_username=er_username,
        er_password=er_password,
        er_id=er_id,
        smart_ca_uuids=smart_ca_uuid,
    )

    sync = _make_synchronizer(
        config,
        ctx=ctx,
        state_store=JsonFileStateStore(path=state_file),
        observations_topic=topic,
    )
    sync.synchronize_er_events()


# ── patrols subcommand ──────────────────────────────────────────


@main.command()
@click.option(
    "--config", "config_file", type=click.Path(exists=True), help="YAML config file"
)
@smart_options
@er_options
@click.option("--smart-ca-uuid", multiple=True, help="Conservation area UUID(s)")
@click.option("--topic", default="", help="Pub/Sub topic for publishing patrols")
@click.option(
    "--state-file", default="/tmp/er-smart-sync-state.json", help="Path to state file"
)
@click.pass_context
def patrols(
    ctx,
    config_file,
    smart_api,
    smart_username,
    smart_password,
    smart_version,
    smart_language,
    er_endpoint,
    er_token,
    er_username,
    er_password,
    er_id,
    smart_ca_uuid,
    topic,
    state_file,
):
    """Sync EarthRanger patrols to SMART Connect."""
    config = _build_config(
        config_file=config_file,
        smart_api=smart_api,
        smart_username=smart_username,
        smart_password=smart_password,
        smart_version=smart_version,
        smart_language=smart_language,
        er_endpoint=er_endpoint,
        er_token=er_token,
        er_username=er_username,
        er_password=er_password,
        er_id=er_id,
        smart_ca_uuids=smart_ca_uuid,
    )

    sync = _make_synchronizer(
        config,
        ctx=ctx,
        state_store=JsonFileStateStore(path=state_file),
        observations_topic=topic,
    )
    sync.synchronize_er_patrols()


# ── validate-config subcommand ──────────────────────────────────


@main.command("config-template")
@click.option(
    "--output",
    "-o",
    type=click.Path(),
    default=None,
    help="Write the template to this path instead of stdout.",
)
def config_template_cmd(output):
    """Print a commented YAML config template.

    The template lists every key the YAML config file accepts, with comments
    explaining each. Pipe to a file to bootstrap your own config:

        er-smart-sync config-template > sync.yaml
    """
    template = _CONFIG_YAML_TEMPLATE
    if output:
        with open(output, "w") as f:
            f.write(template)
        click.echo(f"Wrote {output}", err=True)
    else:
        click.echo(template, nl=False)


_CONFIG_YAML_TEMPLATE = """\
# er-smart-sync configuration file.
# Pass to any subcommand via `--config sync.yaml`.
# All fields under `smart:` and `earthranger:` map directly to the
# SmartConnectConfig and EarthRangerConfig models in er_smart_sync.config.

smart:
  # SMART Connect server URL, e.g. https://smart.example.org/server
  endpoint: https://smart.example.org/server

  # SMART login credentials. Used for both data-model sync (SMART → ER)
  # and for fetching conservation-area metadata.
  login: smart-user
  password: smart-secret

  # SMART Connect server version. Determines whether events need a
  # smart_observation_uuid patched onto them (versions < 7.5.3 do).
  version: "7.5.7"

  # Language code used when resolving display names in the data model.
  use_language_code: en

  # Conservation-area UUIDs to sync. Required for the `datamodel`,
  # `events`, and `patrols` subcommands when not using --from-file.
  ca_uuids:
    - 00000000-0000-0000-0000-000000000000

  # Optional. Per-CA list of configurable models to push as separate ER
  # event categories. Each entry must have `uuid`, `name`, and
  # `use_with_earth_ranger: true`. Only used by the API-based datamodel
  # sync; not needed when loading from --cm-from-file.
  configurable_models_lists: {}
  # configurable_models_lists:
  #   00000000-0000-0000-0000-000000000000:
  #     - uuid: 11111111-1111-1111-1111-111111111111
  #       name: Incidents Configurable Model
  #       use_with_earth_ranger: true

  # Provider key used when forwarding events/patrols to Gundi routing.
  provider_key: smart_connect

earthranger:
  # Opaque integration identifier. Used as the key in the state file so
  # multiple instances can coexist (e.g. one ER per row).
  id: my-er-instance

  # EarthRanger API root, e.g. https://site.pamdas.org/api/v1.0
  endpoint: https://site.pamdas.org/api/v1.0

  # EarthRanger authentication. Provide either `token` OR (`login` and
  # `password`). Token is preferred.
  token: ""
  login: ""
  password: ""

  # Optional. OAuth client_id used when authenticating with login/password.
  # Defaults to "das_web_client".
  client_id: das_web_client

  # EarthRanger event-type API version: "v1" or "v2". Default: v2.
  # v2 is the current EarthRanger event-type shape (JSON Schema 2020-12 +
  # UI envelope). v1 is the legacy shape and is still supported for tenants
  # that haven't enabled v2.
  event_type_version: v2

  # URL prefix used in v2 event-type schema $refs (e.g.
  # "{choices_base_url}/choices.json?field=<field>"). Default matches ER's
  # standard /api/v2.0/schemas layout.
  choices_base_url: /api/v2.0/schemas
"""


@main.command("validate-config")
@click.option(
    "--config", "config_file", type=click.Path(exists=True), help="YAML config file"
)
@smart_options
@er_options
def validate_config_cmd(
    config_file,
    smart_api,
    smart_username,
    smart_password,
    smart_version,
    smart_language,
    er_endpoint,
    er_token,
    er_username,
    er_password,
    er_id,
):
    """Check that SMART and EarthRanger credentials work."""
    config = _build_config(
        config_file=config_file,
        smart_api=smart_api,
        smart_username=smart_username,
        smart_password=smart_password,
        smart_version=smart_version,
        smart_language=smart_language,
        er_endpoint=er_endpoint,
        er_token=er_token,
        er_username=er_username,
        er_password=er_password,
        er_id=er_id,
    )

    sync = ERSmartSynchronizer(config=config)

    er_ok = _try_call(
        sync.er_client.get_event_categories,
        label=f"EarthRanger {config.earthranger.endpoint}",
    )

    smart_ok = True
    if config.smart.endpoint:
        # Hitting any cheap SMART endpoint is enough to validate auth.
        smart_ok = _try_call(
            lambda: (
                list(config.smart.ca_uuids)
                and sync.smart_client.get_conservation_area(
                    ca_uuid=config.smart.ca_uuids[0]
                )
                or sync.smart_client.get_conservation_area(ca_uuid="probe")
            ),
            label=f"SMART Connect {config.smart.endpoint}",
            allow_404=True,
        )
    else:
        click.echo("SMART: no endpoint configured, skipping.")

    if not er_ok or not smart_ok:
        raise click.ClickException("One or more credential checks failed")


def _try_call(fn, *, label: str, allow_404: bool = False) -> bool:
    try:
        fn()
        click.echo(f"OK  {label}")
        return True
    except Exception as e:
        msg = str(e)
        if allow_404 and ("404" in msg or "not found" in msg.lower()):
            click.echo(f"OK  {label} (auth accepted)")
            return True
        click.echo(f"FAIL {label}: {e}", err=True)
        return False


# ── list-cas subcommand ─────────────────────────────────────────


@main.command("list-cas")
@click.option(
    "--config", "config_file", type=click.Path(exists=True), help="YAML config file"
)
@smart_options
@er_options
def list_cas_cmd(
    config_file,
    smart_api,
    smart_username,
    smart_password,
    smart_version,
    smart_language,
    er_endpoint,
    er_token,
    er_username,
    er_password,
    er_id,
):
    """List the conservation areas reachable on the configured SMART endpoint."""
    config = _build_config(
        config_file=config_file,
        smart_api=smart_api,
        smart_username=smart_username,
        smart_password=smart_password,
        smart_version=smart_version,
        smart_language=smart_language,
        er_endpoint=er_endpoint,
        er_token=er_token,
        er_username=er_username,
        er_password=er_password,
        er_id=er_id,
    )

    if not config.smart.endpoint:
        raise click.UsageError(
            "list-cas requires a SMART endpoint (--smart-api or via --config)"
        )

    from smartconnect import SmartClient

    client = SmartClient(
        api=config.smart.endpoint,
        username=config.smart.login,
        password=config.smart.password,
        version=config.smart.version,
        use_language_code=config.smart.use_language_code,
    )

    # SMART exposes CAs via SmartClient.get_conservation_areas (plural) on
    # recent versions; fall back to iterating configured ca_uuids otherwise.
    ca_uuids = config.smart.ca_uuids
    if not ca_uuids and hasattr(client, "get_conservation_areas"):
        cas = client.get_conservation_areas()
        rows = [(c.uuid, c.label, _extract_id(c.label)) for c in cas]
    else:
        rows = []
        for uuid in ca_uuids:
            try:
                ca = client.get_conservation_area(ca_uuid=uuid)
                rows.append((uuid, ca.label, _extract_id(ca.label)))
            except Exception as e:
                rows.append((uuid, f"<error: {e}>", ""))

    if not rows:
        click.echo("No conservation areas found.")
        return

    width = max(len(r[1]) for r in rows)
    click.echo(f"{'UUID':<36}  {'Label':<{width}}  Identifier")
    click.echo("-" * (36 + width + 14))
    for uuid, label, ident in rows:
        click.echo(f"{uuid:<36}  {label:<{width}}  {ident}")


def _extract_id(label: str) -> str:
    """Wrapper for the synchronizer's CA-label extractor (avoids circular import)."""
    return ERSmartSynchronizer.get_identifier_from_ca_label(label)


# ── inspect-datamodel subcommand ────────────────────────────────


@main.command("inspect-datamodel")
@click.option(
    "--config", "config_file", type=click.Path(exists=True), help="YAML config file"
)
@smart_options
@er_options
@click.option("--smart-ca-uuid", help="Conservation area UUID to inspect")
@click.option(
    "--from-file",
    "datamodel_file",
    type=click.Path(exists=True),
    help="Load data model from local XML file instead of SMART API",
)
@click.option(
    "--cm-from-file",
    "cm_file",
    type=click.Path(exists=True),
    help="Load configurable model overlay from local XML file (used with --from-file)",
)
@click.option(
    "--cm-uuid",
    "cm_uuid",
    default=None,
    help="Configurable-model UUID (used with --cm-from-file). Required when loading multiple configurable models for the same SMART CA to avoid event-type value collisions. Defaults to the zero UUID.",
)
@click.option(
    "--ca-identifier",
    "ca_identifier",
    default=None,
    callback=_validate_ca_identifier,
    help=(
        "Short code (2-30 chars; letters, digits, hyphens, underscores) used "
        "as the ER event-category identifier when --from-file is used. Ignored "
        "when --smart-ca-uuid is given (identifier extracted from CA label)."
    ),
)
@click.option(
    "--event-type-version",
    type=click.Choice(["v1", "v2"]),
    default=None,
    help="Which event-type schema shape to print. Overrides --config or the default (v2).",
)
def inspect_datamodel_cmd(
    config_file,
    smart_api,
    smart_username,
    smart_password,
    smart_version,
    smart_language,
    er_endpoint,
    er_token,
    er_username,
    er_password,
    er_id,
    smart_ca_uuid,
    datamodel_file,
    cm_file,
    cm_uuid,
    ca_identifier,
    event_type_version,
):
    """Show the EarthRanger event types that *would* be created/updated from a SMART data model.

    Performs zero writes against EarthRanger. The output groups event types by
    category and lists each event type's schema fields, types, and enum values.
    """
    config = _build_config(
        config_file=config_file,
        smart_api=smart_api,
        smart_username=smart_username,
        smart_password=smart_password,
        smart_version=smart_version,
        smart_language=smart_language,
        er_endpoint=er_endpoint,
        er_token=er_token,
        er_username=er_username,
        er_password=er_password,
        er_id=er_id,
        smart_ca_uuids=[smart_ca_uuid] if smart_ca_uuid else None,
    )

    # CLI flag overrides config; otherwise inherit from config.
    if event_type_version is None:
        event_type_version = config.earthranger.event_type_version

    from smartconnect import ConfigurableDataModel, SmartClient

    if datamodel_file:
        if not ca_identifier:
            raise click.UsageError(
                "--ca-identifier is required when --from-file is used"
            )
        sclient = SmartClient(
            api="https://tempuri.org/",
            username="",
            password="",
            use_language_code=smart_language,
        )
        dm = sclient.load_datamodel(filename=datamodel_file)
    elif smart_ca_uuid:
        if ca_identifier:
            logger.warning(
                "--ca-identifier %r ignored: identifier will be extracted "
                "from the conservation-area label fetched from the SMART API.",
                ca_identifier,
            )
        sclient = SmartClient(
            api=config.smart.endpoint,
            username=config.smart.login,
            password=config.smart.password,
            version=config.smart.version,
            use_language_code=config.smart.use_language_code,
        )
        dm = sclient.get_data_model(ca_uuid=smart_ca_uuid)
        ca = sclient.get_conservation_area(ca_uuid=smart_ca_uuid)
        ca_identifier = ERSmartSynchronizer.get_identifier_from_ca_label(ca.label)
        if not ca_identifier:
            # Match the runtime sync behavior (push_smart_datamodel_to_earthranger
            # raises on the same condition) — fail with the same actionable message
            # rather than silently rendering "CA: " with a blank identifier.
            raise click.ClickException(
                f"Could not extract a CA identifier from SMART label "
                f"{ca.label!r} (ca_uuid={smart_ca_uuid}). The label must "
                f"contain a bracketed short code, e.g. 'Foasf Reserve [FOASF]'. "
                f"Fix the label in SMART Connect, or use --from-file with "
                f"an explicit --ca-identifier."
            )
    else:
        raise click.UsageError("Either --from-file or --smart-ca-uuid is required.")

    if cm_uuid and not cm_file:
        raise click.UsageError("--cm-uuid requires --cm-from-file")

    cm = None
    if cm_file:
        cm = ConfigurableDataModel(
            use_language_code=smart_language,
            cm_uuid=_resolve_cm_uuid(cm_uuid),
        )
        with open(cm_file) as f:
            cm.load(f.read())

    ca_uuid = smart_ca_uuid or "ca-uuid-placeholder"

    if event_type_version == "v2":
        from .smart_to_er_v2 import build_event_types_v2

        event_types = build_event_types_v2(
            dm=dm.export_as_dict(),
            cm=cm.export_as_dict() if cm else None,
            ca_uuid=ca_uuid,
            ca_identifier=ca_identifier,
            choices_base_url=config.earthranger.choices_base_url,
        )
        _print_event_type_summary_v2(event_types, ca_identifier=ca_identifier)
        choice_sets = build_choice_sets(
            dm=dm.export_as_dict(),
            cm=cm.export_as_dict() if cm else None,
            ca_uuid=ca_uuid,
        )
        _print_choice_set_summary(choice_sets)
    else:
        from .smart_to_er import build_event_types

        event_types = build_event_types(
            dm=dm.export_as_dict(),
            cm=cm.export_as_dict() if cm else None,
            ca_uuid=ca_uuid,
            ca_identifier=ca_identifier,
        )
        _print_event_type_summary(event_types, ca_identifier=ca_identifier)


def _print_event_type_summary(event_types, *, ca_identifier: str) -> None:
    import json as _json

    click.echo(f"CA: {ca_identifier}")
    click.echo(f"Event types: {len(event_types)}")
    active = [et for et in event_types if et.is_active]
    inactive = [et for et in event_types if not et.is_active]
    click.echo(f"  active:   {len(active)}")
    click.echo(f"  inactive: {len(inactive)}")
    click.echo("")

    for et in event_types:
        active_marker = "" if et.is_active else " [inactive]"
        click.echo(f"- {et.value}{active_marker}")
        click.echo(f"    display: {et.display}")
        if not et.event_schema:
            continue
        schema = _json.loads(et.event_schema).get("schema", {})
        properties = schema.get("properties", {})
        if not properties:
            continue
        click.echo("    fields:")
        for key, prop in properties.items():
            type_part = prop.get("type", "?")
            if "format" in prop:
                type_part = f"{type_part}/{prop['format']}"
            enum = prop.get("enum")
            items = prop.get("items", {})
            if not enum and isinstance(items, dict):
                enum = items.get("enum")
            extras = []
            if enum:
                extras.append(f"enum={enum}")
            if prop.get("readOnly"):
                extras.append("readOnly")
            extras_str = f" ({', '.join(extras)})" if extras else ""
            click.echo(f"      {key}: {type_part}{extras_str}")


def _print_event_type_summary_v2(event_types, *, ca_identifier: str) -> None:
    click.echo(f"CA: {ca_identifier}")
    click.echo(f"Event types: {len(event_types)}")
    active = [et for et in event_types if et.is_active]
    inactive = [et for et in event_types if not et.is_active]
    click.echo(f"  active:   {len(active)}")
    click.echo(f"  inactive: {len(inactive)}")
    click.echo("")

    for et in event_types:
        active_marker = "" if et.is_active else " [inactive]"
        click.echo(f"- {et.value}{active_marker}")
        click.echo(f"    display: {et.display}")
        if not et.event_schema:
            continue
        properties = et.event_schema.get("json", {}).get("properties", {})
        ui_fields = et.event_schema.get("ui", {}).get("fields", {})
        if not properties:
            continue
        click.echo("    fields:")
        for key, prop in properties.items():
            type_part = prop.get("type", "?")
            if "format" in prop:
                type_part = f"{type_part}/{prop['format']}"
            ui = ui_fields.get(key, {})
            extras = []
            ui_type = ui.get("type")
            if ui_type:
                input_type = ui.get("inputType")
                extras.append(
                    f"ui={ui_type}/{input_type}" if input_type else f"ui={ui_type}"
                )
            enum = prop.get("enum")
            items = prop.get("items", {})
            if not enum and isinstance(items, dict):
                enum = items.get("enum")
            if enum:
                extras.append(f"enum={enum}")
            if prop.get("deprecated"):
                extras.append("deprecated")
            extras_str = f" ({', '.join(extras)})" if extras else ""
            click.echo(f"      {key}: {type_part}{extras_str}")


def _print_choice_set_summary(choice_sets) -> None:
    if not choice_sets:
        return
    click.echo("")
    click.echo(f"Choice sets: {len(choice_sets)}")
    for cs in choice_sets:
        click.echo(f"- field: {cs.field}")
        click.echo(f"    options ({len(cs.options)}):")
        for opt in cs.options:
            marker = "" if opt.is_active else " [inactive]"
            click.echo(f"      - {opt.value}: {opt.display}{marker}")
