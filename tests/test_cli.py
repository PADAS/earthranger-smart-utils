from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from er_smart_sync.cli import main
from er_smart_sync.defaults import DryRunERClient


def test_cli_help():
    runner = CliRunner()
    result = runner.invoke(main, ["--help"])
    assert result.exit_code == 0
    assert "Synchronize SMART Connect" in result.output


def test_datamodel_help():
    runner = CliRunner()
    result = runner.invoke(main, ["datamodel", "--help"])
    assert result.exit_code == 0
    assert "--from-file" in result.output
    assert "--er-endpoint" in result.output


def test_events_help():
    runner = CliRunner()
    result = runner.invoke(main, ["events", "--help"])
    assert result.exit_code == 0
    assert "--state-file" in result.output


def test_patrols_help():
    runner = CliRunner()
    result = runner.invoke(main, ["patrols", "--help"])
    assert result.exit_code == 0
    assert "--topic" in result.output


def test_datamodel_requires_endpoint_or_config():
    runner = CliRunner()
    result = runner.invoke(main, ["datamodel"])
    assert result.exit_code != 0


def test_events_rejects_endpoint_without_credentials():
    runner = CliRunner()
    result = runner.invoke(
        main, ["events", "--er-endpoint", "https://er.example.com/api/v1.0"]
    )
    assert result.exit_code != 0
    assert "token" in result.output.lower() or "auth" in result.output.lower()


def test_events_accepts_username_password_pair():
    # Should fail later (no SMART endpoint, etc.) but must pass auth validation.
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "events",
            "--er-endpoint",
            "https://er.example.com/api/v1.0",
            "--er-username",
            "alice",
            "--er-password",
            "secret",
        ],
    )
    # Should not fail on auth validation; failure happens later.
    assert "auth" not in (result.output or "").lower() or result.exit_code == 0


# ── --dry-run + new subcommand help ─────────────────────────────


def test_dry_run_flag_in_help():
    runner = CliRunner()
    result = runner.invoke(main, ["--help"])
    assert "--dry-run" in result.output


def test_config_template_outputs_valid_yaml(tmp_path):
    import yaml

    from er_smart_sync.config import SyncConfig

    runner = CliRunner()
    result = runner.invoke(main, ["config-template"])
    assert result.exit_code == 0

    parsed = yaml.safe_load(result.output)
    # The template is a real, parseable config. Fill in the required
    # auth field so SyncConfig instantiation has something concrete.
    parsed["earthranger"]["token"] = "test-token"
    cfg = SyncConfig(**parsed)
    assert cfg.smart.endpoint.startswith("https://")
    assert cfg.earthranger.endpoint.startswith("https://")


def test_config_template_to_file_then_used_by_config_flag(tmp_path):
    # config-template -o sync.yaml; then --config sync.yaml should be
    # accepted by, e.g., the events subcommand (it'll fail later for
    # network reasons but the parse + validation must succeed).
    runner = CliRunner()
    out = tmp_path / "sync.yaml"

    write = runner.invoke(main, ["config-template", "-o", str(out)])
    assert write.exit_code == 0
    assert out.exists()

    # Doctor the on-disk config to have a real token so validation passes.
    text = out.read_text().replace('token: ""', 'token: "abc"')
    out.write_text(text)

    # The events subcommand will fail when it actually tries to reach ER,
    # but it must get past _build_config + _validate_config first.
    result = runner.invoke(main, ["events", "--config", str(out)])
    assert "earthranger.endpoint is required" not in (result.output or "")
    assert "earthranger requires a token" not in (result.output or "")


def test_validate_config_help():
    runner = CliRunner()
    result = runner.invoke(main, ["validate-config", "--help"])
    assert result.exit_code == 0


def test_list_cas_help():
    runner = CliRunner()
    result = runner.invoke(main, ["list-cas", "--help"])
    assert result.exit_code == 0


def test_inspect_datamodel_help():
    runner = CliRunner()
    result = runner.invoke(main, ["inspect-datamodel", "--help"])
    assert result.exit_code == 0
    assert "--from-file" in result.output


def test_datamodel_mode_flag_in_help():
    runner = CliRunner()
    result = runner.invoke(main, ["datamodel", "--help"])
    assert "--mode" in result.output
    assert "create-only" in result.output
    assert "update-only" in result.output


# ── DryRunERClient behavior ─────────────────────────────────────


def test_dry_run_client_passes_through_reads():
    inner = MagicMock()
    inner.get_event_categories.return_value = [{"value": "x"}]
    client = DryRunERClient(inner)
    assert client.get_event_categories() == [{"value": "x"}]


def test_dry_run_client_logs_writes_without_calling_inner():
    inner = MagicMock()
    client = DryRunERClient(inner)
    result = client.post_event_category(data={"value": "x"})
    inner.post_event_category.assert_not_called()
    assert result["dry_run"] is True
    assert client.calls == [("post_event_category", (), {"data": {"value": "x"}})]


