"""
tools/source_set.collect_sources() against fake repository trees: the three
frontend flags (.svh inclusion, helper gating, designs_common gating), the
always-appended clock-tree wrappers and driver `files:`, ordering
and de-duplication. No toolchain is needed.
"""

import os
import sys
import yaml

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from tools import source_set   # noqa: E402


def _write(path, text=""):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        fh.write(text)
    return path


def _peripheral(file=None, files=()):
    drv = {}
    if file:
        drv["file"] = file
    if files:
        drv["files"] = list(files)
    return {"peripheral_id": "p", "peripheral": {"driver": drv}, "params": {}, "bind": {}}


@pytest.fixture
def repo(tmp_path):
    """A fake checkout with every rtl/ directory collect_sources() looks at."""
    root = str(tmp_path / "repo")
    per = os.path.join(root, "rtl", "peripherals")
    for name in source_set.HELPER_MODULES:
        _write(os.path.join(per, name), "module %s; endmodule\n" % name[:-3])
    for name in ("seven_segment_display.sv", "shift_reg.sv", "strobe_gen.sv"):
        _write(os.path.join(per, "designs_common", name), "module %s; endmodule\n" % name[:-3])
    _write(os.path.join(per, "designs_common", "README.md"), "not a source\n")
    _write(os.path.join(per, "hdmi_tmds_out.sv"), "module hdmi_tmds_out; endmodule\n")
    _write(os.path.join(per, "dvi.sv"), "module dvi; endmodule\n")
    _write(os.path.join(root, "rtl", "pll", "pll_xilinx_mmcm.sv"), "module pll_xilinx_mmcm; endmodule\n")
    return root


@pytest.fixture
def design(tmp_path):
    """designs/lab/ with a top, a sibling module that instantiates shift_reg,
    a header, a testbench and a nested directory (the adapted-design layout).
    Returns the design_top.sv path."""
    d = str(tmp_path / "designs" / "lab")
    top = _write(os.path.join(d, "design_top.sv"), "module design_top; endmodule\n")
    _write(os.path.join(d, "helper.sv"), "module helper; shift_reg u_sr(); endmodule\n")
    _write(os.path.join(d, "defs.svh"), "`define LAB 1\n")
    _write(os.path.join(d, "tb.sv"), "module tb; endmodule\n")
    _write(os.path.join(d, "cpu", "core.v"), "module core; endmodule\n")
    return top


@pytest.fixture
def gen_top(tmp_path):
    def make(text="module top; endmodule\n"):
        return _write(str(tmp_path / "out" / "top.sv"), text)
    return make


def _p(repo, *rel):
    return os.path.join(repo, *rel)


def _design_files(top):
    d = os.path.dirname(top)
    return [os.path.join(d, "helper.sv"), os.path.join(d, "cpu", "core.v")]


# ---------------------------------------------------------------- ordering

def test_default_order_tops_design_walk_then_gated_common(repo, design, gen_top):
    top = gen_top()
    files = source_set.collect_sources(repo, [], design, top)
    # generated top, user top, design dir (design_top.sv / tb.sv / .svh skipped,
    # nested .v walked), then shift_reg — named by helper.sv, not by the top.
    assert files == [top, design] + _design_files(design) + [
        _p(repo, "rtl", "peripherals", "designs_common", "shift_reg.sv")]


def test_common_module_used_by_another_common_module_is_collected(repo, tmp_path, gen_top):
    """pulse_extender instantiates shift_reg, which sorts before it: shift_reg
    is still collected (5_4_yrv on the yosys flows needed both)."""
    _write(_p(repo, "rtl", "peripherals", "designs_common", "pulse_extender.sv"),
           "module pulse_extender; shift_reg u(); endmodule\n")
    top = _write(str(tmp_path / "designs" / "pe" / "design_top.sv"),
                 "module design_top; pulse_extender u(); endmodule\n")
    files = source_set.collect_sources(repo, [], top, gen_top())
    common = [os.path.basename(f) for f in files if "designs_common" in f]
    assert common == ["pulse_extender.sv", "shift_reg.sv"]


def test_generated_top_first_user_top_second_even_when_named_oddly(repo, tmp_path, gen_top):
    d = str(tmp_path / "designs" / "odd")
    user_top = _write(os.path.join(d, "blink.sv"), "module blink; endmodule\n")
    _write(os.path.join(d, "aaa.sv"), "module aaa; endmodule\n")
    top = gen_top()
    files = source_set.collect_sources(repo, [], user_top, top, gate_common=False)
    assert files[:2] == [top, user_top]
    # blink.sv is also met by the directory walk: listed once, in its top slot.
    assert files.count(user_top) == 1
    assert files[2] == os.path.join(d, "aaa.sv")


