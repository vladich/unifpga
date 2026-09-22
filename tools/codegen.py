"""
Generate `top.sv` for a resolved configuration.

The generated module:
  - declares the FPGA's external pins as top-level ports (one port per pin or
    pin-bank that any attached peripheral binds to);
  - establishes context wires (clk, rst, rst_n) from the clock/reset providers;
  - declares one bus per capability, sized by the sum of provider widths;
  - for each attached peripheral, either wires it through directly (passthrough)
    or instantiates its driver SV module with port_map + pin_assigns;
  - instantiates `design_top` at the bottom with parameter values and capability
    buses wired to its ports.

Phases:
  1. Index — load capabilities, compute per-capability widths and offsets.
  2. Plan FPGA ports — figure out which board pin banks are used by any
     peripheral's bind and expose them as module ports.
  3. Emit SV — write the top module text using the plan.

The output is a single string. Callers (synthesize.py) write it to disk.
"""

import logging
import os
import re
import sys
from collections import OrderedDict, defaultdict, namedtuple

import yaml

from config import init as config_init
from tools import pll_solver


log = logging.getLogger(__name__)
REPO = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))


class CodegenError(Exception):
    """A configuration cannot be turned into a correct top: unresolved bind,
    duplicated pin, missing clock frequency, ... The message names the
    configuration and the offending element."""


# ---------------------------------------------------------------------------
# Helpers for parsing references in YAML port_maps and configuration binds
# ---------------------------------------------------------------------------

# A configuration's bind: RHS like `onboard_switches`, `pmod_jc[2]`,
# `onboard_7seg.anodes`, `onboard_7seg.anodes[0]`.
_BANK_REF = re.compile(
    r"^\s*([A-Za-z_][A-Za-z0-9_]*)"        # bank name
    r"(?:\.([A-Za-z_][A-Za-z0-9_]*))?"     # optional .subkey
    r"(?:\[(\d+)\])?"                      # optional [index]
    r"\s*$"
)


def _parse_bank_ref(text):
    """`pmod_jc[2]` -> ('pmod_jc', None, 2). `onboard_7seg.dp` -> ('onboard_7seg', 'dp', None)."""
    m = _BANK_REF.match(text)
    if not m:
        return None
    bank, sub, idx = m.group(1), m.group(2), m.group(3)
    return (bank, sub, int(idx) if idx is not None else None)


def _bank_pin(pinmap, bank_name, subkey, index):
    """Look up a single pin (or list of pins) inside a board's pinBanks."""
    bank = pinmap.get("pinBanks", {}).get(bank_name)
    if bank is None:
        return None
    pins = bank.get("pins") if isinstance(bank, dict) else None
    if pins is None:
        return None
    if subkey is not None:
        sub = pins.get(subkey) if isinstance(pins, dict) else None
        if sub is None:
            return None
        if index is None:
            return sub
        return sub[index] if isinstance(sub, list) and index < len(sub) else None
    if index is not None:
        return pins[index] if isinstance(pins, list) and index < len(pins) else None
    return pins


def _bank_width(pinmap, bank_name, subkey=None):
    """Number of pins the given bank (or subkey) holds. None if not a list."""
    bank = pinmap.get("pinBanks", {}).get(bank_name) or {}
    pins = bank.get("pins")
    if subkey is not None and isinstance(pins, dict):
        pins = pins.get(subkey)
    if isinstance(pins, list):
        return len(pins)
    if isinstance(pins, dict):
        return None      # nested
    return 1             # scalar


# ---------------------------------------------------------------------------
# Phase 1: index capabilities
# ---------------------------------------------------------------------------

class CapabilityPlan:
    """Per-capability aggregation plan: total width and per-provider offset/width."""

    def __init__(self, cap_id, cap_def):
        self.id = cap_id
        self.cap = cap_def
        self.aggregation = cap_def.get("aggregation")
        self.providers = []         # list of (peripheral_idx, params)
        self.offsets = {}           # peripheral_idx -> bit offset (concat only)
        self.widths = {}            # peripheral_idx -> width
        self.params = {}            # final resolved capability params (e.g. width, screen_width)

    def add_provider(self, peripheral_idx, peripheral_def, params):
        self.providers.append((peripheral_idx, peripheral_def, params))


def _eval_param(spec, peripheral_params, peripheral_def=None):
    """Resolve a peripheral param spec — either a literal or `$<name>`
    referring to a peripheral-instance parameter (or its default from the
    peripheral YAML when the configuration doesn't override it)."""
    if isinstance(spec, str) and spec.startswith("$"):
        key = spec[1:]
        v = peripheral_params.get(key)
        if v is not None:
            return v
        if peripheral_def is not None:
            param_def = (peripheral_def.get("parameters") or {}).get(key) or {}
            return param_def.get("default")
        return None
    return spec


_PRIMARY_PARAM = {
    "switches":      "width",
    "buttons":       "width",
    "leds":          "width",
    "rgb_leds":      "count",
    "seven_segment": "digits",
    "gpio":          "width",
}


def build_capability_plans(resolved):
    capabilities = config_init.read_capabilities()
    plans = OrderedDict((cid, CapabilityPlan(cid, cdef)) for cid, cdef in capabilities.items())

    for idx, attach in enumerate(resolved["peripherals"]):
        perif = attach["peripheral"]
        params = attach.get("params") or {}
        for entry in perif.get("provides") or []:
            cap_id = entry["capability"]
            plan = plans[cap_id]
            cap_params_spec = entry.get("params") or {}
            cap_params_resolved = {k: _eval_param(v, params, perif) for k, v in cap_params_spec.items()}
            plan.add_provider(idx, perif, cap_params_resolved)

    # Compute widths and offsets per aggregation rule.
    for plan in plans.values():
        if not plan.providers:
            continue
        if plan.aggregation == "exclusive":
            if len(plan.providers) > 1:
                ids = ", ".join(p[1]["id"] for p in plan.providers)
                log.warning(
                    "Capability '%s' is exclusive but %d peripherals provide it (%s) — "
                    "using the first provider; remove duplicates from the configuration "
                    "to silence this warning.",
                    plan.id, len(plan.providers), ids,
                )
                plan.providers = plan.providers[:1]
            _, _, params = plan.providers[0]
            plan.params = dict(params)
        elif plan.aggregation == "concat":
            primary = _PRIMARY_PARAM.get(plan.id, "width")
            offset = 0
            for pidx, perif, params in plan.providers:
                w = params.get(primary) or params.get("width") or params.get("count") \
                        or params.get("digits") or 1
                plan.offsets[pidx] = offset
                plan.widths[pidx] = w
                offset += w
            plan.params = {primary: offset}
        else:
            plan.params = {}

    return plans


# ---------------------------------------------------------------------------
# Clock resolution (single source of truth for clk_mhz and create_clock)
# ---------------------------------------------------------------------------

_MHZ_IN_NAME = re.compile(r"(\d+(?:_\d+)?)mhz", re.IGNORECASE)


def resolve_clock(resolved, plans=None):
    """Return the system clock as a dict, or None when no clock provider is attached:

        {"bank_ref": "clk",          # bind RHS as written in the configuration
         "port":     "clk",          # generated top-level port name
         "mhz":      100.0 or None,  # None when nothing declares a frequency
         "source":   "configuration" | "pinmap" | "bank-name" | None}

    Precedence: the attach's `frequency_mhz` param, then the pinmap bank's
    `frequency_mhz`, then a `<n>mhz` token in the bank name. Every emitter and
    the design_top parameter block must use this function so the constraint
    period and the advertised `clk_mhz` can never disagree."""
    if plans is None:
        plans = build_capability_plans(resolved)
    clk_plan = plans["clock"]
    if not clk_plan.providers:
        return None
    pidx, _perif, cap_params = clk_plan.providers[0]
    attach = resolved["peripherals"][pidx]
    pinmap = resolved["board_pinmap"]
    clk_bank = (attach.get("bind") or {}).get("clk")
    if clk_bank is None:
        # Heuristic fallback: first pin bank whose name looks like a clock.
        for bank_name in (pinmap.get("pinBanks") or {}):
            low = bank_name.lower()
            if low.startswith("clk") or "clock" in low or low.startswith("osc"):
                clk_bank = bank_name
                break
    if clk_bank is None:
        log.warning("Configuration %s: no clock bound; using literal clk port",
                    resolved["configuration"]["id"])
        clk_bank = "clk"

    mhz = (cap_params or {}).get("frequency_mhz")
    source = "configuration" if mhz is not None else None
    if mhz is None:
        parsed = _parse_bank_ref(str(clk_bank))
        bank = (pinmap.get("pinBanks") or {}).get(parsed[0]) if parsed else None
        if isinstance(bank, dict) and bank.get("frequency_mhz") is not None:
            mhz = bank["frequency_mhz"]
            source = "pinmap"
    if mhz is None:
        m = _MHZ_IN_NAME.search(str(clk_bank))
        if m:
            mhz = float(m.group(1).replace("_", "."))
            source = "bank-name"
    if mhz is None:
        log.warning("Configuration %s: clock bank %r has no frequency_mhz in the "
                    "configuration or the pinmap; clk_mhz will default to 50 and no "
                    "clock constraint will be emitted (see PLAN.md CLK-FREQ)",
                    resolved["configuration"]["id"], clk_bank)
    return {"bank_ref": clk_bank, "port": _bank_port_name(clk_bank),
            "mhz": float(mhz) if mhz is not None else None, "source": source}


def _clock_period_ns(clock):
    return 1000.0 / float(clock["mhz"])


# ---------------------------------------------------------------------------
# Clock tree: PLL-derived clocks requested by peripherals (P3.1, decision D8)
#
# A peripheral declares `clocks: [{name: pixel, mhz: 9, tolerance_pct: 0.5}]`
# and refers to it as `clock.pixel` in port_map / pin_assigns. Codegen
# instantiates one vendor PLL wrapper per distinct clock name on the board
# clock, with dividers from tools/pll_solver.py, and exposes `clk_<name>`.
# ---------------------------------------------------------------------------

def collect_clock_requirements(resolved):
    """{name: {"mhz": float, "tolerance_pct": float, "users": [attach idx],
    "from": source name or None, "divide": int or None}}.

    A source clock is `{name: pixel, mhz: 9}`; a configuration may override
    its frequency with `params.clock_<name>_mhz`. A derived clock is
    `{name: pixel, from: serial, divide: 10}` (its frequency follows the
    source's, so the override on `serial` moves both). Two peripherals may
    request the same clock only with the same definition."""
    cfg_id = resolved["configuration"]["id"]
    reqs = OrderedDict()
    for idx, attach in enumerate(resolved["peripherals"]):
        for c in attach["peripheral"].get("clocks") or []:
            name = c["name"]
            tol = float(c.get("tolerance_pct", 0.5))
            if "from" in c:
                entry = {"mhz": None, "from": c["from"], "divide": int(c["divide"])}
            else:
                mhz = float(c["mhz"])
                override = (attach.get("params") or {}).get("clock_{}_mhz".format(name))
                if override is not None:
                    mhz = float(override)
                entry = {"mhz": mhz, "from": None, "divide": None}
            have = reqs.get(name)
            if have is not None:
                same = (have["from"] == entry["from"] and have["divide"] == entry["divide"]
                        and (have["mhz"] is None) == (entry["mhz"] is None)
                        and (have["mhz"] is None or abs(have["mhz"] - entry["mhz"]) < 1e-6))
                if not same:
                    raise CodegenError("Configuration {}: clock '{}' is defined differently by {} ({}) and {} ({})"
                                       .format(cfg_id, name, have["users"], have, attach["peripheral_id"], entry))
                have["users"].append(idx)
                continue
            entry["tolerance_pct"] = tol
            entry["users"] = [idx]
            reqs[name] = entry
    for name, r in reqs.items():
        if r["from"] is not None:
            src = reqs.get(r["from"])
            if src is None or src["from"] is not None:
                raise CodegenError("Configuration {}: clock '{}' is derived from '{}', which is not a source clock"
                                   .format(cfg_id, name, r["from"]))
            if r["divide"] < 1:
                raise CodegenError("Configuration {}: clock '{}' has divide {}".format(cfg_id, name, r["divide"]))
            r["mhz"] = src["mhz"] / r["divide"]
    return reqs


# Solutions for the non-PLL entries of the clock tree; all expose `f_out` like
# the solver results so callers can treat every entry alike.
ClockAlias   = namedtuple("ClockAlias",   "f_out source")            # == the board clock
ClockDerived = namedtuple("ClockDerived", "f_out source divide")     # vendor clock divider
MmcmOutput   = namedtuple("MmcmOutput",   "f_out index mmcm")        # one CLKOUT of a shared MMCM


def _pll_vendor(board):
    """Which PLL wrapper the board's device family takes, or None."""
    producer = (board.get("PartProducer") or "").lower()
    family = (board.get("PartFamily") or "").lower()
    part = (board.get("Part") or "").upper()
    if "gowin" in producer or part.startswith("GW"):
        if part.startswith("GW5") or "gw5" in family or "arorav" in family:
            return "gowin_gw5"           # PLLA-based Gowin_PLL: not wrapped yet (P3.1b)
        return "gowin_rpll"
    if "lattice" in producer and "ice40" in family:
        return "ice40"
    if ("xilinx" in producer or "amd" in producer or part.startswith(("XC7", "XA7"))) and (
            part.startswith(("XC7", "XA7")) or "7" in family or "zynq" in family):
        return "xilinx_mmcm"
    return None


def _is_gowin_littlebee(board):
    part = (board.get("Part") or "").upper()
    family = (board.get("PartFamily") or "").lower()
    return part.startswith("GW1N") or "littlebee" in family or "gw1n" in family


def diff_buf_kind(resolved):
    """Which differential output buffer rtl/io/diff_obuf.sv should use on this
    board (`context.diff_buf`): the pinmap's `io.diff_obuf` when set, else by
    family: ELVDS_OBUF on Gowin LittleBee (GW1N*), TLVDS_OBUF on Gowin Arora,
    OBUFDS on Xilinx, pseudo-differential (`n = ~p`) elsewhere."""
    io = resolved["board_pinmap"].get("io") or {}
    if io.get("diff_obuf"):
        return str(io["diff_obuf"])
    board = resolved["board"]
    vendor = _pll_vendor(board)
    if vendor in ("gowin_rpll", "gowin_gw5"):
        return "gowin_elvds" if _is_gowin_littlebee(board) else "gowin_tlvds"
    if vendor == "xilinx_mmcm":
        return "xilinx"
    return "generic"


def _gowin_rpll_primitive(board):
    """GW1NS / GW1NSR (Tang Nano 4K) have PLLVR instead of rPLL."""
    part = (board.get("Part") or "").upper()
    return "PLLVR" if part.startswith(("GW1NS", "GW1NSR", "GW1NSE", "GW1NSER")) else "rPLL"


def _gw5_primitive(board):
    """BGM's Gowin_PLL wrappers: primitive PLL on GW5AST / GW5AT (Tang Mega
    138K), PLLA on GW5A (Tang Primer 25K)."""
    part = (board.get("Part") or "").upper()
    return "PLLA" if part.startswith("GW5A-") else "PLL"


def _gowin_rpll_device(resolved):
    """The rPLL `DEVICE` parameter BGM uses (`GW1NR-9C`, `GW2AR-18C`): the
    `-name` plus `-device_version` from the board's Gowin set_device args.
    Under the open flow nextpnr-gowin compares the parameter with its own
    family name (`GW2A-18` for the Primer 20K, "wrong PLL device" otherwise),
    so the pinmap may pin `toolchain_options.apicula.rpll_device`."""
    tc_opts = resolved["board_pinmap"].get("toolchain_options") or {}
    if resolved["toolchain"]["Id"].startswith("nextpnr_apicula"):
        dev = (tc_opts.get("apicula") or {}).get("rpll_device")
        if dev:
            return str(dev)
    opts = tc_opts.get("gowin") or {}
    args = opts.get("set_device") or ""
    m_name = re.search(r"-name\s+(\S+)", args)
    m_ver = re.search(r'-device_version\s+("[^"]*"|\S+)', args)
    if m_name:
        name = m_name.group(1)
        ver = (m_ver.group(1).strip('"') if m_ver else "")
        # BGM: `-name GW2A-18C -device_version C` -> DEVICE "GW2A-18C" (the
        # name already carries the revision); `-name GW1NR-9 -device_version C`
        # -> "GW1NR-9C".
        return name if not ver or name.endswith(ver) else name + ver
    part = (resolved["board"].get("Part") or "").upper()
    m = re.match(r"^(GW\d[A-Z]*)-[A-Z]*(\d+)", part)
    return "{}-{}C".format(m.group(1), m.group(2)) if m else "GW1NR-9C"


