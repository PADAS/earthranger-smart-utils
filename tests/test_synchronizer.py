import copy
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from er_smart_sync.defaults import NullTracing
from er_smart_sync.state import SyncState
from er_smart_sync.synchronizer import ERSmartSynchronizer


class CapturingStateStore:
    """Spy that records a deep copy of state on every set_last_poll."""

    def __init__(self):
        self.state = SyncState()
        self.history = []

    def get_last_poll(self, integration_id):
        return self.state

    def set_last_poll(self, integration_id, state):
        self.state = state
        self.history.append((integration_id, copy.deepcopy(state)))


class TestStaticHelpers:
    def test_calculate_event_category_value_simple(self):
        result = ERSmartSynchronizer.calculate_event_category_value(ca_label="FOASF")
        assert result == "foasf"

    def test_calculate_event_category_value_with_cm(self):
        result = ERSmartSynchronizer.calculate_event_category_value(
            ca_label="FOASF", cm_label="Monitoring"
        )
        assert result == "foasf_monitoring"

    def test_calculate_event_category_value_special_chars(self):
        result = ERSmartSynchronizer.calculate_event_category_value(
            ca_label="TestArea"
        )
        assert result == "testarea"

    def test_calculate_event_category_value_empty_raises(self):
        with pytest.raises(ValueError):
            ERSmartSynchronizer.calculate_event_category_value(ca_label="")

    def test_get_identifier_from_ca_label(self):
        assert (
            ERSmartSynchronizer.get_identifier_from_ca_label("Some Name [SONM]")
            == "SONM"
        )

    def test_get_identifier_from_ca_label_no_brackets(self):
        assert ERSmartSynchronizer.get_identifier_from_ca_label("No Brackets") == ""

    def test_get_identifier_from_ca_label_multiple_brackets(self):
        assert ERSmartSynchronizer.get_identifier_from_ca_label("[A] and [B]") == "B"

    def test_get_identifier_from_ca_label_none(self):
        # Regression: None must not raise.
        assert ERSmartSynchronizer.get_identifier_from_ca_label(None) == ""

    def test_get_identifier_from_ca_label_default(self):
        assert ERSmartSynchronizer.get_identifier_from_ca_label() == ""


class TestDiffHelpers:
    def test_list_diff_length_mismatch(self):
        from er_smart_sync.synchronizer import _summarize_list_diff

        result = _summarize_list_diff([1, 2, 3], [1, 2])
        assert "length new=3 existing=2" in result

    def test_list_diff_element_mismatch_names_index(self):
        from er_smart_sync.synchronizer import _summarize_list_diff

        result = _summarize_list_diff(["a", "b", "c"], ["a", "x", "c"])
        assert "first-diff-at-index=1" in result
        assert "new='b'" in result and "existing='x'" in result

    def test_list_diff_equal_lists_returns_placeholder(self):
        from er_smart_sync.synchronizer import _summarize_list_diff

        assert _summarize_list_diff([1, 2], [1, 2]) == "(equal but != returned True?)"

    def test_value_diff_routes_dicts_to_schema_diff(self):
        from er_smart_sync.synchronizer import _summarize_value_diff

        result = _summarize_value_diff({"a": 1}, {"a": 2})
        assert "a: new=1 existing=2" in result

    def test_value_diff_type_mismatch(self):
        from er_smart_sync.synchronizer import _summarize_value_diff

        result = _summarize_value_diff([1, 2], {"a": 1})
        assert "type-mismatch" in result and "list" in result and "dict" in result

    def test_schema_diff_recurses_into_nested_list(self):
        from er_smart_sync.synchronizer import _summarize_schema_diff

        # Long list values trigger the recursion branch (repr > 120 chars).
        long_new = [f"item_{i}" for i in range(20)]
        long_existing = long_new[:10] + ["different"] + long_new[11:]
        result = _summarize_schema_diff(
            {"definition": long_new}, {"definition": long_existing}
        )
        assert "definition" in result
        assert "first-diff-at-index=10" in result


