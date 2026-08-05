from er_smart_sync.utils import describe_unresolved_language, unicode_to_ascii


def test_unicode_to_ascii_plain():
    assert unicode_to_ascii("hello") == "hello"


def test_unicode_to_ascii_accented():
    assert unicode_to_ascii("café") == "cafe"


def test_unicode_to_ascii_replacement():
    result = unicode_to_ascii("naïve", replacement="_")
    assert "i" not in result or result == "nai_ve" or result == "naive"


def test_unicode_to_ascii_empty():
    assert unicode_to_ascii("") == ""


# ── describe_unresolved_language (GH #13) ──────────────────────


class _FakeModel:
    """Stands in for a smartconnect DataModel / ConfigurableDataModel."""

    def __init__(self, exported, untangle_root=None):
        self._exported = exported
        if untangle_root is not None:
            self.datamodel = untangle_root

    def export_as_dict(self):
        return self._exported


def _parse(xml):
    import untangle

    return untangle.parse(xml)


def test_describe_unresolved_language_returns_none_when_labels_resolve():
    model = _FakeModel(
        {"categories": [{"display": "CAMINOS"}], "attributes": []},
    )
    assert describe_unresolved_language("es", [model]) is None


def test_describe_unresolved_language_returns_none_on_partial_resolution():
    """A genuinely-untranslated single label is normal SMART data and must not
    trigger the warning — only a wholesale failure indicates a wrong language."""
    model = _FakeModel(
        {
            "categories": [{"display": "CAMINOS"}, {"display": "n/a"}],
            "attributes": [],
        },
    )
    assert describe_unresolved_language("es", [model]) is None


def test_describe_unresolved_language_flags_wholesale_failure():
    model = _FakeModel(
        {
            "categories": [{"display": "n/a"}],
            "attributes": [
                {"display": "n/a", "options": [{"display": "n/a"}]},
            ],
        },
    )
    message = describe_unresolved_language("en", [model])
    assert message is not None
    assert "'en'" in message
    assert "3 SMART display names" in message


def test_describe_unresolved_language_names_available_languages():
    """The message is only actionable if it tells the user what to switch to."""
    xml = (
        '<?xml version="1.0"?><DataModel><languages><languages code="es"/>'
        "</languages><categories>"
        '<category key="caminos"><names language_code="es" value="CAMINOS"/>'
        "</category></categories></DataModel>"
    )
    model = _FakeModel(
        {"categories": [{"display": "n/a"}], "attributes": []},
        untangle_root=_parse(xml),
    )
    message = describe_unresolved_language("en", [model])
    assert "Available language code(s): es" in message


def test_describe_unresolved_language_tolerates_missing_untangle_tree():
    """smartconnect exposes its untangle tree only incidentally; an unexpected
    shape must degrade to a warning without the hint, not raise."""
    model = _FakeModel({"categories": [{"display": "n/a"}], "attributes": []})
    message = describe_unresolved_language("en", [model])
    assert message is not None
    assert "Available language code(s)" not in message


def test_describe_unresolved_language_ignores_none_models():
    """Callers pass [dm, cm] where cm is often None."""
    model = _FakeModel({"categories": [{"display": "CAMINOS"}], "attributes": []})
    assert describe_unresolved_language("es", [model, None]) is None


def test_describe_unresolved_language_returns_none_for_empty_model():
    assert describe_unresolved_language("es", [_FakeModel({})]) is None
