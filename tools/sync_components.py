"""
The component set of a configuration exactly as BGM's board_specific_top.sv
wires it — `tools/sync_from_bgm.py --components`. Everything is derived from
the preprocessed active text of the BGM variant and mapped to our pinmap by
physical pin identity (like --sv-binds):

  * i2s_audio_out instances: BGM drives an I2S DAC on ~50 boards (header pins,
    LCD / camera pins used as GPIO, Pmod JB and JC at once, the on-board
    headphone DAC with `assign PA_EN = 1'b1`).
  * gpio providers from lab_top's `.gpio ( { GPIO_0, GPIO_1 } )` connection,
    in BGM's bit order (gpio_header / pmod_12pin attaches).
  * `tie:` entries for the pins BGM drives with a constant or the reset
    (`assign M_CLK = 1'b0`, `assign ARDUINO_RESET_N = ~ rst`).
  * led_bank attaches in BGM's bit order and polarity (a lost single LED,
    a reversed bank, `LEDR_N = ~ led [6]` next to plain `LED1 = led [4]`).
  * removal of attaches whose pins BGM's top never declares (an invented UART).

Idempotent: a configuration already matching BGM is reported "unchanged".
"""

import os
import re
from collections import OrderedDict

import yaml

from config import init as config_init
from tools import bgm_oracle
from tools import sync_from_bgm as sy

_LAB_LED = ("led", "lab_led")
_GPIO_PERIPHERALS = ("gpio_header", "pmod_12pin")
# attaches --components never removes as "invented": their pins are checked by
# other slices (--clock) or are not BGM ports at all
_KEEP = {"clock_input", "reset_button", "i2s_audio_out", "led_bank", "pin_tie"} | set(_GPIO_PERIPHERALS)

_REF = re.compile(r"^([A-Za-z_]\w*)(?:\.(\w+))?(?:\[(\d+)\])?$")
_CONST = {"1'b0": "0", "'0": "0", "0": "0", "1'd0": "0", "1'h0": "0",
          "1'b1": "1", "'1": "1", "1": "1", "1'd1": "1", "1'h1": "1",
          "~rst": "~rst", "!rst": "~rst", "rst": "rst"}


# ---------------------------------------------------------------- helpers

def _split_ref(ref):
    m = _REF.match(str(ref).strip().strip('"'))
    if not m:
        return None
    return m.group(1), m.group(2), (int(m.group(3)) if m.group(3) is not None else None)


def _bank_width(pinmap, bank, sub):
    pins = ((pinmap.get("pinBanks") or {}).get(bank) or {}).get("pins")
    if isinstance(pins, dict):
        pins = pins.get(sub) if sub else None
    if isinstance(pins, list):
        return len(pins)
    return 1 if isinstance(pins, str) else 0


def _pin_to_refs(pinmap):
    """Normalized pin -> every bank ref that locates it (a header pin is often
    also a named bank: iCEBreaker LEDs on the break-off Pmod, Tang Nano 9K LCD
    colours on the TMDS pairs)."""
    out = {}

    def put(p, ref):
        for key in {sy._norm_pin(p), sy._norm_pin(str(p).split(",", 1)[0])}:
            out.setdefault(key, [])
            if ref not in out[key]:
                out[key].append(ref)

    for bank, b in (pinmap.get("pinBanks") or {}).items():
        pins = (b or {}).get("pins")
        if isinstance(pins, str):
            put(pins, bank)
        elif isinstance(pins, list):
            for i, p in enumerate(pins):
                if p is not None:
                    put(p, "{}[{}]".format(bank, i))
        elif isinstance(pins, dict):
            for sub, v in pins.items():
                if isinstance(v, list):
                    for i, p in enumerate(v):
                        if p is not None:
                            put(p, "{}.{}[{}]".format(bank, sub, i))
                elif isinstance(v, str):
                    put(v, "{}.{}".format(bank, sub))
    return out


_KIND_PATTERNS = {
    "gpio":  (re.compile(r"gpio|pmod|header|arduino|hdr|conn|_j\d|^j\d|small_lcd|cam", re.I),),
    "led":   (re.compile(r"led", re.I),),
    "i2s":   (re.compile(r"headphone|audio|codec|i2s|dac|amp|speaker|sound", re.I),
              re.compile(r"gpio|pmod|header|arduino", re.I)),
    "hdmi":  (re.compile(r"hdmi|tmds|dvi", re.I),),
    "vga":   (re.compile(r"vga", re.I),),
    "clock": (re.compile(r"clk|clock|osc", re.I),),
}


class _Rev(object):
    """pin -> ref chooser: a bank the configuration already binds wins, then
    the kind's name patterns, then the pinmap order."""

    def __init__(self, pinmap, bound_banks):
        self.all = _pin_to_refs(pinmap)
        self.bound = set(bound_banks)

    def get(self, pin, kind=None):
        if pin is None:
            return None
        refs = self.all.get(sy._norm_pin(pin)) or []
        if not refs:
            return None
        for rx in _KIND_PATTERNS.get(kind, ()):
            hit = [r for r in refs if rx.search(_split_ref(r)[0])]
            if hit:
                bound = [r for r in hit if _split_ref(r)[0] in self.bound]
                return (bound or hit)[0]
        bound = [r for r in refs if _split_ref(r)[0] in self.bound]
        return (bound or refs)[0]


def _macro_body(raw_text, name):
    """Body of an object-like `define NAME ... with backslash continuations."""
    m = re.search(r"`define\s+" + re.escape(name) + r"\b[ \t]*((?:[^\n]*\\\n)*[^\n]*)", raw_text)
    if not m:
        return None
    return " ".join(m.group(1).replace("\\\n", " ").split())


def _split_top_level(body):
    out, depth, cur = [], 0, ""
    for ch in body:
        if ch in "{([":
            depth += 1
        elif ch in "})]":
            depth -= 1
        if ch == "," and depth == 0:
            out.append(cur.strip())
            cur = ""
        else:
            cur += ch
    if cur.strip():
        out.append(cur.strip())
    return out


def _bus_keys(name, sig_pins):
    """Constraint keys of a BGM port, MSB first: `NAME` for a scalar, else
    `NAME[k]` descending."""
    up = name.upper()
    if up in sig_pins:
        return [up]
    idx = sorted((int(m.group(1)) for k in sig_pins for m in [re.match(re.escape(up) + r"\[(\d+)\]$", k)] if m),
                 reverse=True)
    return ["{}[{}]".format(up, i) for i in idx]