_GOWIN_CLKDIV = {2, 4, 5, 8, 10}          # rtl/pll/clkdiv_gowin.sv; 8 needs Arora


def plan_clock_tree(resolved, plans=None):
    """Resolve every requested clock. Returns [(name, req, kind, solution)]
    where kind is the PLL wrapper ("gowin_rpll", "ice40", "xilinx_mmcm"),
    "derived" (vendor clock divider on another clock), or "alias" (the clock
    equals the board clock, so it is the board clock: BGM's Tang Nano 4K
    feeds the DVI pixel clock straight from the 27 MHz oscillator). Every
    solution has `f_out`. Raises CodegenError for an unknown board clock, an
    unsupported family or divider, or an unreachable frequency."""
    reqs = collect_clock_requirements(resolved)
    if not reqs:
        return []
    cfg_id = resolved["configuration"]["id"]
    board = resolved["board"]
    clock = resolve_clock(resolved, plans)
    if clock is None or clock["mhz"] is None:
        raise CodegenError("Configuration {}: peripherals need PLL clocks ({}) but the board clock "
                           "frequency is unknown".format(cfg_id, ", ".join(reqs)))
    f_in = clock["mhz"]
    vendor = _pll_vendor(board)

    def same(a, b):
        return abs(a - b) < 1e-6

    sources = [(n, r) for n, r in reqs.items() if r["from"] is None and not same(r["mhz"], f_in)]
    if sources and vendor is None:
        raise CodegenError("Configuration {}: clock '{}' ({} MHz) needs a PLL but no wrapper exists for {} / {} "
                           "(PLAN.md P3.1)".format(cfg_id, sources[0][0], sources[0][1]["mhz"],
                                                   board.get("PartProducer"), board.get("PartFamily")))
    out = OrderedDict()
    # 1. clocks that already exist: the board clock itself
    for name, r in reqs.items():
        if r["from"] is None and same(r["mhz"], f_in):
            out[name] = (name, r, "alias", ClockAlias(f_in, "clk"))

    # 2. PLL-generated source clocks
    if vendor in ("xilinx_mmcm", "gowin_gw5") and sources:
        # one MMCM / Arora V PLL makes every clock, derived ones included
        # (clk_wiz / Gowin_PLL style)
        wanted = [(n, r) for n, r in reqs.items() if n not in out]
        if len(wanted) > 3:
            raise CodegenError("Configuration {}: {} PLL clocks requested, the {} wrapper has 3 outputs"
                               .format(cfg_id, len(wanted), vendor))
        tol = min(r["tolerance_pct"] for _n, r in wanted)
        solve = pll_solver.xilinx_mmcm if vendor == "xilinx_mmcm" else pll_solver.gowin_gw5_pll
        multi = solve(f_in, [r["mhz"] for _n, r in wanted], tol)
        if multi is None:
            raise CodegenError("Configuration {}: no {} setting reaches {} MHz from {} MHz within {}%"
                               .format(cfg_id, vendor, [r["mhz"] for _n, r in wanted], f_in, tol))
        for i, (n, r) in enumerate(wanted):
            out[n] = (n, r, vendor, MmcmOutput(multi.f_outs[i], i, multi))
    else:
        for name, r in sources:
            if vendor == "gowin_rpll":
                # Gowin EDA: "suitable VCO range 500 MHz to 1250 MHz" on GW2AR-18C;
                # BGM's GW1NR-9C settings sit as low as 432 MHz.
                vco = (400.0, 1200.0) if _is_gowin_littlebee(board) else (500.0, 1250.0)
                sol = pll_solver.gowin_rpll(f_in, r["mhz"], r["tolerance_pct"], vco_max=vco[1], vco_min=vco[0])
            else:   # ice40
                sol = pll_solver.ice40_pll(f_in, r["mhz"], r["tolerance_pct"])
            if sol is None:
                raise CodegenError("Configuration {}: no {} PLL setting reaches {} MHz from {} MHz within {}%"
                                   .format(cfg_id, vendor, r["mhz"], f_in, r["tolerance_pct"]))
            out[name] = (name, r, vendor, sol)

    # 3. derived clocks through a vendor divider
    for name, r in reqs.items():
        if name in out or r["from"] is None:
            continue
        if same(r["mhz"], f_in):
            out[name] = (name, r, "alias", ClockAlias(f_in, "clk"))
            continue
        src_kind = out[r["from"]][2]
        if src_kind == "alias":
            raise CodegenError("Configuration {}: clock '{}' would divide the board clock by {}; no fabric "
                               "divider is generated (declare it as a source clock instead)"
                               .format(cfg_id, name, r["divide"]))
        if src_kind in ("xilinx_mmcm", "gowin_gw5"):
            continue                        # already an output of the shared PLL
        if vendor == "gowin_rpll":
            if r["divide"] not in _GOWIN_CLKDIV or (r["divide"] == 8 and _is_gowin_littlebee(board)):
                raise CodegenError("Configuration {}: clock '{}' = {} / {} has no CLKDIV/CLKDIV2 combination "
                                   "on this Gowin family".format(cfg_id, name, r["from"], r["divide"]))
            out[name] = (name, r, "derived", ClockDerived(r["mhz"], r["from"], r["divide"]))
        else:
            raise CodegenError("Configuration {}: clock '{}' derived from '{}' needs a clock divider; none is "
                               "generated for {} (PLAN.md P3.2)".format(cfg_id, name, r["from"], vendor))
    return [out[n] for n in reqs]


def lab_clock(resolved, plans=None):
    """The clock the lab (design_top, resets, tm1638, ...) runs on. Default:
    the board oscillator (`clk`). A configuration whose BGM twin runs the lab
    on a PLL clock (`localparam lab_mhz = pixel_mhz; assign clk = pixel_clk`
    on the iCEBreaker DVI and Tang Primer 20K Dock LCD/HDMI variants) says
    `lab_clock: pixel` and the whole lab moves to `clk_pixel`.
    Returns {"net", "mhz", "name"} (name None for the board clock)."""
    clock = resolve_clock(resolved, plans)
    name = resolved["configuration"].get("lab_clock")
    if not name:
        return {"net": "clk", "mhz": clock["mhz"] if clock else None, "name": None}
    reqs = collect_clock_requirements(resolved)
    if name not in reqs:
        raise CodegenError("Configuration {}: lab_clock '{}' is not a clock any attached peripheral "
                           "declares (have: {})".format(resolved["configuration"]["id"], name,
                                                        ", ".join(reqs) or "none"))
    return {"net": "clk_" + name, "mhz": reqs[name]["mhz"], "name": name}


# Emission state for the top being generated (codegen is single-threaded):
# the lab clock net `context.clk` resolves to, and the differential buffer
# kind `context.diff_buf` renders as.
_EMIT = {"lab_clk": "clk", "diff_buf": "generic"}


def _emit_clock_tree(resolved, plans, clock, strict=True):
    try:
        tree = plan_clock_tree(resolved, plans)
    except CodegenError as exc:
        if strict:
            raise
        # Non-strict output (audits, lint of refused configurations): keep the
        # module elaborable, say loudly what is missing. synthesize.py never
        # takes this path.
        lines = ["    // ---- Clock tree: NOT GENERATED ({}) ----".format(str(exc).split(": ", 1)[-1])]
        for name in collect_clock_requirements(resolved):
            lines.append("    wire clk_{n} = clk;            // TODO PLL; placeholder, wrong frequency".format(n=name))
            lines.append("    wire clk_{n}_locked = 1'b1;".format(n=name))
        return lines
    if not tree:
        return []
    lab = lab_clock(resolved, plans)
    lines = ["    // ---- Clock tree: PLL-derived clocks requested by peripherals ----"]
    fin = clock["mhz"]
    fin_str = str(int(fin)) if float(fin).is_integer() else "{:g}".format(fin)
    mmcm_done = False
    for name, r, vendor, sol in tree:
        net = "clk_" + name
        if vendor == "alias":
            lines.append("    wire {n} = clk;               // {f:g} MHz: the board clock itself".format(n=net, f=sol.f_out))
            lines.append("    wire {n}_locked = 1'b1;".format(n=net))
            continue
        if vendor == "derived":
            src = "clk_" + sol.source
            lines.append("    wire {n};".format(n=net))
            lines.append("    // {n}: {f:.4f} MHz = {s} / {d} (Gowin CLKDIV{d2})".format(
                n=net, f=sol.f_out, s=src, d=sol.divide, d2="2 + CLKDIV 5" if sol.divide == 10 else ""))
            lines.append("    clkdiv_gowin # (.DIV({d})) i_div_{name} (.clk_in({s}), .resetn({s}_locked), .clk_out({n}));"
                         .format(d=sol.divide, name=name, s=src, n=net))
            lines.append("    wire {n}_locked = {s}_locked;".format(n=net, s=src))
            continue
        if vendor == "gowin_gw5":
            if not mmcm_done:
                m = sol.mmcm
                outs = [(n2, s2) for n2, _r2, v2, s2 in tree if v2 == "gowin_gw5"]
                lines.append("    wire clk_pll_locked;")
                lines.append("    wire " + ", ".join("clk_" + n2 for n2, _s2 in outs) + ";")
                lines.append("    // Arora V PLL: {fin} MHz / {i} * {f} * {md} = VCO {vco:.1f} MHz; ".format(
                    fin=fin_str, i=m.idiv, f=m.fbdiv, md=m.mdiv, vco=m.f_vco) + "; ".join(
                    "clk_{} = VCO / {} = {:.4f} MHz".format(n2, m.odivs[s2.index], s2.f_out) for n2, s2 in outs))
                divs = list(m.odivs) + [8] * (3 - len(m.odivs))
                ports = ["clk_" + n2 for n2, _s2 in outs] + [""] * (3 - len(outs))
                lines.append('    pll_gowin_gw5 # (.PRIMITIVE("{prim}"), .FCLKIN("{fin}"), .IDIV_SEL({i}), .FBDIV_SEL({f}), '
                             '.MDIV_SEL({md}), .ODIV0_SEL({o0}), .ODIV1_SEL({o1}), .ODIV2_SEL({o2}), '
                             '.CLKOUT1_EN("{e1}"), .CLKOUT2_EN("{e2}")) i_pll '
                             '(.clkin(clk), .clkout0({p0}), .clkout1({p1}), .clkout2({p2}), .lock(clk_pll_locked));'.format(
                                 prim=_gw5_primitive(resolved["board"]), fin=fin_str, i=m.idiv, f=m.fbdiv, md=m.mdiv,
                                 o0=divs[0], o1=divs[1], o2=divs[2],
                                 e1="TRUE" if len(outs) > 1 else "FALSE", e2="TRUE" if len(outs) > 2 else "FALSE",
                                 p0=ports[0], p1=ports[1], p2=ports[2]))
                for n2, _s2 in outs:
                    lines.append("    wire clk_{}_locked = clk_pll_locked;".format(n2))
                mmcm_done = True
            continue
        if vendor == "xilinx_mmcm":
            if not mmcm_done:
                m = sol.mmcm
                outs = [(n2, s2) for n2, _r2, v2, s2 in tree if v2 == "xilinx_mmcm"]
                lines.append("    wire clk_mmcm_locked;")
                lines.append("    wire " + ", ".join("clk_" + n2 for n2, _s2 in outs) + ";")
                lines.append("    // MMCM: {fin} MHz / {d} * {mult} = VCO {vco:.1f} MHz; ".format(
                    fin=fin_str, d=m.divclk, mult=m.mult, vco=m.f_vco) + "; ".join(
                    "clk_{} = VCO / {} = {:.4f} MHz".format(n2, m.odivs[s2.index], s2.f_out) for n2, s2 in outs))
                divs = list(m.odivs) + [1] * (3 - len(m.odivs))
                ports = ["clk_" + n2 for n2, _s2 in outs] + [""] * (3 - len(outs))
                lines.append("    pll_xilinx_mmcm # (.CLKIN_PERIOD({per:.3f}), .DIVCLK_DIVIDE({d}), .CLKFBOUT_MULT_F({mult}.0), "
                             ".CLKOUT0_DIVIDE({o0}.0), .CLKOUT1_DIVIDE({o1}), .CLKOUT2_DIVIDE({o2})) i_mmcm "
                             "(.clkin(clk), .clkout0({p0}), .clkout1({p1}), .clkout2({p2}), .lock(clk_mmcm_locked));".format(
                                 per=1000.0 / fin, d=m.divclk, mult=m.mult, o0=divs[0], o1=divs[1], o2=divs[2],
                                 p0=ports[0], p1=ports[1], p2=ports[2]))
                for n2, _s2 in outs:
                    lines.append("    wire clk_{}_locked = clk_mmcm_locked;".format(n2))
                mmcm_done = True
            continue
        lines.append("    wire {n}, {n}_locked;".format(n=net))
        if vendor == "gowin_rpll":
            lines.append("    // {}: {:.4f} MHz from {} MHz (PFD {:.3f} MHz, VCO {:.1f} MHz{})".format(
                net, sol.f_out, fin_str, sol.f_pfd, sol.f_vco, ", via CLKOUTD" if sol.use_clkoutd else ""))
            lines.append('    pll_gowin_rpll # (.PRIMITIVE("{prim}"), .FCLKIN("{fin}"), .IDIV_SEL({i}), .FBDIV_SEL({f}), '
                         '.ODIV_SEL({o}), .DYN_SDIV_SEL({s}), .USE_CLKOUTD(1\'b{d}), .DEVICE("{dev}")) i_pll_{name} '
                         '(.clkin(clk), .clkout({net}), .lock({net}_locked));'.format(
                             prim=_gowin_rpll_primitive(resolved["board"]),
                             fin=fin_str, i=sol.idiv, f=sol.fbdiv, o=sol.odiv, s=sol.sdiv,
                             d=1 if sol.use_clkoutd else 0, dev=_gowin_rpll_device(resolved),
                             name=name, net=net))
        elif vendor == "ice40":
            lines.append("    // {}: {:.4f} MHz from {} MHz (PFD {:.3f} MHz, VCO {:.1f} MHz)".format(
                net, sol.f_out, fin_str, sol.f_pfd, sol.f_vco))
            # SB_PLL40_PAD (BGM's choice) takes the clock pad itself, which then
            # cannot feed the fabric: only when the lab moved onto this PLL.
            use_pad = 1 if lab["name"] == name else 0
            lines.append("    pll_ice40 # (.DIVR(4'd{r}), .DIVF(7'd{f}), .DIVQ(3'd{q}), .FILTER_RANGE(3'd{fr}), "
                         ".USE_PAD(1'b{pad})) i_pll_{name} (.clkin(clk), .clkout({net}), .lock({net}_locked));".format(
                             r=sol.divr, f=sol.divf, q=sol.divq, fr=sol.filter_range, pad=use_pad,
                             name=name, net=net))
    return lines


_CLOCK_TREE_MODULES = (
    ("pll_gowin_rpll",  os.path.join("rtl", "pll", "pll_gowin_rpll.sv")),
    ("pll_ice40",       os.path.join("rtl", "pll", "pll_ice40.sv")),
    ("pll_xilinx_mmcm", os.path.join("rtl", "pll", "pll_xilinx_mmcm.sv")),
    ("clkdiv_gowin",    os.path.join("rtl", "pll", "clkdiv_gowin.sv")),
    ("pll_gowin_gw5",   os.path.join("rtl", "pll", "pll_gowin_gw5.sv")),
)


def pll_source_files(top_text):
    """Repo-relative RTL files a generated top needs for its clock tree."""
    return [rel for mod, rel in _CLOCK_TREE_MODULES if mod in top_text]


