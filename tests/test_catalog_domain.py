"""Scoped domain checks consume parsed documents from an exact source tree."""

from copy import deepcopy

from tools.catalog_domain import validate_catalog_documents


def _catalog():
    return {
        "toolchains.yml": {"Toolchains": [{"Id": "tool"}]},
        "chips/vendor/family.yml": {"Chips": [{"Id": "chip"}]},
        "boards/vendor/family.yml": {"Boards": [{"Id": "board", "Chip": "chip"}]},
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