def _element_keys(el, sig_pins, text):
    """One element of a concat -> constraint keys MSB first, or None when it
    is not a port (a dummy wire)."""
    el = el.strip()
    m = re.match(r"^([A-Za-z_]\w*)\s*(?:\[\s*(\d+)\s*(?::\s*(\d+))?\s*\])?$", el)
    if not m:
        return None
    name, hi, lo = m.group(1), m.group(2), m.group(3)
    if hi is not None:
        hi = int(hi)
        lo = int(lo) if lo is not None else hi
        return ["{}[{}]".format(name.upper(), i) for i in range(max(hi, lo), min(hi, lo) - 1, -1)]
    keys = _bus_keys(name, sig_pins)
    if keys:
        return keys
    # a wire the top assigns to a port slice: `assign GPIO_P2 [14:6] = lab_gpio;`
    am = re.search(r"\bassign\s+([A-Za-z_]\w*)\s*(\[[^\]]*\])?\s*=\s*" + re.escape(name) + r"\s*;", text)
    if am:
        return _element_keys(am.group(1) + (am.group(2) or ""), sig_pins, text)
    return None


def _keys_to_refs(keys, sig_pins, rev, kind):
    """[(ref or None, key)] in the given order."""
    out = []
    for k in keys:
        pin = sig_pins.get(k)
        out.append((rev.get(pin, kind) if pin is not None else None, k))
    return out


def _runs(refs, same=lambda a, b: True):
    """Group an LSB-first ref list into runs of one bank/sub-key with indices
    ascending by one (or descending by one). Each run:
    {"bank", "sub", "idx": [..], "refs": [..], "items": [..]}; `same(a, b)`
    adds a caller condition (equal polarity)."""
    runs = []
    for item in refs:
        ref = item[0] if isinstance(item, tuple) else item
        parts = _split_ref(ref) if ref else None
        if parts is None:
            runs.append({"bank": None, "sub": None, "idx": [None], "refs": [ref], "items": [item]})
            continue
        bank, sub, idx = parts
        last = runs[-1] if runs else None
        if (last and last["bank"] == bank and last["sub"] == sub and idx is not None
                and last["idx"][-1] is not None and same(last["items"][-1], item)):
            last["idx"].append(idx)
            last["refs"].append(ref)
            last["items"].append(item)
        else:
            runs.append({"bank": bank, "sub": sub, "idx": [idx], "refs": [ref], "items": [item]})
    return runs


def _run_bind(run, pinmap):
    """(bind ref, mirror) for a run: the whole bank when the run covers it in
    order (reversed -> mirror), else the explicit ref list."""
    n = len(run["refs"])
    base = run["bank"] + ("." + run["sub"] if run["sub"] else "")
    width = _bank_width(pinmap, run["bank"], run["sub"])
    if run["idx"] == [None]:
        return base, False
    if n == width and run["idx"] == list(range(n)):
        return base, False
    if n == width and run["idx"] == list(range(n - 1, -1, -1)):
        return base, True
    return list(run["refs"]), False


# ---------------------------------------------------------------- gpio

def gpio_elements(text, raw_text, defines=None):
    """Elements of lab_top's `.gpio ( ... )` connection, MSB first; [] for an
    empty connection; None when lab_top is not instantiated. A macro is taken
    from the preprocessor's active-branch table (`defines`) first: BGM's
    iCEBreaker defines USER_GPIO differently with and without the TM1638."""
    insts = bgm_oracle.instantiations(text, "lab_top")
    if not insts:
        return None
    expr = next((e for p, e in insts[0]["ports"] if p == "gpio"), None)
    if expr is None or not expr.strip():
        return []
    expr = expr.strip()
    if expr.startswith("`"):
        name = expr[1:].strip()
        body = (defines or {}).get(name)
        if body is None:
            body = _macro_body(raw_text, name)
        if body is None:
            return None
        expr = body.strip()
        if not expr:
            return []
    if expr.startswith("{") and expr.endswith("}"):
        return _split_top_level(expr[1:-1])
    return [expr]


def gpio_bits(text, raw_text, sig_pins, rev, defines=None):
    """LSB-first [(ref, key)] of BGM's gpio bus, plus notes. None when the
    variant has no lab_top."""
    elements = gpio_elements(text, raw_text, defines)
    if elements is None:
        return None, ["no lab_top instantiation"]
    keys, notes = [], []
    for el in elements:
        ek = _element_keys(el, sig_pins, text)
        if ek is None:
            notes.append("gpio element {!r} is not a port (dropped; numbering shifts)".format(el))
            continue
        keys.extend(ek)
    bits = _keys_to_refs(keys, sig_pins, rev, "gpio")
    for ref, k in bits:
        if ref is None:
            notes.append("gpio {}: pin {} not in the pinmap".format(k, sig_pins.get(k)))
    return list(reversed([b for b in bits if b[0] is not None])), notes


def gpio_attaches(bits, pinmap):
    """[(peripheral id, params, bind)] LSB-first for BGM's gpio bus."""
    out = []
    for run in _runs(bits):
        if run["bank"] is None:
            continue
        ref, mirror = _run_bind(run, pinmap)
        n = len(run["refs"])
        if isinstance(ref, str) and run["bank"].startswith("pmod") and n == 8 and not mirror:
            out.append(("pmod_12pin", {}, {"io": ref}))
        else:
            params = {"width": n}
            if mirror:
                params["mirror"] = True
            out.append(("gpio_header", params, {"io": ref}))
    return out


# ---------------------------------------------------------------- I2S

def i2s_attaches(text, sig_pins, rev):
    """[(binds, notes)] for every i2s_audio_out BGM instantiates; PA_EN goes
    to the first instance (the on-board headphone DAC)."""
    out = []
    for inst in bgm_oracle.instantiations(text, "i2s_audio_out"):
        binds, notes = {}, []
        for port, expr in inst["ports"]:
            if port not in ("mclk", "bclk", "lrclk", "sdata") or not expr.strip():
                continue
            m = sy._EXPR.match(expr.strip())
            if not m:
                notes.append("{}: expression {!r} not a port".format(port, expr))
                continue
            key = m.group(1).upper() + ("[{}]".format(m.group(2)) if m.group(2) is not None else "")
            pin = sig_pins.get(key)
            ref = rev.get(pin, "i2s") if pin is not None else None
            if ref is None:
                notes.append("{}: {} has no pin in the pinmap".format(port, key))
                continue
            binds[port] = ref
        if binds:
            out.append((binds, notes))
    if out:
        for port, expr in bgm_oracle.port_assigns(text).items():
            if port.upper() == "PA_EN" and _CONST.get(expr.replace(" ", "")) == "1":
                pin = sig_pins.get("PA_EN")
                ref = rev.get(pin, "i2s") if pin is not None else None
                if ref:
                    out[0][0]["pa_en"] = ref
    return out


