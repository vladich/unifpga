"""
P3.1: peripheral-declared clocks (`clocks:` + `clock.<name>`), the vendor PLL
wrappers codegen instantiates for them, the `lab_clock:` switch and the
constraints for pads driven straight from a PLL clock.
"""

import copy
import logging
import os
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
logging.disable(logging.CRITICAL)

from config import init as config_init   # noqa: E402
from tools import codegen                # noqa: E402


def _resolve(cid):
    return config_init.resolve_configuration(cid)


def _attach(resolved, pid):
    return next(a for a in resolved["peripherals"] if a["peripheral_id"] == pid)


# ---------------------------------------------------------------------------
# Gowin rPLL: Tang Nano 9K 480x272 (BGM: Gowin_rPLL 27 -> 9 MHz on LARGE_LCD_CK)
# ---------------------------------------------------------------------------

def test_gowin_rpll_clock_tree_for_lcd():
    r = _resolve("tang_nano_9k_lcd_480_272_no_tm1638")
    tree = codegen.plan_clock_tree(r)
    assert [(n, v) for n, _req, v, _sol in tree] == [("pixel", "gowin_rpll")]
    sol = tree[0][3]
    assert abs(sol.f_out - 9.0) < 1e-9
    assert 3.0 <= sol.f_pfd <= 400.0 and 400.0 <= sol.f_vco <= 1200.0

    top = codegen.emit_top_sv(r, strict=True)
    assert "wire clk_pixel, clk_pixel_locked;" in top
    assert 'pll_gowin_rpll # (.PRIMITIVE("rPLL"), .FCLKIN("27")' in top and '.DEVICE("GW1NR-9C")' in top
    assert ".clkin(clk), .clkout(clk_pixel)" in top
    assert ".PixelClk(clk_pixel)" in top
    assert "assign onboard_lcd_ck = clk_pixel;" in top
    # the lab stays on the board clock
    assert "localparam int clk_mhz = 27;" in top
    assert "        .clk(clk)," in top

    sdc = codegen.emit_sdc(r)
    assert "create_clock -name sys_clk_27mhz -period 37.037 [get_ports {clk}]" in sdc
    # BGM: create_clock -name LARGE_LCD_CK -period 111.11 [get_ports {LARGE_LCD_CK}]
    assert "create_clock -name onboard_lcd_ck -period 111.111 [get_ports {onboard_lcd_ck}]" in sdc

    assert codegen.pll_source_files(top) == [os.path.join("rtl", "pll", "pll_gowin_rpll.sv")]


def test_gowin_rpll_device_from_set_device_args():
    r = _resolve("tang_nano_20k_lcd_800_480_no_tm1638")
    top = codegen.emit_top_sv(r, strict=True)
    assert '.DEVICE("GW2AR-18C")' in top          # BGM gowin_rpll.v: defparam rpll_inst.DEVICE = "GW2AR-18C"


def test_clock_frequency_override_per_configuration():
    """`params.clock_pixel_mhz` (written by sync --clock-tree from BGM's
    gowin_rpll.v) overrides the peripheral default."""
    r = _resolve("tang_nano_9k_lcd_480_272_no_tm1638")
    a = _attach(r, "lcd_480_272")
    a.setdefault("params", {})["clock_pixel_mhz"] = 32.4
    tree = codegen.plan_clock_tree(r)
    assert abs(tree[0][3].f_out - 32.4) < 1e-9


def test_conflicting_clock_requests_are_an_error():
    r = _resolve("tang_nano_9k_lcd_480_272_no_tm1638")
    a = _attach(r, "lcd_480_272")
    twin = copy.deepcopy(a)
    twin["params"] = {"clock_pixel_mhz": 33}
    r["peripherals"].append(twin)
    with pytest.raises(codegen.CodegenError):
        codegen.plan_clock_tree(r)


# ---------------------------------------------------------------------------
# iCE40 SB_PLL40 + lab_clock (BGM iCEBreaker DVI: lab runs on the 25.125 MHz PLL)
# ---------------------------------------------------------------------------

def test_ice40_pll_and_lab_clock():
    r = _resolve("icebreaker_dvi_12b_no_tm1638_yosys")
    r["configuration"].pop("lab_clock", None)
    top = codegen.emit_top_sv(r, strict=True)
    assert "pll_ice40 # (.DIVR(4'd0), .DIVF(7'd66), .DIVQ(3'd5), .FILTER_RANGE(3'd1), .USE_PAD(1'b0))" in top
    assert "localparam int clk_mhz = 12;" in top
    assert "        .clk(clk)," in top

    r["configuration"]["lab_clock"] = "pixel"
    top = codegen.emit_top_sv(r, strict=True)
    # SB_PLL40_PAD (BGM's choice) once nothing else needs the clock pad
    assert ".USE_PAD(1'b1)" in top
    assert "localparam int clk_mhz = 25;" in top          # BGM: lab_mhz = pixel_mhz = 25
    assert "        .clk(clk_pixel)," in top               # design_top
    assert "(.clk (clk), .rst (rst_on_power_up))" not in top    # nothing left on the board clock
    lab = codegen.lab_clock(r)
    assert lab == {"net": "clk_pixel", "mhz": 25.125, "name": "pixel"}
    assert codegen.pll_source_files(top) == [os.path.join("rtl", "pll", "pll_ice40.sv")]