class TestDatamodelSync:
    def test_push_smart_ca_datamodel_requires_dm(self, sync_config, mock_er_client):
        sync = ERSmartSynchronizer(
            config=sync_config, er_client=mock_er_client, smart_client=MagicMock()
        )
        with pytest.raises(ValueError, match="dm is required"):
            sync.push_smart_ca_datamodel_to_earthranger(
                dm=None, smart_ca_uuid="uuid", ca_identifier="TEST"
            )

    def test_push_smart_datamodel_to_earthranger_unbracketed_label_raises(
        self, sync_config, mock_er_client
    ):
        """API-path runs against a CA whose label has no [CODE] bracket should
        raise a clear, actionable error naming the label and the fix."""
        sync = ERSmartSynchronizer(
            config=sync_config, er_client=mock_er_client, smart_client=MagicMock()
        )
        sync.smart_client.get_data_model.return_value = MagicMock()
        ca = MagicMock()
        ca.label = "Conservation Area Without Brackets"

        with pytest.raises(ValueError) as excinfo:
            sync.push_smart_datamodel_to_earthranger(
                smart_ca_uuid="some-ca-uuid", ca=ca,
            )

        message = str(excinfo.value)
        assert "Conservation Area Without Brackets" in message
        assert "bracketed" in message.lower() or "[" in message
        assert "some-ca-uuid" in message

    def test_creates_event_category_when_missing(self, sync_config, mock_er_client):
        mock_er_client.get_event_categories.return_value = []

        dm = MagicMock()
        dm.export_as_dict.return_value = {"categories": []}

        with patch(
            "er_smart_sync.synchronizer.build_event_types",
            return_value=[],
        ):
            sync = ERSmartSynchronizer(
                config=sync_config,
                er_client=mock_er_client,
                smart_client=MagicMock(),
            )
            sync.push_smart_ca_datamodel_to_earthranger(
                dm=dm, smart_ca_uuid="uuid", ca_identifier="TEST"
            )

        mock_er_client.post_event_category.assert_called_once()
        posted = mock_er_client.post_event_category.call_args.kwargs["data"]
        assert posted["value"] == "test"

    def test_v1_event_type_uses_category_slug(
        self, sync_config, mock_er_client
    ):
        """ER's event_type endpoint resolves `category` by slug for both v1
        and v2. Sending the UUID 400s with `event_category: <uuid> does not
        exist` because the slug lookup can't match a UUID string."""
        from smartconnect.er_sync_utils import EREventType

        category_uuid = "11111111-1111-1111-1111-111111111111"
        mock_er_client.get_event_categories.return_value = [
            {"id": category_uuid, "value": "test", "display": "TEST"}
        ]
        mock_er_client.get_event_types.return_value = []

        dm = MagicMock()
        dm.export_as_dict.return_value = {"categories": []}

        et = EREventType(value="some_event", display="Some Event", is_active=True)
        with patch(
            "er_smart_sync.synchronizer.build_event_types",
            return_value=[et],
        ):
            sync = ERSmartSynchronizer(
                config=sync_config,
                er_client=mock_er_client,
                smart_client=MagicMock(),
            )
            sync.push_smart_ca_datamodel_to_earthranger(
                dm=dm, smart_ca_uuid="uuid", ca_identifier="TEST"
            )

        mock_er_client.post_event_type.assert_called_once()
        posted = mock_er_client.post_event_type.call_args.kwargs["event_type"]
        assert posted["category"] == "test", (
            f"v1 should POST category as slug; got {posted['category']!r}"
        )

    def test_v2_event_type_uses_category_slug(
        self, sync_config_v2, mock_er_client
    ):
        """v2 ER expects event_type.category as the category's value (slug)."""
        from er_smart_sync.smart_to_er_v2 import ERV2EventType

        category_uuid = "22222222-2222-2222-2222-222222222222"
        mock_er_client.get_event_categories.return_value = [
            {"id": category_uuid, "value": "test", "display": "TEST"}
        ]
        mock_er_client.get_event_types.return_value = []

        dm = MagicMock()
        dm.export_as_dict.return_value = {"categories": []}

        et = ERV2EventType(value="some_event", display="Some Event")
        with patch(
            "er_smart_sync.synchronizer.build_event_types_v2",
            return_value=[et],
        ), patch(
            "er_smart_sync.synchronizer.build_choice_sets",
            return_value=[],
        ), patch(
            "er_smart_sync.synchronizer.upsert_choices",
            return_value=__import__(
                "er_smart_sync.choices", fromlist=["ChoicesStats"]
            ).ChoicesStats(),
        ):
            sync = ERSmartSynchronizer(
                config=sync_config_v2,
                er_client=mock_er_client,
                smart_client=MagicMock(),
            )
            sync.push_smart_ca_datamodel_to_earthranger(
                dm=dm, smart_ca_uuid="uuid", ca_identifier="TEST"
            )

        mock_er_client.post_event_type.assert_called_once()
        posted = mock_er_client.post_event_type.call_args.kwargs["event_type"]
        assert posted["category"] == "test", (
            f"v2 should POST category as slug; got {posted['category']!r}"
        )

    def test_event_type_post_uses_slug_after_creating_category(
        self, sync_config, mock_er_client
    ):
        """When we POST a fresh event category, the follow-up event-type POST
        still keys on slug — independent of whatever id ER assigns server-side."""
        from smartconnect.er_sync_utils import EREventType

        mock_er_client.get_event_categories.return_value = []
        mock_er_client.post_event_category.return_value = {
            "id": "33333333-3333-3333-3333-333333333333",
            "value": "test",
            "display": "TEST",
        }
        mock_er_client.get_event_types.return_value = []

        dm = MagicMock()
        dm.export_as_dict.return_value = {"categories": []}

        et = EREventType(value="some_event", display="Some Event", is_active=True)
        with patch(
            "er_smart_sync.synchronizer.build_event_types",
            return_value=[et],
        ):
            sync = ERSmartSynchronizer(
                config=sync_config,
                er_client=mock_er_client,
                smart_client=MagicMock(),
            )
            sync.push_smart_ca_datamodel_to_earthranger(
                dm=dm, smart_ca_uuid="uuid", ca_identifier="TEST"
            )

        mock_er_client.post_event_type.assert_called_once()
        posted = mock_er_client.post_event_type.call_args.kwargs["event_type"]
        assert posted["category"] == "test", (
            f"event-type POST must use category slug; got {posted['category']!r}"
        )

    def test_dedup_collapses_same_value_to_one_post(
        self, sync_config, mock_er_client, caplog
    ):
        """SMART builder occasionally emits multiple distinct event types
        with the same value (CM-variant ambiguity). Without dedup the same ER
        row gets PATCHed N times per run and never settles."""
        from smartconnect.er_sync_utils import EREventType

        mock_er_client.get_event_categories.return_value = [
            {"id": "cat-id", "value": "test", "display": "TEST"}
        ]
        mock_er_client.get_event_types.return_value = []

        ets = [
            EREventType(value="dupe", display="A variant", is_active=True),
            EREventType(value="dupe", display="B variant", is_active=True),
            EREventType(value="dupe", display="C variant", is_active=True),
        ]
        dm = MagicMock()
        dm.export_as_dict.return_value = {"categories": []}
        with patch(
            "er_smart_sync.synchronizer.build_event_types",
            return_value=ets,
        ):
            sync = ERSmartSynchronizer(
                config=sync_config,
                er_client=mock_er_client,
                smart_client=MagicMock(),
            )
            with caplog.at_level("WARNING"):
                sync.push_smart_ca_datamodel_to_earthranger(
                    dm=dm, smart_ca_uuid="uuid", ca_identifier="TEST"
                )

        assert mock_er_client.post_event_type.call_count == 1, (
            "dedup must collapse same-value variants to one POST"
        )
        assert any(
            "duplicate value 'dupe'" in r.message and "alphabetical" in r.message
            for r in caplog.records
        )

    def test_dedup_picks_variant_matching_existing_display(
        self, sync_config, mock_er_client, caplog
    ):
        """For idempotent re-runs, the picker prefers the variant whose
        display matches what ER already has — so noop runs stay noop."""
        from smartconnect.er_sync_utils import EREventType

        mock_er_client.get_event_categories.return_value = [
            {"id": "cat-id", "value": "test", "display": "TEST"}
        ]
        mock_er_client.get_event_types.return_value = [
            {
                "value": "dupe",
                "display": "B variant",
                "is_active": True,
                "schema": "{}",
            }
        ]

        ets = [
            EREventType(value="dupe", display="A variant", is_active=True),
            EREventType(value="dupe", display="B variant", is_active=True),
            EREventType(value="dupe", display="C variant", is_active=True),
        ]
        dm = MagicMock()
        dm.export_as_dict.return_value = {"categories": []}
        with patch(
            "er_smart_sync.synchronizer.build_event_types",
            return_value=ets,
        ):
            sync = ERSmartSynchronizer(
                config=sync_config,
                er_client=mock_er_client,
                smart_client=MagicMock(),
            )
            with caplog.at_level("WARNING"):
                sync.push_smart_ca_datamodel_to_earthranger(
                    dm=dm, smart_ca_uuid="uuid", ca_identifier="TEST"
                )

        # Match-existing fired, and since the chosen variant equals what's in
        # ER, no PATCH should be issued.
        assert any(
            "matched existing ER display" in r.message for r in caplog.records
        )
        assert mock_er_client.patch_event_type.call_count == 0
        assert mock_er_client.post_event_type.call_count == 0

    def test_skips_event_category_creation_when_exists(
        self, sync_config, mock_er_client
    ):
        mock_er_client.get_event_categories.return_value = [
            {"value": "test", "display": "TEST"}
        ]

        dm = MagicMock()
        dm.export_as_dict.return_value = {"categories": []}

        with patch(
            "er_smart_sync.synchronizer.build_event_types",
            return_value=[],
        ):
            sync = ERSmartSynchronizer(
                config=sync_config,
                er_client=mock_er_client,
                smart_client=MagicMock(),
            )
            sync.push_smart_ca_datamodel_to_earthranger(
                dm=dm, smart_ca_uuid="uuid", ca_identifier="TEST"
            )

        mock_er_client.post_event_category.assert_not_called()

    def test_recovers_from_duplicate_key_on_create_category(
        self, sync_config, mock_er_client
    ):
        # First call: category missing. Second call (after duplicate-key error):
        # category appeared (race with a concurrent sync run).
        mock_er_client.get_event_categories.side_effect = [
            [],
            [{"id": "cat-1", "value": "test", "display": "TEST"}],
        ]
        mock_er_client.post_event_category.side_effect = Exception(
            "duplicate key value violates unique constraint"
        )

        dm = MagicMock()
        dm.export_as_dict.return_value = {"categories": []}

        with patch(
            "er_smart_sync.synchronizer.build_event_types",
            return_value=[],
        ):
            sync = ERSmartSynchronizer(
                config=sync_config,
                er_client=mock_er_client,
                smart_client=MagicMock(),
            )
            # Must not raise; must continue and refetch.
            sync.push_smart_ca_datamodel_to_earthranger(
                dm=dm, smart_ca_uuid="uuid", ca_identifier="TEST"
            )

        assert mock_er_client.get_event_categories.call_count == 2

    def test_raises_on_unexpected_post_category_error(
        self, sync_config, mock_er_client
    ):
        mock_er_client.get_event_categories.return_value = []
        mock_er_client.post_event_category.side_effect = Exception(
            "Some other ER error"
        )

        dm = MagicMock()
        dm.export_as_dict.return_value = {"categories": []}

        with patch(
            "er_smart_sync.synchronizer.build_event_types",
            return_value=[],
        ):
            sync = ERSmartSynchronizer(
                config=sync_config,
                er_client=mock_er_client,
                smart_client=MagicMock(),
            )
            with pytest.raises(Exception, match="Some other ER error"):
                sync.push_smart_ca_datamodel_to_earthranger(
                    dm=dm, smart_ca_uuid="uuid", ca_identifier="TEST"
                )

    def test_create_or_update_event_types_handles_none(
        self, sync_config, mock_er_client
    ):
        # Regression: previously an unbound `event_type` reference in the
        # except handler would crash when the input list was None/empty.
        sync = ERSmartSynchronizer(
            config=sync_config,
            er_client=mock_er_client,
            smart_client=MagicMock(),
        )
        sync.create_or_update_er_event_types(
            event_category={"value": "test"}, event_types=None
        )
        sync.create_or_update_er_event_types(
            event_category={"value": "test"}, event_types=[]
        )

    def test_create_or_update_event_types_continues_after_failure(
        self, sync_config, mock_er_client
    ):
        # If one event type fails, subsequent ones should still be processed.
        mock_er_client.get_event_types.return_value = []

        good = MagicMock()
        good.value = "good"
        good.is_active = True
        bad = MagicMock()
        bad.value = "bad"
        bad.is_active = True

        sync = ERSmartSynchronizer(
            config=sync_config,
            er_client=mock_er_client,
            smart_client=MagicMock(),
        )
        with patch.object(
            sync, "_create_event_type", side_effect=[Exception("boom"), None]
        ) as create_mock:
            sync.create_or_update_er_event_types(
                event_category={"value": "test"}, event_types=[bad, good]
            )

        assert create_mock.call_count == 2

    def test_create_event_type_falls_back_to_patch_on_duplicate_value(
        self, sync_config, mock_er_client
    ):
        # ER's unique constraint on event type value is per-tenant, not per
        # (value, category). If our local cache missed an existing record we
        # POST → 500 with "duplicate key". The synchronizer must recover by
        # re-fetching and patching instead of failing the whole run.
        existing_in_er = {
            "id": "et-existing-1",
            "value": "the_value",
            "category": {"value": "different_category"},
            "is_active": True,
            "display": "old display",
            "schema": "{}",
        }

        # First get returns empty (our cache misses the existing one).
        # The fallback call inside _create_event_type returns the truth.
        mock_er_client.get_event_types.side_effect = [
            [],
            [existing_in_er],
        ]
        mock_er_client.post_event_type.side_effect = Exception(
            "duplicate key value violates unique constraint "
            '"activity_eventtype_unique_value_across_tenants"'
        )

        from smartconnect.er_sync_utils import EREventType

        event_type = EREventType(
            value="the_value", display="new display", is_active=True
        )

        sync = ERSmartSynchronizer(
            config=sync_config,
            er_client=mock_er_client,
            smart_client=MagicMock(),
        )
        sync.create_or_update_er_event_types(
            event_category={"value": "the_category"},
            event_types=[event_type],
        )

        # POST attempted (and failed with duplicate-key).
        assert mock_er_client.post_event_type.called
        # PATCH took over.
        assert mock_er_client.patch_event_type.called
        # Stats: not counted as a fresh create; counted as an update.
        assert sync.datamodel_stats["event_types_created"] == 0
        assert sync.datamodel_stats["event_types_updated"] == 1

    def test_each_event_type_logged_at_debug(self, sync_config, mock_er_client, caplog):
        # With -v on the CLI we route our logger to DEBUG, and each event
        # type should produce a log line — including unchanged ones — so
        # users can see exactly what's being checked.
        import logging

        existing = {
            "id": "et-1",
            "value": "the_value",
            "category": {"value": "cat"},
            "is_active": True,
            "display": "Display",
            "schema": "{}",
        }
        mock_er_client.get_event_types.return_value = [existing]

        from smartconnect.er_sync_utils import EREventType

        same = EREventType(value="the_value", display="Display", is_active=True)

        sync = ERSmartSynchronizer(
            config=sync_config,
            er_client=mock_er_client,
            smart_client=MagicMock(),
        )

        with caplog.at_level(logging.DEBUG, logger="er_smart_sync.synchronizer"):
            sync.create_or_update_er_event_types(
                event_category={"value": "cat"}, event_types=[same]
            )

        messages = [r.getMessage() for r in caplog.records]
        assert any("Checking event type" in m for m in messages)
        assert any("Unchanged" in m for m in messages)

    def test_lookup_matches_on_value_alone_across_categories(
        self, sync_config, mock_er_client
    ):
        # An existing event type with the same value but a different category
        # must be found by the lookup so we patch (not POST → 500).
        existing_in_er = {
            "id": "et-1",
            "value": "the_value",
            "category": {"value": "category_A"},
            "is_active": True,
            "display": "old display",
            "schema": "{}",
        }
        mock_er_client.get_event_types.return_value = [existing_in_er]

        from smartconnect.er_sync_utils import EREventType

        event_type = EREventType(
            value="the_value", display="new display", is_active=True
        )

        sync = ERSmartSynchronizer(
            config=sync_config,
            er_client=mock_er_client,
            smart_client=MagicMock(),
        )
        sync.create_or_update_er_event_types(
            event_category={"value": "category_B"},  # different category
            event_types=[event_type],
        )

        # Should PATCH, not POST.
        mock_er_client.post_event_type.assert_not_called()
        assert mock_er_client.patch_event_type.called
        assert sync.datamodel_stats["event_types_updated"] == 1

    def test_synchronizer_passes_cm_variant_mode_to_builder(
        self, sync_config_v2, mock_er_client
    ):
        """cm_variant_mode from config is forwarded as a kwarg to build_event_types_v2."""
        sync_config_v2.earthranger.cm_variant_mode = "consolidate"
        sync = ERSmartSynchronizer(
            config=sync_config_v2,
            er_client=mock_er_client,
            smart_client=MagicMock(),
        )
        dm = MagicMock()
        dm.export_as_dict.return_value = {"categories": [], "attributes": []}
        mock_er_client.get_event_categories.return_value = [
            {"id": "c", "value": "test", "display": "T"}
        ]
        mock_er_client.get_event_types.return_value = []
        with patch(
            "er_smart_sync.synchronizer.build_event_types_v2", return_value=[]
        ) as mock_build, patch(
            "er_smart_sync.synchronizer.build_choice_sets", return_value=[]
        ), patch(
            "er_smart_sync.synchronizer.upsert_choices",
            return_value=__import__(
                "er_smart_sync.choices", fromlist=["ChoicesStats"]
            ).ChoicesStats(),
        ):
            sync.push_smart_ca_datamodel_to_earthranger(
                dm=dm, smart_ca_uuid="u", ca_identifier="TEST"
            )
        assert mock_build.call_args.kwargs["cm_variant_mode"] == "consolidate"