def test_dry_run_client_intercepts_patch_and_delete():
    inner = MagicMock()
    client = DryRunERClient(inner)
    client.patch_event_type(event_type={"id": "1"})
    client.delete_event(event_id="x")
    inner.patch_event_type.assert_not_called()
    inner.delete_event.assert_not_called()
    assert [c[0] for c in client.calls] == ["patch_event_type", "delete_event"]


# ── inspect-datamodel against fixture XML ───────────────────────


def test_inspect_datamodel_with_fixture_xml(tmp_path):
    # Build a small SMART data model XML inline. This is the wire format
    # SMART exposes; we use it to test that inspect-datamodel runs end-to-end
    # without touching ER or SMART services.
    xml = """<?xml version="1.0" encoding="UTF-8"?>
<DataModel xmlns:xs="http://www.w3.org/2001/XMLSchema">
  <attributes>
    <attribute key="species" type="LIST" isrequired="false">
      <names language_code="en" value="Species"/>
      <values key="lion" isactive="true">
        <names language_code="en" value="Lion"/>
      </values>
      <values key="tiger" isactive="true">
        <names language_code="en" value="Tiger"/>
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
    dm_file = tmp_path / "dm.xml"
    dm_file.write_text(xml)

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "inspect-datamodel",
            "--er-endpoint",
            "https://er.example.com/api/v1.0",
            "--er-token",
            "x",
            "--from-file",
            str(dm_file),
            "--ca-identifier",
            "TEST",
        ],
    )
    if result.exit_code != 0:
        print(result.output)
        if result.exception:
            raise result.exception
    assert result.exit_code == 0
    assert "Event types" in result.output
    assert "incidents.poaching" in result.output or "poaching" in result.output
    assert "species" in result.output


# ── --dry-run end-to-end via datamodel --from-file ──────────────


def test_datamodel_dry_run_makes_no_writes(tmp_path):
    xml = """<?xml version="1.0" encoding="UTF-8"?>
<DataModel>
  <attributes>
    <attribute key="note" type="TEXT" isrequired="false">
      <names language_code="en" value="Note"/>
    </attribute>
  </attributes>
  <categories>
    <category key="c" path="c" hkeyPath="c" isactive="true" ismultiple="false">
      <names language_code="en" value="C"/>
      <attribute attributekey="note" isactive="true"/>
    </category>
  </categories>
</DataModel>
"""
    dm_file = tmp_path / "dm.xml"
    dm_file.write_text(xml)

    # Replace the ERClient that the synchronizer would build with a mock so we
    # can assert no writes hit it. We use a side_effect that fakes a fresh ER.
    mock_inner = MagicMock()
    mock_inner.get_event_categories.return_value = []
    mock_inner.get_event_types.return_value = []

    with patch("er_smart_sync.synchronizer.ERClient", return_value=mock_inner):
        runner = CliRunner()
        result = runner.invoke(
            main,
            [
                "--dry-run",
                "datamodel",
                "--er-endpoint",
                "https://er.example.com/api/v1.0",
                "--er-token",
                "x",
                "--from-file",
                str(dm_file),
                "--ca-identifier",
                "TEST",
            ],
        )
        if result.exit_code != 0 and result.exception:
            raise result.exception
        assert result.exit_code == 0
        # No write methods called on the inner client.
        mock_inner.post_event_category.assert_not_called()
        mock_inner.post_event_type.assert_not_called()
        mock_inner.patch_event_type.assert_not_called()


def test_cm_from_file_uses_zero_uuid_in_event_type_value(tmp_path):
    # When --cm-from-file is used, we don't have a real configurable-model
    # UUID. The CLI must substitute the all-zero UUID so the resulting
    # event-type `value` is stable and lowercase — not "smart-ca-import_none_..."
    dm_xml = """<?xml version="1.0" encoding="UTF-8"?>
<DataModel>
  <attributes>
    <attribute key="note" type="TEXT" isrequired="false">
      <names language_code="en" value="Note"/>
    </attribute>
  </attributes>
  <categories>
    <category key="c" path="c" hkeyPath="c" isactive="true" ismultiple="false">
      <names language_code="en" value="C"/>
      <attribute attributekey="note" isactive="true"/>
    </category>
  </categories>
</DataModel>
"""
    cm_xml = """<?xml version="1.0" encoding="UTF-8"?>
<ConfigurableModel>
  <name language_code="en" value="Test CM"/>
  <nodes>
    <node key="c" categoryKey="c" categoryHkey="c.">
      <name language_code="en" value="C"/>
      <attribute attributeKey="note">
        <option id="IS_VISIBLE" doubleValue="1.0"/>
      </attribute>
    </node>
  </nodes>
  <attributeConfig key="note"/>
