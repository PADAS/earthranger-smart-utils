from datetime import datetime, timezone
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from er_smart_sync.config import EarthRangerConfig, SmartConnectConfig, SyncConfig
from er_smart_sync.defaults import (
    NullPublisher,
)


@pytest.fixture(autouse=True)
def _no_retry_sleeps(monkeypatch):
    """Disable retry backoff in tests so they don't run for seconds at a time."""
    import er_smart_sync.synchronizer as sync_mod

    monkeypatch.setattr(sync_mod.time, "sleep", lambda _: None)


@pytest.fixture
def smart_config():
    return SmartConnectConfig(
        endpoint="https://smart.example.com/server",
        login="admin",
        password="secret",
        version="7.5.7",
        use_language_code="en",
        ca_uuids=["ca-uuid-1"],
        configurable_models_lists={},
        provider_key="smart_connect",
    )


@pytest.fixture
def er_config():
    return EarthRangerConfig(
        id="test-integration-id",
        endpoint="https://test.pamdas.org/api/v1.0",
        token="test-token",
    )


@pytest.fixture
def sync_config(smart_config, er_config):
    return SyncConfig(smart=smart_config, earthranger=er_config)


@pytest.fixture
def mock_er_client():
    client = MagicMock()
    client.get_event_categories.return_value = []
    client.get_event_types.return_value = []
    client.get_events.return_value = [
        {
            "id": str(uuid4()),
            "serial_number": 1001,
            "title": "Test Event",
            "event_type": "test_type",
            "event_details": {},
            "patrols": [],
            "files": [],
            "time": datetime.now(tz=timezone.utc).isoformat(),
            "created_at": datetime.now(tz=timezone.utc).isoformat(),
            "updated_at": datetime.now(tz=timezone.utc).isoformat(),
            "priority": 0,
            "priority_label": "Gray",
            "state": "active",
            "url": "https://test.pamdas.org/api/v1.0/activity/events/1001",
        }
    ]
    client.get_patrols.return_value = [
        {
            "id": str(uuid4()),
            "serial_number": 2001,
            "title": "Test Patrol",
            "patrol_segments": [
                {
                    "leader": {"id": str(uuid4()), "name": "Ranger 1"},
                    "start_location": {"latitude": -1.5, "longitude": 36.8},
                    "events": [],
                    "event_details": [],
                    "track_points": [],
                    "updates": [],
                }
            ],
            "updates": [{"time": datetime.now(tz=timezone.utc).isoformat()}],
            "files": [],
        }
    ]
    client.get_subjects.return_value = []
    client.get_subject_observations.return_value = []
    return client


@pytest.fixture
def mock_smart_client():
    client = MagicMock()
    client.version = "7.5.7"
    return client


@pytest.fixture
def mock_publisher():
    return MagicMock(spec=NullPublisher)


@pytest.fixture
def mock_file_storage():
    storage = MagicMock()
    storage.check_exists.return_value = False
    return storage


@pytest.fixture
def mock_state_store():
    from er_smart_sync.state import SyncState

    store = MagicMock()
    store.get_last_poll.return_value = SyncState()
    return store