def _port_ref(inst, port, sig_pins, rev, kind):
    expr = next((e for p, e in inst["ports"] if p == port), "").strip()
    m = sy._EXPR.match(expr) if expr else None
    if not m:
        return None
    key = m.group(1).upper() + ("[{}]".format(m.group(2)) if m.group(2) is not None else "")
    pin = sig_pins.get(key)
    return rev.get(pin, kind) if pin is not None else None


def wm8731_attach(i2s_list, text, sig_pins, rev):
    """When BGM instantiates Terasic's I2C_AUDIO_Config next to an
    i2s_audio_out on the codec lines, that instance becomes one wm8731_i2s_out
    attach. Returns (remaining i2s list, wm8731 binds or None)."""
    insts = bgm_oracle.instantiations(text, "I2C_AUDIO_Config")
    if not insts:
        return i2s_list, None
    sclk = _port_ref(insts[0], "I2C_SCLK", sig_pins, rev, "i2s")
    sdat = _port_ref(insts[0], "I2C_SDAT", sig_pins, rev, "i2s")
    for i, (binds, _notes) in enumerate(i2s_list):
        bank = _split_ref(binds.get("bclk", ""))
        if bank and re.search(r"codec|aud", bank[0], re.I) and sclk and sdat:
            wm = {"xck": binds.get("mclk"), "bclk": binds["bclk"], "dac_lrck": binds["lrclk"],
                  "dac_dat": binds["sdata"], "i2c_sclk": sclk, "i2c_sdat": sdat}
            return i2s_list[:i] + i2s_list[i + 1:], {k: v for k, v in wm.items() if v}
    return i2s_list, None


def adv7513_attach(text, sig_pins, rev):
    """(params, binds) for BGM's parallel ADV7513 transmitter: `assign
    HDMI_TX_CLK = pixel_clk` plus an I2C_HDMI_Config / I2C_Config instance."""
    conf = bgm_oracle.instantiations(text, "I2C_HDMI_Config") or bgm_oracle.instantiations(text, "I2C_Config")
    assigns = bgm_oracle.port_assigns(text)
    if not conf or "HDMI_TX_CLK" not in assigns:
        return None
    binds = {}
    for sig, port in (("clk", "HDMI_TX_CLK"), ("de", "HDMI_TX_DE"), ("hs", "HDMI_TX_HS"), ("vs", "HDMI_TX_VS")):
        ref = rev.get(sig_pins.get(port), "hdmi")
        if ref:
            binds[sig] = ref
    d = [rev.get(sig_pins.get("HDMI_TX_D[{}]".format(i)), "hdmi") for i in range(24)]
    if all(d):
        binds["d"] = d
        # collapse to the whole sub-bank when the 24 refs are bank.sub[0..23]
        m0 = re.match(r"^(.*)\[0\]$", d[0])
        if m0 and all(x == "{}[{}]".format(m0.group(1), i) for i, x in enumerate(d)):
            binds["d"] = m0.group(1)
    for sig, port in (("i2c_scl", "I2C_SCLK"), ("i2c_sda", "I2C_SDAT"), ("int", "HDMI_TX_INT")):
        ref = _port_ref(conf[0], port, sig_pins, rev, "hdmi")
        if ref:
            binds[sig] = ref
    table = "de10_nano" if bgm_oracle.instantiations(text, "I2C_HDMI_Config") else "c5gx"
    params = OrderedDict()
    if table != "de10_nano":
        params["config_table"] = table
    return params, binds


# ---------------------------------------------------------------- ties

def tie_entries(text, sig_pins, rev, exclude_pins):
    """{ref: value} for scalar ports BGM assigns a constant or the reset,
    skipping pins another attach owns."""
    out = OrderedDict()
    for port, expr in bgm_oracle.port_assigns(text).items():
        v = _CONST.get(expr.replace(" ", ""))
        if v is None:
            continue
        pin = sig_pins.get(port.upper())
        ref = rev.get(pin) if pin is not None else None
        if ref is None or sy._norm_pin(pin) in exclude_pins:
            continue
        out[ref] = v
    return out


# ---------------------------------------------------------------- LEDs

_LED_RHS = re.compile(r"^(~?)\s*(?:\w+'\s*\(\s*)?(~?)\s*(led|lab_led)\s*(?:\[\s*(\d+)\s*(?::\s*(\d+))?\s*\])?\s*\)?$")


