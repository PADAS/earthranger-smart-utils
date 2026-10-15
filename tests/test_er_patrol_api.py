from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from erclient.er_errors import ERClientNotFound, ERClientPermissionDenied

from er_smart_sync.defaults import DryRunERClient
from er_smart_sync.er_patrol_api import ERForbidden, ERPatrolAPI


def _client():
    c = MagicMock()
    c._er_url.side_effect = lambda path, base_url=None: (
        f"https://er.test/api/v1.0/{path}"
    )
    c.auth_headers.return_value = {"Authorization": "Bearer t"}
    return c


def test_preview_features_and_flag():
    c = _client()
    c._get.return_value = {
        "version": "x",
        "preview_features": {"patrol_schemas": False},
    }
    api = ERPatrolAPI(c)
    assert api.preview_features() == {"patrol_schemas": False}
    assert api.patrol_schemas_enabled() is False
    c._get.assert_called_with(path="status", max_retries=0)


def test_patrol_type_get_create_patch():
    c = _client()
    c._get.side_effect = ERClientNotFound("nope")
    api = ERPatrolAPI(c)
    assert api.get_patrol_type("patrol_foot") is None
    c._get.side_effect = None
    c._get.return_value = {
        "id": "1",
        "value": "patrol_foot",
        "display": "Foot",
        "is_active": True,
    }
    assert api.get_patrol_type("patrol_foot")["id"] == "1"
    c._get.assert_called_with(
        path="activity/patrols/types/patrol_foot",
        params={"include_inactive": "true"},
        max_retries=0,
    )
    api.create_patrol_type(
        {"value": "patrol_foot", "display": "Foot", "is_active": True}
    )
    c._post.assert_called_with(
        path="activity/patrols/types",
        payload={"value": "patrol_foot", "display": "Foot", "is_active": True},
    )
    api.patch_patrol_type("patrol_foot", {"display": "On foot"})
    c._patch.assert_called_with(
        path="activity/patrols/types/patrol_foot", payload={"display": "On foot"}
    )


def test_forbidden_is_typed():
    c = _client()
    c._post.side_effect = ERClientPermissionDenied("no")
    with pytest.raises(ERForbidden):
        ERPatrolAPI(c).create_patrol_type({"value": "x", "display": "x"})


def test_segment_schema_get_404_and_put():
    c = _client()
    c._get.side_effect = ERClientNotFound("dark")
    api = ERPatrolAPI(c)
    assert api.get_segment_schema() is None
    c._get.side_effect = None
    c._get.return_value = {
        "id": "s",
        "value": "default",
        "display": "Default",
        "schema": {"json": {"properties": {}}, "ui": {}},
    }
    assert api.get_segment_schema() == {"json": {"properties": {}}, "ui": {}}
    c._get.assert_called_with(
        path="activity/patrols/segments/types/default",
        params={"include_schema": "true"},
        max_retries=0,
    )
    with patch("er_smart_sync.er_patrol_api.requests.put") as put:
        put.return_value.ok = True
        put.return_value.status_code = 200
        put.return_value.json.return_value = {"data": {"id": "s"}}
        assert api.put_segment_schema({"json": {}, "ui": {}}) == {"id": "s"}
        put.assert_called_once()
        args, kwargs = put.call_args
        assert (
            args[0]
            == "https://er.test/api/v1.0/activity/patrols/segments/types/default"
        )
        assert kwargs["json"] == {"schema": {"json": {}, "ui": {}}}
        assert kwargs["headers"]["Authorization"] == "Bearer t"
    with patch("er_smart_sync.er_patrol_api.requests.put") as put:
        put.return_value.ok = False
        put.return_value.status_code = 403
        put.return_value.text = "forbidden"
        with pytest.raises(ERForbidden):
            api.put_segment_schema({"json": {}, "ui": {}})


def test_put_is_skipped_under_dry_run():
    inner = _client()
    api = ERPatrolAPI(DryRunERClient(inner))
    with patch("er_smart_sync.er_patrol_api.requests.put") as put:
        out = api.put_segment_schema({"json": {}, "ui": {}})
        put.assert_not_called()
    assert out.get("dry_run") is True


def test_subjects_and_groups():
    c = _client()
    c.get_subjects.return_value = [
        {"id": "a", "name": "A", "additional": {}, "is_active": True}
    ]
    api = ERPatrolAPI(c)
    assert api.list_subjects()[0]["id"] == "a"
    api.create_subject(
        {"name": "B", "subject_subtype": "ranger", "additional": {}, "is_active": True}
    )
    c.post_subject.assert_called_once()
    api.patch_subject("a", {"is_active": False})
    c._patch.assert_called_with(path="subject/a", payload={"is_active": False})
    c.get_subjectgroups.return_value = [
        {"id": "g1", "name": "Rangers"},
        {"id": "g2", "name": "Vehicles"},
    ]
    assert api.find_subject_group("rangers")["id"] == "g1"
    assert api.find_subject_group("Nope") is None
    api.add_subjects_to_group("g1", ["a", "b"])
    c._post.assert_called_with(
        path="subjectgroup/g1/subjects/", payload=[{"id": "a"}, {"id": "b"}]
    )
    c.add_subjects_to_subjectgroup.assert_not_called()
    c.get_subjects.return_value = [{"id": "a"}, {"id": "z"}]
    assert api.group_subject_ids("g1") == {"a", "z"}


