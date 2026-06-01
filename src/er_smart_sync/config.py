from typing import Literal

import pydantic


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
    endpoint: str  # e.g. https://site.pamdas.org/api/v1.0
    login: str = ""
    password: str = ""
    token: str = ""
    client_id: str = "das_web_client"
    event_type_version: Literal["v1", "v2"] = "v2"
    cm_variant_mode: Literal["split", "consolidate"] = "split"
    choices_base_url: str = "/api/v2.0/schemas"

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