def pll_source_paths(repo, generated_top, peripherals=None):
    """Absolute extra source paths for the toolchain source collectors: the
    clock-tree wrappers the generated top instantiates (rtl/pll/*.sv) and the
    attached peripherals' `driver.files` (modules a driver file depends on,
    e.g. hdmi_tmds_out -> dvi.sv + diff_obuf.sv)."""
    try:
        with open(generated_top) as fh:
            text = fh.read()
    except OSError:
        text = ""
    rels = list(pll_source_files(text))
    for attach in peripherals or []:
        drv = (attach.get("peripheral") or {}).get("driver") or {}
        for rel in drv.get("files") or []:
            if rel not in rels:
                rels.append(rel)
    return [os.path.join(repo, rel) for rel in rels if os.path.exists(os.path.join(repo, rel))]


def clock_driven_pins(resolved):
    """[(port_name, clock_name, mhz)] for pins a peripheral drives straight
    from a PLL clock (`pin.ck: clock.pixel`), so the constraint emitters can
    put BGM's `create_clock` on the output pad."""
    reqs = collect_clock_requirements(resolved)
    out = []
    for attach in resolved["peripherals"]:
        bind = attach.get("bind") or {}
        for lhs, rhs in (attach["peripheral"].get("pin_assigns") or {}).items():
            if not (isinstance(rhs, str) and rhs.strip().startswith("clock.")):
                continue
            pin = _pin_of(lhs)
            name = rhs.strip()[len("clock."):]
            if pin and pin in bind and name in reqs:
                out.append((_bank_port_name(bind[pin]), name, reqs[name]["mhz"]))
    return out


# ---------------------------------------------------------------------------
# Phase 2: plan FPGA top-level ports
# ---------------------------------------------------------------------------

def collect_referenced_banks(resolved):
    """Return ordered set of bank names referenced by any peripheral binding
    or by a `reset.sources[].pin` entry."""
    banks = OrderedDict()
    for attach in resolved["peripherals"]:
        for ref in (attach.get("bind") or {}).values():
            for one in (ref if isinstance(ref, list) else [ref]):
                parsed = _parse_bank_ref(one) if isinstance(one, str) else None
                if parsed is None:
                    continue
                banks[parsed[0]] = True
    for bank in _reset_pin_banks(resolved):
        banks[bank] = True
    return list(banks.keys())


def fpga_port_decls(resolved, referenced_banks):
    """Emit the FPGA top module's port list. Direction is inferred per sub-key
    when a bank has differently-directed pins (e.g. UART tx/rx)."""
    pinmap = resolved["board_pinmap"]
    decls = []
    ports = []

    for bank_name in referenced_banks:
        bank = (pinmap.get("pinBanks") or {}).get(bank_name) or {}
        pins = bank.get("pins")

        if isinstance(pins, str) or pins is None:
            d = _infer_pin_direction(resolved, bank_name, None)
            ports.append((bank_name, 1, d))
            decls.append("    {dir:<6s} {name}".format(dir=_dir_kw(d), name=bank_name))
        elif isinstance(pins, list):
            w = len(pins)
            d = _infer_pin_direction(resolved, bank_name, None)
            ports.append((bank_name, w, d))
            decls.append("    {dir:<6s} [{hi}:0] {name}"
                         .format(dir=_dir_kw(d), hi=w-1, name=bank_name))
        elif isinstance(pins, dict):
            for sub, val in pins.items():
                pname = "{}_{}".format(bank_name, sub)
                d = _infer_pin_direction(resolved, bank_name, sub)
                if isinstance(val, list):
                    w = len(val)
                    ports.append((pname, w, d))
                    decls.append("    {dir:<6s} [{hi}:0] {name}"
                                 .format(dir=_dir_kw(d), hi=w-1, name=pname))
                else:
                    ports.append((pname, 1, d))
                    decls.append("    {dir:<6s} {name}"
                                 .format(dir=_dir_kw(d), name=pname))
    return ",\n".join(decls), ports


def _infer_pin_direction(resolved, bank_name, subkey):
    """Determine direction for a specific bank pin (or sub-keyed pin set)."""
    if subkey is None and bank_name in _reset_pin_banks(resolved):
        return "input"
    has_in = has_out = has_inout = False
    for attach in resolved["peripherals"]:
        perif = attach["peripheral"]
        sig_dirs = {s["name"]: s.get("direction", "inout") for s in perif.get("signals", [])}
        for sig_name, ref in (attach.get("bind") or {}).items():
            for one in (ref if isinstance(ref, list) else [ref]):
                if not isinstance(one, str):
                    continue
                parsed = _parse_bank_ref(one)
                if parsed is None or parsed[0] != bank_name:
                    continue
                # Only consider this binding if its sub-key matches what we're inferring.
                if subkey is not None and parsed[1] != subkey:
                    continue
                if subkey is None and parsed[1] is not None:
                    continue
                d = sig_dirs.get(sig_name)
                if d == "input":
                    has_in = True
                elif d == "output":
                    has_out = True
                else:
                    has_inout = True
    if has_inout or (has_in and has_out):
        return "inout"
    if has_in:
        return "input"
    if has_out:
        return "output"
    return "inout"


def _dir_kw(direction):
    return {"input": "input", "output": "output", "inout": "inout"}[direction]




# ---------------------------------------------------------------------------
# Phase 2b: validation (the pin ledger)
# ---------------------------------------------------------------------------

def _bind_pins(pinmap, ref):
    """Physical pins covered by one bind RHS, in port-bit order, as a list of
    (port_bit_name, pin_or_None). Unknown bank/sub-key/index -> None entry."""
    out = []
    if isinstance(ref, list):
        for el in ref:
            out.extend(_bind_pins(pinmap, el))
        return out
    parsed = _parse_bank_ref(str(ref))
    if parsed is None:
        return [(str(ref), None)]
    bank, sub, idx = parsed
    val = _bank_pin(pinmap, bank, sub, idx)
    if val is None and not (idx is None and isinstance(_bank_pin(pinmap, bank, sub, None), list)):
        return [(_bank_ref_to_port(ref), None)]
    if isinstance(val, list):
        base = _bank_port_name(ref)
        return [("{}[{}]".format(base, i), p) for i, p in enumerate(val)]
    if isinstance(val, dict):
        return [(_bank_ref_to_port(ref), None)]       # bound a whole dict bank: unsupported
    return [(_bank_ref_to_port(ref), val)]


def _describe_missing(pinmap, ref, port_bit):
    """Explain why a bind element has no pin: unparsable, unknown bank,
    unknown sub-key, index out of range, or an explicit null entry."""
    refs = ref if isinstance(ref, list) else [ref]
    # Find the element that produced this port bit.
    for one in refs:
        parsed = _parse_bank_ref(str(one))
        if parsed is None:
            return "{!r}: unparsable reference".format(one)
        bank, sub, idx = parsed
        banks = pinmap.get("pinBanks") or {}
        if bank not in banks:
            return "{!r}: bank {!r} does not exist in the pinmap".format(one, bank)
        pins = (banks[bank] or {}).get("pins")
        if sub is not None:
            if not isinstance(pins, dict) or sub not in pins:
                have = sorted(pins.keys()) if isinstance(pins, dict) else "a flat list"
                return "{!r}: sub-key {!r} does not exist (bank has {})".format(one, sub, have)
            pins = pins[sub]
        if idx is not None:
            if not isinstance(pins, list) or idx >= len(pins):
                return "{!r}: index {} out of range".format(one, idx)
            if pins[idx] is None:
                return "{!r}: pin is null".format(one)
            continue
        if isinstance(pins, list) and any(p is None for p in pins):
            return "{}: null entry in the pinmap list".format(port_bit)
    return "{}: no pin".format(port_bit)


def _is_gpio_passthrough(perif):
    return perif.get("driver") is None and any(
        p.get("capability") == "gpio" for p in perif.get("provides") or [])


def validate_configuration(resolved, plans=None):
    """Return a list of human-readable problems that make the generated top
    incorrect. Empty list means the wiring is sound. Checked:

      * every bind resolves to an existing bank / sub-key / index
      * no bound pin is `null`
      * a physical pin is constrained for one top port bit only
      * a top port bit is used by at most one attachment, except a gpio
        passthrough sharing it with a driver peripheral (the gpio bit is then
        left dangling, see _gpio_connection)
      * non-optional peripheral signals are bound
      * `params.width` equals the bound bank's pin count
      * a clock provider with a known frequency exists
    """
    if plans is None:
        plans = build_capability_plans(resolved)
    pinmap = resolved["board_pinmap"]
    cfg_id = resolved["configuration"]["id"]
    problems = []
    pin_to_bits = defaultdict(set)          # physical pin -> {port bit}
    bit_to_attaches = defaultdict(list)     # port bit -> [(idx, is_gpio_passthrough)]
    bit_dirs = defaultdict(set)             # port bit -> {input, output} from non-gpio signals

    banks = pinmap.get("pinBanks") or {}

    def _is_virtual(ref):
        # A `virtual: true` bank has no package pin (Efinity's internal
        # oscillator on the FireAnt); nothing to constrain or collide with.
        parsed = _parse_bank_ref(str(ref)) if isinstance(ref, str) else None
        return bool(parsed and (banks.get(parsed[0]) or {}).get("virtual"))

    for idx, attach in enumerate(resolved["peripherals"]):
        perif = attach["peripheral"]
        label = "{}#{}".format(perif["id"], idx)
        sig_defs = {s["name"]: s for s in perif.get("signals", [])}
        bind = attach.get("bind") or {}
        for sig, ref in bind.items():
            if _is_virtual(ref):
                continue
            entries = _bind_pins(pinmap, ref)
            sig_dir = (sig_defs.get(sig) or {}).get("direction")
            for port_bit, pin in entries:
                if pin is None:
                    problems.append("{}: bind {} -> {}".format(
                        label, sig, _describe_missing(pinmap, ref, port_bit)))
                    continue
                pin_to_bits[str(pin)].add(port_bit)
                bit_to_attaches[port_bit].append((idx, _is_gpio_passthrough(perif)))
                if sig_dir in ("input", "output") and not _is_gpio_passthrough(perif):
                    bit_dirs[port_bit].add(sig_dir)
            sdef = sig_defs.get(sig)
            if sdef is not None and sdef.get("type") == "bus" and isinstance(ref, str):
                want = sdef.get("width")
                if isinstance(want, str) and want.startswith("$"):
                    want = _eval_param(want, attach.get("params") or {}, perif)
                parsed = _parse_bank_ref(ref)
                have = _bank_width(pinmap, parsed[0], parsed[1]) if parsed and parsed[2] is None else 1
                if want is not None and have is not None and int(want) != int(have):
                    problems.append("{}: bind {} -> {!r}: signal is {} wide but the bank has {} pins"
                                    .format(label, sig, ref, want, have))
        for sig, sdef in sig_defs.items():
            if sig not in bind and not sdef.get("optional"):
                problems.append("{}: required signal {!r} is not bound".format(label, sig))

    for pin, bits in sorted(pin_to_bits.items()):
        if len(bits) > 1:
            problems.append("pin {} is constrained for {} top ports: {}".format(pin, len(bits), ", ".join(sorted(bits))))
    for bit, users in sorted(bit_to_attaches.items()):
        if len(users) < 2:
            continue
        non_gpio = [i for i, g in users if not g]
        if len(non_gpio) > 1:
            names = ", ".join("{}#{}".format(resolved["peripherals"][i]["peripheral_id"], i) for i in non_gpio)
            problems.append("port bit {} is driven/bound by {} peripherals: {}".format(bit, len(non_gpio), names))
    for bit, dirs in sorted(bit_dirs.items()):
        if len(dirs) > 1:
            problems.append("port bit {} is bound both as an input and as an output "
                            "(e.g. a peripheral output on the board oscillator pin)".format(bit))

    clock = resolve_clock(resolved, plans)
    if clock is None:
        problems.append("no clock_input attachment")
    elif clock["mhz"] is None:
        problems.append("clock bank {!r} has no frequency_mhz (configuration or pinmap)".format(clock["bank_ref"]))
    else:
        try:
            plan_clock_tree(resolved, plans)
            lab_clock(resolved, plans)
        except CodegenError as exc:
            problems.append(str(exc).split(": ", 1)[-1])

    return ["Configuration {}: {}".format(cfg_id, p) for p in problems]


# ---------------------------------------------------------------------------
# Phase 3: emit SV
# ---------------------------------------------------------------------------

def emit_top_sv(resolved, strict=True):
    """Generate top.sv. With `strict` (the default, what synthesize.py uses)
    any wiring problem found by validate_configuration() raises CodegenError
    instead of producing a top that silently drops or shorts signals."""
    cfg = resolved["configuration"]
    board = resolved["board"]
    pinmap = resolved["board_pinmap"]
    toolchain = resolved["toolchain"]

    plans = build_capability_plans(resolved)
    if strict:
        problems = validate_configuration(resolved, plans)
        if problems:
            raise CodegenError("\n".join(problems))
    referenced_banks = collect_referenced_banks(resolved)
    _EMIT["lab_clk"] = lab_clock(resolved, plans)["net"]
    _EMIT["diff_buf"] = diff_buf_kind(resolved)

    out = []
    out.append("// =============================================================================")
    out.append("// Auto-generated top.sv — DO NOT EDIT")
    out.append("// Configuration: {}".format(cfg["id"]))
    out.append("// Board:         {} ({})".format(board.get("BoardName", board["Id"]), board["Id"]))
    out.append("// Toolchain:     {}".format(toolchain["Id"]))
    out.append("// Generated by tools/codegen.py from config/configurations/{}.yml".format(cfg["id"]))
    out.append("// =============================================================================")
    out.append("")

    # ---- FPGA module header ----
    port_text, _ports = fpga_port_decls(resolved, referenced_banks)
    out.append("module top (")
    out.append(port_text)
    out.append(");")
    out.append("")

    # ---- Context wires ----
    out.extend(_emit_context(resolved, plans))
    out.append("")

    # ---- Capability bus declarations ----
    out.extend(_emit_capability_busses(plans))
    out.append("")

    # ---- Clock tree (PLLs on the board clock) ----
    tree_lines = _emit_clock_tree(resolved, plans, resolve_clock(resolved, plans), strict=strict)
    if tree_lines:
        out.extend(tree_lines)
        out.append("")

    # ---- Reset (may reference the switches / buttons buses) ----
    out.extend(_emit_reset(resolved, plans))
    out.append("")

    # ---- Peripheral wiring (passthroughs + driver instances) ----
    for idx, attach in enumerate(resolved["peripherals"]):
        out.append("    // ---- {} (peripheral '{}') ----"
                   .format(_attach_label(attach), attach["peripheral_id"]))
        out.extend(_emit_attachment(resolved, idx, attach, plans))
        out.append("")

    # ---- design_top instantiation ----
    out.extend(_emit_lab_top(resolved, plans))
    out.append("")
    out.append("endmodule")
    return "\n".join(out)


def _attach_label(attach):
    bind = attach.get("bind") or {}
    if not bind:
        return attach["peripheral_id"]
    sample = next(iter(bind.values()))
    if isinstance(sample, list):
        sample = sample[0] if sample else "?"
    return "{} on {}".format(attach["peripheral_id"], sample)


# ---- Context emission ---------------------------------------------------

def _emit_context(resolved, plans):
    lines = []
    lines.append("    // ---- Context: clk, rst, rst_n ----")

    rst_plan = plans["reset"]

    clock = resolve_clock(resolved, plans)
    if clock is not None:
        clk_port = _bank_ref_to_port(clock["bank_ref"])
        if clk_port == "clk":
            # FPGA top-level port is already named `clk`; emit nothing — the
            # port is directly visible as the system clock wire.
            lines.append("    // System clock comes from the top-level `clk` port directly.")
        else:
            lines.append("    wire clk = {};".format(clk_port))
    else:
        log.warning("Configuration %s: no clock provider — generated top will not work as-is",
                    resolved["configuration"]["id"])
        lines.append("    wire clk = 1'b0;   // TODO: no clock provider")

    # Reset: declared here, assigned by _emit_reset() once the capability
    # buses it may depend on (switches, buttons) have been declared.
    lines.append("    wire rst;")
    lines.append("    wire rst_n = ~ rst;")

    # Advertise clk_mhz to the peripheral drivers (context.clk_mhz). Same
    # value design_top receives (see _emit_lab_top); 50 only as a last resort.
    # With `lab_clock:` this is the PLL clock's frequency (BGM's lab_mhz).
    lab = lab_clock(resolved, plans)
    if lab["name"]:
        lines.append("    // Lab clock: context.clk is {} ({:g} MHz), see the clock tree below."
                     .format(lab["net"], lab["mhz"]))
    lines.append("    localparam int clk_mhz = {};".format(_lab_mhz_int(lab, clock)))
    return lines


