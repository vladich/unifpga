"""Executable-operation admission for catalogue-only toolchains."""

import importlib

import pytest

import config.init
import program
import synthesize
from tools import toolchain_detect


STUB_IDS = {
    "ace", "actel_designer", "forge_fpga", "icecube2", "isplever",
    "isplever_classic", "lattice_diamond", "lattice_radiant", "libero_ide",
    "max_plus_2", "nanoxmap", "pango_ds", "qorc_sdk", "quickworks", "td",
}


def test_registry_declares_executable_operations():
    toolchains = config.init.read_toolchains()
    assert {tid for tid, tc in toolchains.items() if not tc["operations"]} == STUB_IDS
    assert toolchains["ise"]["operations"] == ["synthesize"]
    assert toolchains["libero_soc"]["operations"] == ["synthesize"]
    assert toolchains["vivado"]["operations"] == ["synthesize", "program"]


def test_detection_report_distinguishes_installation_from_operation_support(monkeypatch):
    def found(tid, pin=None):
        return toolchain_detect._found(tid, "vendor-install", [], {}, "test", None, [])

    monkeypatch.setattr(toolchain_detect, "detect", found)
    entries = config.init.read_toolchains()
    report = toolchain_detect.report({tid: entries[tid] for tid in ("ace", "vivado")})
    assert any("ace" in line and "found" in line and "catalogue only" in line for line in report)
    assert any("vivado" in line and "operations: synthesize, program" in line for line in report)


@pytest.mark.parametrize("operations", [None, "synthesize", ["build"], ["program", "program"], [4]])
def test_missing_or_invalid_operation_metadata_fails_closed(operations):
    with pytest.raises(config.init.ConfigError, match="unique operations list"):
        config.init.require_toolchain_operation(
            {"id": "bad", "operations": operations}, "synthesize")


@pytest.mark.parametrize("toolchain_id", sorted(STUB_IDS))
def test_placeholder_driver_methods_cannot_report_success(toolchain_id, tmp_path):
    driver = importlib.import_module("toolchains.{0}.{0}".format(toolchain_id))
    toolchain = {"id": toolchain_id}
    assert driver.synthesize(
        dir=str(tmp_path), configuration={"id": "example"}, board={"id": "board"},
        toolchain=toolchain, peripherals=[], top="design_top.sv",
        include=[], output=str(tmp_path)) != 0
    assert driver.program(board={"id": "board"}, toolchain=toolchain,
                          output=str(tmp_path)) != 0


def test_unsupported_build_is_rejected_before_output_or_tool_setup(tmp_path, monkeypatch):
    resolved = config.init.resolve_configuration("tang_nano_9k_hdmi_tm1638")
    resolved["toolchain"] = config.init.read_toolchains()["qorc_sdk"]
    monkeypatch.setattr(config.init, "read_or_init", lambda _, **__: resolved)
    monkeypatch.setattr(synthesize, "prepare_toolchain", lambda _: pytest.fail("tool setup ran"))
    output = tmp_path / "new-build"
    assert synthesize.main(["--top", "design_top.sv", "-o", str(output)]) == 2
    assert not output.exists()


def test_build_and_program_reject_unimplemented_programmer_before_build(tmp_path, monkeypatch):
    resolved = config.init.resolve_configuration("mojo_v3")
    monkeypatch.setattr(config.init, "read_or_init", lambda _, **__: resolved)
    monkeypatch.setattr(synthesize, "prepare_toolchain", lambda _: pytest.fail("tool setup ran"))
    output = tmp_path / "new-build"
    assert synthesize.main(["--top", "design_top.sv", "-o", str(output), "--program"]) == 2
    assert not output.exists()


def test_program_existing_build_rejects_unimplemented_programmer(tmp_path, monkeypatch):
    resolved = config.init.resolve_configuration("m2s025_starter")
    monkeypatch.setattr(config.init, "read_or_init", lambda _: resolved)
    monkeypatch.setattr(synthesize, "toolchain_module", lambda _: pytest.fail("driver loaded"))
    assert program.main(["-o", str(tmp_path)]) == 2


def test_direct_partial_programmers_refuse_success(tmp_path):
    from toolchains.ise import ise
    from toolchains.libero_soc import libero_soc

    (tmp_path / (ise.PROJECT_NAME + ".bit")).write_bytes(b"bitstream")
    (tmp_path / "libero_project").mkdir()
    assert ise.program(board={"id": "board"}, toolchain={"id": "ise"},
                       output=str(tmp_path)) != 0
    assert libero_soc.program(board={"id": "board"}, toolchain={"id": "libero_soc"},
                              output=str(tmp_path)) != 0