def led_bits(text, sig_pins, rev):
    """LSB-first [(ref, inverted)] of BGM's lab `led` bus, with notes. None
    when the top does not route `led` to ports at all."""
    by_bit, notes = {}, []
    ports = bgm_oracle.top_ports(text)

    def put(bit, key, inv):
        if key in sig_pins:
            by_bit[bit] = (key, inv)
        else:
            notes.append("led[{}] -> {}: no pin in the constraint files".format(bit, key))

    for m in bgm_oracle._ASSIGN_ANY.finditer(text):
        lhs, rhs = m.group(1).strip(), " ".join(m.group(2).split())
        r = _LED_RHS.match(rhs)
        if not r:
            continue
        inv = bool(r.group(1) or r.group(2))
        hi, lo = r.group(4), r.group(5)
        if lhs.startswith("{"):
            keys = []
            for el in _split_top_level(lhs[1:-1]):
                keys.extend(_element_keys(el, sig_pins, text) or [])
            base = int(lo) if lo is not None else 0
            for i, key in enumerate(reversed(keys)):
                put(base + i, key, inv)
            continue
        lm = re.match(r"^([A-Za-z_]\w*)\s*(?:\[\s*(\d+)\s*\])?$", lhs)
        if not lm or lm.group(1) not in ports:
            continue
        name, idx = lm.group(1), lm.group(2)
        if hi is not None and lo is None:                       # led [j]
            key = name.upper() + ("[{}]".format(idx) if idx is not None else "")
            if idx is None and name.upper() not in sig_pins:
                keys = _bus_keys(name, sig_pins)
                if len(keys) == 1:
                    key = keys[0]
            put(int(hi), key, inv)
        else:                                                   # whole bus or slice
            keys = _bus_keys(name, sig_pins) if idx is None else [name.upper() + "[{}]".format(idx)]
            base = int(lo) if lo is not None else 0
            for i, key in enumerate(reversed(keys)):
                put(base + i, key, inv)
    # `SWAP_BITS (LED, ~ lab_led)`: LED[k] = led[W-1-k]
    for m in re.finditer(r"SWAP_BITS\s*\(\s*([A-Za-z_]\w*)\s*,\s*(~?)\s*(?:led|lab_led)\s*\)", text):
        keys = _bus_keys(m.group(1), sig_pins)
        for i, key in enumerate(keys):                         # keys are MSB first
            put(i, key, bool(m.group(2)))
    # direct `.led ( LED )` connection of a port
    for inst in bgm_oracle.instantiations(text, "lab_top"):
        expr = next((e for p, e in inst["ports"] if p == "led"), "").strip()
        if expr in ports and not by_bit:
            for i, key in enumerate(reversed(_bus_keys(expr, sig_pins))):
                put(i, key, False)
    if not by_bit:
        return None, notes
    n = max(by_bit) + 1
    bits = []
    for i in range(n):
        if i not in by_bit:
            notes.append("led[{}] drives no port".format(i))
            bits.append((None, False))
            continue
        key, inv = by_bit[i]
        ref = rev.get(sig_pins[key], "led")
        if ref is None:
            notes.append("led[{}] -> {}: pin {} not in the pinmap".format(i, key, sig_pins[key]))
        bits.append((ref, inv))
    return bits, notes


def led_attaches(bits, pinmap):
    """[(params, bind)] led_bank attaches, LSB-first, from [(ref, inverted)]."""
    out = []
    for run in _runs([b for b in bits if b[0] is not None], same=lambda a, b: a[1] == b[1]):
        if run["bank"] is None:
            continue
        ref, mirror = _run_bind(run, pinmap)
        inv = run["items"][0][1]
        params = OrderedDict([("width", len(run["refs"]))])
        bank_active = ((pinmap.get("pinBanks") or {}).get(run["bank"]) or {}).get("active")
        want = "low" if inv else "high"
        if (bank_active or "high") != want:
            params["active"] = want
        bank_mirror = bool(((pinmap.get("pinBanks") or {}).get(run["bank"]) or {}).get("mirror"))
        if mirror != bank_mirror:
            params["mirror"] = mirror
        out.append((params, {"led": ref}))
    return out


def _led_signature(attaches, pinmap):
    """[(pin, inverted)] LSB-first of a list of led_bank attaches (YAML dicts)."""
    sig = []
    for a in attaches:
        ref = (a.get("bind") or {}).get("led")
        params = a.get("params") or {}
        refs = ref if isinstance(ref, list) else [ref]
        pins = []
        for one in refs:
            parts = _split_ref(one)
            if parts is None:
                continue
            bank, sub, idx = parts
            vals = ((pinmap.get("pinBanks") or {}).get(bank) or {}).get("pins")
            if isinstance(vals, dict):
                vals = vals.get(sub) if sub else None
            if isinstance(vals, list):
                vals = vals if idx is None else [vals[idx]]
            else:
                vals = [vals]
            pins.extend(sy._norm_pin(v) for v in vals if v is not None)
        bank0 = _split_ref(refs[0])[0] if refs and _split_ref(refs[0]) else None
        battrs = (pinmap.get("pinBanks") or {}).get(bank0) or {}
        mirror = params.get("mirror", battrs.get("mirror", False))
        active = params.get("active", battrs.get("active", "high"))
        if mirror:
            pins = list(reversed(pins))
        sig.extend((p, active == "low") for p in pins)
    return sig


# ---------------------------------------------------------------- apply

def _render(pid, params, binds, comment, indent):
    lines = ["{}- peripheral: {}   # {}".format(indent, pid, comment)]
    if params:
        lines.append(indent + "  params:")
        for k, v in params.items():
            lines.append("{}    {}: {}".format(indent, k, "true" if v is True else v))
    lines.append(indent + "  bind:")
    for k, v in binds.items():
        if isinstance(v, list):
            lines.append("{}    {}: [{}]".format(indent, k, ", ".join('"{}"'.format(x) for x in v)))
        else:
            lines.append('{}    {}: "{}"'.format(indent, k, v) if any(c in v for c in "[],") else "{}    {}: {}".format(indent, k, v))
    return lines


def _remove_blocks(lines, pid):
    removed = 0
    for i, j, _ind in reversed(sy._find_attach_blocks(lines, pid)):
        del lines[i:j]
        removed += 1
    return removed


def _append_after_attaches(lines, block, item_indent):
    idx = len(lines)
    for k in range(len(lines) - 1, -1, -1):
        if lines[k].startswith(item_indent + "  ") or re.match(r"^ {2,4}- peripheral:", lines[k]):
            idx = k + 1
            break
    lines[idx:idx] = block


def _strip_tie_block(lines):
    out, i = [], 0
    while i < len(lines):
        l = lines[i]
        if re.match(r"^  tie:\s*(#.*)?$", l):
            if out and out[-1].startswith("  # Pins BGM"):
                out.pop()
            i += 1
            while i < len(lines) and (lines[i].startswith("    ") or lines[i].strip() == ""):
                if lines[i].strip() == "" and (i + 1 >= len(lines) or not lines[i + 1].startswith("    ")):
                    break
                i += 1
            continue
        out.append(l)
        i += 1
    return out


def _yaml_attaches(lines):
    try:
        return yaml.safe_load("\n".join(lines))["Configuration"].get("attach") or []
    except Exception:
        return []


