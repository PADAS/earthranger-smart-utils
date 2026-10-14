import uuid

import pytest

from er_smart_sync.patrol_ids import er_id, namespace


def test_namespace_is_host_and_ca():
    a = namespace("https://site.pamdas.org/api/v1.0", "ABC-1")
    assert a == uuid.uuid5(uuid.NAMESPACE_URL, "site.pamdas.org/abc-1")
    assert namespace("https://other.pamdas.org/api/v1.0", "abc-1") != a


def test_namespace_override_wins():
    ns = "6f1c1a0e-3c9b-4f3e-9a51-6a3e1c2b7d10"
    assert str(namespace("https://x/api/v1.0", "c", override=ns)) == ns


def test_er_id_is_deterministic_and_never_the_smart_uuid():
    ns = namespace("https://site.pamdas.org/api/v1.0", "c")
    smart = "3deb6b5a-f9af-41ae-a2fa-1604b72d9027"
    assert er_id(ns, "patrol", smart) == er_id(ns, "patrol", smart.upper())
    assert er_id(ns, "patrol", smart) != smart
    assert er_id(ns, "patrol", smart) != er_id(ns, "segment", smart)
    with pytest.raises(ValueError):
        er_id(ns, "nope", smart)
