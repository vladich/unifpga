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
from collections import OrderedDict, defaultdict

import yaml

from config import init as config_init


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
    lines.append("    localparam int clk_mhz = {};".format(_clk_mhz_int(clock)))
    return lines


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
                                "active": _peripheral_active_polarity(perif, r_attach)}))
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
                         "(.clk (clk), .rst (rst_on_power_up));")
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


def _peripheral_active_polarity(perif, attach):
    """Returns 'high' or 'low'. Configuration `params:` overrides the
    peripheral YAML's `parameters.active.default`."""
    cfg_params = attach.get("params") or {}
    if "active" in cfg_params:
        return cfg_params["active"]
    pdef = (perif.get("parameters") or {}).get("active") or {}
    return pdef.get("default") or "high"


def _emit_passthrough(resolved, idx, attach, plans):
    """For peripherals with driver: null. Wire pin signals to capability slices
    or vice versa, depending on each capability's aggregation rule and the
    peripheral signal's direction. Inverts when the peripheral is active-low."""
    lines = []
    perif = attach["peripheral"]
    bind = attach.get("bind") or {}
    active = _peripheral_active_polarity(perif, attach)
    inv = "~ " if active == "low" else ""

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
                if width == 1:
                    slice_expr = "cap_{}_{}[{}]".format(cap_id, cap_sig_name, offset)
                else:
                    slice_expr = "cap_{}_{}[{}:{}]".format(cap_id, cap_sig_name,
                                                           offset+width-1, offset)
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
        return invert + s[len("context."):] + idx_suffix
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
        return invert + _sv_literal(v) + idx_suffix
    return invert + s + idx_suffix


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

    clk_mhz = _clk_mhz_int(resolve_clock(resolved, plans))

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
        "        .clk(clk)",
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
    pin_str = str(pin)
    # Quote pin names that contain commas (Gowin diff pairs) — Vivado doesn't
    # use those, but the harvested data may still carry them; warn.
    if "," in pin_str:
        return ("# WARNING: pin '{p}' for port '{port}' is a differential pair; "
                "Vivado XDC needs the pair handled in the SV (LVDS / OBUFDS).".format(
                    p=pin_str, port=port_expr))
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
    return overrides.get(pin) or default


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
    default_iotype = _GOWIN_IOTYPE.get(
        (pinmap.get("defaults") or {}).get("iostandard"), "LVCMOS33")

    out = []
    out.append("// =============================================================================")
    out.append("// Auto-generated CST constraints — DO NOT EDIT")
    out.append("// Configuration: {}".format(cfg["id"]))
    out.append("// =============================================================================")
    out.append("")

    referenced = collect_referenced_banks(resolved)

    for bank_name in referenced:
        bank = (pinmap.get("pinBanks") or {}).get(bank_name)
        if bank is None:
            out.append("// WARNING: bank '{}' referenced but not in pinBanks".format(bank_name))
            continue
        pins = bank.get("pins")
        overrides = bank.get("overrides") or {}
        bank_iotype = _GOWIN_IOTYPE.get(bank.get("iostandard"), default_iotype)

        if isinstance(pins, str):
            out.extend(_cst_lines(pins, bank_name, _pin_iostd(pins, overrides, bank_iotype)))
        elif isinstance(pins, list):
            for i, p in enumerate(pins):
                if p is None:
                    continue
                port = "{}[{}]".format(bank_name, i)
                out.extend(_cst_lines(p, port, _pin_iostd(p, overrides, bank_iotype)))
        elif isinstance(pins, dict):
            for sub, val in pins.items():
                pname = "{}_{}".format(bank_name, sub)
                if isinstance(val, list):
                    for i, p in enumerate(val):
                        if p is None:
                            continue
                        port = "{}[{}]".format(pname, i)
                        out.extend(_cst_lines(p, port, _pin_iostd(p, overrides, bank_iotype)))
                elif isinstance(val, str):
                    out.extend(_cst_lines(val, pname, _pin_iostd(val, overrides, bank_iotype)))

    out.append("")
    return "\n".join(out)


def _cst_lines(pin, port_expr, iotype):
    pin_str = str(pin)
    if "," in pin_str:
        # Gowin's differential-pair form, exactly as BGM writes it for TMDS
        # (`IO_LOC "O_TMDS_CLK_P" 33,34;` with no IO_PORT line): the P port
        # is located on the pair and the buffer type comes from the design.
        p, n = [x.strip() for x in pin_str.split(",", 1)]
        return ['IO_LOC  "{port}" {p},{n};'.format(port=port_expr, p=p, n=n)]
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