def seven_seg_attach(text, vdir, rev, pinmap):
    """(params, binds) for a shared seven-segment display BGM drives through
    header pins (marsohod3gw2: `assign IO[6] = ~ abcdefgh[7]` on the add-on
    shield), from BGM's per-pin map; None when BGM drives no display."""
    by_pin = sy._bgm_seven_seg_by_pin(vdir)
    if not by_pin:
        return None
    seg = {bit: p for p, (kind, bit, _inv) in by_pin.items() if kind == "seg"}
    dig = {bit: p for p, (kind, bit, _inv) in by_pin.items() if kind == "dig"}
    if not all(b in seg for b in range(1, 8)) or not dig or sorted(dig) != list(range(max(dig) + 1)):
        return None
    refs = {p: rev.get(p) for p in list(seg.values()) + list(dig.values())}
    if not all(refs.values()):
        return None
    seg_inv = {by_pin[p][2] for p in seg.values()}
    dig_inv = {by_pin[p][2] for p in dig.values()}
    if len(seg_inv) != 1 or len(dig_inv) != 1:
        return None
    params = OrderedDict([("digits", max(dig) + 1),
                          ("active", "low" if seg_inv.pop() else "high"),
                          ("digits_active", "low" if dig_inv.pop() else "high")])
    binds = OrderedDict([("segments", [refs[seg[b]] for b in range(7, 0, -1)])])
    if 0 in seg:
        binds["dp"] = refs[seg[0]]
    binds["digits"] = [refs[dig[b]] for b in range(max(dig) + 1)]
    return params, binds


_ASSIGN_LHS = re.compile(r"\bassign\s+(\{[^}]*\}|[A-Za-z_]\w*\s*(?:\[[^\]]*\])?)\s*=\s*([^;]+);")
_CONST_ELEM = re.compile(r"^(\d*)'([bdh])([0-9a-fA-F_xXzZ]+)$")


def _lhs_keys(lhs, sig_pins, text):
    """Constraint keys of an assign LHS (`PORT`, `PORT[i]`, `PORT[hi:lo]`,
    `{A, B}`), MSB first; None when it is not made of ports."""
    lhs = lhs.strip()
    if lhs.startswith("{"):
        keys = []
        for el in _split_top_level(lhs[1:-1]):
            ek = _element_keys(el, sig_pins, text)
            if ek is None:
                return None
            keys.extend(ek)
        return keys
    return _element_keys(lhs, sig_pins, text)


def vga_header_attach(text, sig_pins, rev, pinmap, widths):
    """(params, binds, ties) for BGM's `vga` timing generator driving header
    pins (Zybo Z7: `assign jc[3:0] = red; .hsync (jd[4])`; Tang Primer 20K
    Lite: `wire VGA_R = display_on ? red : '0; assign GPIO_0 = {VGA_R, VGA_B}`),
    or None when BGM instantiates no `vga` or the colours do not reach ports."""
    insts = bgm_oracle.instantiations(text, "vga")
    if not insts:
        return None
    ports = dict(insts[0]["ports"])
    src = {}                       # expression name -> ("colour", c) | ("sync", s)
    for port, sig in (("hsync", "hs"), ("vsync", "vs")):
        e = ports.get(port, "").strip()
        if e:
            src[e] = ("sync", sig)
    for colour in ("red", "green", "blue"):
        src[colour] = ("colour", colour)
        for m in re.finditer(r"\b(?:wire|logic)\s*(?:\[[^\]]*\])?\s*([A-Za-z_]\w*)\s*=\s*[^;]*?(?<![A-Za-z0-9_])"
                             + colour + r"(?![A-Za-z0-9_])[^;]*;", text):
            src[m.group(1)] = ("colour", colour)
    w = {c: int(widths.get("w_" + c, 0) or 0) for c in ("red", "green", "blue")}
    colour_bits = {c: {} for c in ("red", "green", "blue")}
    sync_ref, ties = {}, OrderedDict()

    def take(key, kind, name, bit):
        pin = sig_pins.get(key)
        ref = rev.get(pin, "vga") if pin is not None else None
        if ref is None:
            return
        if kind == "colour":
            colour_bits[name][bit] = ref
        elif kind == "sync":
            sync_ref[name] = ref
        else:
            ties[ref] = name

    # port bits on the vga instance itself: .hsync ( jd[4] ), and the colour
    # outputs of BGM's other vga module: .red ( jb [7:4] ) (arty VGA Pmod)
    for e, (kind, name) in list(src.items()):
        if kind == "sync":
            m = re.match(r"^([A-Za-z_]\w*)\s*(?:\[\s*(\d+)\s*\])?$", e)
            if m:
                key = m.group(1).upper() + ("[{}]".format(m.group(2)) if m.group(2) else "")
                if key in sig_pins:
                    take(key, "sync", name, None)
    lab = bgm_oracle.instantiations(text, "lab_top")
    lab_ports = dict(lab[0]["ports"]) if lab else {}
    for port, colour in (("red", "red"), ("green", "green"), ("blue", "blue"),
                         ("vga_r", "red"), ("vga_g", "green"), ("vga_b", "blue")):
        # the vga module's colour outputs, or lab_top's colours going straight
        # to header pins (arty: `.red ( jb [7:4] )` on the VGA Pmod)
        e = (ports.get(port) or lab_ports.get(port) or "").strip()
        keys = _element_keys(e, sig_pins, text) if e else None
        if keys:
            for b, key in enumerate(reversed(keys)):
                take(key, "colour", colour, b)
            if not w[colour]:
                w[colour] = len(keys)
    for m in _ASSIGN_LHS.finditer(text):
        keys = _lhs_keys(m.group(1), sig_pins, text)
        if not keys:
            continue
        rhs = " ".join(m.group(2).split())
        elems = _split_top_level(rhs[1:-1]) if rhs.startswith("{") and rhs.endswith("}") else [rhs]
        plan = []                  # (kind, name, width, top bit) MSB first
        for el in elems:
            el = el.strip()
            cm = _CONST_ELEM.match(el.replace(" ", ""))
            if cm:
                digits = cm.group(3).replace("_", "")
                if cm.group(2) == "b" and set(digits) <= {"0", "1"}:
                    n = int(cm.group(1)) if cm.group(1) else len(digits)
                    bits = digits.rjust(n, "0")[-n:]
                    for ch in bits:
                        plan.append(("const", ch, 1, 0))
                    continue
                plan = None
                break
            if el in ("'0", "'1"):
                plan.append(("fill", el[1], None, 0))
                continue
            em = re.match(r"^([A-Za-z_]\w*)\s*(?:\[\s*(\d+)\s*(?::\s*(\d+))?\s*\])?$", el)
            if not em or em.group(1) not in src:
                plan = None
                break
            kind, name = src[em.group(1)]
            hi, lo = em.group(2), em.group(3)
            if kind == "sync":
                plan.append((kind, name, 1, 0))
            elif hi is not None:
                lo_i = int(lo) if lo is not None else int(hi)
                plan.append((kind, name, int(hi) - lo_i + 1, int(hi)))
            else:
                plan.append((kind, name, w[name], w[name] - 1))
        if plan is None or not any(k in ("colour", "sync") for k, _n, _w, _t in plan):
            continue
        known = sum(pw for _k, _n, pw, _t in plan if pw is not None)
        fills = [pl for pl in plan if pl[2] is None]
        fill_w = (len(keys) - known) // len(fills) if fills else 0
        i = 0
        for kind, name, pw, top in plan:
            if pw is None:
                pw, kind, name, top = fill_w, "const", name, 0
            for b in range(pw):
                if i >= len(keys):
                    break
                take(keys[i], kind, name, top - b)
                i += 1
    if not all(sync_ref.get(s) for s in ("hs", "vs")):
        return None
    for c in ("red", "green", "blue"):
        if w[c] and sorted(colour_bits[c]) != list(range(w[c])):
            return None
    params = OrderedDict([("bits_r", w["red"]), ("bits_g", w["green"]), ("bits_b", w["blue"]),
                          ("pin_bits_r", w["red"]), ("pin_bits_g", w["green"]), ("pin_bits_b", w["blue"])])
    binds = OrderedDict()
    for sig, c in (("r", "red"), ("g", "green"), ("b", "blue")):
        refs = [colour_bits[c][b] for b in range(w[c])]
        run = _runs(refs)
        binds[sig] = _run_bind(run[0], pinmap)[0] if len(run) == 1 and run[0]["bank"] and not _run_bind(run[0], pinmap)[1] else refs
    binds["hs"] = sync_ref["hs"]
    binds["vs"] = sync_ref["vs"]
    return params, binds, ties


