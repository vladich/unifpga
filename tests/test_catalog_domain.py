"""Scoped domain checks consume parsed documents from an exact source tree."""

from copy import deepcopy
from pathlib import Path
import runpy

import pytest

from config import init as config_init
from config.references import parse_versioned_ref
from tools.catalog_domain import validate_catalog_documents


def _catalog():
    return {
        "toolchains.yml": {"Toolchains": [{"Id": "tool"}]},
        "programmers.yml": {"Programmers": [{"Id": "prog", "Bundled": "tool"}]},
        "features.yml": {"Features": [{"Id": "led_feature",
                                          "Capabilities": ["leds"]}]},
        "peripheral_devices.yml": {"Devices": [{"Id": "led_device",
                                                  "Feature": "led_feature",
                                                  "PeripheralDrivers": ["led"]}]},
        "board_producers.yml": {"Producers": [{"Id": "maker"}]},
        "chips/vendor/family.yml": {"DefaultToolchains": ["tool[*]"],
                                    "Chips": [{"Id": "chip"}]},
        "boards/vendor/family.yml": {"Boards": [{
            "Id": "board", "Chip": "chip", "BoardProducer": "maker",
            "Programmer": "prog", "ExtraProgrammers": ["prog[*]"],
            "Features": ["led_feature"], "Devices": [{"Id": "led_device"}]}]},
        "mezzanines/vendor/family.yml": {"Mezzanines": [{
            "Id": "addon", "Producer": "maker", "Chip": "chip",
            "Features": ["led_feature"], "Devices": ["led_device"],
            "CompatibleBoards": ["board"], "DefaultCarrier": "board"}]},
        "boards/vendor/family/board.yml": {"Board": {"id": "board"}},
        "capabilities/leds.yml": {"Capability": {"id": "leds"}},
        "peripherals/led.yml": {"Peripheral": {
            "id": "led", "provides": [{"capability": "leds"}]}},
        "configurations/rig.yml": {"Configuration": {
            "id": "rig", "board": "board", "toolchain": "tool",
            "attach": [{"peripheral": "led"}]}},
        "setups/rig.yml": {"Setup": {"id": "rig", "board": "board",
                                     "toolchain": "tool", "use": []}},
        "layouts/board.yml": {"Layout": {"board": "board"}},
        "profiles/rig.yml": {"Profile": {"configuration": "rig"}},
        "modules/addon.yml": {"Module": {"id": "addon", "peripheral": "led"}},
        "connectors.yml": {"Connectors": {}},
        "vendor_constraints.yml": {"VendorConstraints": {}},
    }


def test_valid_direct_references_are_only_a_partial_pass():
    report = validate_catalog_documents(_catalog())
    assert report["status"] == "partial_passed"
    assert report["findings"] == []
    assert report["unexamined"] == []
    assert report["scope"] == "identity-and-direct-references/v1"
    assert report["counts"]["board"] == 1


def test_unknown_references_and_duplicate_id_are_precise():
    documents = _catalog()
    documents["boards/vendor/other.yml"] = {"Boards": [{"Id": "board"}]}
    documents["configurations/rig.yml"]["Configuration"]["board"] = "absent"
    documents["configurations/rig.yml"]["Configuration"]["attach"] = [
        {"peripheral": "missing"}]
    documents["peripherals/led.yml"]["Peripheral"]["provides"] = [
        {"capability": "unknown"}]
    report = validate_catalog_documents(documents)
    assert report["status"] == "failed"
    assert {row["code"] for row in report["findings"]} == {
        "duplicate_identity", "unknown_reference"}
    assert any("boards/vendor/family.yml" in row["detail"]
               for row in report["findings"] if row["code"] == "duplicate_identity")


