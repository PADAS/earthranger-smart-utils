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


def test_cm_variant_mode_accepts_consolidate():
    cfg = EarthRangerConfig(
        id="i",
        endpoint="https://x/api/v1.0",
        cm_variant_mode="consolidate",
    )
    assert cfg.cm_variant_mode == "consolidate"


def test_cm_variant_mode_rejects_unknown():
    with pytest.raises(pydantic.ValidationError):
        EarthRangerConfig(
            id="i",
            endpoint="https://x/api/v1.0",
            cm_variant_mode="nonsense",  # ty: ignore[invalid-argument-type]
        )


# ── ER endpoint normalization ──────────────────────────────────


@pytest.mark.parametrize(
    "given,expected",
    [
        # Bare domain.
        ("site.pamdas.org", "https://site.pamdas.org/api/v1.0"),
        # Scheme + host, with and without trailing slash.
        ("https://site.pamdas.org", "https://site.pamdas.org/api/v1.0"),
        ("https://site.pamdas.org/", "https://site.pamdas.org/api/v1.0"),
        # Full service root passes through (existing configs keep working).
        ("https://site.pamdas.org/api/v1.0", "https://site.pamdas.org/api/v1.0"),
        ("https://site.pamdas.org/api/v1.0/", "https://site.pamdas.org/api/v1.0"),
        # Explicit http is preserved (dev servers).
        ("http://localhost:8000", "http://localhost:8000/api/v1.0"),
        # Whitespace tolerated.
        ("  site.pamdas.org  ", "https://site.pamdas.org/api/v1.0"),
    ],
)
def test_normalize_er_endpoint_forms(given, expected):
    from er_smart_sync.config import normalize_er_endpoint

    assert normalize_er_endpoint(given) == expected


def test_er_config_normalizes_endpoint():
    from er_smart_sync.config import EarthRangerConfig

    cfg = EarthRangerConfig(id="x", endpoint="site.pamdas.org", token="t")
    assert cfg.endpoint == "https://site.pamdas.org/api/v1.0"


def test_er_config_migrate_legacy_v1_defaults_on():
    cfg = EarthRangerConfig(id="x", endpoint="https://x.pamdas.org", token="t")
    assert cfg.migrate_legacy_v1 is True


def test_er_config_migrate_legacy_v1_can_be_disabled():
    cfg = EarthRangerConfig(
        id="x", endpoint="https://x.pamdas.org", token="t", migrate_legacy_v1=False
    )
    assert cfg.migrate_legacy_v1 is False


def test_patrol_tool_config_from_yaml(tmp_path):
    from er_smart_sync.config import load_patrol_tool_config

    p = tmp_path / "sync.yaml"
    p.write_text(
        "smart:\n"
        "  endpoint: https://smart.example.org/server\n"
        "  login: u\n"
        "  password: p\n"
        "  use_language_code: fr\n"
        "  timezone: Asia/Manila\n"
        "earthranger:\n"
        "  id: site\n"
        "  endpoint: site.pamdas.org\n"
        "  token: tok\n"
        "bundle:\n"
        "  path: /data/bundle\n"
    )
    c = load_patrol_tool_config(str(p))
    assert c.earthranger.endpoint == "https://site.pamdas.org/api/v1.0"
    assert c.earthranger.token == "tok"
    assert (
        c.bundle.path == "/data/bundle"
        and c.language == "fr"
        and c.timezone == "Asia/Manila"
    )
    p.write_text(
        "earthranger:\n  id: site\n  endpoint: site.pamdas.org\n  token: tok\n"
    )
    c = load_patrol_tool_config(str(p))
    assert (
        c.bundle is None and c.language is None and c.timezone is None
    )  # smart: block optional