def _lab_mhz_int(lab, clock):
    if lab["mhz"] is not None:
        return int(round(lab["mhz"]))
    return _clk_mhz_int(clock)


def _clk_mhz_int(clock):
    """Integer MHz for parameters; fractional board clocks are rare and the
    interface parameter is an int (matches BGM's `parameter clk_mhz = 27`)."""
    if clock is None or clock["mhz"] is None:
        return 50
    return int(round(clock["mhz"]))


# ---- Reset policy ----------------------------------------------------------
#
# BGM derives `rst` per board in board_specific_top.sv: from a dedicated pin
# (`~RESET_N`), from the top switch (`sw[w_sw-1]`), from any key
# (`| (~KEY)`), from a power-up imitation, or an OR of those. The
# configuration expresses the same policy declaratively:
#
#   reset:
#     sources:
#       - pin: cpu_resetn          # a bank ref; `active: low` unless stated
#         active: low
#       - switch_msb: true          # highest switch of the `switches` capability
#       - switch: 3                 # a specific switch index
#       - any_key: true             # OR of the `buttons` capability
#       - key: msb                  # highest button, or an index (`key: 0`)
#       - power_up: true            # imitate_reset_on_power_up
#
# `reset_button` attachments count as `pin` sources too. With nothing declared
# the policy is `power_up`, never a constant 0.

_RESET_KINDS = ("pin", "switch_msb", "switch", "any_key", "key", "power_up")


def reset_sources(resolved, plans=None):
    """Return the ordered list of (kind, detail) reset sources for a configuration."""
    if plans is None:
        plans = build_capability_plans(resolved)
    sources = []
    for pidx, perif, _ in plans["reset"].providers:
        r_attach = resolved["peripherals"][pidx]
        rst_bank = (r_attach.get("bind") or {}).get("rst")
        if rst_bank is None:
            continue
        sources.append(("pin", {"ref": rst_bank,
                                "active": _peripheral_active_polarity(perif, r_attach,
                                                                      resolved["board_pinmap"])}))
    spec = resolved["configuration"].get("reset") or {}
    for src in spec.get("sources") or []:
        if isinstance(src, str):
            src = {src: True}
        if not isinstance(src, dict):
            raise CodegenError("reset.sources entries must be mappings, got {!r}".format(src))
        unknown = set(src) - set(_RESET_KINDS) - {"active"}
        if unknown:
            raise CodegenError("reset.sources: unknown keys {}".format(sorted(unknown)))
        if src.get("pin"):
            sources.append(("pin", {"ref": src["pin"], "active": src.get("active", "low")}))
        if src.get("switch_msb"):
            sources.append(("switch", {"index": "msb"}))
        if "switch" in src and src["switch"] is not None and src["switch"] is not False:
            sources.append(("switch", {"index": src["switch"]}))
        if src.get("any_key"):
            sources.append(("key", {"index": "any"}))
        if "key" in src and src["key"] is not None and src["key"] is not False:
            sources.append(("key", {"index": src["key"]}))
        if src.get("power_up"):
            sources.append(("power_up", {}))
    if not sources:
        sources.append(("power_up", {}))
    return sources


def _index_expr(bus, width, index, what, cfg_id):
    """`msb` -> bus[width-1]; `any` -> (| bus); int -> bus[int]."""
    if index == "any":
        return "(| {})".format(bus)
    if index == "msb":
        return "{}[{}]".format(bus, int(width) - 1)
    try:
        i = int(index)
    except (TypeError, ValueError):
        raise CodegenError("Configuration {}: reset {} index must be msb, any or an integer, got {!r}"
                           .format(cfg_id, what, index))
    if not 0 <= i < int(width):
        raise CodegenError("Configuration {}: reset {} index {} outside 0..{}"
                           .format(cfg_id, what, i, int(width) - 1))
    return "{}[{}]".format(bus, i)


def _reset_pin_banks(resolved):
    """Banks referenced by `reset.sources[].pin` entries (they must become
    top-level input ports even though no peripheral binds them)."""
    banks = []
    spec = resolved["configuration"].get("reset") or {}
    for src in spec.get("sources") or []:
        if isinstance(src, dict) and src.get("pin"):
            parsed = _parse_bank_ref(str(src["pin"]))
            if parsed:
                banks.append(parsed[0])
    return banks


def _emit_reset(resolved, plans):
    lines = ["    // ---- Reset: OR of the configured sources, active-high ----"]
    terms = []
    for kind, d in reset_sources(resolved, plans):
        if kind == "pin":
            ref = _bank_ref_to_port(d["ref"]) if isinstance(d["ref"], str) else d["ref"]
            terms.append("(~ {})".format(ref) if d["active"] == "low" else "({})".format(ref))
        elif kind == "switch":
            w = plans["switches"].params.get("width") if plans["switches"].providers else None
            if not w:
                log.warning("Configuration %s: reset from a switch but no switches capability",
                            resolved["configuration"]["id"])
                continue
            terms.append(_index_expr("cap_switches_sw", w, d["index"], "switch",
                                     resolved["configuration"]["id"]))
        elif kind == "key":
            w = plans["buttons"].params.get("width") if plans["buttons"].providers else None
            if not w:
                log.warning("Configuration %s: reset from a key but no buttons capability",
                            resolved["configuration"]["id"])
                continue
            terms.append(_index_expr("cap_buttons_btn", w, d["index"], "key",
                                     resolved["configuration"]["id"]))
        elif kind == "power_up":
            lines.append("    wire rst_on_power_up;")
            lines.append("    imitate_reset_on_power_up i_imitate_reset_on_power_up "
                         "(.clk ({}), .rst (rst_on_power_up));".format(_EMIT["lab_clk"]))
            terms.append("rst_on_power_up")
    lines.append("    assign rst = {};".format(" | ".join(terms) if terms else "1'b0"))
    return lines


# ---- Capability bus declarations -----------------------------------------

def _emit_capability_busses(plans):
    lines = ["    // ---- Capability buses ----"]
    for cap_id, plan in plans.items():
        if not plan.providers:
            continue
        for sig in plan.cap.get("signals", []):
            sig_name = sig["name"]
            if sig.get("direction") == "inout":
                # Bidirectional capabilities (gpio) are not buffered through a
                # wire: an `assign` is one-way. design_top's port is connected
                # straight to the pin nets (see _gpio_connection).
                lines.append("    // {}.{}: bidirectional, wired directly to design_top".format(cap_id, sig_name))
                continue
            if sig.get("type") == "scalar":
                lines.append("    wire cap_{}_{};".format(cap_id, sig_name))
            else:
                width = _signal_width(plan, sig)
                lines.append("    wire [{}:0] cap_{}_{};".format(width-1, cap_id, sig_name))
    return lines


def _signal_width(plan, sig):
    """Resolve a capability signal's bit width using the plan's params."""
    raw = sig.get("width")
    if raw is None:
        # Default for 'bus' signals (e.g. screen.x/y) — derive from params.
        if plan.id == "screen" and sig["name"] == "x":
            from math import ceil, log2
            w = plan.params.get("width", 1) or 1
            return max(1, int(ceil(log2(max(2, w)))))
        if plan.id == "screen" and sig["name"] == "y":
            from math import ceil, log2
            h = plan.params.get("height", 1) or 1
            return max(1, int(ceil(log2(max(2, h)))))
        if plan.id == "screen" and sig["name"] in ("red", "green", "blue"):
            return _screen_channel_width(plan.params, sig["name"])
        return 1
    if isinstance(raw, str) and raw.startswith("$"):
        key = raw[1:]
        v = plan.params.get(key)
        if v is None:
            return 1
        return int(v)
    return int(raw)


def _screen_channel_width(params, channel):
    """User-visible bits for one colour channel: an explicit `bits_r/g/b`
    capability param (BGM's w_red/w_green/w_blue) wins over `color_depth`."""
    explicit = (params or {}).get("bits_" + channel[0])
    if explicit:
        return int(explicit)
    return _channel_width((params or {}).get("color_depth", 444), channel)


def _channel_width(depth, channel):
    """Bits per colour channel for a screen `color_depth` (444 / 565 / 888,
    plus 111 for one-bit-per-channel displays such as HUB75 panels and
    resistor-less VGA)."""
    if depth == 111:
        return 1
    if depth == 444:
        return 4
    if depth == 565:
        return {"red": 5, "green": 6, "blue": 5}[channel]
    if depth == 888:
        return 8
    return 4


# ---- Attachment emission --------------------------------------------------

def _emit_attachment(resolved, idx, attach, plans):
    perif = attach["peripheral"]
    if perif.get("driver") is None:
        return _emit_passthrough(resolved, idx, attach, plans)
    return _emit_driver_instance(resolved, idx, attach, plans)


def _bank_attr(pinmap, ref, name):
    """Board-level attribute (`active`, `mirror`, ...) of the bank a bind
    references, or None. Attributes live next to `pins:` in the pinmap."""
    if isinstance(ref, list):
        ref = ref[0] if ref else None
    parsed = _parse_bank_ref(str(ref)) if isinstance(ref, str) else None
    if parsed is None:
        return None
    bank = (pinmap.get("pinBanks") or {}).get(parsed[0])
    return bank.get(name) if isinstance(bank, dict) else None


def _peripheral_active_polarity(perif, attach, pinmap=None):
    """Returns 'high' or 'low'. Precedence: configuration `params.active`,
    the bound bank's `active:` attribute in the pinmap (the board fact, written
    from BGM by tools/sync_from_bgm.py --polarity), the peripheral YAML's
    `parameters.active.default`, then 'high'."""
    cfg_params = attach.get("params") or {}
    if "active" in cfg_params:
        return cfg_params["active"]
    if pinmap is not None:
        for ref in (attach.get("bind") or {}).values():
            v = _bank_attr(pinmap, ref, "active")
            if v is not None:
                return v
    pdef = (perif.get("parameters") or {}).get("active") or {}
    return pdef.get("default") or "high"


def _peripheral_mirror(attach, pinmap):
    """True when the bank's bit order is reversed relative to the user's bus
    (BGM `SWAP_BITS (LED, ...)` on the Tang Nano 9K). Configuration
    `params.mirror` overrides the bank attribute."""
    cfg_params = attach.get("params") or {}
    if "mirror" in cfg_params:
        return bool(cfg_params["mirror"])
    for ref in (attach.get("bind") or {}).values():
        v = _bank_attr(pinmap, ref, "mirror")
        if v is not None:
            return bool(v)
    return False


def _reversed_bits(expr, width):
    """`{expr[0], expr[1], ..., expr[w-1]}` for a bus expression, i.e. the
    bit-reversed value of `expr[w-1:0]`."""
    if width <= 1:
        return expr
    return "{" + ", ".join("{}[{}]".format(expr, i) for i in range(width)) + "}"


def _reversed_slice(base, hi, lo):
    return "{" + ", ".join("{}[{}]".format(base, i) for i in range(lo, hi + 1)) + "}"


def _emit_passthrough(resolved, idx, attach, plans):
    """For peripherals with driver: null. Wire pin signals to capability slices
    or vice versa, depending on each capability's aggregation rule and the
    peripheral signal's direction. Inverts when the peripheral is active-low."""
    lines = []
    perif = attach["peripheral"]
    bind = attach.get("bind") or {}
    pinmap = resolved["board_pinmap"]
    active = _peripheral_active_polarity(perif, attach, pinmap)
    inv = "~ " if active == "low" else ""
    mirror = _peripheral_mirror(attach, pinmap)

    for entry in perif.get("provides") or []:
        cap_id = entry["capability"]
        plan = plans[cap_id]
        if plan.aggregation == "exclusive":
            for cap_sig in plan.cap.get("signals", []):
                cap_sig_name = cap_sig["name"]
                pin_sig_name = cap_sig_name
                if pin_sig_name not in bind:
                    continue
                pin_expr = _resolve_ref("pin." + pin_sig_name, attach, plans, bind)
                cap_target = "cap_{}_{}".format(cap_id, cap_sig_name)
                if cap_sig.get("direction") == "user_to_hw":
                    lines.append("    assign {} = {}{};".format(pin_expr, inv, cap_target))
                else:
                    lines.append("    assign {} = {}{};".format(cap_target, inv, pin_expr))
        elif plan.aggregation == "concat":
            offset = plan.offsets[idx]
            width = plan.widths[idx]
            for cap_sig in plan.cap.get("signals", []):
                cap_sig_name = cap_sig["name"]
                pin_sig_name = cap_sig_name
                if pin_sig_name not in bind:
                    continue
                if cap_sig.get("direction") == "inout":
                    hi, lo = offset + width - 1, offset
                    lines.append("    // {}[{}:{}] <-> {} (bidirectional, connected at design_top)"
                                 .format(cap_id, hi, lo, _attach_label(attach)))
                    continue
                pin_expr = _resolve_ref("pin." + pin_sig_name, attach, plans, bind)
                cap_base = "cap_{}_{}".format(cap_id, cap_sig_name)
                if width == 1:
                    slice_expr = "{}[{}]".format(cap_base, offset)
                else:
                    slice_expr = "{}[{}:{}]".format(cap_base, offset + width - 1, offset)
                if mirror and width > 1:
                    # Board bit order is the reverse of the user's (BGM SWAP_BITS):
                    # pin[i] <-> user bit (w-1-i). Emitted per bit on the side
                    # that is a plain bus so the other side stays a slice.
                    lines.append("    // mirrored: bank bit i <-> capability bit {}-i".format(width - 1))
                    bound = bind.get(pin_sig_name)
                    if isinstance(bound, list):
                        pin_bits = [_bank_ref_to_port(b) for b in bound]          # LSB first
                        pin_rev = "{" + ", ".join(pin_bits) + "}"                  # first element = MSB
                    else:
                        pin_rev = _reversed_bits(pin_expr, width)
                    if cap_sig.get("direction") == "user_to_hw":
                        lines.append("    assign {} = {}{};".format(
                            pin_expr, inv, _reversed_slice(cap_base, offset + width - 1, offset)))
                    else:
                        lines.append("    assign {} = {}{};".format(slice_expr, inv, pin_rev))
                    continue
                if cap_sig.get("direction") == "user_to_hw":
                    lines.append("    assign {} = {}{};".format(pin_expr, inv, slice_expr))
                else:
                    lines.append("    assign {} = {}{};".format(slice_expr, inv, pin_expr))
        elif plan.aggregation == "or":
            pass

    # Apply pin_assigns from the peripheral YAML. For active-low peripherals,
    # invert the RHS when both sides aren't already inverted. Optional pins
    # the board does not have are skipped.
    optional_unbound = _optional_unbound_pins(perif, bind)
    for lhs, rhs in (perif.get("pin_assigns") or {}).items():
        if _pin_of(lhs) in optional_unbound or _pin_of(rhs) in optional_unbound:
            lines.append("    // {} <- {}: optional pin not present on this board".format(lhs, rhs))
            continue
        lhs_resolved = _resolve_ref(lhs, attach, plans, bind, lhs_context=True, slice_for_idx=idx)
        rhs_resolved = _resolve_ref(rhs, attach, plans, bind, slice_for_idx=idx)
        # Only auto-invert when RHS comes from a capability (active-high
        # user-perspective signal heading to an active-low pin).
        wants_invert = (active == "low"
                        and isinstance(rhs, str)
                        and rhs.lstrip("~ ").strip().startswith("capability."))
        if wants_invert:
            rhs_resolved = "~ ({})".format(rhs_resolved)
        lines.append("    assign {} = {};".format(lhs_resolved, rhs_resolved))

    if not lines:
        lines.append("    // (passthrough — no provided capabilities or no matching signals)")
    return lines