def _event_dict(updated_at, serial_number=1001):
    return {
        "id": str(uuid4()),
        "serial_number": serial_number,
        "title": f"Event {serial_number}",
        "event_type": "test_type",
        "event_details": {},
        "patrols": [],
        "files": [],
        "time": updated_at.isoformat(),
        "created_at": updated_at.isoformat(),
        "updated_at": updated_at.isoformat(),
        "priority": 0,
        "priority_label": "Gray",
        "state": "active",
        "url": f"https://test.pamdas.org/api/v1.0/activity/events/{serial_number}",
    }


class TestEventSync:
    def test_synchronize_er_events_publishes(
        self,
        sync_config,
        mock_er_client,
        mock_smart_client,
        mock_publisher,
        mock_state_store,
    ):
        sync = ERSmartSynchronizer(
            config=sync_config,
            er_client=mock_er_client,
            smart_client=mock_smart_client,
            publisher=mock_publisher,
            state_store=mock_state_store,
            observations_topic="test-topic",
        )
        sync.synchronize_er_events()

        assert mock_er_client.get_events.called
        assert mock_publisher.publish.called
        assert mock_state_store.set_last_poll.called

    def test_per_event_checkpointing_advances_state(
        self,
        sync_config,
        mock_er_client,
        mock_smart_client,
        mock_publisher,
    ):
        # Three events with distinct updated_at; state should advance after each.
        now = datetime.now(tz=timezone.utc)
        events = [
            _event_dict(now - timedelta(minutes=3), 1),
            _event_dict(now - timedelta(minutes=2), 2),
            _event_dict(now - timedelta(minutes=1), 3),
        ]
        mock_er_client.get_events.return_value = events
        state_store = CapturingStateStore()

        sync = ERSmartSynchronizer(
            config=sync_config,
            er_client=mock_er_client,
            smart_client=mock_smart_client,
            publisher=mock_publisher,
            state_store=state_store,
            observations_topic="test-topic",
        )
        sync.synchronize_er_events()

        # 3 per-event checkpoints + 1 final = 4 set_last_poll calls.
        assert len(state_store.history) == 4
        # Each per-event checkpoint should match the event's updated_at.
        for i in range(3):
            saved_at = state_store.history[i][1].event_last_poll_at
            assert saved_at.isoformat() == events[i]["updated_at"]

    def test_event_publish_failure_halts_at_last_successful_checkpoint(
        self,
        sync_config,
        mock_er_client,
        mock_smart_client,
    ):
        # Simulate the 2nd event failing to publish.
        now = datetime.now(tz=timezone.utc)
        events = [
            _event_dict(now - timedelta(minutes=3), 1),
            _event_dict(now - timedelta(minutes=2), 2),
            _event_dict(now - timedelta(minutes=1), 3),
        ]
        mock_er_client.get_events.return_value = events
        state_store = CapturingStateStore()

        publisher = MagicMock()
        publisher.publish.side_effect = [None, RuntimeError("network blip")]

        sync = ERSmartSynchronizer(
            config=sync_config,
            er_client=mock_er_client,
            smart_client=mock_smart_client,
            publisher=publisher,
            state_store=state_store,
            observations_topic="test-topic",
        )
        with pytest.raises(RuntimeError, match="network blip"):
            sync.synchronize_er_events()

        # State was checkpointed at the first event's updated_at before the crash.
        assert len(state_store.history) == 1
        saved_at = state_store.history[0][1].event_last_poll_at
        assert saved_at.isoformat() == events[0]["updated_at"]