</ConfigurableModel>
"""
    dm_file = tmp_path / "dm.xml"
    cm_file = tmp_path / "cm.xml"
    dm_file.write_text(dm_xml)
    cm_file.write_text(cm_xml)

    mock_inner = MagicMock()
    mock_inner.get_event_categories.return_value = []
    mock_inner.get_event_types.return_value = []

    with patch("er_smart_sync.synchronizer.ERClient", return_value=mock_inner):
        runner = CliRunner()
        result = runner.invoke(
            main,
            [
                "datamodel",
                "--er-endpoint",
                "https://er.example.com/api/v1.0",
                "--er-token",
                "x",
                "--from-file",
                str(dm_file),
                "--cm-from-file",
                str(cm_file),
                "--ca-identifier",
                "TEST",
            ],
        )
        if result.exit_code != 0 and result.exception:
            raise result.exception
        assert result.exit_code == 0

    # Look at what we tried to POST.
    posted_event_types = [
        call.kwargs.get("event_type")
        for call in mock_inner.post_event_type.call_args_list
    ]
    assert posted_event_types, "expected at least one post_event_type call"
    for et in posted_event_types:
        # No "None" / "none" segment.
        assert "_none_" not in et["value"]
        assert "_None_" not in et["value"]
        # Has the zero UUID prefix instead.
        assert "00000000-0000-0000-0000-000000000000" in et["value"]
        # Fully lowercase.
        assert et["value"] == et["value"].lower()


def test_cm_uuid_flag_is_used_in_event_type_value(tmp_path):
    # When a user passes --cm-uuid, that UUID — not the zero placeholder —
    # must appear in the generated event-type values. This is required when
    # the same SMART CA loads multiple configurable models into ER.
    dm_xml = """<?xml version="1.0" encoding="UTF-8"?>
<DataModel>
  <attributes>
    <attribute key="note" type="TEXT" isrequired="false">
      <names language_code="en" value="Note"/>
    </attribute>
  </attributes>
  <categories>
    <category key="c" path="c" hkeyPath="c" isactive="true" ismultiple="false">
      <names language_code="en" value="C"/>
      <attribute attributekey="note" isactive="true"/>
    </category>
  </categories>
</DataModel>
"""
    cm_xml = """<?xml version="1.0" encoding="UTF-8"?>
<ConfigurableModel>
  <name language_code="en" value="Test CM"/>
  <nodes>
    <node key="c" categoryKey="c" categoryHkey="c.">
      <name language_code="en" value="C"/>
      <attribute attributeKey="note">
        <option id="IS_VISIBLE" doubleValue="1.0"/>
      </attribute>
    </node>
  </nodes>
  <attributeConfig key="note"/>
</ConfigurableModel>
"""
    dm_file = tmp_path / "dm.xml"
    cm_file = tmp_path / "cm.xml"
    dm_file.write_text(dm_xml)
    cm_file.write_text(cm_xml)

    cm_uuid = "11111111-2222-3333-4444-555555555555"

    mock_inner = MagicMock()
    mock_inner.get_event_categories.return_value = []
    mock_inner.get_event_types.return_value = []

    with patch("er_smart_sync.synchronizer.ERClient", return_value=mock_inner):
        runner = CliRunner()
        result = runner.invoke(
            main,
            [
                "datamodel",
                "--er-endpoint",
                "https://er.example.com/api/v1.0",
                "--er-token",
                "x",
                "--from-file",
                str(dm_file),
                "--cm-from-file",
                str(cm_file),
                "--cm-uuid",
                cm_uuid,
                "--ca-identifier",
                "TEST",
            ],
        )
        if result.exit_code != 0 and result.exception:
            raise result.exception
        assert result.exit_code == 0

    posted = [
        call.kwargs.get("event_type")
        for call in mock_inner.post_event_type.call_args_list
    ]
    assert posted, "expected at least one post_event_type call"
    for et in posted:
        assert cm_uuid in et["value"]
        # No zero UUID leaks through.
        assert "00000000-0000-0000-0000-000000000000" not in et["value"]


def test_cm_uuid_rejects_invalid_uuid():
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "datamodel",
            "--er-endpoint",
            "https://er.example.com/api/v1.0",
            "--er-token",
            "x",
            "--from-file",
            "/dev/null",
            "--cm-from-file",
            "/dev/null",
            "--cm-uuid",
            "not-a-uuid",
        ],
    )
    assert result.exit_code != 0
    assert "uuid" in result.output.lower()


def test_include_base_datamodel_pushes_both_dm_and_cm(tmp_path):
    # With --include-base-datamodel, both the base data model and the CM
    # should each be pushed as their own ER event category. That means two
    # post_event_category calls (one for each) and event-type values from both.
    dm_xml = """<?xml version="1.0" encoding="UTF-8"?>
