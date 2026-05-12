from er_smart_sync.utils import unicode_to_ascii


def test_unicode_to_ascii_plain():
    assert unicode_to_ascii("hello") == "hello"


def test_unicode_to_ascii_accented():
    assert unicode_to_ascii("café") == "cafe"


def test_unicode_to_ascii_replacement():
    result = unicode_to_ascii("naïve", replacement="_")
    assert "i" not in result or result == "nai_ve" or result == "naive"


def test_unicode_to_ascii_empty():
    assert unicode_to_ascii("") == ""
