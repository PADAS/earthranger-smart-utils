from __future__ import annotations

import pytest

from er_smart_sync.defaults import DryRunERClient

DATAMODEL_XML = """<?xml version="1.0" encoding="UTF-8"?>
<DataModel xmlns:xs="http://www.w3.org/2001/XMLSchema">
  <attributes>
    <attribute key="species" type="LIST" isrequired="false">
      <names language_code="en" value="Species"/>
      <values key="lion" isactive="true">
        <names language_code="en" value="Lion"/>
      </values>
    </attribute>
    <attribute key="note" type="TEXT" isrequired="false">
      <names language_code="en" value="Note"/>
    </attribute>
  </attributes>
  <categories>
    <category key="incidents" path="incidents" hkeyPath="incidents" isactive="true" ismultiple="false">
      <names language_code="en" value="Incidents"/>
    </category>
    <category key="poaching" path="incidents.poaching" hkeyPath="incidents.poaching" isactive="true" ismultiple="false">
      <names language_code="en" value="Poaching"/>
      <attribute attributekey="species" isactive="true"/>
      <attribute attributekey="note" isactive="true"/>
    </category>
  </categories>
</DataModel>
"""


class _Reads:
    """The reads the synchronizer makes before deciding what to write."""

    def get_event_categories(self, *a, **k):
        return []

    def get_event_types(self, *a, **k):
        return []

    def get_event_schema(self, *a, **k):
        return {}

    def get_objects_multithreaded(self, *a, **k):
        return []

    def _get(self, *a, **k):
        # choices upsert reads existing choices as a paged {"results": [...]} body
        return {"results": [], "next": None}

    def __getattr__(self, name):
        if name.startswith("get_"):
            return lambda *a, **k: []
        if name.startswith(("post_", "patch_", "delete_", "put_", "_post", "_patch", "_delete", "_put")):
            # DryRunERClient looks a write up on the inner client before it
            # intercepts the call, so the stand-in must have it. Never called.
            def _unreachable(*a, **k):
                raise AssertionError(f"{name} reached the inner client")

            return _unreachable
        raise AttributeError(name)


def test_push_from_file_makes_the_same_writes_as_the_cli(tmp_path):
    from er_smart_sync.datamodel_push import push_datamodel_files

    xml = tmp_path / "datamodel.xml"
    xml.write_text(DATAMODEL_XML)
    client = DryRunERClient(_Reads())
    stats = push_datamodel_files(client, endpoint="https://x.pamdas.org", datamodel_file=str(xml),
                                 ca_identifier="smart-test")
    names = {name for name, _, _ in client.calls}
    assert "post_event_category" in names and "post_event_type" in names
    assert stats["event_types_created"] == sum(1 for n, _, _ in client.calls if n == "post_event_type")


@pytest.mark.parametrize("kwargs, message", [
    ({"cm_uuid": "00000000-0000-0000-0000-000000000001", "cm_file": None}, "cm_uuid needs cm_file"),
    ({"include_base_datamodel": True, "cm_file": None}, "include_base_datamodel needs cm_file"),
    ({"cm_uuid": "not-a-uuid", "cm_file": "cm.xml"}, "is not a UUID"),
    ({"bundle_dir": "bundle", "event_type_version": "v1"}, "bundle_dir needs v2"),
])
def test_arguments_that_do_not_go_together_are_refused_before_any_write(kwargs, message):
    from er_smart_sync.datamodel_push import PushArgumentError, push_datamodel_files

    client = DryRunERClient(_Reads())
    with pytest.raises(PushArgumentError, match=message):
        push_datamodel_files(client, endpoint="https://x.pamdas.org", datamodel_file="dm.xml", ca_identifier="x", **kwargs)
    assert client.calls == []


# Model B: "lion" replaced by "tiger", a second category, and poaching retired
# (a retired category is not pushed unless a bundle has observations of it).
MODEL_B_XML = (
    DATAMODEL_XML.replace('key="lion"', 'key="tiger"')
    .replace('value="Lion"', 'value="Tiger"')
    .replace(
        '<category key="poaching" path="incidents.poaching" hkeyPath="incidents.poaching" isactive="true"',
        '<category key="poaching" path="incidents.poaching" hkeyPath="incidents.poaching" isactive="false"',
    )
    .replace(
        "  </categories>",
        """    <category key="snaring" path="incidents.snaring" hkeyPath="incidents.snaring" isactive="true" ismultiple="false">
      <names language_code="en" value="Snaring"/>
      <attribute attributekey="species" isactive="true"/>
    </category>
  </categories>""",
    )
)
POACHING = "smart-ca-import_poaching"
SNARING = "smart-ca-import_snaring"