<DataModel>
  <attributes>
    <attribute key="note" type="TEXT" isrequired="false">
      <names language_code="en" value="Note"/>
    </attribute>
  </attributes>
  <categories>
    <category key="c" path="c" hkeyPath="c" isactive="true" ismultiple="false">
      <names language_code="en" value="C"/>
      <attribute attributekey="note" isactive="true"/>
    </category>
  </categories>
</DataModel>
"""
    cm_xml = """<?xml version="1.0" encoding="UTF-8"?>
<ConfigurableModel>
  <name language_code="en" value="Test CM"/>
  <nodes>
    <node key="c" categoryKey="c" categoryHkey="c.">
      <name language_code="en" value="C"/>
      <attribute attributeKey="note">
        <option id="IS_VISIBLE" doubleValue="1.0"/>
      </attribute>
    </node>
  </nodes>
  <attributeConfig key="note"/>
</ConfigurableModel>
"""
    dm_file = tmp_path / "dm.xml"
    cm_file = tmp_path / "cm.xml"
    dm_file.write_text(dm_xml)
    cm_file.write_text(cm_xml)

    mock_inner = MagicMock()
    mock_inner.get_event_categories.return_value = []
    mock_inner.get_event_types.return_value = []

    with patch("er_smart_sync.synchronizer.ERClient", return_value=mock_inner):
        runner = CliRunner()
        result = runner.invoke(
            main,
            [
                "datamodel",
                "--er-endpoint",
                "https://er.example.com/api/v1.0",
                "--er-token",
                "x",
                "--from-file",
                str(dm_file),
                "--cm-from-file",
                str(cm_file),
                "--include-base-datamodel",
                "--ca-identifier",
                "TEST",
            ],
        )
        if result.exit_code != 0 and result.exception:
            raise result.exception
        assert result.exit_code == 0

    # Two distinct event categories created — one for the base DM, one for the CM.
    assert mock_inner.post_event_category.call_count == 2
    posted_categories = [
        call.kwargs.get("data")["value"]
        for call in mock_inner.post_event_category.call_args_list
    ]
    assert len(set(posted_categories)) == 2


def test_include_base_datamodel_requires_cm_from_file():
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "datamodel",
            "--er-endpoint",
            "https://er.example.com/api/v1.0",
            "--er-token",
            "x",
            "--from-file",
            "/dev/null",
            "--include-base-datamodel",
        ],
    )
    assert result.exit_code != 0
    assert "--cm-from-file" in result.output


def test_cm_uuid_requires_cm_from_file():
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "datamodel",
            "--er-endpoint",
            "https://er.example.com/api/v1.0",
            "--er-token",
            "x",
            "--cm-uuid",
            "11111111-2222-3333-4444-555555555555",
        ],
    )
    assert result.exit_code != 0
    assert "--cm-from-file" in result.output


def test_datamodel_event_type_version_v1_flag_overrides_config_default(
    tmp_path, monkeypatch
):
    """--event-type-version v1 should produce a synchronizer with _event_type_version == 'v1'."""
    captured = {}

    def fake_make_sync(config, ctx=None):
        from er_smart_sync.synchronizer import ERSmartSynchronizer

        sync = ERSmartSynchronizer.__new__(ERSmartSynchronizer)
        sync._event_type_version = config.earthranger.event_type_version
        sync.sync_mode = "both"
        sync.datamodel_stats = {
            "categories_created": 0,
            "categories_existing": 0,
            "event_types_created": 0,
            "event_types_updated": 0,
            "event_types_unchanged": 0,
            "event_types_skipped_by_mode": 0,
            "event_types_errored": 0,
        }
        # Stub out the network calls the command would make
        sync.push_smart_ca_datamodel_to_earthranger = lambda **kwargs: None
        sync.synchronize_datamodel = lambda: None
        captured["sync"] = sync
        return sync

    monkeypatch.setattr("er_smart_sync.cli._make_synchronizer", fake_make_sync)

    # Use a dummy XML file (file-based path)
    dm_file = tmp_path / "dm.xml"
    dm_file.write_text("<datamodel/>")

    # Patch the SmartClient.load_datamodel so we don't actually parse XML
    monkeypatch.setattr(
        "smartconnect.SmartClient.load_datamodel",
        lambda self, filename: MagicMock(),
    )

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "datamodel",
            "--from-file",
            str(dm_file),
            "--er-endpoint",
            "https://x/api/v1.0",
            "--er-token",
            "t",
            "--er-id",
            "i",
            "--ca-identifier",
            "TEST",
            "--event-type-version",
            "v1",
        ],
    )
    assert result.exit_code == 0, result.output
    assert captured["sync"]._event_type_version == "v1"


def test_datamodel_event_type_version_defaults_to_v2(tmp_path, monkeypatch):
    """No --event-type-version flag → uses config default which is v2."""
    captured = {}

    def fake_make_sync(config, ctx=None):
        from er_smart_sync.synchronizer import ERSmartSynchronizer

        sync = ERSmartSynchronizer.__new__(ERSmartSynchronizer)
        sync._event_type_version = config.earthranger.event_type_version
        sync.sync_mode = "both"
        sync.datamodel_stats = {
            "categories_created": 0,
            "categories_existing": 0,
            "event_types_created": 0,
            "event_types_updated": 0,
            "event_types_unchanged": 0,
            "event_types_skipped_by_mode": 0,
            "event_types_errored": 0,
        }
        sync.push_smart_ca_datamodel_to_earthranger = lambda **kwargs: None
        sync.synchronize_datamodel = lambda: None
        captured["sync"] = sync
        return sync

    monkeypatch.setattr("er_smart_sync.cli._make_synchronizer", fake_make_sync)

    dm_file = tmp_path / "dm.xml"
    dm_file.write_text("<datamodel/>")

    monkeypatch.setattr(
        "smartconnect.SmartClient.load_datamodel",
        lambda self, filename: MagicMock(),
    )

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "datamodel",
            "--from-file",
            str(dm_file),
            "--er-endpoint",
            "https://x/api/v1.0",
            "--er-token",
            "t",
            "--er-id",
            "i",
            "--ca-identifier",
            "TEST",
        ],
    )
    assert result.exit_code == 0, result.output
    assert captured["sync"]._event_type_version == "v2"


def test_inspect_datamodel_v2_prints_field_types(tmp_path, monkeypatch):
    dm_mock = MagicMock()
    dm_mock.export_as_dict.return_value = {
        "categories": [
            {
                "path": "incidents",
                "hkeyPath": "incidents",
                "display": "Incidents",
                "is_multiple": False,
                "is_active": True,
                "attributes": [{"key": "color", "is_active": True}],
            }
        ],
        "attributes": [
            {
                "key": "color",
                "type": "LIST",
                "isrequired": False,
                "display": "Color",
                "options": [
                    {"key": "red", "display": "Red", "isActive": True},
                    {"key": "blue", "display": "Blue", "isActive": True},
                ],
            }
        ],
    }
    monkeypatch.setattr(
        "smartconnect.SmartClient.load_datamodel",
        lambda self, filename: dm_mock,
    )

    dm_file = tmp_path / "dm.xml"
    dm_file.write_text("<datamodel/>")

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "inspect-datamodel",
            "--er-endpoint",
            "https://er.example.com/api/v1.0",
            "--er-token",
            "x",
            "--from-file",
            str(dm_file),
            "--ca-identifier",
            "FOASF",
            "--event-type-version",
            "v2",
        ],
    )
    assert result.exit_code == 0, result.output
    # v2 printer should mention CHOICE_LIST or DROPDOWN somewhere
    assert "CHOICE_LIST" in result.output or "DROPDOWN" in result.output
    assert "color" in result.output


def test_datamodel_update_only_skips_creates(tmp_path):
    # With no matching category in ER and --mode update-only, create_or_update
    # should report zero creates and an event_types_skipped_by_mode count of 1.
    xml = """<?xml version="1.0" encoding="UTF-8"?>