def _emit_driver_instance(resolved, idx, attach, plans):
    lines = []
    perif = attach["peripheral"]
    drv = perif["driver"]
    bind = attach.get("bind") or {}
    inst_name = "i_{}_{}".format(perif["id"], idx)

    # Driver parameters
    param_decls = []
    for pname, pval in (drv.get("parameters") or {}).items():
        param_decls.append(".{}({})".format(pname, _resolve_ref(pval, attach, plans, bind)))

    lines.append("    {mod} {params}{inst} (".format(
        mod=drv["module"],
        params=("# (" + ", ".join(param_decls) + ") ") if param_decls else "",
        inst=inst_name))

    # Driver port_map. `slice_for_idx` ensures that capability refs are
    # narrowed to THIS peripheral's slice when the capability is concat with
    # multiple providers (e.g. tm1638's `keys` port wires to its 8 of the
    # combined switches bus, not the full bus). A `pin.X` whose signal is
    # optional and unbound (a panel without HSYNC pins) is left unconnected.
    optional_unbound = _optional_unbound_pins(perif, bind)
    port_lines = []
    for port, ref in (drv.get("port_map") or {}).items():
        if ref is None or ref == "" or _pin_of(ref) in optional_unbound:
            port_lines.append("        .{}()".format(port))
        else:
            port_lines.append("        .{}({})".format(
                port, _resolve_ref(ref, attach, plans, bind, slice_for_idx=idx)))
    lines.append(",\n".join(port_lines))
    lines.append("    );")

    # pin_assigns (combinational connections outside the driver instance)
    for lhs, rhs in (perif.get("pin_assigns") or {}).items():
        if _pin_of(lhs) in optional_unbound or _pin_of(rhs) in optional_unbound:
            lines.append("    // {} <- {}: optional pin not present on this board".format(lhs, rhs))
            continue
        lhs_resolved = _resolve_ref(lhs, attach, plans, bind, lhs_context=True, slice_for_idx=idx)
        rhs_resolved = _resolve_ref(rhs, attach, plans, bind, slice_for_idx=idx)
        lines.append("    assign {} = {};".format(lhs_resolved, rhs_resolved))
    return lines


def _pin_of(ref):
    """`pin.hs`, `~pin.hs`, `pin.d_p[0]` -> `hs` / `d_p`; anything else -> None."""
    if not isinstance(ref, str):
        return None
    s = ref.strip().lstrip("~").strip()
    if not s.startswith("pin."):
        return None
    return re.split(r"[\[\s]", s[len("pin."):], 1)[0]


def _optional_unbound_pins(perif, bind):
    return {s["name"] for s in perif.get("signals", [])
            if s.get("optional") and s["name"] not in bind}


def _bank_ref_to_port(ref):
    """`onboard_rgb_led_0.r`  -> `onboard_rgb_led_0_r`.
       `pmod_jc[2]`           -> `pmod_jc[2]`.
       `onboard_7seg.anodes[0]` -> `onboard_7seg_anodes[0]`."""
    if not isinstance(ref, str):
        return ref
    s = ref.strip()
    # Split index suffix off, translate dots, re-attach.
    m = re.match(r"^(.+?)(\[\d+\])$", s)
    suffix = ""
    if m:
        s, suffix = m.group(1), m.group(2)
    return s.replace(".", "_") + suffix


def _resolve_ref(ref, attach, plans, bind, lhs_context=False, slice_for_idx=None):
    """Translate a YAML reference (`pin.x`, `capability.<id>.<sig>`, `context.<x>`,
    `const.<v>`) into the SV expression usable in the generated top module.

    `slice_for_idx`: when set to a peripheral index, capability refs to a
    concat-aggregated bus get sliced to THIS peripheral's contribution
    (`cap_<id>_<sig>[off+w-1:off]`). Without this, multi-provider concat
    capabilities would silently drive overlapping slices."""
    if ref is None:
        return ""
    if isinstance(ref, list):
        return "{" + ", ".join(_resolve_ref(x, attach, plans, bind, lhs_context, slice_for_idx) for x in ref) + "}"
    if not isinstance(ref, str):
        return str(ref)
    s = ref.strip()
    invert = ""
    if s.startswith("~"):
        invert = "~"
        s = s[1:].strip()

    # Optional indexing/slicing suffix:  `pin.d_p[0]`, `cap.x.y[7:0]`.
    idx_suffix = ""
    m = re.match(r"^(.+?)(\[\d+(?::\d+)?\])$", s)
    if m:
        s, idx_suffix = m.group(1), m.group(2)

    if s.startswith("pin."):
        pin_sig = s[len("pin."):]
        bound = bind.get(pin_sig)
        if bound is None:
            return invert + pin_sig + idx_suffix
        if isinstance(bound, list):
            # Multi-pin peripheral signal bound to a list of bank refs.
            # Convention: list[0] is bit 0 (LSB-first in YAML). An index
            # suffix selects list elements (a concat cannot be indexed in SV).
            if idx_suffix:
                m_idx = re.match(r"^\[(\d+)(?::(\d+))?\]$", idx_suffix)
                hi = int(m_idx.group(1))
                lo = int(m_idx.group(2)) if m_idx.group(2) is not None else hi
                if hi >= len(bound) or lo > hi:
                    raise CodegenError("pin.{} index {} out of range for a {}-element bind"
                                       .format(pin_sig, idx_suffix, len(bound)))
                if hi == lo:
                    return invert + _bank_ref_to_port(bound[hi])
                inner = ", ".join(_bank_ref_to_port(b) for b in reversed(bound[lo:hi + 1]))
                return invert + "{" + inner + "}"
            inner = ", ".join(_bank_ref_to_port(b) for b in reversed(bound))
            return invert + "{" + inner + "}"
        return invert + _bank_ref_to_port(bound) + idx_suffix
    if s.startswith("capability."):
        rest = s[len("capability."):]
        parts = rest.split(".")
        if len(parts) >= 2:
            cap_id = parts[0]
            sig = "_".join(parts[1:])
            base = "cap_{}_{}".format(cap_id, sig)
            # Apply per-peripheral slice when requested AND this capability
            # is concat-aggregated with multiple providers.
            if slice_for_idx is not None and not idx_suffix:
                plan = plans.get(cap_id)
                if (plan is not None and plan.aggregation == "concat"
                        and len(plan.providers) > 1
                        and slice_for_idx in plan.offsets):
                    off = plan.offsets[slice_for_idx]
                    w = plan.widths[slice_for_idx]
                    idx_suffix = "[{}]".format(off) if w == 1 else "[{}:{}]".format(off+w-1, off)
            return invert + base + idx_suffix
    if s.startswith("context."):
        name = s[len("context."):]
        if name == "clk":
            name = _EMIT["lab_clk"]
        elif name == "diff_buf":
            return _sv_literal(_EMIT["diff_buf"])
        return invert + name + idx_suffix
    if s.startswith("clock."):
        return invert + "clk_" + s[len("clock."):] + idx_suffix
    if s.startswith("const."):
        v = s[len("const."):]
        return invert + ("1'b" + v if v in ("0", "1") else v) + idx_suffix
    if s.startswith("$"):
        # Peripheral-instance parameter (configuration `params:` or the YAML
        # default), rendered as an SV literal: ints/floats verbatim, booleans
        # as 1/0, strings quoted.
        v = _eval_param(s, attach.get("params") or {}, attach.get("peripheral"))
        if v is None:
            raise CodegenError("{}: parameter {} has no value and no default".format(
                attach.get("peripheral_id", "?"), s))
        if isinstance(v, str) and _looks_like_ref(v):
            # `params: {bl: const.0}` / `{bl: context.rst_n}`: a wiring choice
            # the configuration makes (BGM: `assign LCD_BL = ~ rst` on one
            # board, `1'b0` on another).
            return invert + _resolve_ref(v, attach, plans, bind, lhs_context, slice_for_idx) + idx_suffix
        return invert + _sv_literal(v) + idx_suffix
    return invert + s + idx_suffix


_REF_PREFIXES = ("pin.", "capability.", "context.", "clock.", "const.")


def _looks_like_ref(v):
    return v.strip().lstrip("~").strip().startswith(_REF_PREFIXES)


def _sv_literal(v):
    if isinstance(v, bool):
        return "1'b1" if v else "1'b0"
    if isinstance(v, (int, float)):
        return str(v)
    return '"{}"'.format(str(v).replace('"', '\\"'))


# ---- Bidirectional gpio -------------------------------------------------------

def _bind_bit_ports(resolved, ref):
    """Expand one bind RHS into the list of generated top port bits it covers,
    LSB first: `pmod_ja` -> [pmod_ja[0], ..., pmod_ja[7]]; `pmod_ja[3]` ->
    [pmod_ja[3]]; `onboard_uart.tx` -> [onboard_uart_tx]. Lists expand element-wise."""
    pinmap = resolved["board_pinmap"]
    if isinstance(ref, list):
        out = []
        for el in ref:
            out.extend(_bind_bit_ports(resolved, el))
        return out
    parsed = _parse_bank_ref(str(ref))
    if parsed is None:
        return []
    bank, sub, idx = parsed
    if idx is not None:
        return [_bank_ref_to_port(ref)]
    width = _bank_width(pinmap, bank, sub)
    base = _bank_port_name(ref)
    if width is None:
        return []
    if width == 1 and not isinstance(_bank_pin(pinmap, bank, sub, None), list):
        return [base]
    return ["{}[{}]".format(base, i) for i in range(width)]


def _claimed_port_bits(resolved, plans, gpio_indices):
    """Port bits used by any attachment other than the gpio providers. A gpio
    bit whose pin is claimed (a mic on pmod_ja[6], a TM1638 on gpio[0..2]) is
    left dangling on the design side so nothing double-drives the pad while the
    user's gpio numbering stays identical to the board header."""
    claimed = set()
    for idx, attach in enumerate(resolved["peripherals"]):
        if idx in gpio_indices:
            continue
        for ref in (attach.get("bind") or {}).values():
            claimed.update(_bind_bit_ports(resolved, ref))
    return claimed


def _gpio_connection(resolved, plans):
    """Return (expression, declaration_lines) for design_top's `gpio` port, or
    (None, []) when no provider exists."""
    plan = plans["gpio"]
    if not plan.providers:
        return None, []
    sig = next((s for s in plan.cap.get("signals", []) if s.get("direction") == "inout"), None)
    if sig is None:
        return None, []
    gpio_indices = {pidx for pidx, _p, _params in plan.providers}
    claimed = _claimed_port_bits(resolved, plans, gpio_indices)

    bits = [None] * int(plan.params.get("width", 0) or 0)
    decls = []
    for pidx, perif, _params in plan.providers:
        attach = resolved["peripherals"][pidx]
        ref = (attach.get("bind") or {}).get(sig["name"])
        if ref is None:
            continue
        ports = _bind_bit_ports(resolved, ref)
        offset, width = plan.offsets[pidx], plan.widths[pidx]
        if len(ports) != width:
            log.warning("Configuration %s: %s provides %d gpio bits but its bind %r covers %d pins",
                        resolved["configuration"]["id"], perif["id"], width, ref, len(ports))
        for i in range(width):
            n = offset + i
            if n >= len(bits):
                break
            port = ports[i] if i < len(ports) else None
            if port is None or port in claimed:
                decls.append("    wire gpio_nc_{};   // {}".format(
                    n, "pin claimed by another peripheral" if port else "no pin on this header position"))
                bits[n] = "gpio_nc_{}".format(n)
            else:
                bits[n] = port
    for n, b in enumerate(bits):
        if b is None:
            decls.append("    wire gpio_nc_{};".format(n))
            bits[n] = "gpio_nc_{}".format(n)
    expr = "{" + ", ".join(reversed(bits)) + "}" if bits else None
    return expr, decls


# ---- design_top instantiation -----------------------------------------------

def _emit_lab_top(resolved, plans):
    lines = ["    // ---- User logic (design_top) ----"]
    gpio_expr, gpio_decls = _gpio_connection(resolved, plans)
    if gpio_decls:
        lines.append("    // gpio bits without a usable pin (claimed by a driver peripheral, or")
        lines.append("    // absent on this header) are left dangling so numbering matches the board.")
        lines.extend(gpio_decls)

    cap_widths = {
        "switches":      plans["switches"].params.get("width", 0)      if plans["switches"].providers else 0,
        "buttons":       plans["buttons"].params.get("width", 0)       if plans["buttons"].providers else 0,
        "leds":          plans["leds"].params.get("width", 0)          if plans["leds"].providers else 0,
        # rgb_leds is concat-aggregated on `count` (see _PRIMARY_PARAM), not `width`.
        "rgb_leds":      plans["rgb_leds"].params.get("count", 0)      if plans["rgb_leds"].providers else 0,
        "seven_segment": plans["seven_segment"].params.get("digits", 0) if plans["seven_segment"].providers else 0,
        "gpio":          plans["gpio"].params.get("width", 0)          if plans["gpio"].providers else 0,
    }

    if plans["screen"].providers:
        sp = plans["screen"].params
        sw, sh = sp.get("width", 0), sp.get("height", 0)
        wr = _screen_channel_width(sp, "red")
        wg = _screen_channel_width(sp, "green")
        wb = _screen_channel_width(sp, "blue")
    else:
        sw = sh = wr = wg = wb = 0

    lab = lab_clock(resolved, plans)
    clk_mhz = _lab_mhz_int(lab, resolve_clock(resolved, plans))

    params = [
        ("clk_mhz",       clk_mhz),
        ("w_sw",          cap_widths["switches"]),
        ("w_btn",         cap_widths["buttons"]),
        ("w_led",         cap_widths["leds"]),
        ("w_digit",       cap_widths["seven_segment"]),
        ("w_rgb_led",     cap_widths["rgb_leds"]),
        ("screen_width",  sw),
        ("screen_height", sh),
        ("w_red",         wr),
        ("w_green",       wg),
        ("w_blue",        wb),
        ("w_gpio",        cap_widths["gpio"]),
    ]
    param_block = ",\n".join("        .{}({})".format(n, v) for n, v in params)
    lines.append("    design_top # (")
    lines.append(param_block)
    lines.append("    ) i_design_top (")

    port_lines = [
        "        .clk({})".format(lab["net"]),
        "        .rst(rst)",
        "        .sw(cap_switches_sw)"        if plans["switches"].providers      else "        .sw('0)",
        "        .btn(cap_buttons_btn)"       if plans["buttons"].providers       else "        .btn('0)",
        "        .led(cap_leds_led)"          if plans["leds"].providers          else "        .led()",
        "        .abcdefgh(cap_seven_segment_abcdefgh)" if plans["seven_segment"].providers else "        .abcdefgh()",
        "        .digit(cap_seven_segment_digit)"       if plans["seven_segment"].providers else "        .digit()",
        "        .rgb_r(cap_rgb_leds_r)"      if plans["rgb_leds"].providers      else "        .rgb_r()",
        "        .rgb_g(cap_rgb_leds_g)"      if plans["rgb_leds"].providers      else "        .rgb_g()",
        "        .rgb_b(cap_rgb_leds_b)"      if plans["rgb_leds"].providers      else "        .rgb_b()",
        "        .x(cap_screen_x)"            if plans["screen"].providers        else "        .x('0)",
        "        .y(cap_screen_y)"            if plans["screen"].providers        else "        .y('0)",
        "        .red(cap_screen_red)"        if plans["screen"].providers        else "        .red()",
        "        .green(cap_screen_green)"    if plans["screen"].providers        else "        .green()",
        "        .blue(cap_screen_blue)"      if plans["screen"].providers        else "        .blue()",
        "        .mic_sample(cap_audio_in_sample)" if plans["audio_in"].providers else "        .mic_sample('0)",
        "        .mic_valid(cap_audio_in_valid)"   if plans["audio_in"].providers else "        .mic_valid(1'b0)",
        "        .sound(cap_audio_out_sample)"     if plans["audio_out"].providers else "        .sound()",
        "        .uart_rx(cap_serial_console_rx)"  if plans["serial_console"].providers else "        .uart_rx(1'b1)",
        "        .uart_tx(cap_serial_console_tx)"  if plans["serial_console"].providers else "        .uart_tx()",
        "        .gpio({})".format(gpio_expr)      if gpio_expr                   else "        .gpio()",
    ]
    lines.append(",\n".join(port_lines))
    lines.append("    );")
    return lines


# ---------------------------------------------------------------------------
# XDC constraint emission (Vivado / Xilinx)
# ---------------------------------------------------------------------------

