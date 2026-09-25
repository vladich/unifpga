"""Generated component RTL enters the ordinary unifpga source-set boundary."""

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools import cli, source_set


def _export(tmp_path, source=b"module generated; endmodule\n"):
    root = tmp_path / "export"
    root.mkdir()
    rtl = root / "generated.v"
    rtl.write_bytes(source)
    manifest = root / "manifest.json"
    manifest.write_text(json.dumps({
        "schema": source_set.COMPONENT_EXPORT_SCHEMA,
        "component": "example:generated",
        "files": [{"path": rtl.name, "size": len(source),
                   "sha256": hashlib.sha256(source).hexdigest()}],
    }), encoding="utf-8")
    return manifest, rtl


def test_generated_rtl_enters_normal_simulation_source_order(tmp_path):
    manifest, rtl = _export(tmp_path)
    design = tmp_path / "design"
    design.mkdir()
    (design / "design_top.sv").write_text("module design_top; generated u(); endmodule\n")
    (design / "tb.sv").write_text("module tb; design_top u(); endmodule\n")
    command = cli.sim_command(str(design), str(tmp_path / "out"),
                              component_exports=[manifest])
    assert command.index(str(rtl)) < command.index(str(design / "design_top.sv"))
    assert command[command.index("-s") + 1] == "tb"


def test_generated_basename_cannot_hide_a_native_peripheral(tmp_path):
    manifest, rtl = _export(tmp_path)
    replacement = rtl.with_name("pdm_mic_decoder.sv")
    rtl.rename(replacement)
    report = json.loads(manifest.read_text())
    report["files"][0]["path"] = replacement.name
    manifest.write_text(json.dumps(report))
    design = tmp_path / "design"
    design.mkdir()
    (design / "design_top.sv").write_text("module design_top; endmodule\n")
    (design / "tb.sv").write_text("module tb; endmodule\n")
    files = cli.sim_sources(str(design), [manifest])
    assert str(replacement) in files
    assert str(Path(cli.REPO) / "rtl/peripherals/pdm_mic_decoder.sv") in files


def test_modified_generated_rtl_is_rejected(tmp_path):
    manifest, rtl = _export(tmp_path)
    rtl.write_bytes(b"module changed; endmodule\n")
    with pytest.raises(source_set.SourceSetError, match="checksum mismatch"):
        source_set.component_export_sources([manifest])


@pytest.mark.parametrize("bad_path", ["../outside.v", "nested/../generated.v", "/outside.v",
                                      "nested\\generated.v", "generated.v\x00"])
def test_unsafe_generated_path_is_rejected(tmp_path, bad_path):
    manifest, _ = _export(tmp_path)
    report = json.loads(manifest.read_text())
    report["files"][0]["path"] = bad_path
    manifest.write_text(json.dumps(report))
    with pytest.raises(source_set.SourceSetError, match="invalid component export RTL path"):
        source_set.component_export_sources([manifest])


def test_symlink_and_duplicate_json_key_are_rejected(tmp_path):
    manifest, rtl = _export(tmp_path)
    real = rtl.with_name("real.v")
    rtl.rename(real)
    rtl.symlink_to(real)
    with pytest.raises(source_set.SourceSetError, match="missing or escapes"):
        source_set.component_export_sources([manifest])
    rtl.unlink()
    rtl.write_bytes(real.read_bytes())
    content = manifest.read_text()
    manifest.write_text(content.replace('"schema":', '"schema": "wrong", "schema":', 1))
    with pytest.raises(source_set.SourceSetError, match="duplicate JSON key"):
        source_set.component_export_sources([manifest])


def test_unknown_schema_and_invalid_top_are_rejected(tmp_path):
    manifest, _ = _export(tmp_path)
    report = json.loads(manifest.read_text())
    report["schema"] = "litex-private/v1"
    manifest.write_text(json.dumps(report))
    with pytest.raises(source_set.SourceSetError, match="unsupported component export schema"):
        source_set.component_export_sources([manifest])
    with pytest.raises(cli.CliError, match="simulation top"):
        cli.sim_command(str(tmp_path), str(tmp_path / "out"), tb_top="bad;rm")


def test_same_component_cannot_be_selected_twice(tmp_path):
    manifest, _ = _export(tmp_path)
    with pytest.raises(source_set.SourceSetError, match="duplicate component export identity"):
        source_set.component_export_sources([manifest, manifest])


def test_cli_rejects_changed_export_before_creating_output(tmp_path, monkeypatch, capsys):
    manifest, rtl = _export(tmp_path)
    rtl.write_bytes(b"module changed; endmodule\n")
    design = tmp_path / "design"
    design.mkdir()
    (design / "design_top.sv").write_text("module design_top; endmodule\n")
    (design / "tb.sv").write_text("module tb; endmodule\n")
    monkeypatch.setattr(cli.shutil, "which", lambda name: "/stub/" + name)
    monkeypatch.setattr(cli.subprocess, "run", lambda *args, **kwargs:
                        SimpleNamespace(stdout="Icarus Verilog version 13.0"))
    output = tmp_path / "out"
    assert cli.main(["sim", str(design), "--component-export", str(manifest),
                     "--output-dir", str(output), "--no-wave"]) == 1
    assert not output.exists()
    assert "checksum mismatch" in capsys.readouterr().err


def test_explicit_output_cannot_be_inside_design(tmp_path, monkeypatch, capsys):
    design = tmp_path / "design"
    design.mkdir()
    (design / "design_top.sv").write_text("module design_top; endmodule\n")
    (design / "tb.sv").write_text("module tb; endmodule\n")
    monkeypatch.setattr(cli.shutil, "which", lambda name: "/stub/" + name)
    output = design / "build-products"
    assert cli.main(["sim", str(design), "--output-dir", str(output), "--no-wave"]) == 1
    assert not output.exists()
    assert "separate from the design" in capsys.readouterr().err