<DataModel>
  <attributes>
    <attribute key="note" type="TEXT" isrequired="false">
      <names language_code="en" value="Note"/>
    </attribute>
  </attributes>
  <categories>
    <category key="c" path="c" hkeyPath="c" isactive="true" ismultiple="false">
      <names language_code="en" value="C"/>
      <attribute attributekey="note" isactive="true"/>
    </category>
  </categories>
</DataModel>
"""
    dm_file = tmp_path / "dm.xml"
    dm_file.write_text(xml)

    mock_inner = MagicMock()
    mock_inner.get_event_categories.return_value = []
    mock_inner.get_event_types.return_value = []

    with patch("er_smart_sync.synchronizer.ERClient", return_value=mock_inner):
        runner = CliRunner()
        result = runner.invoke(
            main,
            [
                "datamodel",
                "--er-endpoint",
                "https://er.example.com/api/v1.0",
                "--er-token",
                "x",
                "--from-file",
                str(dm_file),
                "--ca-identifier",
                "TEST",
                "--mode",
                "update-only",
            ],
        )
        if result.exit_code != 0 and result.exception:
            raise result.exception
        assert result.exit_code == 0
        # No category was created.
        mock_inner.post_event_category.assert_not_called()
        # Summary shows the skip (labels render with spaces in stdout).
        assert "event types skipped by mode: 1" in result.output
        assert "categories created: 0" in result.output


def test_config_template_mentions_event_type_version():
    runner = CliRunner()
    result = runner.invoke(main, ["config-template"])
    assert result.exit_code == 0, result.output
    assert "event_type_version" in result.output
    assert "v2" in result.output


def test_choices_subcommand_runs_upsert(tmp_path, monkeypatch):
    """`er-smart-sync choices --from-file` invokes build_choice_sets + upsert_choices."""
    from click.testing import CliRunner

    from er_smart_sync.cli import main

    captured = {}

    def fake_build_choice_sets(**kwargs):
        from er_smart_sync.choices import ChoiceOption, ChoiceSet

        captured["build_called"] = True
        return [
            ChoiceSet(
                field="etxxx_color",
                options=(ChoiceOption(value="red", display="Red", is_active=True),),
            )
        ]

    def fake_upsert_choices(*, er_client, choice_sets):
        from er_smart_sync.choices import ChoicesStats

        captured["upsert_called"] = True
        captured["choice_sets"] = choice_sets
        return ChoicesStats(created=1)

    monkeypatch.setattr(
        "er_smart_sync.cli.build_choice_sets",
        fake_build_choice_sets,
    )
    monkeypatch.setattr(
        "er_smart_sync.cli.upsert_choices",
        fake_upsert_choices,
    )
    monkeypatch.setattr(
        "smartconnect.SmartClient.load_datamodel",
        lambda self, filename: MagicMock(export_as_dict=lambda: {"categories": []}),
    )

    dm_file = tmp_path / "dm.xml"
    dm_file.write_text("<datamodel/>")

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "choices",
            "--from-file",
            str(dm_file),
            "--er-endpoint",
            "https://x/api/v1.0",
            "--er-token",
            "t",
            "--er-id",
            "i",
        ],
    )
    assert result.exit_code == 0, result.output
    assert captured.get("build_called") is True
    assert captured.get("upsert_called") is True
    assert "created=1" in result.output or "Created: 1" in result.output


def test_choices_subcommand_exits_nonzero_on_errors(tmp_path, monkeypatch):
    from click.testing import CliRunner

    from er_smart_sync.cli import main

    monkeypatch.setattr(
        "er_smart_sync.cli.build_choice_sets",
        lambda **kw: [],
    )

    def fake_upsert(**kw):
        from er_smart_sync.choices import ChoicesStats

        return ChoicesStats(errored=1)

    monkeypatch.setattr("er_smart_sync.cli.upsert_choices", fake_upsert)
    monkeypatch.setattr(
        "smartconnect.SmartClient.load_datamodel",
        lambda self, filename: MagicMock(export_as_dict=lambda: {"categories": []}),
    )

    dm_file = tmp_path / "dm.xml"
    dm_file.write_text("<datamodel/>")

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "choices",
            "--from-file",
            str(dm_file),
            "--er-endpoint",
            "https://x/api/v1.0",
            "--er-token",
            "t",
            "--er-id",
            "i",
        ],
    )
    assert result.exit_code != 0


def test_datamodel_skip_choices_flag(tmp_path, monkeypatch):
    """--skip-choices makes the synchronizer skip the choices phase even on v2."""
    from click.testing import CliRunner

    from er_smart_sync.cli import main

    captured = {}

    def fake_make_sync(config, ctx=None):
        from er_smart_sync.synchronizer import ERSmartSynchronizer

        sync = ERSmartSynchronizer.__new__(ERSmartSynchronizer)
        sync._event_type_version = config.earthranger.event_type_version
        sync.sync_mode = "both"
        sync.skip_choices = False
        sync.datamodel_stats = {
            "categories_created": 0,
            "categories_existing": 0,
            "event_types_created": 0,
            "event_types_updated": 0,
            "event_types_unchanged": 0,
            "event_types_skipped_by_mode": 0,
            "event_types_skipped_by_conflict": 0,
            "event_types_errored": 0,
            "choices_created": 0,
            "choices_updated": 0,
            "choices_unchanged": 0,
            "choices_deactivated": 0,
            "choices_errored": 0,
        }
        sync.push_smart_ca_datamodel_to_earthranger = lambda **kwargs: None
        sync.synchronize_datamodel = lambda: None
        captured["sync"] = sync
        return sync

    monkeypatch.setattr("er_smart_sync.cli._make_synchronizer", fake_make_sync)
    monkeypatch.setattr(
        "smartconnect.SmartClient.load_datamodel",
        lambda self, filename: MagicMock(),
    )

    dm_file = tmp_path / "dm.xml"
    dm_file.write_text("<datamodel/>")

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "datamodel",
            "--from-file",
            str(dm_file),
            "--er-endpoint",
            "https://x/api/v1.0",
            "--er-token",
            "t",
            "--er-id",
            "i",
            "--ca-identifier",
            "TEST",
            "--event-type-version",
            "v2",
            "--skip-choices",
        ],
    )
    assert result.exit_code == 0, result.output
    assert captured["sync"].skip_choices is True


def test_inspect_datamodel_v2_prints_choice_sets(tmp_path, monkeypatch):
    """`inspect-datamodel --event-type-version v2` includes a choices section."""
    from click.testing import CliRunner

    from er_smart_sync.cli import main

    dm_mock = MagicMock()
    dm_mock.export_as_dict.return_value = {
        "categories": [
            {
                "path": "incidents",
                "hkeyPath": "incidents",
                "display": "Incidents",
                "is_multiple": False,
                "is_active": True,
                "attributes": [{"key": "color", "is_active": True}],
            }
        ],
        "attributes": [
            {
                "key": "color",
                "type": "LIST",
                "isrequired": False,
                "display": "Color",
                "options": [
                    {"key": "red", "display": "Red", "isActive": True},
                    {"key": "blue", "display": "Blue", "isActive": True},
                ],
            }
        ],
    }
    monkeypatch.setattr(
        "smartconnect.SmartClient.load_datamodel",
        lambda self, filename: dm_mock,
    )

    dm_file = tmp_path / "dm.xml"
    dm_file.write_text("<datamodel/>")

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "inspect-datamodel",
            "--er-endpoint",
            "https://er.example.com/api/v1.0",
            "--er-token",
            "x",
            "--from-file",
            str(dm_file),
            "--ca-identifier",
            "FOASF",
            "--event-type-version",
            "v2",
        ],
    )
    assert result.exit_code == 0, result.output
    # Output should mention "Choice sets" header and the option keys.
    assert "Choice" in result.output or "choice" in result.output
    assert "red" in result.output
    assert "blue" in result.output


def test_inspect_datamodel_api_path_fails_on_unbracketed_label(tmp_path, monkeypatch):
    """API-path inspect should fail with an actionable message when the
    SMART CA label has no bracketed identifier — matches the runtime sync
    behavior in push_smart_datamodel_to_earthranger."""
    from click.testing import CliRunner

    from er_smart_sync.cli import main

    dm_mock = MagicMock()
    dm_mock.export_as_dict.return_value = {"categories": [], "attributes": []}
    ca_mock = MagicMock()
    ca_mock.label = "Conservation Area Without Brackets"

    monkeypatch.setattr(
        "smartconnect.SmartClient.get_data_model",
        lambda self, ca_uuid: dm_mock,
    )
    monkeypatch.setattr(
        "smartconnect.SmartClient.get_conservation_area",
        lambda self, ca_uuid: ca_mock,
    )

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "inspect-datamodel",
            "--smart-api", "https://smart.example.com",
            "--smart-username", "u",
            "--smart-password", "p",
            "--smart-ca-uuid", "some-ca-uuid",
            "--er-endpoint", "https://er.example.com/api/v1.0",
            "--er-token", "x",
        ],
    )
    assert result.exit_code != 0
    assert "Conservation Area Without Brackets" in result.output
    assert "bracketed" in result.output.lower() or "[" in result.output
    assert "some-ca-uuid" in result.output


def test_inspect_datamodel_v2_passes_config_choices_base_url(tmp_path, monkeypatch):
    """The v2 inspect path must thread choices_base_url from config into
    build_event_types_v2 — otherwise previewed $ref URLs use the hardcoded
    default and don't match what the real sync would emit."""
    from click.testing import CliRunner

    from er_smart_sync.cli import main

    dm_mock = MagicMock()
    dm_mock.export_as_dict.return_value = {"categories": [], "attributes": []}
    monkeypatch.setattr(
        "smartconnect.SmartClient.load_datamodel",
        lambda self, filename: dm_mock,
    )

    captured: dict = {}

    def fake_build_v2(**kwargs):
        captured["choices_base_url"] = kwargs.get("choices_base_url")
        return []

    monkeypatch.setattr(
        "er_smart_sync.smart_to_er_v2.build_event_types_v2", fake_build_v2,
    )

    dm_file = tmp_path / "dm.xml"
    dm_file.write_text("<datamodel/>")

    config_file = tmp_path / "sync.yaml"
    config_file.write_text(
        "smart:\n"
        "  endpoint: ''\n"
        "  login: ''\n"
        "  password: ''\n"
        "earthranger:\n"
        "  id: i\n"
        "  endpoint: https://er.example.com/api/v1.0\n"
        "  token: t\n"
        "  event_type_version: v2\n"
        "  choices_base_url: /custom/schemas/prefix\n"
    )

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "inspect-datamodel",
            "--config", str(config_file),
            "--from-file", str(dm_file),
            "--ca-identifier", "FOASF",
        ],
    )
    assert result.exit_code == 0, result.output
    assert captured["choices_base_url"] == "/custom/schemas/prefix"