def test_power_up_reset_follows_the_lab_clock():
    r = _resolve("icebreaker_dvi_12b_tm1638_yosys")     # BGM: imitate_reset_on_power_up on clk = pixel_clk
    r["configuration"]["lab_clock"] = "pixel"
    top = codegen.emit_top_sv(r, strict=True)
    assert "imitate_reset_on_power_up" in top
    assert "(.clk (clk_pixel), .rst (rst_on_power_up))" in top


def test_unknown_lab_clock_is_rejected_by_validation():
    r = _resolve("icebreaker_dvi_12b_no_tm1638_yosys")
    r["configuration"]["lab_clock"] = "serial"
    problems = codegen.validate_configuration(r)
    assert any("lab_clock 'serial'" in p for p in problems)


# ---------------------------------------------------------------------------
# `$param` values that are refs (LCD backlight / init wiring choices)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("value,expected", [
    ("const.0", "assign onboard_lcd_bl = 1'b0;"),
    ("const.1", "assign onboard_lcd_bl = 1'b1;"),
    ("context.rst_n", "assign onboard_lcd_bl = rst_n;"),
])
def test_ref_valued_params_drive_pins(value, expected):
    r = _resolve("tang_nano_9k_lcd_480_272_no_tm1638")
    _attach(r, "lcd_480_272").setdefault("params", {})["bl"] = value
    top = codegen.emit_top_sv(r, strict=True)
    assert expected in top


# ---------------------------------------------------------------------------
# Unsupported families fail loudly instead of silently running on context.clk
# ---------------------------------------------------------------------------

def test_gw5_pll_wrapper_for_arora_v():
    """Tang Mega 138K Pro (GW5AST): primitive PLL; Tang Primer 25K (GW5A):
    PLLA with the serial and pixel clocks as two outputs of one VCO."""
    r = _resolve("tang_mega_138k_pro_lcd_480_272_no_tm1638")
    tree = codegen.plan_clock_tree(r)
    assert [(n, v) for n, _r, v, _s in tree] == [("pixel", "gowin_gw5")]
    top = codegen.emit_top_sv(r, strict=True)
    assert 'pll_gowin_gw5 # (.PRIMITIVE("PLL"), .FCLKIN("50")' in top
    assert ".clkout0(clk_pixel)" in top
    r = _resolve("tang_primer_25k_pmod_hdmi")
    kinds = {n: v for n, _r, v, _s in codegen.plan_clock_tree(r)}
    assert kinds == {"serial": "gowin_gw5", "pixel": "gowin_gw5"}
    top = codegen.emit_top_sv(r, strict=True)
    assert '.PRIMITIVE("PLLA")' in top and ".clkout0(clk_serial), .clkout1(clk_pixel)" in top
    assert '.CLKOUT1_EN("TRUE")' in top


def test_gw5_solver_matches_bgm_ipc():
    from tools import pll_solver
    sol = pll_solver.gowin_gw5_pll(50, [8])        # BGM gowin_pll.ipc: Clkout0ExpectedFrequency=8
    assert sol is not None and sol.f_outs == (8.0,) and 800.0 <= sol.f_vco <= 1600.0
    assert pll_solver.gowin_gw5_pll(50, [250, 25]).odivs == (4, 40)


def test_ecp5_hdmi_serial_pll_and_pixel_alias():
    """BGM colorlight75b_tm1638_ecp5_yosys: clock.v EHXPLLL 25 -> 250 MHz for
    the TMDS serializer, the 25 MHz pixel clock is the board clock itself."""
    r = _resolve("colorlight75b_tm1638_ecp5_yosys")
    tree = codegen.plan_clock_tree(r)
    assert [(n, v, round(s.f_out, 3)) for n, _r, v, s in tree] == [("serial", "ecp5", 250.0), ("pixel", "alias", 25.0)]
    top = codegen.emit_top_sv(r, strict=True)
    assert "pll_ecp5 # (.CLKI_DIV(1), .CLKFB_DIV(5), .CLKOP_DIV(4), .CLKOS_DIV(2)) i_pll_serial" in top
    assert "wire clk_pixel = clk;" in top
    assert 'hdmi_tmds_out # (.DIFF_BUF("generic"))' in top          # pseudo-differential pairs, as BGM's hdmi.v
    assert codegen.pll_source_files(top) == [os.path.join("rtl", "pll", "pll_ecp5.sv")]
