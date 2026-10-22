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


def test_cm_without_datamodel_is_refused():
    from er_smart_sync.datamodel_push import push_datamodel_files

    with pytest.raises(ValueError):
        push_datamodel_files(DryRunERClient(_Reads()), endpoint="https://x.pamdas.org",
                             datamodel_file="dm.xml", ca_identifier="x", cm_uuid="not-a-uuid", cm_file=None)