class TestPatrolSkip:
    def test_patrol_with_missing_leader_segment_is_skipped_entirely(
        self,
        sync_config,
        mock_er_client,
        mock_smart_client,
        mock_publisher,
        mock_state_store,
    ):
        # Patrol has one segment with no leader; the whole patrol must be skipped
        # without fetching segment events or track points.
        now = datetime.now(tz=timezone.utc).isoformat()
        mock_er_client.get_patrols.return_value = [
            {
                "id": str(uuid4()),
                "serial_number": 9001,
                "title": "Broken Patrol",
                "state": "open",
                "patrol_segments": [
                    {
                        "id": str(uuid4()),
                        "patrol_type": "routine",
                        "leader": None,
                        "start_location": {
                            "latitude": -1.5,
                            "longitude": 36.8,
                        },
                        "events": [
                            {
                                "id": str(uuid4()),
                                "event_type": "test",
                                "updated_at": now,
                            }
                        ],
                        "event_details": [],
                        "track_points": [],
                        "updates": [],
                    }
                ],
                "updates": [
                    {
                        "message": "x",
                        "time": now,
                        "user": {"id": str(uuid4())},
                        "type": "update_patrol",
                    }
                ],
                "files": [],
            }
        ]

        sync = ERSmartSynchronizer(
            config=sync_config,
            er_client=mock_er_client,
            smart_client=mock_smart_client,
            publisher=mock_publisher,
            state_store=mock_state_store,
            observations_topic="test-topic",
        )
        sync.synchronize_er_patrols()

        # No segment events fetched, no track points, no publish.
        mock_er_client.get_events.assert_not_called()
        mock_er_client.get_subject_observations.assert_not_called()
        mock_publisher.publish.assert_not_called()

    def test_patrol_skip_reason_helper(self):
        seg_no_start = MagicMock(start_location=None, leader=MagicMock(id="x"))
        seg_no_leader = MagicMock(start_location=MagicMock(), leader=None)
        seg_valid = MagicMock(start_location=MagicMock(), leader=MagicMock(id="x"))

        patrol_missing_start = MagicMock(patrol_segments=[seg_valid, seg_no_start])
        patrol_missing_leader = MagicMock(patrol_segments=[seg_no_leader])
        patrol_valid = MagicMock(patrol_segments=[seg_valid])

        assert (
            ERSmartSynchronizer._patrol_skip_reason(patrol_missing_start)
            == "no_start_location"
        )
        assert (
            ERSmartSynchronizer._patrol_skip_reason(patrol_missing_leader)
            == "no_segment_leader"
        )
        assert ERSmartSynchronizer._patrol_skip_reason(patrol_valid) is None