def test_patrol_config_404_means_none():
    c = _client()
    c._get.side_effect = ERClientNotFound("dark")
    assert ERPatrolAPI(c).get_patrol_config() is None
    c._get.side_effect = None
    c._get.return_value = {
        "teams": [{"id": "t", "value": "alpha", "display": "Alpha"}],
        "members": [{"id": "g1", "name": "Rangers"}],
    }
    cfg = ERPatrolAPI(c).get_patrol_config()
    assert cfg["teams"][0]["value"] == "alpha" and cfg["members"][0]["id"] == "g1"
    c._get.assert_called_with(path="activity/patrols/config/default", max_retries=0)


def _real_client():
    """A real ERClient with its HTTP layer stubbed: erclient's kwarg filtering runs."""
    from erclient import ERClient

    c = ERClient(
        service_root="https://er.test/api/v1.0",
        username="",
        password="",
        token="t",
        client_id="x",
    )
    c._get = MagicMock(return_value=[{"id": "a"}, {"id": "z"}])
    return c


def test_subject_queries_use_the_params_erclient_forwards():
    c = _real_client()
    api = ERPatrolAPI(c)
    assert api.group_subject_ids("g1") == {"a", "z"}
    c._get.assert_called_with(
        "subjects", params={"subject_group": "g1", "include_inactive": True}
    )
    api.list_subjects()
    c._get.assert_called_with("subjects", params={"include_inactive": True})


def test_group_add_is_skipped_under_dry_run():
    inner = _client()
    api = ERPatrolAPI(DryRunERClient(inner))
    api.add_subjects_to_group("g1", ["a"])
    inner._post.assert_not_called()
    inner.add_subjects_to_subjectgroup.assert_not_called()


def test_read_side_403_is_typed():
    c = _client()
    c.get_subjects.side_effect = ERClientPermissionDenied("no")
    c.get_subjectgroups.side_effect = ERClientPermissionDenied("no")
    api = ERPatrolAPI(c)
    with pytest.raises(ERForbidden):
        api.list_subjects()
    with pytest.raises(ERForbidden):
        api.group_subject_ids("g1")
    with pytest.raises(ERForbidden):
        api.find_subject_group("x")


def test_subject_lists_follow_every_page():
    c = _client()
    c.get_subjects.return_value = {"results": [{"id": "a"}], "next": "https://x/p2"}
    c._get.side_effect = [
        {"results": [{"id": "b"}], "next": "https://x/p3"},
        {"results": [{"id": "c"}], "next": None},
    ]
    api = ERPatrolAPI(c)
    assert [s["id"] for s in api.list_subjects()] == ["a", "b", "c"]
    c._get.assert_called_with(path="https://x/p3")
    c._get.side_effect = [{"results": [{"id": "z"}], "next": None}]
    assert api.group_subject_ids("g1") == {"a", "z"}


def test_resolved_config_me_and_event_types():
    c = _client()
    api = ERPatrolAPI(c)
    c._get.side_effect = ERClientNotFound("dark")
    assert api.get_patrol_config_resolved() is None
    c._get.side_effect = None
    c._get.return_value = {
        "members": [{"id": "s1", "is_active": True}],
        "assets": [],
        "teams": [],
    }
    assert api.get_patrol_config_resolved()["members"][0]["id"] == "s1"
    c._get.assert_called_with(
        path="activity/patrols/config/default/resolved", max_retries=0
    )
    c._get.return_value = {"is_superuser": False, "permissions": {"patrol": ["add"]}}
    assert api.get_me()["permissions"] == {"patrol": ["add"]}
    c._get.assert_called_with(path="user/me", max_retries=0)
    c.get_event_types.return_value = [{"value": "a"}]
    assert api.list_event_types() == [{"value": "a"}]
    c.get_event_types.assert_called_with(
        include_inactive=True, include_schema=False, version="v2"
    )
    c.get_event_types.side_effect = ERClientPermissionDenied("no")
    with pytest.raises(ERForbidden):
        api.list_event_types()


def test_patrol_type_lookup_includes_inactive():
    c = _client()
    c._get.return_value = {"value": "patrol_foot", "is_active": False}
    assert ERPatrolAPI(c).get_patrol_type("patrol_foot")["is_active"] is False
    c._get.assert_called_with(
        path="activity/patrols/types/patrol_foot",
        params={"include_inactive": "true"},
        max_retries=0,
    )


