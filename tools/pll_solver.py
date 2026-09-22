#!/usr/bin/env python3
"""
PLL parameter solver (PLAN.md P3.1, decision D8).

Peripherals declare the clocks they need (`clocks: [{name: pixel, mhz: 9}]`);
codegen instantiates one generic per-vendor PLL wrapper per distinct frequency
and needs the primitive's divider settings. This module computes them from
(f_in, f_out) under the vendor's PFD/VCO limits, deterministically, preferring
the exact frequency and then the lowest VCO.

Supported primitives:

  Gowin rPLL (GW1N / GW1NR / GW1NS / GW1NSR / GW2A / GW2AR)
      f_pfd  = f_in / (IDIV_SEL + 1)              3 .. 400 MHz
      f_vco  = f_pfd * (FBDIV_SEL + 1) * ODIV_SEL 400 .. 1200 MHz (GW1N-1: 400 .. 900)
      CLKOUT = f_pfd * (FBDIV_SEL + 1)
      CLKOUTD = CLKOUT / DYN_SDIV_SEL          (SDIV even, 2 .. 128)
      IDIV_SEL, FBDIV_SEL: 0 .. 63; ODIV_SEL in {2,4,8,16,32,48,64,80,96,112,128}

  Lattice iCE40 SB_PLL40_* (SIMPLE feedback)
      f_pfd = f_in / (DIVR + 1)                   10 .. 133 MHz
      f_vco = f_pfd * (DIVF + 1)                  533 .. 1066 MHz
      f_out = f_vco / 2^DIVQ                      DIVQ 1 .. 6
      FILTER_RANGE from f_pfd

BGM's per-board gowin_rpll.v files are the regression vectors (tests/test_pll_solver.py).
"""

from collections import namedtuple

GowinRPLL = namedtuple("GowinRPLL", "idiv fbdiv odiv sdiv use_clkoutd f_pfd f_vco f_clkout f_out error")
Ice40PLL = namedtuple("Ice40PLL", "divr divf divq filter_range f_pfd f_vco f_out error")

_GOWIN_ODIV = (2, 4, 8, 16, 32, 48, 64, 80, 96, 112, 128)


def gowin_rpll(f_in, f_out, tolerance_pct=0.5, vco_max=1200.0, vco_min=400.0,
               pfd_min=3.0, pfd_max=400.0, allow_clkoutd=True):
    """Best rPLL setting for f_out (MHz) from f_in (MHz), or None.

    Candidates are ranked by |error| first (exact solutions win), then by
    whether CLKOUT is used directly (preferred over CLKOUTD), then by the
    lowest VCO (least jitter and power), then by the smallest IDIV."""
    best = None
    for idiv in range(0, 64):
        f_pfd = f_in / (idiv + 1)
        if not (pfd_min <= f_pfd <= pfd_max):
            continue
        for fbdiv in range(0, 64):
            f_clkout = f_pfd * (fbdiv + 1)
            for odiv in _GOWIN_ODIV:
                f_vco = f_clkout * odiv
                if not (vco_min <= f_vco <= vco_max):
                    continue
                cands = [(f_clkout, 2, False)]
                if allow_clkoutd:
                    cands += [(f_clkout / s, s, True) for s in range(2, 129, 2)]
                for f, sdiv, use_d in cands:
                    err = abs(f - f_out) / f_out * 100.0
                    if err > tolerance_pct:
                        continue
                    key = (round(err, 9), 1 if use_d else 0, f_vco, idiv, fbdiv, odiv, sdiv)
                    if best is None or key < best[0]:
                        best = (key, GowinRPLL(idiv, fbdiv, odiv, sdiv, use_d, f_pfd, f_vco, f_clkout, f, err))
    return best[1] if best else None


def ice40_pll(f_in, f_out, tolerance_pct=0.5):
    """Best SB_PLL40 (SIMPLE feedback) setting for f_out from f_in, or None."""
    best = None
    for divr in range(0, 16):
        f_pfd = f_in / (divr + 1)
        if not (10.0 <= f_pfd <= 133.0):
            continue
        for divf in range(0, 128):
            f_vco = f_pfd * (divf + 1)
            if not (533.0 <= f_vco <= 1066.0):
                continue
            for divq in range(1, 7):
                f = f_vco / (2 ** divq)
                err = abs(f - f_out) / f_out * 100.0
                if err > tolerance_pct:
                    continue
                key = (round(err, 9), f_vco, divr, divf, divq)
                if best is None or key < best[0]:
                    best = (key, Ice40PLL(divr, divf, divq, ice40_filter_range(f_pfd), f_pfd, f_vco, f, err))
    return best[1] if best else None


def ice40_filter_range(f_pfd):
    if f_pfd < 17:
        return 1
    if f_pfd < 26:
        return 2
    if f_pfd < 44:
        return 3
    if f_pfd < 66:
        return 4
    if f_pfd < 101:
        return 5
    return 6


def gowin_rpll_frequency(f_in, idiv, fbdiv, odiv=None, sdiv=None, use_clkoutd=False):
    """CLKOUT (or CLKOUTD) frequency produced by explicit rPLL settings; used
    to read BGM's gowin_rpll.v files back into frequencies."""
    f = f_in / (idiv + 1) * (fbdiv + 1)
    return f / sdiv if use_clkoutd and sdiv else f


if __name__ == "__main__":
    import sys
    f_in, f_out = float(sys.argv[1]), float(sys.argv[2])
    print("gowin rPLL:", gowin_rpll(f_in, f_out))
    print("iCE40:", ice40_pll(f_in, f_out))