class TestBatchedEventFetch:
    def test_single_get_events_call_per_patrol(
        self, sync_config, mock_er_client, mock_smart_client
    ):
        # Patrol with 3 segment events should yield exactly one get_events
        # call (the batched call), not 3.
        now = datetime.now(tz=timezone.utc).isoformat()
        event_ids = [str(uuid4()) for _ in range(3)]
        mock_er_client.get_patrols.return_value = [
            {
                "id": str(uuid4()),
                "serial_number": 9100,
                "title": "Patrol with events",
                "state": "open",
                "patrol_segments": [
                    {
                        "id": str(uuid4()),
                        "patrol_type": "routine",
                        "leader": {
                            "id": str(uuid4()),
                            "name": "R1",
                            "subject_subtype": "ranger",
                            "additional": {},
                            "is_active": True,
                        },
                        "start_location": {
                            "latitude": -1.5,
                            "longitude": 36.8,
                        },
                        "events": [
                            {
                                "id": eid,
                                "event_type": "test",
                                "updated_at": now,
                            }
                            for eid in event_ids
                        ],
                        "event_details": [],
                        "track_points": [],
                        "updates": [],
                    }
                ],
                "updates": [
                    {
                        "message": "x",
                        "time": now,
                        "user": {"id": str(uuid4())},
                        "type": "update_patrol",
                    }
                ],
                "files": [],
            }
        ]
        # get_events returns full event details for all IDs in one call.
        mock_er_client.get_events.return_value = [
            _event_dict(datetime.fromisoformat(now), serial_number=i) for i in range(3)
        ]
        # Override the IDs so the matching keys line up.
        for i, eid in enumerate(event_ids):
            mock_er_client.get_events.return_value[i]["id"] = eid

        sync = ERSmartSynchronizer(
            config=sync_config,
            er_client=mock_er_client,
            smart_client=mock_smart_client,
            state_store=CapturingStateStore(),
        )
        sync.synchronize_er_patrols()

        # Exactly one get_events call (the batched one).
        assert mock_er_client.get_events.call_count == 1


class TestRetry:
    def test_post_event_category_retries_on_transient_error(
        self, sync_config, mock_er_client
    ):
        # First call raises a transient error; second succeeds.
        mock_er_client.get_event_categories.return_value = []
        mock_er_client.post_event_category.side_effect = [
            ConnectionError("connection reset"),
            None,
        ]

        dm = MagicMock()
        dm.export_as_dict.return_value = {"categories": []}

        with patch(
            "er_smart_sync.synchronizer.build_event_types",
            return_value=[],
        ):
            sync = ERSmartSynchronizer(
                config=sync_config,
                er_client=mock_er_client,
                smart_client=MagicMock(),
            )
            sync.push_smart_ca_datamodel_to_earthranger(
                dm=dm, smart_ca_uuid="uuid", ca_identifier="TEST"
            )

        assert mock_er_client.post_event_category.call_count == 2

    def test_retry_does_not_retry_on_bad_credentials(self, sync_config, mock_er_client):
        from erclient.er_errors import ERClientBadCredentials

        mock_er_client.get_event_categories.return_value = []
        mock_er_client.post_event_category.side_effect = ERClientBadCredentials("nope")

        dm = MagicMock()
        dm.export_as_dict.return_value = {"categories": []}

        with patch(
            "er_smart_sync.synchronizer.build_event_types",
            return_value=[],
        ):
            sync = ERSmartSynchronizer(
                config=sync_config,
                er_client=mock_er_client,
                smart_client=MagicMock(),
            )
            with pytest.raises(ERClientBadCredentials):
                sync.push_smart_ca_datamodel_to_earthranger(
                    dm=dm, smart_ca_uuid="uuid", ca_identifier="TEST"
                )

        # Only one call — no retries on bad credentials.
        assert mock_er_client.post_event_category.call_count == 1

    @pytest.mark.parametrize(
        "msg",
        [
            "duplicate key value violates unique constraint",
            "Event Type with this Das tenant and Value already exists.",
            "event_category: <uuid> does not exist.",
            "400 Invalid JSON Schema: foo",
            "value too long for type character varying(100)",
        ],
    )
    def test_retry_short_circuits_on_permanent_400_messages(
        self, msg, sync_config, mock_er_client
    ):
        """4xx ER validation errors are permanent — retrying just delays the
        caller's recovery path (e.g. patch-on-duplicate) by ~7s."""
        mock_er_client.get_event_categories.return_value = []
        mock_er_client.post_event_category.side_effect = Exception(msg)

        dm = MagicMock()
        dm.export_as_dict.return_value = {"categories": []}

        with patch(
            "er_smart_sync.synchronizer.build_event_types",
            return_value=[],
        ):
            sync = ERSmartSynchronizer(
                config=sync_config,
                er_client=mock_er_client,
                smart_client=MagicMock(),
            )
            # Non-duplicate-key permanent errors propagate; duplicate-key gets
            # swallowed by the category-creation handler. Either way: one call.
            try:
                sync.push_smart_ca_datamodel_to_earthranger(
                    dm=dm, smart_ca_uuid="uuid", ca_identifier="TEST"
                )
            except Exception:
                pass

        assert mock_er_client.post_event_category.call_count == 1, (
            f"Permanent error %r should not retry" % msg
        )