def test_extended_metadata_links_fail_with_field_specific_findings():
    documents = _catalog()
    board = documents["boards/vendor/family.yml"]["Boards"][0]
    board["Chips"] = [{"Id": "absent_chip"}]
    board["ExtraProgrammers"] = ["prog[]"]
    board["Features"] = ["missing_feature"]
    board["Devices"] = [{"Id": "missing_device"}]
    mezzanine = documents["mezzanines/vendor/family.yml"]["Mezzanines"][0]
    mezzanine["Producer"] = "missing_maker"
    mezzanine["CompatibleBoards"] = ["missing_board"]
    documents["features.yml"]["Features"][0]["Capabilities"] = ["missing_capability"]
    documents["peripheral_devices.yml"]["Devices"][0]["PeripheralDrivers"] = [
        "missing_driver"]
    documents["chips/vendor/family.yml"]["DefaultToolchains"] = ["tool[bad"]
    report = validate_catalog_documents(documents)
    assert report["status"] == "failed"
    assert sum(row["code"] == "invalid_reference" for row in report["findings"]) == 2
    assert sum(row["code"] == "unknown_reference" for row in report["findings"]) == 7
    for field in ("Chips", "ExtraProgrammers", "Features", "Devices", "Producer",
                  "CompatibleBoards", "Capabilities", "PeripheralDrivers",
                  "DefaultToolchains"):
        assert any(field in row["detail"] for row in report["findings"])


def test_shared_versioned_reference_parser_preserves_legacy_error_type():
    assert parse_versioned_ref("tool[*]") == ("tool", "*")
    assert config_init.parse_versioned_ref("tool") == ("tool", None)
    for invalid in ("tool[]", "tool[*]extra", "tool[bad\n]", "tool[nested[x]]",
                    None, ["tool"]):
        with pytest.raises(ValueError, match="Malformed versioned reference"):
            parse_versioned_ref(invalid)
        with pytest.raises(config_init.ConfigError, match="Malformed versioned reference"):
            config_init.parse_versioned_ref(invalid)


def test_direct_config_script_can_import_shared_reference_parser(monkeypatch):
    config_dir = Path(__file__).resolve().parents[1] / "config"
    monkeypatch.syspath_prepend(str(config_dir))
    namespace = runpy.run_path(str(config_dir / "init.py"), run_name="config_direct_probe")
    assert namespace["parse_versioned_ref"]("tool[*]") == ("tool", "*")


def test_identity_and_root_shape_errors_do_not_hide_other_records():
    documents = _catalog()
    documents["boards/vendor/family/board.yml"]["Board"]["id"] = "wrong"
    documents["modules/addon.yml"]["Module"]["id"] = "elsewhere"
    documents["peripherals/bad.yml"] = {"Peripheral": None}
    documents["mezzanines/vendor/empty.yml"] = {"Mezzanines": None}
    report = validate_catalog_documents(documents)
    assert report["status"] == "failed"
    assert {row["code"] for row in report["findings"]} >= {
        "filename_identity", "invalid_root", "unknown_reference"}
    assert report["counts"]["peripheral"] == 1


def test_missing_required_kind_and_unexamined_file_are_visible():
    documents = deepcopy(_catalog())
    del documents["toolchains.yml"]
    documents["future.yml"] = {"Future": {"id": "new"}}
    report = validate_catalog_documents(documents)
    assert report["status"] == "failed"
    assert report["unexamined"] == ["future.yml"]
    assert any(row["code"] == "unsupported_catalog_path"
               for row in report["findings"])
    assert any(row["code"] == "missing_kind" and "toolchain" in row["detail"]
               for row in report["findings"])


def test_malformed_attach_does_not_raise_unhandled_error():
    documents = _catalog()
    documents["configurations/rig.yml"]["Configuration"]["attach"] = [None]
    documents["setups/rig.yml"]["Setup"]["use"] = "invalid"
    report = validate_catalog_documents(documents)
    assert report["status"] == "failed"
    assert any(row["code"] == "invalid_reference" for row in report["findings"])


def test_finding_report_is_bounded(tmp_path, monkeypatch):
    documents = _catalog()
    documents["configurations/rig.yml"]["Configuration"]["attach"] = [
        {"peripheral": "unknown"}] * 20
    monkeypatch.setattr("tools.catalog_domain.MAX_FINDINGS", 2)
    report = validate_catalog_documents(documents)
    assert len(report["findings"]) == 3
    assert any(row["code"] == "finding_limit" for row in report["findings"])
    assert report["status"] == "failed"