def _prune_optional_binds(lines, cfg, pinmap, declared_pins, changes):
    """Drop a bind of an optional peripheral signal whose pins BGM's top never
    declares (omdazz_pmod_mic3: UART_TXD is commented out, only RXD exists)."""
    try:
        peripherals = config_init.read_peripherals()
    except Exception:
        return
    for a in cfg.get("attach") or []:
        pdef = peripherals.get(a.get("peripheral")) or {}
        optional = {sg["name"] for sg in pdef.get("signals") or [] if sg.get("optional")}
        binds = a.get("bind") or {}
        for key, ref in binds.items():
            if key not in optional or isinstance(ref, list):
                continue
            pins = sy._pins_of_ref(pinmap, ref)
            if not pins or pins & declared_pins:
                continue
            # the N half of a differential pair whose P half BGM constrains is
            # placed by the tool (a7_lite TMDS_33 in Vivado): keep it
            if key.endswith("_n") and key[:-2] + "_p" in binds and \
                    sy._pins_of_ref(pinmap, binds[key[:-2] + "_p"]) & declared_pins:
                continue
            for i, j, indent in sy._find_attach_blocks(lines, a["peripheral"]):
                for k in range(i, j):
                    if re.match(r"^{}    {}:\s*\"?{}\"?\s*(#.*)?$".format(indent, re.escape(key), re.escape(str(ref))), lines[k]):
                        del lines[k]
                        changes.append("{}: dropped optional {} on {} (BGM's top declares no such pin)".format(
                            a["peripheral"], key, ref))
                        break
                else:
                    continue
                break


def bgm_clock_port(text):
    """The port BGM's `wire clk = PORT;` (or `assign clk = PORT;`) takes the
    system clock from, or None (a PLL-driven or differential clock)."""
    m = re.search(r"\b(?:wire\s+|assign\s+)clk\s*=\s*([A-Za-z_]\w*)\s*;", text)
    return m.group(1) if m else None


def vga_extra_binds(text, sig_pins, rev):
    """{blank_n: ref, clk: ref} for the ports BGM connects to its `vga`
    instance's display_on / pixel_clk outputs (DAC boards)."""
    out = {}
    for inst in bgm_oracle.instantiations(text, "vga"):
        for port, sig in (("display_on", "blank_n"), ("pixel_clk", "clk")):
            ref = _port_ref(inst, port, sig_pins, rev, "vga")
            if ref:
                out[sig] = ref
    return out


def _add_clock_bank(pm_path, bank, pin, mhz):
    """Append `bank: { pins: PIN, frequency_mhz: MHZ }` to a pinmap's pinBanks."""
    text = open(pm_path, encoding="utf-8").read()
    if re.search(r"^    {}:".format(re.escape(bank)), text, re.M):
        return True
    line = "    {}: {{ pins: {}, frequency_mhz: {} }}   # BGM's system clock port\n".format(
        bank, pin, int(mhz) if mhz and float(mhz).is_integer() else mhz)
    new, n = re.subn(r"^(  pinBanks:\s*\n)", lambda m: m.group(1) + line, text, count=1, flags=re.M)
    if n != 1:
        return False
    open(pm_path, "w", encoding="utf-8").write(new)
    return True


def _set_attach_bind(lines, pid, key, value):
    """Add or replace `key: value` in the bind: of the first `pid` block."""
    blocks = sy._find_attach_blocks(lines, pid)
    if not blocks:
        return False
    i, j, indent = blocks[0]
    rendered = '{}    {}: "{}"'.format(indent, key, value) if any(c in value for c in "[],") \
        else "{}    {}: {}".format(indent, key, value)
    for k in range(i, j):
        if re.match(r"^{}    {}:".format(indent, re.escape(key)), lines[k]):
            if lines[k] == rendered:
                return False
            lines[k] = rendered
            return True
    for k in range(i, j):
        if lines[k].strip() == "bind:":
            lines.insert(k + 1, rendered)
            return True
    return False


def _sync_blocks(lines, pid, wanted, have, comment, indent, changes, label=None):
    """Replace every `pid` block by `wanted` = [(params, binds)] when `have`
    (the current [(params, binds)]) differs."""
    if [(dict(p), b) for p, b in have] == [(dict(p), b) for p, b in wanted]:
        return
    blocks = sy._find_attach_blocks(lines, pid)
    at = blocks[0][0] if blocks else None
    _remove_blocks(lines, pid)
    rendered = []
    for params, binds in wanted:
        rendered.extend(_render(pid, params, binds, comment, indent))
    if at is not None and rendered:
        lines[at:at] = rendered
    elif rendered:
        _append_after_attaches(lines, rendered, indent)
    changes.append("{} {} -> {}".format(label or pid, [b for _p, b in have] or "none",
                                        [b for _p, b in wanted] or "none"))