class TestDatamodelCache:
    def test_synchronize_datamodel_fetches_categories_once(
        self, sync_config, mock_smart_client
    ):
        # With 2 CAs and 0 configurable models each, the cache means
        # get_event_categories runs once total, not twice.
        ca_uuids = ["ca-1", "ca-2"]
        sync_config.smart.ca_uuids = ca_uuids
        sync_config.smart.configurable_models_lists = {}

        er_client = MagicMock()
        er_client.get_event_categories.return_value = []
        er_client.get_event_types.return_value = []

        ca_obj = MagicMock(label="Some CA [TEST]")
        mock_smart_client.get_conservation_area.return_value = ca_obj
        mock_smart_client.get_data_model.return_value = MagicMock(
            export_as_dict=lambda: {"categories": []}
        )

        with patch(
            "er_smart_sync.synchronizer.build_event_types",
            return_value=[],
        ):
            sync = ERSmartSynchronizer(
                config=sync_config,
                er_client=er_client,
                smart_client=mock_smart_client,
            )
            sync.synchronize_datamodel()

        assert er_client.get_event_categories.call_count == 1
        assert er_client.get_event_types.call_count == 1


class TestJsonFileStateStore:
    def test_atomic_write_and_round_trip(self, tmp_path):
        from er_smart_sync.defaults import JsonFileStateStore

        store = JsonFileStateStore(path=str(tmp_path / "state.json"))
        state = SyncState(event_last_poll_at=datetime.now(tz=timezone.utc))
        store.set_last_poll("int-1", state)

        # Re-read in a fresh store and confirm the saved value comes back.
        store2 = JsonFileStateStore(path=str(tmp_path / "state.json"))
        loaded = store2.get_last_poll("int-1")
        assert loaded.event_last_poll_at is not None

    def test_no_temp_file_left_behind_after_write(self, tmp_path):
        from er_smart_sync.defaults import JsonFileStateStore

        store = JsonFileStateStore(path=str(tmp_path / "state.json"))
        store.set_last_poll("int-1", SyncState())

        leftovers = [f for f in tmp_path.iterdir() if f.name.startswith(".")]
        assert leftovers == []


class TestDefaults:
    def test_null_tracing_spans(self):
        tracing = NullTracing()
        with tracing.start_span("test") as span:
            span.set_attribute("key", "value")
            span.add_event("event")
        assert tracing.build_context_headers() == {}