def test_peripheral_driver_files_follow_design_and_skip_missing(repo, design, gen_top):
    top = gen_top()
    peripherals = [_peripheral("rtl/peripherals/hdmi_tmds_out.sv"),
                   _peripheral("rtl/peripherals/does_not_exist.sv"),
                   _peripheral(None)]
    files = source_set.collect_sources(repo, peripherals, design, top, gate_common=False)
    assert files == [top, design] + _design_files(design) + [
        _p(repo, "rtl", "peripherals", "hdmi_tmds_out.sv"),
        _p(repo, "rtl", "peripherals", "designs_common", "seven_segment_display.sv"),
        _p(repo, "rtl", "peripherals", "designs_common", "shift_reg.sv"),
        _p(repo, "rtl", "peripherals", "designs_common", "strobe_gen.sv")]


# ---------------------------------------------------------------- include_svh

@pytest.mark.parametrize("include_svh", [False, True])
def test_include_svh_controls_headers_only(repo, design, gen_top, include_svh):
    top = gen_top()
    files = source_set.collect_sources(repo, [], design, top, include_svh=include_svh)
    svh = os.path.join(os.path.dirname(design), "defs.svh")
    assert (svh in files) is include_svh
    if include_svh:
        # sorted within the directory: defs.svh precedes helper.sv
        assert files.index(svh) < files.index(os.path.join(os.path.dirname(design), "helper.sv"))
    # .sv / .v selection is unaffected
    for f in _design_files(design):
        assert f in files
    assert os.path.join(os.path.dirname(design), "tb.sv") not in files


# ---------------------------------------------------------------- gate_helpers

def _helpers_in(files, repo):
    per = _p(repo, "rtl", "peripherals")
    return [os.path.basename(f) for f in files
            if os.path.dirname(f) == per and os.path.basename(f) in source_set.HELPER_MODULES]


def test_gate_helpers_on_includes_only_mentioned_modules(repo, design, gen_top):
    top = gen_top("module top; slow_clk_gen u_div(); endmodule\n")
    files = source_set.collect_sources(repo, [], design, top, gate_helpers=True, gate_common=False)
    assert _helpers_in(files, repo) == ["slow_clk_gen.sv"]


def test_gate_helpers_recognises_tm1638_alias(repo, design, gen_top):
    top = gen_top("module top; tm1638_board_controller u_tm(); endmodule\n")
    files = source_set.collect_sources(repo, [], design, top, gate_helpers=True, gate_common=False)
    assert _helpers_in(files, repo) == ["tm1638_registers.sv"]


def test_gate_helpers_recognises_design_source_dependency(repo, design, gen_top):
    _write(design, "module design_top; pdm_mic_decoder decoder(); endmodule\n")
    files = source_set.collect_sources(repo, [], design, gen_top(),
                                       gate_helpers=True, gate_common=False)
    assert _helpers_in(files, repo) == ["pdm_mic_decoder.sv"]


def test_gate_helpers_off_takes_all_in_fixed_order(repo, design, gen_top):
    top = gen_top()   # mentions none of them
    files = source_set.collect_sources(repo, [], design, top, gate_helpers=False, gate_common=False)
    assert _helpers_in(files, repo) == ["tm1638_registers.sv", "slow_clk_gen.sv",
                                        "imitate_reset_on_power_up.sv", "pdm_mic_decoder.sv"]
    # helpers come right after the design directory and before designs_common
    first_helper = files.index(_p(repo, "rtl", "peripherals", "tm1638_registers.sv"))
    assert files[first_helper - 1] == _design_files(design)[-1]
    assert files[first_helper + 4].endswith(os.path.join("designs_common", "seven_segment_display.sv"))


# ---------------------------------------------------------------- gate_common

def _common_in(files, repo):
    common = _p(repo, "rtl", "peripherals", "designs_common")
    return [os.path.basename(f) for f in files if os.path.dirname(f) == common]


def test_gate_common_on_matches_top_and_collected_siblings(repo, design, gen_top):
    top = gen_top("module top; seven_segment_display u_ssd(); endmodule\n")
    files = source_set.collect_sources(repo, [], design, top, gate_common=True)
    # seven_segment_display from the top, shift_reg from helper.sv, strobe_gen unmentioned
    assert _common_in(files, repo) == ["seven_segment_display.sv", "shift_reg.sv"]


def test_gate_common_sees_peripheral_driver_text(repo, design, gen_top):
    _write(_p(repo, "rtl", "peripherals", "strober.sv"), "module strober; strobe_gen u(); endmodule\n")
    top = gen_top()
    files = source_set.collect_sources(repo, [_peripheral("rtl/peripherals/strober.sv")], design, top)
    assert "strobe_gen.sv" in _common_in(files, repo)