def emit_xdc(resolved):
    """Emit a Vivado XDC constraint file mapping every top-level FPGA port to
    its physical PACKAGE_PIN + IOSTANDARD. Includes clock create_clock entries
    for any clock-providing peripheral with a known frequency."""
    cfg = resolved["configuration"]
    pinmap = resolved["board_pinmap"]
    default_iostd = (pinmap.get("defaults") or {}).get("iostandard") or "LVCMOS33"

    out = []
    out.append("# =============================================================================")
    out.append("# Auto-generated XDC constraints — DO NOT EDIT")
    out.append("# Configuration: {}".format(cfg["id"]))
    out.append("# Board:         {}".format(resolved["board"].get("BoardName", resolved["board"]["Id"])))
    out.append("# =============================================================================")
    out.append("")
    # Silence Vivado's CFGBVS DRC warning. 3.3 V is the universal default for
    # 7-series education boards; configurations needing 1.8 V can override this
    # constraint downstream.
    out.append("set_property CFGBVS VCCO       [current_design];")
    out.append("set_property CONFIG_VOLTAGE 3.3 [current_design];")
    out.append("")

    referenced = collect_referenced_banks(resolved)
    plans = build_capability_plans(resolved)

    # ---- Pin assignments per bank/sub-key/index ----
    for bank_name in referenced:
        bank = (pinmap.get("pinBanks") or {}).get(bank_name)
        if bank is None:
            out.append("# WARNING: bank '{}' referenced but not in pinBanks".format(bank_name))
            continue
        pins = bank.get("pins")
        overrides = (bank.get("overrides") or {})
        bank_iostd = bank.get("iostandard") or default_iostd

        if isinstance(pins, str):
            out.append(_xdc_line(pins, bank_name, _pin_iostd(pins, overrides, bank_iostd)))
        elif isinstance(pins, list):
            for i, p in enumerate(pins):
                if p is None:
                    continue
                port = "{}[{}]".format(bank_name, i)
                out.append(_xdc_line(p, port, _pin_iostd(p, overrides, bank_iostd)))
        elif isinstance(pins, dict):
            for sub, val in pins.items():
                pname = "{}_{}".format(bank_name, sub)
                if isinstance(val, list):
                    for i, p in enumerate(val):
                        if p is None:
                            continue
                        port = "{}[{}]".format(pname, i)
                        out.append(_xdc_line(p, port, _pin_iostd(p, overrides, bank_iostd)))
                elif isinstance(val, str):
                    out.append(_xdc_line(val, pname, _pin_iostd(val, overrides, bank_iostd)))

    # ---- Clock create_clock entries ----
    out.append("")
    out.append("# ---- Clock definitions ----")
    clock = resolve_clock(resolved, plans)
    if clock is not None and clock["mhz"] is not None:
        period_ns = _clock_period_ns(clock)
        out.append(
            "create_clock -name sys_clk_{f}mhz -period {p:.3f} -waveform {{0 {h:.3f}}} "
            "[get_ports {{ {port} }}];".format(f=_clk_mhz_int(clock), p=period_ns,
                                                h=period_ns / 2.0, port=clock["port"])
        )
    else:
        out.append("# WARNING: no clock frequency known for this configuration (CLK-FREQ)")

    out.append("")
    return "\n".join(out)


def _xdc_line(pin, port_expr, iostd):
    # A "P,N" pair locates the P port; the N port has its own entry (its pin
    # is the pair's second half) and Vivado checks both against the package.
    pin_str = _pair_p(pin)
    return (
        "set_property -dict {{ PACKAGE_PIN {pin} IOSTANDARD {std} }} "
        "[get_ports {{ {port} }}];".format(pin=pin_str, std=iostd, port=port_expr)
    )


def emit_xdc_simple(resolved):
    """Like emit_xdc(), but emits the simple 4-arg `set_property NAME VAL
    [get_ports …]` form that nextpnr-xilinx (openxc7) accepts. Vivado's
    `-dict { … }` shorthand isn't supported by the open flow."""
    cfg = resolved["configuration"]
    pinmap = resolved["board_pinmap"]
    default_iostd = (pinmap.get("defaults") or {}).get("iostandard") or "LVCMOS33"

    out = []
    out.append("# =============================================================================")
    out.append("# Auto-generated XDC constraints (simple form, openxc7) — DO NOT EDIT")
    out.append("# Configuration: {}".format(cfg["id"]))
    out.append("# =============================================================================")
    out.append("")

    referenced = collect_referenced_banks(resolved)
    plans = build_capability_plans(resolved)

    def emit(pin, port, iostd):
        if "," in str(pin):
            out.append("# WARNING: pin '{}' for port '{}' is a differential pair (skipped)".format(pin, port))
            return
        out.append("set_property PACKAGE_PIN {} [get_ports {{{}}}]".format(pin, port))
        out.append("set_property IOSTANDARD {} [get_ports {{{}}}]".format(iostd, port))

    for bank_name in referenced:
        bank = (pinmap.get("pinBanks") or {}).get(bank_name)
        if bank is None:
            continue
        pins = bank.get("pins")
        overrides = bank.get("overrides") or {}
        bank_iostd = bank.get("iostandard") or default_iostd
        if isinstance(pins, str):
            emit(pins, bank_name, _pin_iostd(pins, overrides, bank_iostd))
        elif isinstance(pins, list):
            for i, p in enumerate(pins):
                if p is None:
                    continue
                emit(p, "{}[{}]".format(bank_name, i), _pin_iostd(p, overrides, bank_iostd))
        elif isinstance(pins, dict):
            for sub, val in pins.items():
                pname = "{}_{}".format(bank_name, sub)
                if isinstance(val, list):
                    for i, p in enumerate(val):
                        if p is None:
                            continue
                        emit(p, "{}[{}]".format(pname, i), _pin_iostd(p, overrides, bank_iostd))
                elif isinstance(val, str):
                    emit(val, pname, _pin_iostd(val, overrides, bank_iostd))

    # Clock create_clock entries
    out.append("")
    clock = resolve_clock(resolved, plans)
    if clock is not None and clock["mhz"] is not None:
        out.append("create_clock -name sys_clk_{f}mhz -period {p:.3f} [get_ports {{{port}}}]".format(
            f=_clk_mhz_int(clock), p=_clock_period_ns(clock), port=clock["port"]))
    else:
        out.append("# WARNING: no clock frequency known for this configuration (CLK-FREQ)")

    out.append("")
    return "\n".join(out)


def emit_ucf(resolved):
    """Emit an ISE UCF (User Constraints File) for legacy Xilinx parts.

    UCF differs from XDC in three ways:
      1. `NET "name" LOC = "pin";` syntax instead of `set_property PACKAGE_PIN …`.
      2. Bus signals use `name<N>` (angle brackets) rather than `name[N]`.
      3. Clocks use `TIMESPEC TS_<id> = PERIOD "<net>" <ns> ns HIGH 50%;`
         paired with `NET "<net>" TNM_NET = "<id>";`.

    Targets ISE 14.7 — Spartan 3 / 6 and Virtex 4 / 5 / 6."""
    cfg = resolved["configuration"]
    pinmap = resolved["board_pinmap"]
    default_iostd = (pinmap.get("defaults") or {}).get("iostandard") or "LVCMOS33"

    out = []
    out.append("# =============================================================================")
    out.append("# Auto-generated UCF (ISE) constraints — DO NOT EDIT")
    out.append("# Configuration: {}".format(cfg["id"]))
    out.append("# Board:         {}".format(resolved["board"].get("BoardName", resolved["board"]["Id"])))
    out.append("# =============================================================================")
    out.append("")

    referenced = collect_referenced_banks(resolved)
    plans = build_capability_plans(resolved)

    def emit(pin, port, iostd):
        if "," in str(pin):
            out.append('# WARNING: pin "{}" for port "{}" is a differential pair (skipped)'.format(pin, port))
            return
        # UCF uses <N> for bus indices. Translate `name[N]` → `name<N>`.
        ucf_port = port.replace("[", "<").replace("]", ">")
        out.append('NET "{port}" LOC = "{pin}";'.format(port=ucf_port, pin=pin))
        out.append('NET "{port}" IOSTANDARD = "{iostd}";'.format(port=ucf_port, iostd=iostd))

    for bank_name in referenced:
        bank = (pinmap.get("pinBanks") or {}).get(bank_name)
        if bank is None:
            continue
        pins = bank.get("pins")
        overrides = bank.get("overrides") or {}
        bank_iostd = bank.get("iostandard") or default_iostd
        if isinstance(pins, str):
            emit(pins, bank_name, _pin_iostd(pins, overrides, bank_iostd))
        elif isinstance(pins, list):
            for i, p in enumerate(pins):
                if p is None:
                    continue
                emit(p, "{}[{}]".format(bank_name, i), _pin_iostd(p, overrides, bank_iostd))
        elif isinstance(pins, dict):
            for sub, val in pins.items():
                pname = "{}_{}".format(bank_name, sub)
                if isinstance(val, list):
                    for i, p in enumerate(val):
                        if p is None:
                            continue
                        emit(p, "{}[{}]".format(pname, i), _pin_iostd(p, overrides, bank_iostd))
                elif isinstance(val, str):
                    emit(val, pname, _pin_iostd(val, overrides, bank_iostd))

    # ---- Clock period constraints ----
    out.append("")
    out.append("# ---- Clock definitions ----")
    clock = resolve_clock(resolved, plans)
    if clock is not None and clock["mhz"] is not None:
        port_ucf = clock["port"].replace("[", "<").replace("]", ">")
        tnm = "sys_clk_{}mhz".format(_clk_mhz_int(clock))
        out.append('NET "{port}" TNM_NET = "{tnm}";'.format(port=port_ucf, tnm=tnm))
        out.append('TIMESPEC TS_{tnm} = PERIOD "{tnm}" {p:.3f} ns HIGH 50%;'.format(
            tnm=tnm, p=_clock_period_ns(clock)))
    else:
        out.append("# WARNING: no clock frequency known for this configuration (CLK-FREQ)")

    out.append("")
    return "\n".join(out)


def _pin_iostd(pin, overrides, default):
    return overrides.get(pin) or overrides.get(_pair_p(pin)) or default


def _pair_p(pin):
    """First (P) pin of a `"69,68"` differential pair, the pin itself otherwise."""
    return str(pin).split(",", 1)[0].strip()


def _pair_n_pins(pinmap, referenced):
    """{N pin: P pin} for every "P,N" pair in the referenced banks, so the
    emitters recognise the pair's N half when it also appears as its own
    entry (`clk_n: "68"` next to `clk_p: "69,68"`)."""
    out = {}
    for bank_name in referenced:
        bank = (pinmap.get("pinBanks") or {}).get(bank_name) or {}
        pins = bank.get("pins")
        vals = []
        if isinstance(pins, str):
            vals = [pins]
        elif isinstance(pins, list):
            vals = pins
        elif isinstance(pins, dict):
            for v in pins.values():
                vals.extend(v if isinstance(v, list) else [v])
        for v in vals:
            if isinstance(v, str) and "," in v:
                p, n = [x.strip() for x in v.split(",", 1)]
                out[n] = p
    return out


def _bank_port_name(bank_ref):
    """Translate a bank reference like `clk100mhz` or `onboard_uart.tx` into the
    SV port name produced by emit_top_sv (which strips dots to underscores)."""
    m = _BANK_REF.match(str(bank_ref))
    if not m:
        return str(bank_ref)
    bank, sub, idx = m.group(1), m.group(2), m.group(3)
    name = bank if sub is None else "{}_{}".format(bank, sub)
    return name + ("[{}]".format(idx) if idx is not None else "")


# ---------------------------------------------------------------------------
# QSF / SDC constraint emission (Intel/Altera — Quartus Prime / Quartus II)
# ---------------------------------------------------------------------------

# Map BoardYaml.PartFamily strings to Quartus-canonical FAMILY assignment names.
# When a Cyclone IV part starts with EP4CGX/EP4CE we disambiguate the GX/E
# variants (Quartus rejects bare "Cyclone IV").
_QUARTUS_FAMILY = {
    "MAX 10":         "MAX 10",
    "Cyclone V SoC":  "Cyclone V",
    "Cyclone V":      "Cyclone V",
    "Cyclone IV E":   "Cyclone IV E",
    "Cyclone IV GX":  "Cyclone IV GX",
    "Cyclone III":    "Cyclone III",
    "Cyclone II":     "Cyclone II",
    "Cyclone":        "Cyclone",
    "MAX V":          "MAX V",
    "MAX II":         "MAX II",
    "MAX II CPLD":    "MAX II",
    "MAX V CPLD":     "MAX V",
}


def _quartus_family(board, part):
    """Pick a FAMILY string for the QSF. Disambiguate Cyclone IV by part prefix."""
    fam = (board.get("PartFamily") or "").strip()
    if fam == "Cyclone IV":
        p = (part or "").upper()
        if p.startswith("EP4CGX"):
            return "Cyclone IV GX"
        return "Cyclone IV E"
    return _QUARTUS_FAMILY.get(fam, fam)


def emit_qsf(resolved, part):
    """Emit a Quartus QSF settings file: family, device, top entity, plus
    per-pin set_location_assignment + IO_STANDARD lines for every referenced
    port. Mirrors emit_xdc's bank-walking logic."""
    cfg = resolved["configuration"]
    board = resolved["board"]
    pinmap = resolved["board_pinmap"]
    default_iostd = (pinmap.get("defaults") or {}).get("iostandard") or "3.3-V LVTTL"
    family = _quartus_family(board, part)

    out = []
    out.append("# =============================================================================")
    out.append("# Auto-generated QSF settings — DO NOT EDIT")
    out.append("# Configuration: {}".format(cfg["id"]))
    out.append("# Board:         {}".format(board.get("BoardName", board["Id"])))
    out.append("# =============================================================================")
    out.append("")
    out.append('set_global_assignment -name FAMILY "{}"'.format(family))
    out.append("set_global_assignment -name DEVICE {}".format(part))
    out.append("set_global_assignment -name TOP_LEVEL_ENTITY top")
    # Default Verilog input version: SystemVerilog 2005. Without this, Quartus
    # parses `.v` files (and `\\`include`d `.svh`/`.vh` headers) as Verilog 2001,
    # rejecting `'0`, `always_ff`, `logic`, etc.
    out.append("set_global_assignment -name VERILOG_INPUT_VERSION SYSTEMVERILOG_2005")
    out.append("")

    referenced = collect_referenced_banks(resolved)
    plans = build_capability_plans(resolved)

    for bank_name in referenced:
        bank = (pinmap.get("pinBanks") or {}).get(bank_name)
        if bank is None:
            out.append("# WARNING: bank '{}' referenced but not in pinBanks".format(bank_name))
            continue
        pins = bank.get("pins")
        overrides = bank.get("overrides") or {}
        bank_iostd = bank.get("iostandard") or default_iostd

        if isinstance(pins, str):
            out.extend(_qsf_lines(pins, bank_name, _pin_iostd(pins, overrides, bank_iostd)))
        elif isinstance(pins, list):
            for i, p in enumerate(pins):
                if p is None:
                    continue
                port = "{}[{}]".format(bank_name, i)
                out.extend(_qsf_lines(p, port, _pin_iostd(p, overrides, bank_iostd)))
        elif isinstance(pins, dict):
            for sub, val in pins.items():
                pname = "{}_{}".format(bank_name, sub)
                if isinstance(val, list):
                    for i, p in enumerate(val):
                        if p is None:
                            continue
                        port = "{}[{}]".format(pname, i)
                        out.extend(_qsf_lines(p, port, _pin_iostd(p, overrides, bank_iostd)))
                elif isinstance(val, str):
                    out.extend(_qsf_lines(val, pname, _pin_iostd(val, overrides, bank_iostd)))

    out.append("")
    return "\n".join(out) + "\n"


def _qsf_lines(pin, port_expr, iostd):
    pin_str = str(pin)
    if "," in pin_str:
        return ["# WARNING: pin '{}' for port '{}' is a differential pair (skipped)".format(pin_str, port_expr)]
    return [
        "set_location_assignment PIN_{pin} -to {port}".format(pin=pin_str, port=port_expr),
        'set_instance_assignment -name IO_STANDARD "{std}" -to {port}'.format(std=iostd, port=port_expr),
    ]