class TestEventTypeVersionWiring:
    def test_synchronizer_reads_event_type_version_from_config(
        self, sync_config, mock_er_client
    ):
        # sync_config uses er_config which is pinned to v1.
        sync = ERSmartSynchronizer(
            config=sync_config, er_client=mock_er_client, smart_client=MagicMock()
        )
        assert sync._event_type_version == "v1"

    def test_synchronizer_v2_from_config(self, sync_config_v2, mock_er_client):
        sync = ERSmartSynchronizer(
            config=sync_config_v2, er_client=mock_er_client, smart_client=MagicMock()
        )
        assert sync._event_type_version == "v2"

    def test_push_smart_ca_uses_v2_builder_when_configured(
        self, sync_config_v2, mock_er_client
    ):
        mock_er_client.get_event_categories.return_value = []
        mock_er_client.get_event_types.return_value = []

        dm = MagicMock()
        dm.export_as_dict.return_value = {"categories": []}

        with (
            patch(
                "er_smart_sync.synchronizer.build_event_types_v2",
                return_value=[],
            ) as v2_builder,
            patch(
                "er_smart_sync.synchronizer.build_event_types",
                return_value=[],
            ) as v1_builder,
        ):
            sync = ERSmartSynchronizer(
                config=sync_config_v2,
                er_client=mock_er_client,
                smart_client=MagicMock(),
            )
            sync.push_smart_ca_datamodel_to_earthranger(
                dm=dm, smart_ca_uuid="uuid", ca_identifier="TEST"
            )

        v2_builder.assert_called_once()
        # Lock the config-to-builder threading: choices_base_url must flow.
        call_kwargs = v2_builder.call_args.kwargs
        assert (
            call_kwargs["choices_base_url"]
            == sync_config_v2.earthranger.choices_base_url
        )
        v1_builder.assert_not_called()

    def test_push_smart_ca_uses_v1_builder_when_configured(
        self, sync_config, mock_er_client
    ):
        mock_er_client.get_event_categories.return_value = []
        mock_er_client.get_event_types.return_value = []

        dm = MagicMock()
        dm.export_as_dict.return_value = {"categories": []}

        with (
            patch(
                "er_smart_sync.synchronizer.build_event_types",
                return_value=[],
            ) as v1_builder,
            patch(
                "er_smart_sync.synchronizer.build_event_types_v2",
                return_value=[],
            ) as v2_builder,
        ):
            sync = ERSmartSynchronizer(
                config=sync_config,
                er_client=mock_er_client,
                smart_client=MagicMock(),
            )
            sync.push_smart_ca_datamodel_to_earthranger(
                dm=dm, smart_ca_uuid="uuid", ca_identifier="TEST"
            )

        v1_builder.assert_called_once()
        v2_builder.assert_not_called()

    def test_v2_get_event_types_passes_version_kwarg(
        self, sync_config_v2, mock_er_client
    ):
        mock_er_client.get_event_categories.return_value = []
        mock_er_client.get_event_types.return_value = []

        sync = ERSmartSynchronizer(
            config=sync_config_v2,
            er_client=mock_er_client,
            smart_client=MagicMock(),
        )
        sync.config.smart.ca_uuids = []  # no CAs to iterate, snapshot still runs
        sync.synchronize_datamodel()

        get_calls = mock_er_client.get_event_types.call_args_list
        # snapshot at top of synchronize_datamodel must pass version="v2"
        assert any(c.kwargs.get("version") == "v2" for c in get_calls)

    def test_v2_post_event_type_passes_version_kwarg(
        self, sync_config_v2, mock_er_client
    ):
        mock_er_client.get_event_categories.return_value = []
        mock_er_client.get_event_types.return_value = []

        from er_smart_sync.smart_to_er_v2 import ERV2EventType

        et = ERV2EventType(value="v", display="V", category=None)
        with patch(
            "er_smart_sync.synchronizer.build_event_types_v2",
            return_value=[et],
        ):
            dm = MagicMock()
            dm.export_as_dict.return_value = {"categories": []}
            sync = ERSmartSynchronizer(
                config=sync_config_v2,
                er_client=mock_er_client,
                smart_client=MagicMock(),
            )
            sync.push_smart_ca_datamodel_to_earthranger(
                dm=dm, smart_ca_uuid="uuid", ca_identifier="TEST"
            )

        assert mock_er_client.post_event_type.called
        post_kwargs = mock_er_client.post_event_type.call_args.kwargs
        assert post_kwargs.get("version") == "v2"

    def test_v2_duplicate_key_logs_and_skips_no_patch(
        self, sync_config_v2, mock_er_client, caplog
    ):
        from er_smart_sync.smart_to_er_v2 import ERV2EventType

        mock_er_client.get_event_categories.return_value = []
        mock_er_client.get_event_types.return_value = []
        mock_er_client.post_event_type.side_effect = Exception(
            "duplicate key value violates unique constraint"
        )

        et = ERV2EventType(value="v", display="V", category=None)
        with patch(
            "er_smart_sync.synchronizer.build_event_types_v2",
            return_value=[et],
        ):
            dm = MagicMock()
            dm.export_as_dict.return_value = {"categories": []}
            sync = ERSmartSynchronizer(
                config=sync_config_v2,
                er_client=mock_er_client,
                smart_client=MagicMock(),
            )
            with caplog.at_level("WARNING"):
                sync.push_smart_ca_datamodel_to_earthranger(
                    dm=dm, smart_ca_uuid="uuid", ca_identifier="TEST"
                )

        # Post attempted; patch NOT attempted (no auto-recover on v2)
        assert mock_er_client.post_event_type.called
        assert not mock_er_client.patch_event_type.called
        assert any(
            "already exists" in r.message and "possibly under v1" in r.message
            for r in caplog.records
        )

    def test_event_type_needs_update_v2_dict_equal(
        self, sync_config_v2, mock_er_client
    ):
        from er_smart_sync.smart_to_er_v2 import ERV2EventType

        sync = ERSmartSynchronizer(
            config=sync_config_v2,
            er_client=mock_er_client,
            smart_client=MagicMock(),
        )
        et = ERV2EventType(
            value="v",
            display="V",
            category="c",
            event_schema={"json": {"a": 1}, "ui": {}},  # ty: ignore[unknown-argument]
        )
        existing = {
            "value": "v",
            "display": "V",
            "is_active": True,
            "category": "c",
            "readonly": False,
            "schema": {"json": {"a": 1}, "ui": {}},
        }
        assert sync._event_type_needs_update(et, existing) is False

    def test_event_type_needs_update_v2_dict_different(
        self, sync_config_v2, mock_er_client
    ):
        from er_smart_sync.smart_to_er_v2 import ERV2EventType

        sync = ERSmartSynchronizer(
            config=sync_config_v2,
            er_client=mock_er_client,
            smart_client=MagicMock(),
        )
        et = ERV2EventType(
            value="v",
            display="V",
            category="c",
            event_schema={"json": {"a": 2}, "ui": {}},  # ty: ignore[unknown-argument]
        )
        existing = {
            "value": "v",
            "display": "V",
            "is_active": True,
            "category": "c",
            "readonly": False,
            "schema": {"json": {"a": 1}, "ui": {}},
        }
        assert sync._event_type_needs_update(et, existing) is True

    def test_v2_runs_choices_phase_before_event_types(
        self, sync_config_v2, mock_er_client
    ):
        """build_choice_sets and upsert_choices called BEFORE post_event_type."""

        mock_er_client.get_event_categories.return_value = []
        mock_er_client.get_event_types.return_value = []

        dm = MagicMock()
        dm.export_as_dict.return_value = {"categories": []}

        order = []

        def fake_build_choice_sets(**kwargs):
            order.append("build_choice_sets")
            return []

        def fake_upsert_choices(**kwargs):
            from er_smart_sync.choices import ChoicesStats

            order.append("upsert_choices")
            return ChoicesStats()

        def fake_build_event_types(**kwargs):
            order.append("build_event_types_v2")
            return []

        with (
            patch(
                "er_smart_sync.synchronizer.build_choice_sets",
                side_effect=fake_build_choice_sets,
            ),
            patch(
                "er_smart_sync.synchronizer.upsert_choices",
                side_effect=fake_upsert_choices,
            ),
            patch(
                "er_smart_sync.synchronizer.build_event_types_v2",
                side_effect=fake_build_event_types,
            ),
        ):
            sync = ERSmartSynchronizer(
                config=sync_config_v2,
                er_client=mock_er_client,
                smart_client=MagicMock(),
            )
            sync.push_smart_ca_datamodel_to_earthranger(
                dm=dm, smart_ca_uuid="ca-1", ca_identifier="TEST"
            )

        assert order == [
            "build_choice_sets",
            "upsert_choices",
            "build_event_types_v2",
        ]

    def test_v1_path_does_not_call_choices(self, sync_config, mock_er_client):
        """v1 path is untouched — no build_choice_sets, no upsert_choices."""
        mock_er_client.get_event_categories.return_value = []
        mock_er_client.get_event_types.return_value = []

        dm = MagicMock()
        dm.export_as_dict.return_value = {"categories": []}

        with (
            patch(
                "er_smart_sync.synchronizer.build_choice_sets",
                return_value=[],
            ) as build_choices,
            patch(
                "er_smart_sync.synchronizer.upsert_choices",
            ) as upsert,
            patch(
                "er_smart_sync.synchronizer.build_event_types",
                return_value=[],
            ),
        ):
            sync = ERSmartSynchronizer(
                config=sync_config,
                er_client=mock_er_client,
                smart_client=MagicMock(),
            )
            sync.push_smart_ca_datamodel_to_earthranger(
                dm=dm, smart_ca_uuid="ca-1", ca_identifier="TEST"
            )

        build_choices.assert_not_called()
        upsert.assert_not_called()

    def test_v2_abort_event_types_when_choices_errored(
        self, sync_config_v2, mock_er_client, caplog
    ):
        from er_smart_sync.choices import ChoicesStats

        mock_er_client.get_event_categories.return_value = []
        mock_er_client.get_event_types.return_value = []

        dm = MagicMock()
        dm.export_as_dict.return_value = {"categories": []}

        stats = ChoicesStats(errored=2)

        with (
            patch(
                "er_smart_sync.synchronizer.build_choice_sets",
                return_value=[],
            ),
            patch(
                "er_smart_sync.synchronizer.upsert_choices",
                return_value=stats,
            ),
            patch(
                "er_smart_sync.synchronizer.build_event_types_v2",
            ) as build_types,
        ):
            sync = ERSmartSynchronizer(
                config=sync_config_v2,
                er_client=mock_er_client,
                smart_client=MagicMock(),
            )
            with caplog.at_level("WARNING"):
                sync.push_smart_ca_datamodel_to_earthranger(
                    dm=dm, smart_ca_uuid="ca-1", ca_identifier="TEST"
                )

        # build_event_types_v2 never called; no POSTs attempted.
        build_types.assert_not_called()
        mock_er_client.post_event_type.assert_not_called()
        # Clear warning log.
        assert any("Aborting event-type push" in r.message for r in caplog.records)

    def test_v2_choice_stats_merge_into_datamodel_stats(
        self, sync_config_v2, mock_er_client
    ):
        """The five new counters are populated from upsert_choices' return value."""
        from er_smart_sync.choices import ChoicesStats

        mock_er_client.get_event_categories.return_value = []
        mock_er_client.get_event_types.return_value = []

        dm = MagicMock()
        dm.export_as_dict.return_value = {"categories": []}

        stats = ChoicesStats(
            created=3, updated=1, unchanged=5, deactivated=2, errored=0
        )

        with (
            patch(
                "er_smart_sync.synchronizer.build_choice_sets",
                return_value=[],
            ),
            patch(
                "er_smart_sync.synchronizer.upsert_choices",
                return_value=stats,
            ),
            patch(
                "er_smart_sync.synchronizer.build_event_types_v2",
                return_value=[],
            ),
        ):
            sync = ERSmartSynchronizer(
                config=sync_config_v2,
                er_client=mock_er_client,
                smart_client=MagicMock(),
            )
            sync.push_smart_ca_datamodel_to_earthranger(
                dm=dm, smart_ca_uuid="ca-1", ca_identifier="TEST"
            )

        assert sync.datamodel_stats["choices_created"] == 3
        assert sync.datamodel_stats["choices_updated"] == 1
        assert sync.datamodel_stats["choices_unchanged"] == 5
        assert sync.datamodel_stats["choices_deactivated"] == 2
        assert sync.datamodel_stats["choices_errored"] == 0

    def test_v2_skip_choices_bypasses_choices_phase(
        self, sync_config_v2, mock_er_client
    ):
        mock_er_client.get_event_categories.return_value = []
        mock_er_client.get_event_types.return_value = []

        dm = MagicMock()
        dm.export_as_dict.return_value = {"categories": []}

        with (
            patch(
                "er_smart_sync.synchronizer.build_choice_sets",
            ) as build_choices,
            patch(
                "er_smart_sync.synchronizer.upsert_choices",
            ) as upsert,
            patch(
                "er_smart_sync.synchronizer.build_event_types_v2",
                return_value=[],
            ),
        ):
            sync = ERSmartSynchronizer(
                config=sync_config_v2,
                er_client=mock_er_client,
                smart_client=MagicMock(),
            )
            sync.skip_choices = True
            sync.push_smart_ca_datamodel_to_earthranger(
                dm=dm, smart_ca_uuid="ca-1", ca_identifier="TEST"
            )

        build_choices.assert_not_called()
        upsert.assert_not_called()

    def test_event_type_needs_update_v2_existing_string_schema_parses(
        self, sync_config_v2, mock_er_client
    ):
        """ER returns the v2 schema as a JSON string, not a dict.
        We must parse it before comparing or every re-run patches everything."""
        from er_smart_sync.smart_to_er_v2 import ERV2EventType

        sync = ERSmartSynchronizer(
            config=sync_config_v2,
            er_client=mock_er_client,
            smart_client=MagicMock(),
        )

        schema_dict = {"json": {"a": 1}, "ui": {"b": 2}}
        et = ERV2EventType(
            value="v", display="V", category="c",
            event_schema=schema_dict,
        )
        # ER returns the same content as a JSON string.
        existing = {
            "value": "v", "display": "V", "is_active": True,
            "category": "c", "readonly": False,
            "schema": json.dumps(schema_dict),
        }
        assert sync._event_type_needs_update(et, existing) is False

    def test_event_type_needs_update_v2_existing_string_schema_diff(
        self, sync_config_v2, mock_er_client
    ):
        """When the stringified schema differs in content, we still detect it."""
        from er_smart_sync.smart_to_er_v2 import ERV2EventType

        sync = ERSmartSynchronizer(
            config=sync_config_v2,
            er_client=mock_er_client,
            smart_client=MagicMock(),
        )

        et = ERV2EventType(
            value="v", display="V", category="c",
            event_schema={"json": {"a": 2}, "ui": {}},
        )
        existing = {
            "value": "v", "display": "V", "is_active": True,
            "category": "c", "readonly": False,
            "schema": json.dumps({"json": {"a": 1}, "ui": {}}),
        }
        assert sync._event_type_needs_update(et, existing) is True

    def test_event_type_needs_update_v2_readonly_drift(
        self, sync_config_v2, mock_er_client
    ):
        """v2 has a top-level `readonly` field outside the schema. If ER's
        existing record has readonly=True and we POST readonly=False (or
        vice versa), the synchronizer must detect this and PATCH."""
        from er_smart_sync.smart_to_er_v2 import ERV2EventType

        sync = ERSmartSynchronizer(
            config=sync_config_v2, er_client=mock_er_client,
            smart_client=MagicMock(),
        )
        schema_dict = {"json": {"a": 1}, "ui": {}}
        et = ERV2EventType(
            value="v", display="V", category="c",
            readonly=False,
            event_schema=schema_dict,
        )
        existing = {
            "value": "v", "display": "V", "is_active": True,
            "category": "c", "readonly": True,
            "schema": schema_dict,
        }
        assert sync._event_type_needs_update(et, existing) is True

    def test_event_type_needs_update_v2_category_drift(
        self, sync_config_v2, mock_er_client
    ):
        """v2 has a top-level `category` field outside the schema. If ER's
        existing record points at a different category, the synchronizer
        must detect and PATCH so the event type moves under the right one."""
        from er_smart_sync.smart_to_er_v2 import ERV2EventType

        sync = ERSmartSynchronizer(
            config=sync_config_v2, er_client=mock_er_client,
            smart_client=MagicMock(),
        )
        schema_dict = {"json": {"a": 1}, "ui": {}}
        et = ERV2EventType(
            value="v", display="V", category="new_category",
            event_schema=schema_dict,
        )
        existing = {
            "value": "v", "display": "V", "is_active": True,
            "category": "old_category", "readonly": False,
            "schema": schema_dict,
        }
        assert sync._event_type_needs_update(et, existing) is True

    def test_v1_duplicate_key_detects_cross_version_v2_record(
        self, sync_config, mock_er_client, caplog
    ):
        """When pushing v1 and the duplicate-key conflict is actually a v2
        record (uniqueness spans both versions), the v1 lookup won't find
        it and we'd surface a confusing generic error. Instead, detect the
        cross-version collision and log-and-skip with a clear message."""
        from smartconnect.er_sync_utils import EREventType

        mock_er_client.get_event_categories.return_value = []
        mock_er_client.get_event_types.return_value = []
        mock_er_client.post_event_type.side_effect = Exception(
            "duplicate key value violates unique constraint"
        )

        # The conflicting record IS in v2 records.
        def get_event_types_by_version(*, version, **kwargs):
            if version == "v2":
                return [{"value": "v", "id": "v2-uuid"}]
            return []

        mock_er_client.get_event_types.side_effect = get_event_types_by_version

        sync = ERSmartSynchronizer(
            config=sync_config, er_client=mock_er_client,
            smart_client=MagicMock(),
        )
        et = EREventType(value="v", display="V", is_active=True)
        with caplog.at_level("WARNING"):
            outcome = sync._create_event_type(et)

        assert outcome == "skipped"
        # No v1 PATCH attempted.
        mock_er_client.patch_event_type.assert_not_called()
        # The warning should mention the cross-version situation.
        assert any(
            "v2" in r.message and "exists" in r.message.lower()
            for r in caplog.records
        )
