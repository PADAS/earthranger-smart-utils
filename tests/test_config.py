import pydantic
import pytest

from er_smart_sync.config import (
    EarthRangerConfig,
    SmartConnectConfig,
    SyncConfig,
)


def test_sync_config_round_trip():
    config = SyncConfig(
        smart=SmartConnectConfig(
            endpoint="https://smart.example.com",
            login="user",
            password="pass",
            ca_uuids=["uuid1"],
        ),
        earthranger=EarthRangerConfig(
            id="er-1",
            endpoint="https://er.example.com/api/v1.0",
            token="tok",
        ),
    )
    data = config.dict()
    restored = SyncConfig(**data)
    assert restored == config


def test_smart_config_defaults():
    cfg = SmartConnectConfig(endpoint="https://x", login="u", password="p")
    assert cfg.version == "7.0"
    assert cfg.use_language_code == "en"
    assert cfg.ca_uuids == []


def test_er_config_defaults():
    cfg = EarthRangerConfig(id="1", endpoint="https://x")
    assert cfg.login == ""
    assert cfg.token == ""
    assert cfg.client_id == "das_web_client"


def test_er_config_event_type_version_defaults_to_v2():
    cfg = EarthRangerConfig(id="i", endpoint="https://x/api/v1.0")
    assert cfg.event_type_version == "v2"


def test_er_config_event_type_version_accepts_v2():
    cfg = EarthRangerConfig(
        id="i", endpoint="https://x/api/v1.0", event_type_version="v2"
    )
    assert cfg.event_type_version == "v2"


def test_er_config_event_type_version_accepts_v1():
    cfg = EarthRangerConfig(
        id="i", endpoint="https://x/api/v1.0", event_type_version="v1"
    )
    assert cfg.event_type_version == "v1"


def test_er_config_event_type_version_accepts_dotted_aliases():
    cfg = EarthRangerConfig(
        id="i",
        endpoint="https://x/api/v1.0",
        event_type_version="v2.0",  # ty: ignore[invalid-argument-type]
    )
    assert cfg.event_type_version == "v2"

    cfg = EarthRangerConfig(
        id="i",
        endpoint="https://x/api/v1.0",
        event_type_version="V1",  # ty: ignore[invalid-argument-type]
    )
    assert cfg.event_type_version == "v1"


def test_er_config_event_type_version_rejects_unknown():
    with pytest.raises(pydantic.ValidationError):
        EarthRangerConfig(
            id="i",
            endpoint="https://x/api/v1.0",
            event_type_version="v3",  # ty: ignore[invalid-argument-type]
        )


def test_er_config_choices_base_url_default():
    cfg = EarthRangerConfig(id="i", endpoint="https://x/api/v1.0")
    assert cfg.choices_base_url == "/api/v2.0/schemas"


def test_er_config_choices_base_url_override():
    cfg = EarthRangerConfig(
        id="i",
        endpoint="https://x/api/v1.0",
        choices_base_url="/custom/path",
    )
    assert cfg.choices_base_url == "/custom/path"


def test_cm_variant_mode_defaults_to_split(er_config):
    assert er_config.cm_variant_mode == "split"


def test_cm_variant_mode_rejects_unknown(smart_config):
    with pytest.raises(pydantic.ValidationError):
        SyncConfig(
            smart=smart_config,
            earthranger=EarthRangerConfig(endpoint="https://x", token="t", cm_variant_mode="nonsense"),  # ty: ignore[invalid-argument-type]
        )