def _have(cfg, pid):
    out = []
    for a in cfg.get("attach") or []:
        if a.get("peripheral") == pid:
            out.append((a.get("params") or {},
                        {k: (v if isinstance(v, list) else str(v).strip('"')) for k, v in (a.get("bind") or {}).items()}))
    return out


def apply_components(path, dry_run):
    original = open(path, encoding="utf-8").read()
    cfg = yaml.safe_load(original)["Configuration"]
    if cfg.get("manual"):
        return "skip (manual configuration)"
    vdir = bgm_oracle.variant_dir_for(cfg["id"], cfg["board"])
    if vdir is None:
        return "skip (no BGM variant)"
    pinmap = config_init.read_board_pinmap(cfg["board"]) or {}
    config_init._apply_pin_overrides(cfg["id"], cfg, pinmap)
    pp = bgm_oracle.preprocess_variant(vdir)
    text = bgm_oracle.strip_comments(pp.text)       # commented-out instances and assigns are not wiring
    raw = "\n".join(open(f, encoding="utf-8", errors="replace").read() for f in pp.files if os.path.exists(f))
    sig_pins = sy._bgm_signal_pins(vdir)
    bound_banks = set()
    for a in cfg.get("attach") or []:
        for ref in (a.get("bind") or {}).values():
            for one in (ref if isinstance(ref, list) else [ref]):
                parts = _split_ref(one) if isinstance(one, str) else None
                if parts:
                    bound_banks.add(parts[0])
    rev = _Rev(pinmap, bound_banks)
    ports = bgm_oracle.top_ports(text)
    upper_ports = {p.upper() for p in ports}
    declared_pins = set()
    for key, pin in sig_pins.items():
        if key.split("[", 1)[0] in upper_ports:
            for half in str(pin).split(","):
                declared_pins.add(sy._norm_pin(half))

    lines = original.split("\n")
    indent = sy._attach_item_indent(lines)
    comment = "from BGM {}/board_specific_top.sv".format(os.path.basename(vdir))
    changes, warnings = [], []

    # ---- system clock: the same oscillator pin as BGM ------------------------
    clk_port = bgm_clock_port(text)
    clk_pin = sig_pins.get(clk_port.upper()) if clk_port else None
    if clk_pin is not None:
        cur = next((a for a in cfg.get("attach") or [] if a.get("peripheral") == "clock_input"), None)
        cur_ref = (cur.get("bind") or {}).get("clk") if cur else None
        if cur_ref and sy._norm_pin(clk_pin) not in sy._pins_of_ref(pinmap, cur_ref):
            ref = rev.get(clk_pin, "clock")
            if ref is None:
                # the oscillator BGM uses is not in the pinmap at all (SoCKit
                # OSC_50_B3B): add it as its own clock bank
                bank = re.sub(r"\W+", "_", clk_port).lower()
                mhz = bgm_oracle.clk_mhz(text)
                pm_path = sy._pinmap_path(cfg["board"])
                if pm_path and not dry_run and _add_clock_bank(pm_path, bank, clk_pin, mhz):
                    config_init.clear_cache()
                    ref = bank
                    changes.append("pinmap: added clock bank {} (pin {}, {} MHz, BGM port {})".format(
                        bank, clk_pin, mhz, clk_port))
                elif pm_path and dry_run:
                    ref = bank
                    changes.append("pinmap: would add clock bank {} (pin {}, BGM port {})".format(bank, clk_pin, clk_port))
                else:
                    warnings.append("clock: BGM takes clk from {} (pin {}), not in the pinmap".format(clk_port, clk_pin))
            if ref is not None and _set_attach_bind(lines, "clock_input", "clk", ref):
                changes.append("clock_input {} -> {} (BGM: wire clk = {})".format(cur_ref, ref, clk_port))

    # ---- I2S DACs, WM8731 codec, ADV7513 HDMI --------------------------------
    wanted = i2s_attaches(text, sig_pins, rev)
    wanted, wm = wm8731_attach(wanted, text, sig_pins, rev)
    for _b, notes in wanted:
        warnings.extend("i2s_audio_out: " + n for n in notes)
    # BGM orangecrab_ecp5_yosys: the PCM5102 and the INMP441 both sit on GPIO
    # 3..6 in the active text (upstream conflict); a DAC on pins another driver
    # peripheral already owns is left out and reported
    driver_pins = set()
    for a in cfg.get("attach") or []:
        if a.get("peripheral") in ("inmp441_i2s_mic", "pmod_mic3", "pdm_mic", "tm1638_led_key", "hdmi_tmds"):
            for ref in (a.get("bind") or {}).values():
                for one in (ref if isinstance(ref, list) else [ref]):
                    driver_pins |= sy._pins_of_ref(pinmap, one)
    kept = []
    for binds, notes in wanted:
        mine = set()
        for ref in binds.values():
            mine |= sy._pins_of_ref(pinmap, ref)
        if mine & driver_pins:
            warnings.append("i2s_audio_out on {} shares pins with another driver peripheral in BGM's own top; left out"
                            .format(sorted(binds.values())))
        else:
            kept.append((binds, notes))
    wanted = kept
    _sync_blocks(lines, "i2s_audio_out", [({}, b) for b, _n in wanted], _have(cfg, "i2s_audio_out"),
                 comment, indent, changes)
    _sync_blocks(lines, "wm8731_i2s_out", [({}, wm)] if wm else [], _have(cfg, "wm8731_i2s_out"),
                 comment, indent, changes)
    adv = adv7513_attach(text, sig_pins, rev)
    _sync_blocks(lines, "hdmi_adv7513", [adv] if adv else [], _have(cfg, "hdmi_adv7513"), comment, indent, changes)

    # ---- VGA DAC extras (BLANK_N = display_on, CLK = pixel clock) ------------
    extra_ties = OrderedDict()
    if (pinmap.get("pinBanks") or {}).get("onboard_vga"):
        if any(a.get("peripheral") == "vga_4bit" for a in cfg.get("attach") or []):
            for sig, ref in vga_extra_binds(text, sig_pins, rev).items():
                if _set_attach_bind(lines, "vga_4bit", sig, ref):
                    changes.append("vga_4bit {} -> {}".format(sig, ref))
    else:
        # ---- VGA through header pins (resistor ladder / VGA Pmod) ------------
        vga = vga_header_attach(text, sig_pins, rev, pinmap, sy._bgm_colour_bits(vdir))
        if vga is not None:
            params, binds, extra_ties = vga
            _sync_blocks(lines, "vga_4bit", [(params, binds)], _have(cfg, "vga_4bit"), comment + " (VGA on header pins)",
                         indent, changes)

    # ---- seven-segment display on header pins ----------------------------------
    if not any(a.get("peripheral") in ("seven_segment_8digit_shared", "seven_segment_per_digit")
               for a in cfg.get("attach") or []):
        seg = seven_seg_attach(text, vdir, rev, pinmap)
        if seg is not None:
            _sync_blocks(lines, "seven_segment_8digit_shared", [seg], [], comment + " (display on header pins)",
                         indent, changes)

    # ---- gpio providers ------------------------------------------------------
    bits, notes = gpio_bits(text, raw, sig_pins, rev, pp.defines)
    warnings.extend("gpio: " + n for n in notes)
    if bits is not None:
        want = gpio_attaches(bits, pinmap)
        have = [(a["peripheral"], a.get("params") or {}, {k: (v if isinstance(v, list) else str(v).strip('"'))
                                                          for k, v in (a.get("bind") or {}).items()})
                for a in cfg.get("attach") or [] if a.get("peripheral") in _GPIO_PERIPHERALS]
        norm = lambda lst: [(p, {k: v for k, v in pr.items() if k in ("width", "mirror")}, b) for p, pr, b in lst]
        if norm(have) != norm(want):
            for pid in _GPIO_PERIPHERALS:
                _remove_blocks(lines, pid)
            for pid, params, binds in want:
                _append_after_attaches(lines, _render(pid, params, binds, comment + " .gpio", indent), indent)
            changes.append("gpio {} -> {}".format([(p, b["io"]) for p, _pr, b in have],
                                                   [(p, b["io"]) for p, _pr, b in want] or "none"))

    # ---- LEDs ----------------------------------------------------------------
    lbits, notes = led_bits(text, sig_pins, rev)
    warnings.extend("led: " + n for n in notes)
    if lbits is not None:
        want = led_attaches(lbits, pinmap)
        cur = [a for a in cfg.get("attach") or [] if a.get("peripheral") == "led_bank"]
        want_dicts = [{"params": dict(p), "bind": b} for p, b in want]
        if _led_signature(cur, pinmap) != _led_signature(want_dicts, pinmap):
            _sync_blocks(lines, "led_bank", want, [(a.get("params") or {}, a.get("bind") or {}) for a in cur],
                         comment, indent, changes)
        # a passthrough on pins the lab led bus now owns (Eclypse Z7: BGM uses
        # the two RGB LEDs as six plain LEDs) is an invented component
        led_pins = set()
        for _p, b in want:
            for one in (b["led"] if isinstance(b["led"], list) else [b["led"]]):
                led_pins |= sy._pins_of_ref(pinmap, one)
        for a in _yaml_attaches(lines):
            pid = a.get("peripheral")
            if pid in ("led_bank", "pin_tie") or pid in _GPIO_PERIPHERALS:
                continue
            pins = set()
            for ref in (a.get("bind") or {}).values():
                for one in (ref if isinstance(ref, list) else [ref]):
                    pins |= sy._pins_of_ref(pinmap, one)
            if pins and pins <= led_pins:
                for i, j, _ in reversed(sy._find_attach_blocks(lines, pid)):
                    body = "\n".join(lines[i:j])
                    if all(str(v).strip('"') in body for v in (a.get("bind") or {}).values() if not isinstance(v, list)):
                        del lines[i:j]
                        changes.append("removed {} on {} (its pins carry the lab led bus in BGM)".format(
                            pid, sorted((a.get("bind") or {}).values(), key=str)))
                        break

    # ---- invented components -------------------------------------------------
    for a in _yaml_attaches(lines):
        pid = a.get("peripheral")
        if pid in _KEEP:
            continue
        pins = set()
        for ref in (a.get("bind") or {}).values():
            for one in (ref if isinstance(ref, list) else [ref]):
                pins |= sy._pins_of_ref(pinmap, one)
        if not pins or pins & declared_pins:
            continue
        for i, j, _ in reversed(sy._find_attach_blocks(lines, pid)):
            body = "\n".join(lines[i:j])
            if all(str(v).strip('"') in body for v in (a.get("bind") or {}).values() if not isinstance(v, list)):
                del lines[i:j]
                changes.append("removed {} on {} (BGM's top declares none of its pins)".format(
                    pid, sorted((a.get("bind") or {}).values(), key=str)))
                break

    _prune_optional_binds(lines, cfg, pinmap, declared_pins, changes)

    # ---- ties ----------------------------------------------------------------
    bound = set()
    for a in _yaml_attaches(lines):
        for ref in (a.get("bind") or {}).values():
            for one in (ref if isinstance(ref, list) else [ref]):
                bound |= sy._pins_of_ref(pinmap, one)
    for src in ((cfg.get("reset") or {}).get("sources") or []):
        if isinstance(src, dict) and src.get("pin"):
            bound |= sy._pins_of_ref(pinmap, src["pin"])
    ties = tie_entries(text, sig_pins, rev, bound)
    for ref, v in extra_ties.items():
        if not (sy._pins_of_ref(pinmap, ref) & bound):
            ties.setdefault(ref, v)
    cur_ties = OrderedDict((k, str(v)) for k, v in (cfg.get("tie") or {}).items())
    if OrderedDict((k, str(v)) for k, v in ties.items()) != cur_ties:
        lines = _strip_tie_block(lines)
        while lines and lines[-1].strip() == "":
            lines.pop()
        if ties:
            lines.append("")
            lines.append("  # Pins BGM's top drives with a constant or the reset — `assign PIN = 1'b0` /"
                         " `~ rst` (tools/sync_from_bgm.py --components)")
            lines.append("  tie:")
            for ref, v in ties.items():
                lines.append("    {}: {}".format('"{}"'.format(ref) if "[" in ref else ref, v))
        lines.append("")
        changes.append("tie {} -> {}".format(dict(cur_ties) or "none", dict(ties) or "none"))

    text_out = "\n".join(lines)
    if not text_out.endswith("\n"):
        text_out += "\n"
    if warnings:
        changes.append("WARNINGS: " + "; ".join(warnings))
    if text_out == original:
        return "unchanged" if not warnings else "unchanged; " + "; ".join(warnings)
    if not dry_run:
        open(path, "w", encoding="utf-8").write(text_out)
    return "; ".join(changes)