def test_patrol_and_event_reads_treat_404_as_absent():
    c = _client()
    c._get.side_effect = ERClientNotFound("nope")
    api = ERPatrolAPI(c)
    assert api.get_patrol("p") is None and api.get_event("e") is None
    assert api.get_source("smart:x") is None


def test_patrol_and_event_writes_use_the_documented_paths():
    c = _client()
    api = ERPatrolAPI(c)
    api.create_patrol({"id": "p"})
    c._post.assert_called_with(path="activity/patrols", payload={"id": "p"})
    api.patch_patrol("p", {"title": "t"})
    c._patch.assert_called_with(path="activity/patrols/p", payload={"title": "t"})
    api.create_event({"id": "e"})
    c._post.assert_called_with(path="activity/events", payload={"id": "e"})
    api.assign_source(
        "s1", "src", "2020-01-01T00:00:00+00:00", "2020-01-02T00:00:00+00:00"
    )
    c._post.assert_called_with(
        path="subject/s1/sources",
        payload={
            "source": "src",
            "assigned_range": {
                "lower": "2020-01-01T00:00:00+00:00",
                "upper": "2020-01-02T00:00:00+00:00",
            },
        },
    )


def test_post_observations_treats_409_as_already_there():
    from erclient.er_errors import ERClientException

    c = _client()
    c._post.side_effect = ERClientException("x\n\t 409 from ER. Message: dup")
    assert ERPatrolAPI(c).post_observations([{"recorded_at": "t"}]) is False
    c._post.side_effect = ERClientException("x\n\t 500 from ER. Message: boom")
    with pytest.raises(ERClientException):
        ERPatrolAPI(c).post_observations([{"recorded_at": "t"}])


def test_collection_event_type_is_only_incident_collection():
    """Only ER's stock incident_collection is used, found in either listing
    (it is often a v1-only type); another site's collection type is never
    borrowed for SMART waypoints."""
    c = _client()
    lists = {
        "v2": [{"value": "plain", "is_collection": False, "is_active": True}],
        "v1": [
            {"value": "incident_collection", "is_collection": True, "is_active": True}
        ],
    }
    c.get_event_types.side_effect = lambda **k: lists[k["version"]]
    assert ERPatrolAPI(c).collection_event_type() == "incident_collection"
    lists["v1"] = [
        {"value": "someones_test_incident", "is_collection": True, "is_active": True},
        {"value": "incident_collection", "is_collection": True, "is_active": False},
    ]
    assert ERPatrolAPI(c).collection_event_type() is None


def test_existence_checks_do_not_log_errors(caplog):
    """erclient logs every 404 at ERROR; a lookup that expects one must not
    fill an import's log with errors."""
    import logging

    def missing(**kwargs):
        logging.getLogger("ERClient").error("404 when calling x")
        raise ERClientNotFound("nope")

    c = _client()
    c._get.side_effect = missing
    with caplog.at_level(logging.INFO):
        assert ERPatrolAPI(c).get_event("e") is None
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]


def test_source_provider_created_only_when_missing():
    c = _client()
    c._get.return_value = {"results": [{"provider_key": "smart_patrol_import"}]}
    ERPatrolAPI(c).ensure_source_provider("smart_patrol_import", "SMART")
    c._post.assert_not_called()
    c._get.return_value = {"results": []}
    ERPatrolAPI(c).ensure_source_provider("smart_patrol_import", "SMART")
    c._post.assert_called_with(
        path="sourceproviders",
        payload={
            "provider_key": "smart_patrol_import",
            "display_name": "SMART",
            "additional": {},  # das: "This field is required."
        },
    )


def test_source_lookup_uses_the_list_endpoint():
    """das's source/<identifier> route rejects ':' so `smart:<uuid>` must be
    found through sources?manufacturer_id=…&provider=…."""
    c = _client()
    c._get.return_value = {
        "results": [
            {
                "id": "s1",
                "manufacturer_id": "smart:abc",
                "provider": "smart_patrol_import",
            }
        ]
    }
    assert ERPatrolAPI(c).get_source("smart:abc")["id"] == "s1"
    c._get.assert_called_with(
        path="sources",
        params={"manufacturer_id": "smart:abc", "provider": "smart_patrol_import"},
        max_retries=0,
    )
    c._get.return_value = {"results": []}
    assert ERPatrolAPI(c).get_source("smart:abc") is None


def test_event_ids_of_reads_every_segment():
    p = {
        "patrol_segments": [
            {"id": "s1", "events": [{"id": "e1"}, {"id": "e2"}]},
            {"id": "s2", "events": []},
            {"id": "s3"},
        ]
    }
    assert ERPatrolAPI.event_ids_of(p) == {"e1", "e2"}
    assert ERPatrolAPI.event_ids_of(None) == set()