def push(site, tmp_path, xml, ca_identifier, **kw):
    from er_smart_sync.datamodel_push import push_datamodel_files

    f = tmp_path / f"dm-{ca_identifier}.xml"
    f.write_text(xml)
    return push_datamodel_files(site, endpoint="https://example.pamdas.org/api/v1.0",
                                datamodel_file=str(f), ca_identifier=ca_identifier, **kw)


def species(site):
    field = next(f for f in site.choice_fields() if f.endswith("species"))
    return {v for v, c in site.choices(field).items() if c["is_active"]}


def push_observed(site, tmp_path, xml, category, observed):
    """Push `xml`'s event types as a bundle with observations of `observed`
    would: a retired category that was observed becomes an inactive type."""
    from smartconnect import SmartClient

    from er_smart_sync.config import EarthRangerConfig, SmartConnectConfig, SyncConfig
    from er_smart_sync.smart_to_er_v2 import build_event_types_v2
    from er_smart_sync.synchronizer import ERSmartSynchronizer

    f = tmp_path / "observed.xml"
    f.write_text(xml)
    dm = SmartClient(api="https://tempuri.org/", username="", password="",
                     use_language_code="en").load_datamodel(filename=str(f)).export_as_dict()
    types = build_event_types_v2(dm=dm, ca_uuid="smart-ca-import", ca_identifier="x",
                                 observed_categories=observed)
    sync = ERSmartSynchronizer(
        config=SyncConfig(smart=SmartConnectConfig(endpoint="", login="", password=""),
                          earthranger=EarthRangerConfig(id="t", endpoint="https://example.pamdas.org/api/v1.0",
                                                        token="unused", event_type_version="v2")),
        er_client=site)
    sync.additive = True
    sync.create_or_update_er_event_types(event_category={"value": category}, event_types=types,
                                         existing_event_types=site.get_event_types(version="v2"))
    return {t.value: t for t in types}, sync.datamodel_stats


def test_an_additive_push_of_a_second_model_takes_nothing_away(tmp_path):
    from tests.fake_site import FakeSite

    site = FakeSite()
    push(site, tmp_path, DATAMODEL_XML, "smart-a", additive=True)
    first = site.event_types()[POACHING]
    assert first["is_active"] and species(site) == {"lion"}
    stats = push(site, tmp_path, MODEL_B_XML, "smart-b", additive=True)
    assert species(site) == {"lion", "tiger"}
    assert site.event_types()[POACHING] == first
    assert site.event_types()[SNARING]["category"] == first["category"]
    assert site.categories() == {first["category"]}
    assert stats["event_category"] == first["category"]


def test_the_same_two_pushes_without_additive_behave_as_before(tmp_path):
    from tests.fake_site import FakeSite

    site = FakeSite()
    push(site, tmp_path, DATAMODEL_XML, "smart-a")
    a_category = site.event_types()[POACHING]["category"]
    stats = push(site, tmp_path, MODEL_B_XML, "smart-b")
    assert species(site) == {"tiger"}   # "lion" was deactivated
    snaring = site.event_types()[SNARING]
    assert snaring["category"] != a_category and len(site.categories()) == 2
    assert stats["event_category"] == snaring["category"]
    # and a push of A again moves B's category back out from under nothing: poaching returns to A's
    push(site, tmp_path, DATAMODEL_XML.replace("</categories>", """<category key="snaring" path="incidents.snaring" hkeyPath="incidents.snaring" isactive="true" ismultiple="false">
      <names language_code="en" value="Snaring"/>
      <attribute attributekey="species" isactive="true"/>
    </category>
  </categories>"""), "smart-a")
    assert site.event_types()[SNARING]["category"] == a_category   # moved: today's behaviour


def test_an_additive_push_leaves_a_type_it_has_retired_but_the_site_has_active(tmp_path):
    from tests.fake_site import FakeSite

    site = FakeSite()
    push(site, tmp_path, DATAMODEL_XML, "smart-a", additive=True)
    first = site.event_types()[POACHING]
    types, stats = push_observed(site, tmp_path, MODEL_B_XML, "elsewhere", {"poaching"})
    assert types[POACHING].is_active is False, "the test needs B to plan poaching as an inactive type"
    assert site.event_types()[POACHING] == first
    assert site.event_types()[SNARING]["category"] == "elsewhere"   # this call was given its category
    assert stats["event_types_unchanged"] == 1


def test_an_additive_push_activates_what_the_site_has_inactive(tmp_path):
    from tests.fake_site import FakeSite

    site = FakeSite()
    types, _ = push_observed(site, tmp_path, MODEL_B_XML, "smart_b", {"poaching"})
    assert not site.event_types()[POACHING]["is_active"]
    push(site, tmp_path, DATAMODEL_XML, "smart-a", additive=True)
    alone = FakeSite()
    push(alone, tmp_path, DATAMODEL_XML, "smart-a", additive=True)
    poaching = site.event_types()[POACHING]
    assert poaching["is_active"] and poaching["schema"] == alone.event_types()[POACHING]["schema"]
    assert poaching["category"] == "smart_b" and site.categories() <= {"smart_b"}