def emit_sdc(resolved):
    """Emit an SDC timing constraints file. Currently just create_clock entries
    for each clock-providing peripheral. Used by Quartus, Gowin EDA, and
    Efinity. The trailing `derive_pll_clocks` / `derive_clock_uncertainty`
    are Quartus-specific Tcl commands the others reject — gated on the
    toolchain id."""
    cfg = resolved["configuration"]
    toolchain_id = (resolved.get("toolchain") or {}).get("Id", "")
    plans = build_capability_plans(resolved)

    out = []
    out.append("# =============================================================================")
    out.append("# Auto-generated SDC timing — DO NOT EDIT")
    out.append("# Configuration: {}".format(cfg["id"]))
    out.append("# =============================================================================")
    out.append("")

    clock = resolve_clock(resolved, plans)
    if clock is not None and clock["mhz"] is not None:
        out.append(
            "create_clock -name sys_clk_{f}mhz -period {p:.3f} "
            "[get_ports {{{port}}}]".format(f=_clk_mhz_int(clock), p=_clock_period_ns(clock),
                                            port=clock["port"])
        )
    else:
        out.append("# WARNING: no clock frequency known for this configuration (CLK-FREQ)")
    # PLL clocks forwarded to output pads (LCD pixel clock): constrain the pad
    # the way BGM's board_specific.sdc does (`create_clock -name LARGE_LCD_CK ...`).
    for port, name, mhz in clock_driven_pins(resolved):
        out.append("create_clock -name {name} -period {p:.3f} [get_ports {{{port}}}]".format(
            name=re.sub(r"\W+", "_", port).strip("_"), port=port, p=1000.0 / mhz))

    if toolchain_id.startswith("quartus"):
        out.append("derive_pll_clocks -create_base_clocks")
        out.append("derive_clock_uncertainty")
    out.append("")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# CST / SDC constraint emission (Gowin EDA)
# ---------------------------------------------------------------------------

# Map default IOSTANDARD strings between vendor dialects. Gowin's CST uses
# its own IO_TYPE keywords (LVCMOS33, LVDS25, etc.) — most of our pinmaps
# already store the Gowin form for Gowin boards, but normalize just in case.
_GOWIN_IOTYPE = {
    "LVCMOS33":      "LVCMOS33",
    "LVCMOS25":      "LVCMOS25",
    "LVCMOS18":      "LVCMOS18",
    "LVCMOS15":      "LVCMOS15",
    "LVCMOS12":      "LVCMOS12",
    "LVDS25":        "LVDS25",
    "3.3-V LVTTL":   "LVCMOS33",
    "LVTTL":         "LVCMOS33",
}


def emit_cst(resolved):
    """Emit a Gowin CST physical constraints file. IO_LOC for pin numbers and
    IO_PORT for IO_TYPE / drive strength."""
    cfg = resolved["configuration"]
    pinmap = resolved["board_pinmap"]
    # No IO_TYPE unless the pinmap states one (BGM's Gowin CSTs constrain
    # only IO_LOC on most boards; the tool then keeps its defaults and the
    # bank voltages the embedded functions dictate. An invented LVCMOS33 on
    # the Tang Nano 9K's 1.8 V bank 3 is refused with CT1136.)
    default_raw = (pinmap.get("defaults") or {}).get("iostandard")
    default_iotype = _GOWIN_IOTYPE.get(default_raw, default_raw) if default_raw else None

    out = []
    out.append("// =============================================================================")
    out.append("// Auto-generated CST constraints — DO NOT EDIT")
    out.append("// Configuration: {}".format(cfg["id"]))
    out.append("// =============================================================================")
    out.append("")

    referenced = collect_referenced_banks(resolved)
    # Differential pairs: Gowin EDA locates a true/emulated LVDS pair through
    # the P port alone (`IO_LOC "TMDS_CLK_P" 69,68;`, BGM) and infers the N
    # port from the buffer; the open flow (apicula) wants both halves as
    # plain IO_LOCs (BGM's _yosys variants); pseudo-differential outputs are
    # two ordinary LVCMOS pins.
    pair_style = "pair"
    kind = diff_buf_kind(resolved)
    if resolved["toolchain"]["Id"].startswith("nextpnr_"):
        pair_style = "split"
    elif kind == "generic":
        pair_style = "lvcmos"
    pair_n = _pair_n_pins(pinmap, referenced)

    def lines_for(p, port, iot, explicit):
        if isinstance(p, str) and "," in p:
            if explicit and kind == "gowin_elvds" and pair_style == "pair" and re.match(r"^LVCMOS\d+$", str(iot)):
                # ELVDS_OBUF: emulated differential on the bank's own voltage
                iot = iot + "D"
            return _cst_pair_lines(p, port, iot, explicit, pair_style)
        if str(p) in pair_n:
            if pair_style == "pair":
                return ['// "{}" is the N half of the pair on {}'.format(port, pair_n[str(p)])]
            if pair_style == "split":
                return ['IO_LOC  "{}" {};'.format(port, p)]
        if not iot:
            return ['IO_LOC  "{}" {};'.format(port, p)]
        return _cst_lines(p, port, iot)

    for bank_name in referenced:
        bank = (pinmap.get("pinBanks") or {}).get(bank_name)
        if bank is None:
            out.append("// WARNING: bank '{}' referenced but not in pinBanks".format(bank_name))
            continue
        pins = bank.get("pins")
        overrides = bank.get("overrides") or {}
        explicit = bool(bank.get("iostandard"))
        bank_iotype = (_GOWIN_IOTYPE.get(bank["iostandard"], bank["iostandard"]) if explicit
                       else default_iotype)

        if isinstance(pins, str):
            out.extend(lines_for(pins, bank_name, _pin_iostd(pins, overrides, bank_iotype),
                                 explicit or pins in overrides))
        elif isinstance(pins, list):
            for i, p in enumerate(pins):
                if p is None:
                    continue
                port = "{}[{}]".format(bank_name, i)
                out.extend(lines_for(p, port, _pin_iostd(p, overrides, bank_iotype), explicit or p in overrides))
        elif isinstance(pins, dict):
            for sub, val in pins.items():
                pname = "{}_{}".format(bank_name, sub)
                if isinstance(val, list):
                    for i, p in enumerate(val):
                        if p is None:
                            continue
                        port = "{}[{}]".format(pname, i)
                        out.extend(lines_for(p, port, _pin_iostd(p, overrides, bank_iotype),
                                             explicit or p in overrides))
                elif isinstance(val, str):
                    out.extend(lines_for(val, pname, _pin_iostd(val, overrides, bank_iotype),
                                         explicit or val in overrides))

    out.append("")
    return "\n".join(out)


def _cst_pair_lines(pin, port_expr, iotype, explicit, style):
    p, n = [x.strip() for x in str(pin).split(",", 1)]
    iot = _GOWIN_IOTYPE.get(iotype, iotype)
    if style == "split":
        return ['IO_LOC  "{}" {};'.format(port_expr, p)]
    if style == "lvcmos":
        return ['IO_LOC  "{}" {};'.format(port_expr, p)] + (
            ['IO_PORT "{}" IO_TYPE={};'.format(port_expr, iot)] if iot else [])
    lines = ['IO_LOC  "{}" {},{};'.format(port_expr, p, n)]
    if explicit and iot:      # e.g. marsohod3gw2: IO_TYPE=LVCMOS18D on the pair
        lines.append('IO_PORT "{}" IO_TYPE={};'.format(port_expr, iot))
    return lines


def _cst_lines(pin, port_expr, iotype):
    pin_str = str(pin)
    if "," in pin_str:
        return _cst_pair_lines(pin_str, port_expr, iotype, False, "pair")
    iotype_resolved = _GOWIN_IOTYPE.get(iotype, iotype)
    return [
        'IO_LOC  "{port}" {pin};'.format(port=port_expr, pin=pin_str),
        'IO_PORT "{port}" IO_TYPE={iot};'.format(port=port_expr, iot=iotype_resolved),
    ]


# ---------------------------------------------------------------------------
# LPF constraint emission (nextpnr-trellis — Lattice ECP5)
# ---------------------------------------------------------------------------

def emit_lpf(resolved):
    """Emit a Lattice Physical Constraint (.lpf) file for nextpnr-ecp5.
    Format per port:
        LOCATE COMP "<port>" SITE "<pin>";
        IOBUF PORT "<port>" IO_TYPE=<iotype>;
    Indexed bus elements use `port[idx]` syntax."""
    cfg = resolved["configuration"]
    pinmap = resolved["board_pinmap"]
    default_iotype = (pinmap.get("defaults") or {}).get("iostandard") or "LVCMOS33"
    # Normalize Gowin/Quartus-style iostandard names to ECP5/Lattice names.
    iotype_map = {
        "3.3-V LVTTL": "LVCMOS33",
        "LVTTL":       "LVCMOS33",
        "LVCMOS33":    "LVCMOS33",
        "LVCMOS25":    "LVCMOS25",
        "LVCMOS18":    "LVCMOS18",
        "LVCMOS15":    "LVCMOS15",
        "LVCMOS12":    "LVCMOS12",
    }
    default_iotype = iotype_map.get(default_iotype, default_iotype)

    out = []
    out.append("# =============================================================================")
    out.append("# Auto-generated LPF constraints — DO NOT EDIT")
    out.append("# Configuration: {}".format(cfg["id"]))
    out.append("# =============================================================================")
    out.append("")

    referenced = collect_referenced_banks(resolved)

    for bank_name in referenced:
        bank = (pinmap.get("pinBanks") or {}).get(bank_name)
        if bank is None:
            out.append("# WARNING: bank '{}' referenced but not in pinBanks".format(bank_name))
            continue
        pins = bank.get("pins")
        overrides = bank.get("overrides") or {}
        bank_iotype = iotype_map.get(bank.get("iostandard"), default_iotype)

        if isinstance(pins, str):
            out.extend(_lpf_lines(pins, bank_name, _pin_iostd(pins, overrides, bank_iotype), iotype_map))
        elif isinstance(pins, list):
            for i, p in enumerate(pins):
                if p is None:
                    continue
                port = "{}[{}]".format(bank_name, i)
                out.extend(_lpf_lines(p, port, _pin_iostd(p, overrides, bank_iotype), iotype_map))
        elif isinstance(pins, dict):
            for sub, val in pins.items():
                pname = "{}_{}".format(bank_name, sub)
                if isinstance(val, list):
                    for i, p in enumerate(val):
                        if p is None:
                            continue
                        port = "{}[{}]".format(pname, i)
                        out.extend(_lpf_lines(p, port, _pin_iostd(p, overrides, bank_iotype), iotype_map))
                elif isinstance(val, str):
                    out.extend(_lpf_lines(val, pname, _pin_iostd(val, overrides, bank_iotype), iotype_map))

    # Clock frequency for nextpnr-ecp5's timing analysis.
    out.append("")
    clock = resolve_clock(resolved)
    if clock is not None and clock["mhz"] is not None:
        out.append('FREQUENCY PORT "{port}" {f:g} MHZ;'.format(port=clock["port"], f=clock["mhz"]))
    else:
        out.append("# WARNING: no clock frequency known for this configuration (CLK-FREQ)")

    out.append("")
    return "\n".join(out)


def _lpf_lines(pin, port_expr, iotype, iotype_map):
    pin_str = str(pin)
    if "," in pin_str:
        return ['# WARNING: pin "{}" for port "{}" is a differential pair (skipped)'.format(pin_str, port_expr)]
    iotype_resolved = iotype_map.get(iotype, iotype)
    return [
        'LOCATE COMP "{port}" SITE "{pin}";'.format(port=port_expr, pin=pin_str),
        'IOBUF PORT "{port}" IO_TYPE={iot};'.format(port=port_expr, iot=iotype_resolved),
    ]


# ---------------------------------------------------------------------------
# Efinity peri.xml + project.xml emission (Efinix Trion / Titanium)
# ---------------------------------------------------------------------------

def _xml_attr(s):
    """Minimal XML-attribute escape."""
    return (str(s).replace("&", "&amp;").replace('"', "&quot;")
                  .replace("<", "&lt;").replace(">", "&gt;"))


def emit_peri_xml(resolved, device_def):
    """Emit Efinity's peripheral XML — one <efxpt:gpio> per top-level port,
    plus iobank/bus declarations and oscillator info for any virtual clock.
    Pin numbers are GPIOR_NN strings (Efinity-specific def names), not
    physical ball/pad numbers.

    `device_def`: e.g. "T8F81" — same string used in board.fpga.part."""
    cfg = resolved["configuration"]
    pinmap = resolved["board_pinmap"]
    default_iostd = (pinmap.get("defaults") or {}).get("iostandard") or "3.3 V LVTTL / LVCMOS"

    out = []
    out.append('<?xml version="1.0" encoding="UTF-8"?>')
    out.append(
        '<efxpt:design_db name="{name}" device_def="{dev}" '
        'version="2023.2.307" db_version="20232999" '
        'xmlns:efxpt="http://www.efinixinc.com/peri_design_db" '
        'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
        'xsi:schemaLocation="http://www.efinixinc.com/peri_design_db peri_design_db.xsd">'
        .format(name=_xml_attr(cfg["id"]), dev=_xml_attr(device_def))
    )

    # ---- iobank info (collected across all referenced banks) ----
    out.append("    <efxpt:device_info>")
    out.append("        <efxpt:iobank_info>")
    # T8F81 standard banks. Should match what BGM uses; if a board needs
    # something else, override via board_pinmap.iobanks.
    iobanks = (pinmap.get("iobanks") or {
        "1A": "3.3 V LVTTL / LVCMOS",
        "1B": "3.3 V LVTTL / LVCMOS",
        "1C": "1.1 V",
        "2A": "3.3 V LVTTL / LVCMOS",
        "2B": "3.3 V LVTTL / LVCMOS",
    })
    for name, iostd in iobanks.items():
        out.append('            <efxpt:iobank name="{}" iostd="{}"/>'.format(_xml_attr(name), _xml_attr(iostd)))
    out.append("        </efxpt:iobank_info>")
    out.append("    </efxpt:device_info>")

    # ---- gpio info ----
    referenced = collect_referenced_banks(resolved)
    plans = build_capability_plans(resolved)
    osc_clocks = []   # (osc-driven port_name, frequency_mhz) for the osc_info block

    out.append('    <efxpt:gpio_info device_def="{}">'.format(_xml_attr(device_def)))
    buses = []   # (bus_name, mode, msb, lsb)

    for bank_name in referenced:
        bank = (pinmap.get("pinBanks") or {}).get(bank_name)
        if bank is None:
            continue
        # Virtual oscillator-sourced clock — record it in osc_clocks
        # and skip the gpio entry.
        if bank.get("virtual"):
            if bank.get("source") == "osc":
                osc_clocks.append((bank_name, bank.get("frequency_mhz", 50)))
            continue

        pins = bank.get("pins")
        bank_iostd = bank.get("iostandard") or default_iostd
        direction = _infer_pin_direction(resolved, bank_name,
                                         None if not isinstance(pins, dict) else next(iter(pins)))
        mode = direction if direction in ("input", "output", "inout") else "input"

        if isinstance(pins, str):
            out.extend(_efxpt_gpio(bank_name, pins, mode, "", bank_iostd))
        elif isinstance(pins, list):
            buses.append((bank_name, mode, len(pins) - 1, 0))
            for i, p in enumerate(pins):
                if p is None:
                    continue
                portname = "{}[{}]".format(bank_name, i)
                out.extend(_efxpt_gpio(portname, p, mode, bank_name, bank_iostd))
        elif isinstance(pins, dict):
            for sub, val in pins.items():
                pname = "{}_{}".format(bank_name, sub)
                d = _infer_pin_direction(resolved, bank_name, sub)
                m = d if d in ("input", "output", "inout") else "input"
                if isinstance(val, list):
                    buses.append((pname, m, len(val) - 1, 0))
                    for i, p in enumerate(val):
                        if p is None:
                            continue
                        portname = "{}[{}]".format(pname, i)
                        out.extend(_efxpt_gpio(portname, p, m, pname, bank_iostd))
                elif isinstance(val, str):
                    out.extend(_efxpt_gpio(pname, val, m, "", bank_iostd))

    # Default unused-pin policy (same as BGM)
    out.append('        <efxpt:global_unused_config state="input with weak pullup"/>')

    for name, mode, msb, lsb in buses:
        out.append('        <efxpt:bus name="{}" mode="{}" msb="{}" lsb="{}"/>'
                   .format(_xml_attr(name), _xml_attr(mode), msb, lsb))
    out.append("    </efxpt:gpio_info>")

    # ---- oscillator info for virtual clocks ----
    out.append("    <efxpt:pll_info/>")
    if osc_clocks:
        out.append("    <efxpt:osc_info>")
        for clock_port, _freq in osc_clocks:
            out.append('        <efxpt:osc name="osc_inst1" osc_def="OSC_0" clock_name="{}"/>'
                       .format(_xml_attr(clock_port)))
        out.append("    </efxpt:osc_info>")
    else:
        out.append("    <efxpt:osc_info/>")
    out.append("    <efxpt:jtag_info/>")
    out.append("</efxpt:design_db>")
    return "\n".join(out) + "\n"


