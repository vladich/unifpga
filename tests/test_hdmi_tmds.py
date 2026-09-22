"""
P3.2: HDMI/TMDS — derived clocks (`from`/`divide`), the Gowin CLKDIV chain,
the shared Xilinx MMCM, the differential buffer kind and the pair-aware
constraint emitters.
"""

import logging
import os
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
logging.disable(logging.CRITICAL)

from config import init as config_init   # noqa: E402
from tools import codegen                # noqa: E402
from tools import pll_solver             # noqa: E402


def _resolve(cid):
    return config_init.resolve_configuration(cid)


def _attach(resolved, pid):
    return next(a for a in resolved["peripherals"] if a["peripheral_id"] == pid)


def test_gowin_hdmi_serial_pll_and_clkdiv_pixel_clock():
    r = _resolve("tang_nano_9k_hdmi_tm1638")
    tree = codegen.plan_clock_tree(r)
    assert [(n, v, round(s.f_out, 4)) for n, _r, v, s in tree] == \
        [("serial", "gowin_rpll", 252.0), ("pixel", "derived", 25.2)]
    top = codegen.emit_top_sv(r, strict=True)
    # BGM tang_primer_20k_dock_hdmi_tm1638_yosys: rPLL IDIV 2 / FBDIV 27 / ODIV 2 -> 252 MHz
    assert ".IDIV_SEL(2), .FBDIV_SEL(27), .ODIV_SEL(2)" in top
    assert 'DEVICE("GW1NR-9C")' in top
    assert "clkdiv_gowin # (.DIV(10)) i_div_pixel (.clk_in(clk_serial), .resetn(clk_serial_locked), .clk_out(clk_pixel));" in top
    assert 'hdmi_tmds_out # (.DIFF_BUF("gowin_elvds"))' in top      # LittleBee: ELVDS_OBUF
    assert ".serial_clk_i(clk_serial)" in top and ".pixel_clk_i(clk_pixel)" in top
    assert ".tmds_clk_p(onboard_hdmi_clk_p)" in top and ".tmds_d_p(onboard_hdmi_d_p)" in top
    assert set(codegen.pll_source_files(top)) == {
        os.path.join("rtl", "pll", "pll_gowin_rpll.sv"), os.path.join("rtl", "pll", "clkdiv_gowin.sv")}
    # driver.files reach the source collectors
    extra = codegen.pll_source_paths(REPO, os.devnull, r["peripherals"])
    assert os.path.join(REPO, "rtl", "peripherals", "dvi.sv") in extra
    assert os.path.join(REPO, "rtl", "io", "diff_obuf.sv") in extra


def test_gowin_eda_cst_locates_pairs_through_the_p_port():
    r = _resolve("tang_nano_9k_hdmi_tm1638")
    cst = codegen.emit_cst(r)
    assert 'IO_LOC  "onboard_hdmi_clk_p" 69,68;' in cst          # BGM: IO_LOC "TMDS_CLK_P" 69,68;
    assert 'IO_LOC  "onboard_hdmi_d_p[2]" 75,74;' in cst
    assert 'IO_LOC  "onboard_hdmi_clk_n"' not in cst              # the N half is implied by the pair
    assert 'IO_PORT "onboard_hdmi_clk_p"' not in cst              # buffer type comes from the design


def test_apicula_cst_lists_both_halves():
    r = _resolve("tang_primer_20k_dock_hdmi_tm1638_yosys")
    cst = codegen.emit_cst(r)
    # BGM tang_primer_20k_dock_hdmi_tm1638_yosys/board_specific.cst
    assert 'IO_LOC  "onboard_hdmi_clk_p" G16;' in cst
    assert 'IO_LOC  "onboard_hdmi_clk_n" H15;' in cst
    assert 'IO_LOC  "onboard_hdmi_d_p[0]" H14;' in cst
    assert 'IO_LOC  "onboard_hdmi_d_n[0]" H16;' in cst
    top = codegen.emit_top_sv(r, strict=True)
    assert 'hdmi_tmds_out # (.DIFF_BUF("gowin_tlvds"))' in top     # Arora: TLVDS_OBUF


