#!/usr/bin/env python3
"""
PLL parameter solver.

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

Known-good per-board rPLL settings are the regression vectors (tests/test_pll_solver.py).
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
    highest VCO inside the family window (GW1N: 400-1200 MHz, GW2A: 500-1250
    MHz as Gowin EDA enforces; a low VCO is where the tool's checks bite),
    then by the smallest IDIV."""
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
                    key = (round(err, 9), 1 if use_d else 0, -round(f_vco, 6), idiv, fbdiv, odiv, sdiv)
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


class Ecp5PLL(object):
    """One EHXPLLL setting: feedback from CLKOP, the requested clock on CLKOS."""

    def __init__(self, clki_div, clkfb_div, clkop_div, clkos_div, f_pfd, f_vco, f_out, error_pct):
        self.clki_div, self.clkfb_div, self.clkop_div, self.clkos_div = clki_div, clkfb_div, clkop_div, clkos_div
        self.f_pfd, self.f_vco, self.f_out, self.error_pct = f_pfd, f_vco, f_out, error_pct

    def __repr__(self):
        return "Ecp5PLL(CLKI_DIV={}, CLKFB_DIV={}, CLKOP_DIV={}, CLKOS_DIV={}, vco={:.1f}, out={:.4f})".format(
            self.clki_div, self.clkfb_div, self.clkop_div, self.clkos_div, self.f_vco, self.f_out)


ECP5_VCO_MIN, ECP5_VCO_MAX = 400.0, 800.0
ECP5_PFD_MIN, ECP5_PFD_MAX = 10.0, 400.0