def test_inspect_datamodel_respects_config_event_type_version(tmp_path, monkeypatch):
    """When --event-type-version is not passed, fall back to the config value
    instead of hardcoding v2. Otherwise a config that sets event_type_version: v1
    would still get v2 output."""
    from click.testing import CliRunner

    from er_smart_sync.cli import main

    dm_mock = MagicMock()
    dm_mock.export_as_dict.return_value = {
        "categories": [
            {
                "path": "incidents",
                "hkeyPath": "incidents",
                "display": "Incidents",
                "is_multiple": False,
                "is_active": True,
                "attributes": [{"key": "color", "is_active": True}],
            }
        ],
        "attributes": [
            {
                "key": "color",
                "type": "LIST",
                "isrequired": False,
                "display": "Color",
                "options": [
                    {"key": "red", "display": "Red", "isActive": True},
                ],
            }
        ],
    }
    monkeypatch.setattr(
        "smartconnect.SmartClient.load_datamodel",
        lambda self, filename: dm_mock,
    )

    dm_file = tmp_path / "dm.xml"
    dm_file.write_text("<datamodel/>")

    # Write a YAML config that pins v1.
    config_file = tmp_path / "sync.yaml"
    config_file.write_text(
        "smart:\n"
        "  endpoint: ''\n"
        "  login: ''\n"
        "  password: ''\n"
        "earthranger:\n"
        "  id: i\n"
        "  endpoint: https://er.example.com/api/v1.0\n"
        "  token: t\n"
        "  event_type_version: v1\n"
    )

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "inspect-datamodel",
            "--config", str(config_file),
            "--from-file", str(dm_file),
            "--ca-identifier", "FOASF",
            # Note: no --event-type-version; should pick up v1 from config.
        ],
    )
    assert result.exit_code == 0, result.output
    # v1 printer emits inline enums; v2 printer prints "CHOICE_LIST" / "DROPDOWN".
    # Confirm we got v1 output by asserting v2-only markers are absent.
    assert "CHOICE_LIST" not in result.output
    assert "DROPDOWN" not in result.output