def test_pixel_clock_equal_to_board_clock_is_an_alias():
    """BGM tang_nano_4k: I_rgb_clk = clk (27 MHz). With serial = 270 MHz the
    derived pixel clock is the oscillator itself, so no divider is built."""
    r = _resolve("tang_nano_4k_hdmi_tm1638")
    _attach(r, "hdmi_tmds").setdefault("params", {})["clock_serial_mhz"] = 270
    tree = codegen.plan_clock_tree(r)
    kinds = {n: v for n, _r, v, _s in tree}
    assert kinds == {"serial": "gowin_rpll", "pixel": "alias"}
    top = codegen.emit_top_sv(r, strict=True)
    assert "wire clk_pixel = clk;" in top
    assert "clkdiv_gowin" not in top


def test_serial_override_moves_the_derived_pixel_clock():
    r = _resolve("tang_nano_20k_hdmi_tm1638")
    _attach(r, "hdmi_tmds").setdefault("params", {})["clock_serial_mhz"] = 249.75   # BGM 124.875 MHz x 2
    tree = codegen.plan_clock_tree(r)
    by = {n: s.f_out for n, _r, _v, s in tree}
    assert abs(by["serial"] - 249.75) < 1e-6 and abs(by["pixel"] - 24.975) < 1e-6


def test_xilinx_mmcm_shared_outputs():
    sol = pll_solver.xilinx_mmcm(50, [250, 25])
    assert (sol.divclk, sol.mult, sol.odivs) == (1, 20, (4, 40))      # BGM a7_lite_35t clk_wiz: VCO 1000 MHz
    assert sol.f_outs == (250.0, 25.0)
    assert pll_solver.xilinx_mmcm(50, [250, 25, 50]).odivs == (4, 40, 20)
    assert pll_solver.xilinx_mmcm(12, [2000]) is None


def test_a7_lite_hdmi_uses_one_mmcm_and_tmds_33():
    r = _resolve("a7_lite_35t")
    if "hdmi_tmds" not in {a["peripheral_id"] for a in r["peripherals"]}:
        pytest.skip("a7_lite_35t has no hdmi_tmds attach (run sync --sv-binds)")
    tree = codegen.plan_clock_tree(r)
    assert {n: v for n, _r, v, _s in tree} == {"serial": "xilinx_mmcm", "pixel": "xilinx_mmcm"}
    top = codegen.emit_top_sv(r, strict=True)
    assert "pll_xilinx_mmcm # (.CLKIN_PERIOD(20.000), .DIVCLK_DIVIDE(1), .CLKFBOUT_MULT_F(20.0), " \
           ".CLKOUT0_DIVIDE(4.0), .CLKOUT1_DIVIDE(40), .CLKOUT2_DIVIDE(1))" in top
    assert ".clkout0(clk_serial), .clkout1(clk_pixel)" in top
    assert 'hdmi_tmds_out # (.DIFF_BUF("xilinx"))' in top
    xdc = codegen.emit_xdc(r)
    # BGM a7_lite_35t/board_specific.xdc: PACKAGE_PIN L19 + IOSTANDARD TMDS_33 on TMDS_CLK_P
    assert "PACKAGE_PIN L19 IOSTANDARD TMDS_33 } [get_ports { onboard_hdmi_clk_p }]" in xdc
    assert "PACKAGE_PIN L20 IOSTANDARD TMDS_33 } [get_ports { onboard_hdmi_clk_n }]" in xdc
    assert "PACKAGE_PIN G17 IOSTANDARD TMDS_33 } [get_ports { onboard_hdmi_d_p[2] }]" in xdc


def test_diff_buf_kind_by_family():
    assert codegen.diff_buf_kind(_resolve("tang_nano_9k_hdmi_tm1638")) == "gowin_elvds"
    assert codegen.diff_buf_kind(_resolve("tang_nano_20k_hdmi_tm1638")) == "gowin_tlvds"
    assert codegen.diff_buf_kind(_resolve("a7_lite_35t")) == "xilinx"
    assert codegen.diff_buf_kind(_resolve("basys3")) == "xilinx"
    assert codegen.diff_buf_kind(_resolve("de10_lite")) == "generic"


def test_conflicting_clock_definitions_rejected():
    r = _resolve("tang_nano_9k_hdmi_tm1638")
    a = _attach(r, "hdmi_tmds")
    import copy
    twin = copy.deepcopy(a)
    twin["peripheral"] = copy.deepcopy(a["peripheral"])
    twin["peripheral"]["clocks"] = [{"name": "serial", "mhz": 252}, {"name": "pixel", "from": "serial", "divide": 5}]
    r["peripherals"].append(twin)
    with pytest.raises(codegen.CodegenError):
        codegen.collect_clock_requirements(r)