def test_gate_common_off_takes_every_sv_sorted(repo, design, gen_top):
    top = gen_top()
    files = source_set.collect_sources(repo, [], design, top, gate_common=False)
    assert _common_in(files, repo) == ["seven_segment_display.sv", "shift_reg.sv", "strobe_gen.sv"]
    assert not any(f.endswith("README.md") for f in files)


# ---------------------------------------------------------------- pll / driver files

@pytest.mark.parametrize("flags", [
    dict(),
    dict(include_svh=True, gate_helpers=False, gate_common=False),
    dict(include_svh=False, gate_helpers=False, gate_common=False),
])
def test_pll_wrapper_appended_under_every_flag_combination(repo, design, gen_top, flags):
    top = gen_top("module top;\n  pll_xilinx_mmcm #(.MULT(10)) u_pll(.clk_in(clk));\nendmodule\n")
    files = source_set.collect_sources(repo, [], design, top, **flags)
    assert files[-1] == _p(repo, "rtl", "pll", "pll_xilinx_mmcm.sv")
    assert files.count(files[-1]) == 1


def test_no_pll_when_top_does_not_instantiate_one(repo, design, gen_top):
    files = source_set.collect_sources(repo, [], design, gen_top())
    assert not any(os.sep + "pll" + os.sep in f for f in files)


def test_driver_extra_files_appended_after_pll_wrapper(repo, design, gen_top):
    top = gen_top("module top; pll_xilinx_mmcm u_pll(); hdmi_tmds_out u_hdmi(); endmodule\n")
    peripherals = [_peripheral("rtl/peripherals/hdmi_tmds_out.sv", files=["rtl/peripherals/dvi.sv"])]
    files = source_set.collect_sources(repo, peripherals, design, top)
    assert files[-2:] == [_p(repo, "rtl", "pll", "pll_xilinx_mmcm.sv"),
                          _p(repo, "rtl", "peripherals", "dvi.sv")]
    assert files.index(_p(repo, "rtl", "peripherals", "hdmi_tmds_out.sv")) < len(files) - 2


# ---------------------------------------------------------------- de-duplication

def test_dedup_across_stages(repo, design, gen_top):
    # The same peripheral attached twice, its `files:` naming its own `file`
    # and a designs_common module, and the top naming that module too.
    top = gen_top("module top; hdmi_tmds_out u1(); hdmi_tmds_out u2(); shift_reg s(); endmodule\n")
    hdmi = "rtl/peripherals/hdmi_tmds_out.sv"
    peripherals = [_peripheral(hdmi, files=[hdmi, "rtl/peripherals/designs_common/shift_reg.sv"]),
                   _peripheral(hdmi)]
    files = source_set.collect_sources(repo, peripherals, design, top)
    assert len(files) == len(set(files))
    assert files.count(_p(repo, hdmi)) == 1
    assert files.count(_p(repo, "rtl", "peripherals", "designs_common", "shift_reg.sv")) == 1
    # first occurrence wins: shift_reg stays in its designs_common slot, not
    # re-added at the end by pll_source_paths
    assert files[-1] == _p(repo, "rtl", "peripherals", "designs_common", "shift_reg.sv")


# ---------------------------------------------------------------- degenerate inputs

def test_bare_repo_yields_only_the_two_tops(tmp_path, gen_top):
    repo = str(tmp_path / "empty")
    os.makedirs(repo)
    user_top = _write(str(tmp_path / "designs" / "solo" / "design_top.sv"), "module design_top; endmodule\n")
    top = gen_top()
    assert source_set.collect_sources(repo, None, user_top, top) == [top, user_top]
    assert source_set.collect_sources(repo, [], user_top, top, include_svh=True, gate_helpers=False,
                                      gate_common=False) == [top, user_top]


def test_unreadable_generated_top_gates_everything_out(repo, design, tmp_path):
    missing = str(tmp_path / "out" / "never_written.sv")
    files = source_set.collect_sources(repo, [], design, missing)
    assert files[0] == missing
    assert _helpers_in(files, repo) == []
    # helper.sv still names shift_reg, so sibling gating keeps working
    assert _common_in(files, repo) == ["shift_reg.sv"]


def test_generated_runs_never_reenter_a_design_fileset(repo, design, gen_top):
    d = os.path.dirname(design)
    _write(os.path.join(d, "run", "board_a", "top.sv"), "module top; endmodule\n")
    _write(os.path.join(d, "run", "board_b", "top.sv"), "module top; endmodule\n")
    _write(os.path.join(d, "build", "old.sv"), "module old; endmodule\n")
    files = source_set.collect_sources(repo, [], design, gen_top())
    assert not any(p.startswith(os.path.join(d, sub) + os.sep)
                   for p in files for sub in ("run", "build"))