def test_config_template_mentions_choices_base_url():
    runner = CliRunner()
    result = runner.invoke(main, ["config-template"])
    assert result.exit_code == 0, result.output
    assert "choices_base_url" in result.output


def test_ca_identifier_validation_accepts_hyphens_and_underscores(tmp_path, monkeypatch):
    """Hyphens and underscores are allowed; pure-alphanumeric is not the only valid form."""
    from click.testing import CliRunner

    from er_smart_sync.cli import main

    dm_file = tmp_path / "dm.xml"
    dm_file.write_text("<datamodel/>")
    monkeypatch.setattr(
        "smartconnect.SmartClient.load_datamodel",
        lambda self, filename: MagicMock(export_as_dict=lambda: {"categories": []}),
    )

    def fake_make_sync(config, ctx=None):
        from er_smart_sync.synchronizer import ERSmartSynchronizer
        sync = ERSmartSynchronizer.__new__(ERSmartSynchronizer)
        sync._event_type_version = config.earthranger.event_type_version
        sync.sync_mode = "both"
        sync.skip_choices = False
        sync.datamodel_stats = {
            "categories_created": 0, "categories_existing": 0,
            "event_types_created": 0, "event_types_updated": 0,
            "event_types_unchanged": 0, "event_types_skipped_by_mode": 0,
            "event_types_skipped_by_conflict": 0, "event_types_errored": 0,
            "choices_created": 0, "choices_updated": 0,
            "choices_unchanged": 0, "choices_deactivated": 0, "choices_errored": 0,
        }
        sync.push_smart_ca_datamodel_to_earthranger = lambda **kwargs: None
        sync.synchronize_datamodel = lambda: None
        return sync

    monkeypatch.setattr("er_smart_sync.cli._make_synchronizer", fake_make_sync)

    runner = CliRunner()
    for ident in ("smart-import", "smart_import", "FOASF-1", "FOO_BAR_99"):
        result = runner.invoke(
            main,
            [
                "datamodel",
                "--from-file", str(dm_file),
                "--er-endpoint", "https://x/api/v1.0",
                "--er-token", "t",
                "--er-id", "i",
                "--ca-identifier", ident,
            ],
        )
        assert result.exit_code == 0, f"identifier {ident!r} should be accepted; got: {result.output}"


def test_ca_identifier_validation_rejects_special_chars(tmp_path, monkeypatch):
    from click.testing import CliRunner

    from er_smart_sync.cli import main

    dm_file = tmp_path / "dm.xml"
    dm_file.write_text("<datamodel/>")

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "datamodel",
            "--from-file", str(dm_file),
            "--er-endpoint", "https://x/api/v1.0",
            "--er-token", "t",
            "--er-id", "i",
            "--ca-identifier", "has spaces",
        ],
    )
    assert result.exit_code != 0
    assert "2-30" in result.output or "characters" in result.output.lower()


def test_ca_identifier_required_for_file_based_sync(tmp_path, monkeypatch):
    from click.testing import CliRunner

    from er_smart_sync.cli import main

    dm_file = tmp_path / "dm.xml"
    dm_file.write_text("<datamodel/>")

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "datamodel",
            "--from-file", str(dm_file),
            "--er-endpoint", "https://x/api/v1.0",
            "--er-token", "t",
            "--er-id", "i",
            # No --ca-identifier
        ],
    )
    assert result.exit_code != 0
    output = result.output.lower()
    assert "ca-identifier" in output or "required" in output
