"""The EarthRanger calls the patrol migration needs that `erclient.ERClient`
does not wrap: site status, patrol types, the universal segment schema (PUT
only), the patrol configuration, subject updates and subject groups. Thin
on purpose: every method maps one endpoint, converts 404 to None where the
spec says so, and surfaces 403 as `ERForbidden` for the checklist.
"""

from __future__ import annotations

import logging

import requests
from erclient.er_errors import (
    ERClientException,
    ERClientNotFound,
    ERClientPermissionDenied,
)

logger = logging.getLogger(__name__)

STATUS_PATH = "status"
PATROL_TYPES_PATH = "activity/patrols/types"
SEGMENT_SCHEMA_PATH = "activity/patrols/segments/types/default"
PATROL_CONFIG_PATH = "activity/patrols/config/default"


class ERForbidden(Exception):
    """ER answered 403: the operator lacks a permission the step needs."""


def _is_dry_run(client) -> bool:
    # the wrapper proxies attribute access; isinstance on the inner class would be wrong
    return type(client).__name__ == "DryRunERClient"


def _results(page) -> list:
    if isinstance(page, dict):
        return list(page.get("results") or [])
    return list(page or [])


class ERPatrolAPI:
    def __init__(self, er_client):
        self.er = er_client

    # -- reads -------------------------------------------------------------
    def _get_or_none(self, path: str, **params):
        try:
            if params:
                return self.er._get(path=path, params=params, max_retries=0)
            return self.er._get(path=path, max_retries=0)
        except ERClientNotFound:
            return None
        except ERClientPermissionDenied as e:
            raise ERForbidden(f"GET {path}: {e}") from None

    def preview_features(self) -> dict:
        status = self.er._get(path=STATUS_PATH, max_retries=0) or {}
        return status.get("preview_features") or {}

    def patrol_schemas_enabled(self) -> bool:
        return bool(self.preview_features().get("patrol_schemas"))

    def get_patrol_type(self, value: str) -> dict | None:
        # das hides inactive types unless asked; an inactive type must be found,
        # not reported missing (that would invite a duplicate).
        return self._get_or_none(
            f"{PATROL_TYPES_PATH}/{value}", include_inactive="true"
        )

    def get_segment_schema(self) -> dict | None:
        doc = self._get_or_none(SEGMENT_SCHEMA_PATH, include_schema="true")
        return None if doc is None else doc.get("schema")

    def get_patrol_config(self) -> dict | None:
        return self._get_or_none(PATROL_CONFIG_PATH)

    def get_patrol_config_resolved(self) -> dict | None:
        """Active, person-type subjects on the configured member roster (nested
        groups expanded) and the active teams: what das accepts on a segment."""
        return self._get_or_none(f"{PATROL_CONFIG_PATH}/resolved")

    def get_me(self) -> dict:
        try:
            return self.er._get(path="user/me", max_retries=0) or {}
        except ERClientPermissionDenied as e:
            raise ERForbidden(f"GET user/me: {e}") from None

    def _read(self, fn, **kwargs):
        try:
            return fn(**kwargs)
        except ERClientPermissionDenied as e:
            raise ERForbidden(str(e)) from None

    def _all_pages(self, page) -> list:
        """das serves these lists whole unless a page size is asked for, but if
        a page does come back, follow `next` so no subject is missed."""
        out = _results(page)
        while isinstance(page, dict) and page.get("next"):
            page = self._read(self.er._get, path=page["next"])
            out += _results(page)
        return out

    def list_subjects(self) -> list:
        # erclient.get_subjects forwards only `subject_group` and `include_inactive`
        return self._all_pages(self._read(self.er.get_subjects, include_inactive=True))

    def group_subject_ids(self, group_id: str) -> set:
        page = self._read(
            self.er.get_subjects, subject_group=group_id, include_inactive=True
        )
        return {s["id"] for s in self._all_pages(page)}

    def find_subject_group(self, name: str) -> dict | None:
        groups = self._read(
            self.er.get_subjectgroups,
            include_inactive=False,
            include_hidden=True,
            isvisible=True,
            flat=True,
        )
        wanted = name.strip().lower()
        return next(
            (
                g
                for g in _results(groups)
                if (g.get("name") or "").strip().lower() == wanted
            ),
            None,
        )

    def list_event_types(self, version: str = "v2") -> list:
        types = self._read(
            self.er.get_event_types,
            include_inactive=True,
            include_schema=False,
            version=version,
        )
        return list(types or [])

    # -- writes ------------------------------------------------------------
    def _write(self, fn, **kwargs):
        try:
            return fn(**kwargs)
        except ERClientPermissionDenied as e:
            raise ERForbidden(str(e)) from None

    def create_patrol_type(self, payload: dict) -> dict:
        return self._write(self.er._post, path=PATROL_TYPES_PATH, payload=payload)

    def patch_patrol_type(self, value: str, payload: dict) -> dict:
        return self._write(
            self.er._patch, path=f"{PATROL_TYPES_PATH}/{value}", payload=payload
        )

    def _put(self, path: str, payload: dict) -> dict:
        """erclient has no PUT; use requests, unless the client is a dry-run wrapper."""
        if _is_dry_run(self.er):
            logger.info(
                "DryRunERClient: would call _put path=%s payload=%s", path, payload
            )
            self.er.calls.append(("_put", (), {"path": path, "payload": payload}))
            return {"id": "dry-run", "dry_run": True}
        response = requests.put(
            self.er._er_url(path),
            json=payload,
            headers=self.er.auth_headers(),
            timeout=60,
        )
        if response.status_code == 403:
            raise ERForbidden(f"PUT {path}: {response.text}")
        if not response.ok:
            raise ERClientException(
                f"PUT {path} failed: {response.status_code} {response.text}"
            )
        body = response.json()
        return body.get("data", body) if isinstance(body, dict) else body

    def put_segment_schema(self, schema_doc: dict) -> dict:
        return self._put(SEGMENT_SCHEMA_PATH, {"schema": schema_doc})

    def create_subject(self, payload: dict) -> dict:
        return self._write(self.er.post_subject, subject=payload)

    def patch_subject(self, subject_id: str, payload: dict) -> dict:
        return self._write(
            self.er._patch, path=f"subject/{subject_id}", payload=payload
        )

    def add_subjects_to_group(self, group_id: str, subject_ids: list) -> None:
        # the endpoint erclient.add_subjects_to_subjectgroup posts to, but through
        # `_post` so a DryRunERClient wrapper intercepts it
        payload = [{"id": s} for s in subject_ids]
        self._write(
            self.er._post, path=f"subjectgroup/{group_id}/subjects/", payload=payload
        )