def test_a_new_type_joins_the_larger_group_of_a_set_in_two_categories(tmp_path):
    from tests.fake_site import FakeSite

    three = DATAMODEL_XML.replace("  </categories>", """    <category key="snaring" path="incidents.snaring" hkeyPath="incidents.snaring" isactive="true" ismultiple="false">
      <names language_code="en" value="Snaring"/>
      <attribute attributekey="species" isactive="true"/>
    </category>
    <category key="logging" path="incidents.logging" hkeyPath="incidents.logging" isactive="true" ismultiple="false">
      <names language_code="en" value="Logging"/>
      <attribute attributekey="note" isactive="true"/>
    </category>
  </categories>""")
    four = three.replace("  </categories>", """    <category key="fire" path="incidents.fire" hkeyPath="incidents.fire" isactive="true" ismultiple="false">
      <names language_code="en" value="Fire"/>
      <attribute attributekey="note" isactive="true"/>
    </category>
  </categories>""")
    site = FakeSite()
    push(site, tmp_path, three, "smart-a", additive=True)
    home = site.event_types()[POACHING]["category"]
    site.move(POACHING, "aaa_other")   # sorts before home: only the count can choose home
    push(site, tmp_path, four, "smart-b", additive=True)
    types = site.event_types()
    assert len(types) == 4 and types[POACHING]["category"] == "aaa_other"
    assert {types[v]["category"] for v in types if v != POACHING} == {home}
    assert site.categories() == {home, "aaa_other"}


def test_an_additive_dry_run_plans_the_same_writes(tmp_path):
    from tests.fake_site import FakeSite

    site = FakeSite()
    push(site, tmp_path, DATAMODEL_XML, "smart-a", additive=True)
    before, real = site.event_types(), site.clone()
    dry = DryRunERClient(site)
    stats = push(dry, tmp_path, MODEL_B_XML, "smart-b", additive=True)
    assert site.event_types() == before and species(site) == {"lion"}
    assert stats["event_category"] == before[POACHING]["category"]
    names = [n for n, _, _ in dry.calls]
    assert "post_event_category" not in names and names.count("post_event_type") == 1
    assert "patch_event_type" not in names
    assert not any(n == "_patch" and k.get("payload", {}).get("is_active") is False for n, _, k in dry.calls)
    push(real, tmp_path, MODEL_B_XML, "smart-b", additive=True)
    assert set(real.event_types()) - set(before) == {SNARING}


@pytest.mark.parametrize("mode", ["update-only", "create-only"])
def test_additive_respects_the_mode(tmp_path, mode):
    from tests.fake_site import FakeSite

    site = FakeSite()
    push(site, tmp_path, DATAMODEL_XML, "smart-a", additive=True)
    before = site.event_types()
    both = MODEL_B_XML.replace('hkeyPath="incidents.poaching" isactive="false"', 'hkeyPath="incidents.poaching" isactive="true"').replace('value="Poaching"', 'value="Poaching!"')
    stats = push(site, tmp_path, both, "smart-b", additive=True, mode=mode)
    after = site.event_types()
    if mode == "update-only":
        assert after[POACHING]["display"] == "Poaching!" and SNARING not in after
        assert after[POACHING]["category"] == before[POACHING]["category"]
    else:
        assert after[POACHING] == before[POACHING] and SNARING in after
    assert stats["event_types_skipped_by_mode"] >= 1


def test_a_second_area_with_no_event_type_in_common_still_joins_the_set(tmp_path):
    from tests.fake_site import FakeSite

    only_snaring = MODEL_B_XML   # poaching is retired there, so B plans snaring alone
    site = FakeSite()
    push(site, tmp_path, DATAMODEL_XML, "smart-a", additive=True)
    home = site.event_types()[POACHING]["category"]
    # a set made with a configurable model shares the "smart-ca-import_" start but is another set
    site.post_event_type(event_type={"value": "smart-ca-import_00000000-0000-0000-0000-000000000001_x",
                                     "display": "X", "category": "aaa_cm_set", "schema": {}})
    site.post_event_type(event_type={"value": "smart-ca-import_0123456789abcdef0123456789abcdef_y",
                                     "display": "Y", "category": "aaa_cm_set", "schema": {}})
    stats = push(site, tmp_path, only_snaring, "smart-b", additive=True)
    assert site.event_types()[SNARING]["category"] == home
    assert stats["event_types_on_site"] == 1   # poaching: the set was there, though B plans none of its types