def test_explicit_fileset_selects_one_implementation_and_stages_assets(repo, design, gen_top, tmp_path):
    d = os.path.dirname(design)
    alternate = _write(os.path.join(d, "legacy", "core.sv"), "module core; endmodule\n")
    selected = os.path.join(d, "cpu", "core.v")
    tb = os.path.join(d, "tb.sv")
    rom = _write(os.path.join(d, "data", "program.hex"), "00000000\n")
    with open(os.path.join(d, source_set.DESIGN_FILESET), "w") as fh:
        yaml.safe_dump({"version": 1, "sources": ["cpu/core.v", "design_top.sv"],
                        "simulation": ["tb.sv"], "assets": ["data/program.hex"]}, fh)
    generated = gen_top()
    files = source_set.collect_sources(repo, [], design, generated)
    assert files[:3] == [generated, selected, design]
    assert alternate not in files and tb not in files
    assert source_set.design_inputs(d) == ([selected, design], [tb], [rom])
    out = str(tmp_path / "output")
    source_set.stage_assets(d, out)
    assert open(os.path.join(out, "data", "program.hex")).read() == "00000000\n"


@pytest.mark.parametrize("entry", ["../outside.sv", "/absolute.sv", "cpu/../core.v", "missing.sv"])
def test_explicit_fileset_rejects_unsafe_or_missing_sources(design, entry):
    d = os.path.dirname(design)
    with open(os.path.join(d, source_set.DESIGN_FILESET), "w") as fh:
        yaml.safe_dump({"version": 1, "sources": ["design_top.sv", entry],
                        "simulation": [], "assets": []}, fh)
    with pytest.raises(source_set.SourceSetError):
        source_set.design_inputs(d)


def test_explicit_fileset_rejects_symlink_escape(design, tmp_path):
    d = os.path.dirname(design)
    outside = _write(str(tmp_path / "outside.sv"), "module outside; endmodule\n")
    os.symlink(outside, os.path.join(d, "linked.sv"))
    with open(os.path.join(d, source_set.DESIGN_FILESET), "w") as fh:
        yaml.safe_dump({"version": 1, "sources": ["design_top.sv", "linked.sv"],
                        "simulation": [], "assets": []}, fh)
    with pytest.raises(source_set.SourceSetError, match="escapes"):
        source_set.design_inputs(d)


def test_explicit_fileset_rejects_two_paths_to_one_source(design):
    d = os.path.dirname(design)
    os.symlink(os.path.join(d, "cpu", "core.v"), os.path.join(d, "alias.v"))
    with open(os.path.join(d, source_set.DESIGN_FILESET), "w") as fh:
        yaml.safe_dump({"version": 1,
                        "sources": ["design_top.sv", "cpu/core.v", "alias.v"],
                        "simulation": [], "assets": []}, fh)
    with pytest.raises(source_set.SourceSetError, match="duplicate"):
        source_set.design_inputs(d)


def test_asset_staging_rejects_output_symlink(design, tmp_path):
    d = os.path.dirname(design)
    _write(os.path.join(d, "data", "program.hex"), "00000000\n")
    out = tmp_path / "output"
    out.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    os.symlink(outside, out / "data")
    with pytest.raises(source_set.SourceSetError, match="escapes output"):
        source_set.stage_assets(d, str(out))
    assert not (outside / "program.hex").exists()


def test_asset_staging_failure_keeps_previous_complete_file(design, tmp_path, monkeypatch):
    d = os.path.dirname(design)
    source = _write(os.path.join(d, "program.hex"), "new\n")
    out = tmp_path / "output"
    out.mkdir()
    target = out / "program.hex"
    target.write_text("old\n")

    def fail_replace(_source, _target):
        raise OSError("simulated rename failure")

    monkeypatch.setattr(source_set.os, "replace", fail_replace)
    with pytest.raises(source_set.SourceSetError, match="simulated rename failure"):
        source_set.stage_assets(d, str(out))
    assert os.path.isfile(source)
    assert target.read_text() == "old\n"
    assert not list(out.glob(".unifpga-asset-*"))


def test_aps_manifest_excludes_duplicate_legacy_modules_and_orders_packages():
    d = os.path.join(REPO, "designs", "5_5_aps")
    sources, simulation, assets = source_set.design_inputs(d)
    rel = [os.path.relpath(p, d) for p in sources]
    assert "aps_cpu/processor_system.sv" in rel
    assert "aps_cpu/processor_core.sv" in rel
    assert "processor_core.sv" not in rel
    assert "decoder_pkg.sv" not in rel
    assert rel.index("pkg/decoder_pkg.sv") < rel.index("design_top.sv")
    assert rel.index("pkg/decoder_pkg.sv") < rel.index("aps_cpu/processor_core.sv")
    assert simulation == [os.path.join(d, "tb.sv"), os.path.join(d, "tb_firmware.sv")]
    assert os.path.join(d, "program.hex") in assets
