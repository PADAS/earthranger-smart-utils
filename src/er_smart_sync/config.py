from typing import Literal

import pydantic

_ER_DEFAULT_API_PATH = "/api/v1.0"


def normalize_er_endpoint(value: str) -> str:
    """Normalize an EarthRanger endpoint to a full ERClient service root.

    Accepts a bare domain (``site.pamdas.org``), a scheme + host
    (``https://site.pamdas.org``), or a full service root
    (``https://site.pamdas.org/api/v1.0``) and returns the service-root
    form. An explicit ``http://`` scheme is preserved for dev servers; a
    URL already carrying a path is passed through untouched.
    """
    endpoint = value.strip().rstrip("/")
    if not endpoint:
        return endpoint
    if "://" not in endpoint:
        endpoint = f"https://{endpoint}"
    scheme, _, rest = endpoint.partition("://")
    if "/" not in rest:  # host only — no path yet
        endpoint = f"{endpoint}{_ER_DEFAULT_API_PATH}"
    return endpoint


class ConfigurableModelTranslation(pydantic.BaseModel):
    language_code: str = "en"
    value: str = ""


class ConfigurableModelMetadata(pydantic.BaseModel):
    ca_id: str
    ca_name: str
    ca_uuid: str
    name: str
    translations: list[ConfigurableModelTranslation]
    uuid: str


class SmartIntegrationAdditional(pydantic.BaseModel):
    ca_uuids: list[str] = []
    configurable_models_enabled: list[str] = []
    configurable_models_map: dict[str, list[ConfigurableModelMetadata]] = {}


class SmartConnectConfig(pydantic.BaseModel):
    """SMART Connect server connection and sync configuration.

    Replaces the Django OutboundIntegrationConfiguration model fields
    used by the synchronizer.
    """

    endpoint: str
    login: str
    password: str
    version: str = "7.0"
    use_language_code: str = "en"
    ca_uuids: list[str] = []
    configurable_models_lists: dict[str, list[dict]] = {}
    provider_key: str = "smart_connect"


class EarthRangerConfig(pydantic.BaseModel):
    """EarthRanger server connection configuration.

    Replaces the Django InboundIntegrationConfiguration model fields
    used by the synchronizer.
    """

    id: str  # Opaque integration identifier, used for state tracking
    # Accepts a bare domain, scheme+host, or full service root; normalized
    # to e.g. https://site.pamdas.org/api/v1.0 (see normalize_er_endpoint).
    endpoint: str
    login: str = ""
    password: str = ""
    token: str = ""
    client_id: str = "das_web_client"
    event_type_version: Literal["v1", "v2"] = "v2"
    cm_variant_mode: Literal["split", "consolidate"] = "split"
    # When pushing v2, convert SMART-derived v1 event types that collide with
    # the values about to be written via ER's migrate endpoint first.
    migrate_legacy_v1: bool = True
    choices_base_url: str = "/api/v2.0/schemas"
    # import-patrols id namespace; set only for a site pre-seeded under another
    patrol_namespace: str | None = None

    @pydantic.validator("endpoint")
    def _normalize_endpoint(cls, v):
        return normalize_er_endpoint(v)

    @pydantic.validator("event_type_version", pre=True)
    def _normalize_event_type_version(cls, v):
        if not isinstance(v, str):
            return v
        normalized = v.strip().lower()
        return {"v1.0": "v1", "v2.0": "v2"}.get(normalized, normalized)


class SyncConfig(pydantic.BaseModel):
    """Top-level configuration combining SMART and EarthRanger settings."""

    smart: SmartConnectConfig
    earthranger: EarthRangerConfig


class BundleConfig(pydantic.BaseModel):
    """Where the smart-able migration bundle for this site lives
    (the directory holding manifest.json)."""

    path: str


class PatrolToolConfig(pydantic.BaseModel):
    """What the bundle-driven patrol subcommands need: an ER site, the bundle,
    and the two SMART-side settings that still apply without SMART Connect."""

    earthranger: EarthRangerConfig
    bundle: BundleConfig | None = None
    language: str | None = None
    timezone: str | None = None


def load_patrol_tool_config(path: str) -> PatrolToolConfig:
    import yaml

    with open(path) as f:
        data = yaml.safe_load(f) or {}
    smart = data.get("smart") or {}
    return PatrolToolConfig(
        earthranger=EarthRangerConfig(**data["earthranger"]),
        bundle=BundleConfig(**data["bundle"]) if data.get("bundle") else None,
        language=smart.get("use_language_code"),
        timezone=smart.get("timezone"),
    )
