"""
tools/pll_solver.py against known-good per-board Gowin rPLL settings and the
iCEBreaker SB_PLL40 constants. The solver does not have to reproduce the exact
divider choice (several settings give the same frequency), but it must (a)
land on the same output frequency, (b) respect the PFD/VCO limits, and (c) read
the known settings back into their annotated frequency.
"""

import pytest

from tools import pll_solver as ps


# (variant, f_in, IDIV_SEL, FBDIV_SEL, ODIV_SEL, DYN_SDIV_SEL, uses_clkoutd, annotated MHz)
RPLL_VECTORS = [
    ("tang_nano_9k_lcd_480_272_no_tm1638",        27, 2, 0,  48, 2, False, 9.0),
    ("tang_nano_20k_lcd_480_272_no_tm1638",       27, 2, 0,  64, 2, False, 9.0),
    ("tang_nano_20k_lcd_800_480_no_tm1638",       27, 8, 10, 16, 2, False, 33.0),
    ("tang_primer_20k_dock_lcd_800_480_no_tm1638", 27, 8, 10, 16, 2, False, 33.0),
    ("tang_nano_9k_hdmi_no_ip_tm1638",            27, 2, 13, 4,  2, False, 126.0),
    ("tang_primer_20k_dock_hdmi_no_tm1638",       27, 2, 13, 4,  2, False, 126.0),
    ("tang_nano_20k_hdmi_no_tm1638",              27, 7, 36, 8,  2, False, 124.875),
    ("tang_nano_9k_lcd_480_272_no_tm1638_yosys",  27, 4, 23, 4,  4, True,  32.4),
    ("tang_nano_20k_lcd_800_480_tm1638_alt",      27, 1, 28, 2,  8, True,  48.9375),
]


@pytest.mark.parametrize("variant,f_in,idiv,fbdiv,odiv,sdiv,use_d,mhz", RPLL_VECTORS)
def test_rpll_settings_read_back(variant, f_in, idiv, fbdiv, odiv, sdiv, use_d, mhz):
    f = ps.gowin_rpll_frequency(f_in, idiv, fbdiv, odiv, sdiv, use_d)
    assert abs(f - mhz) < 1e-6, variant
    # and the settings are inside the limits the solver enforces
    f_pfd = f_in / (idiv + 1)
    f_vco = f_in / (idiv + 1) * (fbdiv + 1) * odiv
    assert 3.0 <= f_pfd <= 400.0, variant
    assert 400.0 <= f_vco <= 1200.0, variant


@pytest.mark.parametrize("variant,f_in,idiv,fbdiv,odiv,sdiv,use_d,mhz", RPLL_VECTORS)
def test_solver_reaches_rpll_frequencies(variant, f_in, idiv, fbdiv, odiv, sdiv, use_d, mhz):
    sol = ps.gowin_rpll(f_in, mhz)
    assert sol is not None, variant
    assert sol.error < 1e-6, (variant, sol)
    assert 3.0 <= sol.f_pfd <= 400.0 and 400.0 <= sol.f_vco <= 1200.0


def test_solver_prefers_direct_clkout_and_low_vco():
    sol = ps.gowin_rpll(27, 9)
    assert sol.use_clkoutd is False
    assert sol.f_out == 9.0
    assert sol.f_vco <= 1200.0 and sol.f_vco >= 400.0


def test_solver_rejects_impossible():
    assert ps.gowin_rpll(27, 2000.0) is None       # CLKOUT tops out at VCO_max / ODIV_min = 600 MHz
    assert ps.ice40_pll(12, 700.0) is None         # VCO_max / 2^1 = 533 MHz
    # 1 MHz is reachable through CLKOUTD (128 MHz / 128) and must be reported as such
    low = ps.gowin_rpll(27, 1.0)
    assert low is not None and low.use_clkoutd


def test_ice40_matches_icebreaker_dvi():
    # icebreaker: SB_PLL40_PAD DIVR 0, DIVF 66, DIVQ 5, FILTER_RANGE 1: 12 MHz -> 25.125 MHz
    sol = ps.ice40_pll(12, 25.125)
    assert sol is not None
    assert (sol.divr, sol.divf, sol.divq, sol.filter_range) == (0, 66, 5, 1)
    assert abs(sol.f_out - 25.125) < 1e-9


def test_ecp5_pll_matches_colorlight_clock():
    """colorlight75b_tm1638_ecp5_yosys: EHXPLLL CLKI_DIV 1,
    CLKFB_DIV 5, CLKOP_DIV 4 (125 MHz feedback), CLKOS_DIV 2 -> 250 MHz."""
    from tools import pll_solver
    sol = pll_solver.ecp5_pll(25, 250)
    assert (sol.clki_div, sol.clkfb_div, sol.clkop_div, sol.clkos_div) == (1, 5, 4, 2)
    assert sol.f_vco == 500.0 and sol.f_out == 250.0
    assert pll_solver.ecp5_pll(25, 3000) is None
