"""Deterministic EarthRanger ids for imported SMART objects.

Every id is uuid5 of the object's kind and SMART uuid in a namespace made of
the ER host and the conservation area, so a re-run, or a second operator,
addresses the same rows. SMART uuids are never used directly: a SMART
`client_uuid` can point back at an ER-originated patrol from the outbound
sync.
"""

from __future__ import annotations

import uuid
from urllib.parse import urlparse

KINDS = ("patrol", "segment", "note", "event", "incident", "source")


def namespace(er_endpoint: str, ca_uuid: str, override: str | None = None) -> uuid.UUID:
    if override:
        return uuid.UUID(override)
    host = urlparse(er_endpoint).netloc or er_endpoint
    return uuid.uuid5(uuid.NAMESPACE_URL, f"{host}/{ca_uuid.lower()}")


def er_id(ns: uuid.UUID, kind: str, smart_uuid: str) -> str:
    if kind not in KINDS:
        raise ValueError(f"unknown id kind {kind!r}")
    return str(uuid.uuid5(ns, f"{kind}:{smart_uuid.lower()}"))