def _efxpt_gpio(port_name, gpio_def, mode, bus_name, iostd):
    """Render one <efxpt:gpio> element with its nested input_config /
    output_config / inout_config child."""
    lines = ['        <efxpt:gpio name="{}" gpio_def="{}" mode="{}" bus_name="{}" '
             'is_lvds_gpio="false" io_standard="{}">'.format(
                _xml_attr(port_name), _xml_attr(gpio_def), _xml_attr(mode),
                _xml_attr(bus_name), _xml_attr(iostd))]
    if mode == "input":
        lines.append('            <efxpt:input_config name="{}" name_ddio_lo="" '
                     'conn_type="normal" is_register="false" clock_name="" '
                     'is_clock_inverted="false" pull_option="weak pullup" '
                     'is_schmitt_trigger="false" ddio_type="none"/>'.format(_xml_attr(port_name)))
    elif mode == "output":
        lines.append('            <efxpt:output_config name="{}" name_ddio_lo="" '
                     'register_option="none" clock_name="" is_clock_inverted="false" '
                     'is_slew_rate="false" tied_option="none" ddio_type="none" '
                     'drive_strength="1"/>'.format(_xml_attr(port_name)))
    else:  # inout
        lines.append('            <efxpt:inout_config name="{}" name_ddio_lo="" '
                     'conn_type="normal" pull_option="weak pullup"/>'
                     .format(_xml_attr(port_name)))
    lines.append("        </efxpt:gpio>")
    return lines


def emit_efx_project_xml(resolved, device_def, sv_files, sdc_path, peri_path,
                         project_name="unifpga_top"):
    """Emit Efinity's project XML wrapper. Lists every SV/V source, plus the
    SDC and peri XML, and the standard synth/pnr/bitstream parameters."""
    cfg = resolved["configuration"]
    family = (resolved["board"].get("PartFamily") or "Trion").strip()
    out = []
    out.append('<?xml version="1.0" encoding="UTF-8"?>')
    out.append(
        '<efx:project xmlns:efx="http://www.efinixinc.com/enf_proj" '
        'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
        'name="{name}" description="{name}" sw_version="2023.2.307" '
        'xsi:schemaLocation="http://www.efinixinc.com/enf_proj enf_proj.xsd">'
        .format(name=_xml_attr(project_name))
    )
    out.append("    <efx:device_info>")
    out.append('        <efx:family name="{}"/>'.format(_xml_attr(family)))
    out.append('        <efx:device name="{}"/>'.format(_xml_attr(device_def)))
    out.append('        <efx:timing_model name="C2"/>')
    out.append("    </efx:device_info>")
    out.append('    <efx:design_info def_veri_version="sv_09" def_vhdl_version="vhdl_2008">')
    out.append('        <efx:top_module name="top"/>')
    for sv in sv_files:
        ext = "system_verilog" if sv.endswith((".sv", ".svh")) else "verilog"
        out.append('        <efx:design_file name="{}" version="{}" library="default"/>'.format(
            _xml_attr(sv), ext))
    out.append('        <efx:top_vhdl_arch name=""/>')
    out.append("    </efx:design_info>")
    out.append("    <efx:constraint_info>")
    out.append('        <efx:sdc_file name="{}"/>'.format(_xml_attr(sdc_path)))
    out.append('        <efx:inter_file name=""/>')
    out.append("    </efx:constraint_info>")
    out.append("    <efx:sim_info/>")
    out.append("    <efx:misc_info/>")
    out.append('    <efx:synthesis tool_name="efx_map">')
    out.append('        <efx:param name="write_efx_verilog" value="on" value_type="e_bool"/>')
    out.append('        <efx:param name="opt_mode" value="speed" value_type="e_option"/>')
    out.append("    </efx:synthesis>")
    out.append('    <efx:place_and_route tool_name="efx_pnr">')
    out.append('        <efx:param name="verbose" value="off" value_type="e_bool"/>')
    out.append('        <efx:param name="load_delaym" value="on" value_type="e_bool"/>')
    out.append("    </efx:place_and_route>")
    out.append('    <efx:bitstream_generation tool_name="efx_pgm">')
    out.append('        <efx:param name="mode" value="active" value_type="e_option"/>')
    out.append('        <efx:param name="width" value="1" value_type="e_option"/>')
    out.append('        <efx:param name="oscillator_clock_divider" value="DIV8" value_type="e_option"/>')
    out.append('        <efx:param name="enable_roms" value="on" value_type="e_option"/>')
    out.append('        <efx:param name="io_weak_pullup" value="on" value_type="e_bool"/>')
    out.append("    </efx:bitstream_generation>")
    out.append("</efx:project>")
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------------------
# PCF constraint emission (nextpnr-icestorm — Lattice iCE40)
# ---------------------------------------------------------------------------

def emit_pcf(resolved):
    """Emit a PCF (Physical Constraint File) for nextpnr-ice40.
    Format: `set_io -nowarn <port> <pin>` per top-level port. Indexed bus
    elements use `port[idx]` syntax — same as XDC/QSF."""
    cfg = resolved["configuration"]
    pinmap = resolved["board_pinmap"]

    out = []
    out.append("# =============================================================================")
    out.append("# Auto-generated PCF constraints — DO NOT EDIT")
    out.append("# Configuration: {}".format(cfg["id"]))
    out.append("# =============================================================================")
    out.append("")

    referenced = collect_referenced_banks(resolved)
    plans = build_capability_plans(resolved)

    for bank_name in referenced:
        bank = (pinmap.get("pinBanks") or {}).get(bank_name)
        if bank is None:
            out.append("# WARNING: bank '{}' referenced but not in pinBanks".format(bank_name))
            continue
        pins = bank.get("pins")

        if isinstance(pins, str):
            out.append("set_io -nowarn {} {}".format(bank_name, pins))
        elif isinstance(pins, list):
            for i, p in enumerate(pins):
                if p is None:
                    continue
                out.append("set_io -nowarn {}[{}] {}".format(bank_name, i, p))
        elif isinstance(pins, dict):
            for sub, val in pins.items():
                pname = "{}_{}".format(bank_name, sub)
                if isinstance(val, list):
                    for i, p in enumerate(val):
                        if p is None:
                            continue
                        out.append("set_io -nowarn {}[{}] {}".format(pname, i, p))
                elif isinstance(val, str):
                    out.append("set_io -nowarn {} {}".format(pname, val))

    # Frequency hint for the timing analyzer.
    out.append("")
    clock = resolve_clock(resolved, plans)
    if clock is not None and clock["mhz"] is not None:
        out.append("set_frequency {} {:g}".format(clock["port"], clock["mhz"]))
    else:
        out.append("# WARNING: no clock frequency known for this configuration (CLK-FREQ)")
    for port, name, mhz in clock_driven_pins(resolved):
        out.append("set_frequency {} {:g}".format(port, mhz))

    out.append("")
    return "\n".join(out)


def emit_microchip_pdc(resolved):
    """Emit a Microchip Libero IO PDC file (set_io syntax).

    Libero's IO PDC uses TCL `set_io` commands with `-pinname`, `-direction`,
    and `-fixed yes` to lock placement:

        set_io {port}    -pinname AB12  -fixed yes  -direction Input
        set_io {led[0]}  -pinname C5    -fixed yes  -direction Output

    Distinct from the *other* PDC dialect (`ldc_set_location` for Lattice
    Nexus) — Microchip Libero rejects ldc_set_location and Lattice nextpnr
    rejects this dialect. Each vendor has its own constraint flavour even
    when the file extension is the same.
    """
    cfg = resolved["configuration"]
    pinmap = resolved["board_pinmap"]
    board = resolved.get("board") or {}

    out = []
    out.append("# =============================================================================")
    out.append("# Auto-generated Microchip Libero IO PDC — DO NOT EDIT")
    out.append("# Configuration: {}".format(cfg["id"]))
    out.append("# Board:         {}".format(board.get("BoardName", "")))
    out.append("# =============================================================================")
    out.append("")

    referenced = collect_referenced_banks(resolved)

    _LIBERO_DIR = {"input": "Input", "output": "Output", "inout": "Inout"}

    def _dir(bank_name, subkey):
        d = _infer_pin_direction(resolved, bank_name, subkey)
        if d is None:
            d = "input" if "clk" in bank_name.lower() else "output"
        return _LIBERO_DIR.get(d, "Output")

    def _emit(port, pad, direction):
        out.append('set_io {{{port}}} -pinname {pad} -fixed yes -direction {dir}'
                   .format(port=port, pad=pad, dir=direction))

    for bank_name in referenced:
        bank = (pinmap.get("pinBanks") or {}).get(bank_name)
        if bank is None:
            out.append("# WARNING: bank '{}' referenced but not in pinBanks".format(bank_name))
            continue
        pins = bank.get("pins")

        if isinstance(pins, str):
            _emit(bank_name, pins, _dir(bank_name, None))
        elif isinstance(pins, list):
            d = _dir(bank_name, None)
            for i, p in enumerate(pins):
                if p is None:
                    continue
                _emit("{}[{}]".format(bank_name, i), p, d)
        elif isinstance(pins, dict):
            for sub, val in pins.items():
                pname = "{}_{}".format(bank_name, sub)
                d = _dir(bank_name, sub)
                if isinstance(val, list):
                    for i, p in enumerate(val):
                        if p is None:
                            continue
                        _emit("{}[{}]".format(pname, i), p, d)
                elif isinstance(val, str):
                    _emit(pname, val, d)

    out.append("")
    return "\n".join(out)


def emit_pdc(resolved):
    """Emit a PDC (Physical Design Constraints) file for nextpnr-nexus.

    nextpnr-nexus's PDC parser accepts a TCL-flavoured subset of Lattice
    Diamond / Radiant constraints:

        ldc_set_location -site "<pad>" [get_ports <port>]
        ldc_set_port -iobuf {IO_TYPE=<std>} [get_ports <port>]
        create_clock -period <ns> [get_ports <clk_port>]

    Indexed bus elements use the same `port[idx]` convention as XDC/QSF.
    """
    cfg = resolved["configuration"]
    pinmap = resolved["board_pinmap"]
    board = resolved.get("board") or {}
    iostd = (pinmap.get("defaults") or {}).get("iostandard", "LVCMOS33")
    plans = build_capability_plans(resolved)

    out = []
    out.append("# =============================================================================")
    out.append("# Auto-generated PDC constraints — DO NOT EDIT")
    out.append("# Configuration: {}".format(cfg["id"]))
    out.append("# Board:         {}".format(board.get("BoardName", "")))
    out.append("# =============================================================================")
    out.append("")

    referenced = collect_referenced_banks(resolved)

    def _emit(port, pad):
        out.append('ldc_set_location -site "{pad}" [get_ports {port}]'.format(pad=pad, port=port))
        out.append('ldc_set_port -iobuf {{IO_TYPE={s}}} [get_ports {port}]'.format(s=iostd, port=port))

    for bank_name in referenced:
        bank = (pinmap.get("pinBanks") or {}).get(bank_name)
        if bank is None:
            out.append("# WARNING: bank '{}' referenced but not in pinBanks".format(bank_name))
            continue
        pins = bank.get("pins")

        if isinstance(pins, str):
            _emit(bank_name, pins)
        elif isinstance(pins, list):
            for i, p in enumerate(pins):
                if p is None:
                    continue
                _emit("{{{}[{}]}}".format(bank_name, i), p)
        elif isinstance(pins, dict):
            for sub, val in pins.items():
                pname = "{}_{}".format(bank_name, sub)
                if isinstance(val, list):
                    for i, p in enumerate(val):
                        if p is None:
                            continue
                        _emit("{{{}[{}]}}".format(pname, i), p)
                elif isinstance(val, str):
                    _emit(pname, val)

    # Clock period — the parser also accepts create_clock, which keeps the
    # timing analyser honest.
    out.append("")
    clock = resolve_clock(resolved, plans)
    if clock is not None and clock["mhz"] is not None:
        out.append(
            'create_clock -period {p:.3f} -name sys_clk_{f}mhz [get_ports {{{port}}}]'
            .format(f=_clk_mhz_int(clock), p=_clock_period_ns(clock), port=clock["port"])
        )
    else:
        out.append("# WARNING: no clock frequency known for this configuration (CLK-FREQ)")

    out.append("")
    return "\n".join(out)


def emit_ccf(resolved):
    """Emit a CCF (Cologne Chip Constraints File) for nextpnr-himbaechel
    with the gatemate uarch. Format per the himbaechel ccf.cc parser:

        Pin_in   "<port>" LOC=<pad>;
        Pin_out  "<port>" LOC=<pad>;
        Pin_inout "<port>" LOC=<pad>;
        NET      "<port>" LOC=<pad>;     # equivalent to Pin_*

    Both `//` and `#` are comment markers. Lines must end with `;`. We
    keep the directive `Pin_*` (unlike the more generic NET) because the
    parser uses it for direction sanity-checking.
    """
    cfg = resolved["configuration"]
    pinmap = resolved["board_pinmap"]

    out = []
    out.append("# =============================================================================")
    out.append("# Auto-generated CCF constraints — DO NOT EDIT")
    out.append("# Configuration: {}".format(cfg["id"]))
    out.append("# Board:         {}".format((resolved.get("board") or {}).get("BoardName", "")))
    out.append("# =============================================================================")
    out.append("")

    referenced = collect_referenced_banks(resolved)

    def _emit(port, pad):
        # Pin_in vs Pin_out gets resolved by nextpnr from the netlist; the
        # generic `NET` directive matches whichever direction the cell has,
        # which is simplest to emit from a codegen that doesn't track
        # direction per port.
        out.append('NET "{port}" LOC={pad};'.format(port=port, pad=pad))

    for bank_name in referenced:
        bank = (pinmap.get("pinBanks") or {}).get(bank_name)
        if bank is None:
            out.append("# WARNING: bank '{}' referenced but not in pinBanks".format(bank_name))
            continue
        pins = bank.get("pins")

        if isinstance(pins, str):
            _emit(bank_name, pins)
        elif isinstance(pins, list):
            for i, p in enumerate(pins):
                if p is None:
                    continue
                _emit("{}[{}]".format(bank_name, i), p)
        elif isinstance(pins, dict):
            for sub, val in pins.items():
                pname = "{}_{}".format(bank_name, sub)
                if isinstance(val, list):
                    for i, p in enumerate(val):
                        if p is None:
                            continue
                        _emit("{}[{}]".format(pname, i), p)
                elif isinstance(val, str):
                    _emit(pname, val)

    out.append("")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

def generate_for(configuration_id, strict=True):
    resolved = config_init.resolve_configuration(configuration_id)
    return emit_top_sv(resolved, strict=strict)


def generate_xdc_for(configuration_id):
    resolved = config_init.resolve_configuration(configuration_id)
    return emit_xdc(resolved)


def main(argv=None):
    import argparse
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("configuration", help="Configuration id (or path to file)")
    p.add_argument("-o", "--output", help="Write to file instead of stdout")
    args = p.parse_args(argv)

    text = generate_for(args.configuration)
    if args.output:
        with open(args.output, "w") as f:
            f.write(text)
        print("Wrote {}".format(args.output))
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