def ecp5_pll(f_in, f_out, tolerance_pct=0.5):
    """Best EHXPLLL setting for f_out from f_in, or None. Feedback path CLKOP:
    f_vco = f_in / CLKI_DIV * CLKFB_DIV * CLKOP_DIV (400..800 MHz), the output
    is CLKOS = f_vco / CLKOS_DIV. Ties: smallest CLKI_DIV, then the CLKOP
    (feedback) clock closest to half the output — the colorlight clock:
    25 -> 250 MHz with CLKI 1, CLKFB 5, CLKOP 4 (125 MHz), CLKOS 2."""
    best = None
    for clki in range(1, 129):
        f_pfd = f_in / clki
        if not (ECP5_PFD_MIN <= f_pfd <= ECP5_PFD_MAX):
            continue
        for clkfb in range(1, 81):
            for clkop in range(1, 129):
                f_vco = f_pfd * clkfb * clkop
                if f_vco < ECP5_VCO_MIN:
                    continue
                if f_vco > ECP5_VCO_MAX:
                    break
                clkos = int(round(f_vco / f_out))
                if not (1 <= clkos <= 128):
                    continue
                f = f_vco / clkos
                err = abs(f - f_out) / f_out * 100.0
                if err > tolerance_pct:
                    continue
                key = (round(err, 9), clki, round(abs(f_vco / clkop - f_out / 2.0), 6), clkfb)
                if best is None or key < best[0]:
                    best = (key, Ecp5PLL(clki, clkfb, clkop, clkos, f_pfd, f_vco, f, err))
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
    to read existing gowin_rpll.v settings back into frequencies."""
    f = f_in / (idiv + 1) * (fbdiv + 1)
    return f / sdiv if use_clkoutd and sdiv else f


if __name__ == "__main__":
    import sys
    f_in, f_out = float(sys.argv[1]), float(sys.argv[2])
    print("gowin rPLL:", gowin_rpll(f_in, f_out))
    print("iCE40:", ice40_pll(f_in, f_out))


# ---------------------------------------------------------------------------
# Xilinx 7-series MMCM (MMCME2_BASE), several outputs from one VCO
# ---------------------------------------------------------------------------

MMCM_PFD_MIN, MMCM_PFD_MAX = 10.0, 450.0
MMCM_VCO_MIN, MMCM_VCO_MAX = 600.0, 1200.0     # -1 speed grade (the tightest)
MMCM_DIVCLK_MAX = 106
MMCM_MULT_MIN, MMCM_MULT_MAX = 2, 64
MMCM_ODIV_MAX = 128

XilinxMMCM = namedtuple("XilinxMMCM", "divclk mult odivs f_pfd f_vco f_outs errors")


def xilinx_mmcm(f_in, f_outs, tolerance_pct=0.5):
    """Integer DIVCLK_DIVIDE / CLKFBOUT_MULT_F and one integer CLKOUTn_DIVIDE
    per requested output (up to 3), all from one VCO. Prefers exact outputs,
    then the highest VCO (lowest jitter), then the smallest divider set.
    Returns XilinxMMCM or None."""
    if not f_outs or len(f_outs) > 3:
        return None
    best = None
    for divclk in range(1, MMCM_DIVCLK_MAX + 1):
        f_pfd = f_in / divclk
        if f_pfd < MMCM_PFD_MIN:
            break
        if f_pfd > MMCM_PFD_MAX:
            continue
        for mult in range(MMCM_MULT_MIN, MMCM_MULT_MAX + 1):
            f_vco = f_pfd * mult
            if f_vco < MMCM_VCO_MIN:
                continue
            if f_vco > MMCM_VCO_MAX:
                break
            odivs, outs, errs = [], [], []
            ok = True
            for f_out in f_outs:
                odiv = int(round(f_vco / f_out))
                if odiv < 1 or odiv > MMCM_ODIV_MAX:
                    ok = False
                    break
                f = f_vco / odiv
                err = abs(f - f_out) / f_out * 100.0
                if err > tolerance_pct:
                    ok = False
                    break
                odivs.append(odiv)
                outs.append(f)
                errs.append(err)
            if not ok:
                continue
            key = (round(max(errs), 9), -round(f_vco, 6), divclk + mult)
            if best is None or key < best[0]:
                best = (key, XilinxMMCM(divclk, mult, tuple(odivs), f_pfd, f_vco, tuple(outs), tuple(errs)))
    return best[1] if best else None


# ---------------------------------------------------------------------------
# Gowin Arora V (GW5A / GW5AST) PLL / PLLA, several outputs from one VCO
#   f_pfd = f_in / IDIV_SEL; f_vco = f_pfd * FBDIV_SEL * MDIV_SEL; out_i = f_vco / ODIVi_SEL
#   (checked against a Gowin IP-generated gowin_pll.ipc: 50 MHz, IDIV 1, FBDIV 1, MDIV 16, ODIV0 100 -> 8 MHz)
# ---------------------------------------------------------------------------

GW5_PFD_MIN, GW5_PFD_MAX = 19.0, 400.0     # Gowin EDA (PA2078): PFD 19 .. 400 MHz on the GW5A PLLA
GW5_VCO_MIN, GW5_VCO_MAX = 800.0, 1600.0
GW5_IDIV_MAX, GW5_FBDIV_MAX, GW5_MDIV_MAX, GW5_ODIV_MAX = 64, 64, 128, 128

GowinGW5PLL = namedtuple("GowinGW5PLL", "idiv fbdiv mdiv odivs f_pfd f_vco f_outs errors")


def gowin_gw5_pll(f_in, f_outs, tolerance_pct=0.5):
    """Integer IDIV / FBDIV / MDIV and one ODIV per requested output (up to
    3). Prefers exact outputs, then the highest PFD (Gowin's IP keeps IDIV 1),
    then the smallest divider set. Returns GowinGW5PLL or None."""
    if not f_outs or len(f_outs) > 3:
        return None
    best = None
    for idiv in range(1, GW5_IDIV_MAX + 1):
        f_pfd = f_in / idiv
        if f_pfd < GW5_PFD_MIN:
            break
        if f_pfd > GW5_PFD_MAX:
            continue
        for fbdiv in range(1, GW5_FBDIV_MAX + 1):
            for mdiv in range(2, GW5_MDIV_MAX + 1):
                f_vco = f_pfd * fbdiv * mdiv
                if f_vco < GW5_VCO_MIN:
                    continue
                if f_vco > GW5_VCO_MAX:
                    break
                odivs, outs, errs = [], [], []
                ok = True
                for f_out in f_outs:
                    odiv = int(round(f_vco / f_out))
                    if odiv < 1 or odiv > GW5_ODIV_MAX:
                        ok = False
                        break
                    f = f_vco / odiv
                    err = abs(f - f_out) / f_out * 100.0
                    if err > tolerance_pct:
                        ok = False
                        break
                    odivs.append(odiv)
                    outs.append(f)
                    errs.append(err)
                if not ok:
                    continue
                key = (round(max(errs), 9), idiv, fbdiv + mdiv)
                if best is None or key < best[0]:
                    best = (key, GowinGW5PLL(idiv, fbdiv, mdiv, tuple(odivs), f_pfd, round(f_vco, 6),
                                             tuple(outs), tuple(errs)))
    return best[1] if best else None
