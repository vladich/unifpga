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

import json
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

def _concat_const_bits(text, sig_pins):
    """{PORT[i]: "1'b0"|"1'b1"} for the constant elements of a whole-port
    concatenation assign: de2_115 `LEDG = { { $bits (LEDG) - w_lab_led { 1'b0 } },
    lab_led }` (LEDG [8] = 0), zeowaa_wo_dig_0 `DIGIT_N = ~ { lab_digit, 1'b0 }`
    (DIGIT_N [0] = 1). Element widths come from the top's parameters; one
    unknown identifier takes the rest of the port."""
    ports = bgm_oracle.top_ports(text)
    params = _top_params(text)
    out = {}
    for m in re.finditer(r"\bassign\s+([A-Za-z_]\w*)\s*=\s*(~?)\s*\{(.*?)\}\s*;", text, re.S):
        port, inv, body = m.group(1), m.group(2), m.group(3)
        if port not in ports:
            continue
        keys = [k for k in sig_pins if k.split("[", 1)[0] == port.upper() and "[" in k]
        if not keys:
            continue
        pw = max(int(k[k.index("[") + 1:-1]) for k in keys) + 1
        elems = _split_top_level(body)
        widths, consts = [], []
        for el in elems:
            el = " ".join(el.split())
            cm = re.match(r"^(\d+)'[bBdDhH]([0-9a-fA-F_xXzZ]+)$", el)
            rm = re.match(r"^\{\s*(.+?)\s*\{\s*(\d+)'[bB]([01])\s*\}\s*\}$", el)
            if cm:
                w = int(cm.group(1))
                v = int(cm.group(2).replace("_", ""), 2 if "b" in el.lower() else (16 if "h" in el.lower() else 10))
                widths.append(w)
                consts.append((w, v))
            elif rm:
                cnt_expr = re.sub(r"\$bits\s*\(\s*" + re.escape(port) + r"\s*\)", str(pw), rm.group(1))
                k = _eval_int(cnt_expr, params)
                if k is None:
                    widths.append(None)
                    consts.append(None)
                    continue
                widths.append(k * int(rm.group(2)))
                consts.append((k * int(rm.group(2)), (int(rm.group(3)) * ((1 << (k * int(rm.group(2)))) - 1))))
            else:
                name = re.match(r"^~?\s*([A-Za-z_]\w*)", el)
                w = None
                if name:
                    n = name.group(1)
                    w = params.get("w_" + n) or params.get("w_" + n.replace("lab_", "lab_")) or params.get(n)
                    if w is None and n.startswith("lab_"):
                        w = params.get("w_lab_" + n[4:])
                widths.append(w)
                consts.append(None)
        if widths.count(None) > 1:
            continue
        known = sum(w for w in widths if w is not None)
        if None in widths:
            widths[widths.index(None)] = pw - known
        if sum(widths) != pw:
            continue
        bit = pw
        for w, c in zip(widths, consts):
            bit -= w
            if c is None:
                continue
            cw, cv = c
            for i in range(cw):
                v = (cv >> i) & 1
                if inv:
                    v ^= 1
                out["{}[{}]".format(port, bit + i)] = "1'b{}".format(v)
    return out


def tie_entries(text, sig_pins, rev, exclude_pins):
    """{ref: value} for scalar ports BGM assigns a constant or the reset,
    skipping pins another attach owns."""
    out = OrderedDict()
    assigns = dict(bgm_oracle.port_bit_assigns(text))
    for k, v in _concat_const_bits(text, sig_pins).items():
        assigns.setdefault(k, v)
    for port, expr in assigns.items():
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
# colorlight: `assign LED [0] = ( lab_led [0] ? 1'b0 : 1'bz );` — open drain, on = drive 0
_LED_OD = re.compile(r"^\(?\s*(?:led|lab_led)\s*\[\s*(\d+)\s*\]\s*\?\s*1'b([01])\s*:\s*1'bz\s*\)?$")


def led_bits(text, sig_pins, rev):
    """LSB-first [(ref, inverted)] of BGM's lab `led` bus, with notes. None
    when the top does not route `led` to ports at all."""
    by_bit, notes = {}, []
    ports = bgm_oracle.top_ports(text)

    def put(bit, key, inv, od=False):
        if key in sig_pins:
            by_bit[bit] = (key, inv, od)
        else:
            notes.append("led[{}] -> {}: no pin in the constraint files".format(bit, key))

    params = _top_params(text)
    for m in bgm_oracle._ASSIGN_ANY.finditer(text):
        lhs, rhs = m.group(1).strip(), " ".join(m.group(2).split())
        cm = re.match(r"^(~?)\s*\{(.*)\}$", rhs)
        if cm and lhs in ports and re.search(r"\b(led|lab_led)\b", cm.group(2)):
            # de2_115: LEDG = { { $bits (LEDG) - w_lab_led { 1'b0 } }, lab_led }; the
            # lab bus sits above the elements after it
            keys_all = _bus_keys(lhs, sig_pins)
            pw = len(keys_all)
            elems = [" ".join(e.split()) for e in _split_top_level(cm.group(2))]
            pos = None
            below = 0
            for el in reversed(elems):
                em = re.match(r"^(~?)\s*(led|lab_led)$", el)
                if em:
                    pos = below
                    inv_el = bool(em.group(1)) != bool(cm.group(1))
                    break
                w = None
                c1 = re.match(r"^(\d+)'[bBdDhH]", el)
                r1 = re.match(r"^\{\s*(.+?)\s*\{\s*(\d+)'[bB][01]\s*\}\s*\}$", el)
                if c1:
                    w = int(c1.group(1))
                elif r1:
                    k = _eval_int(re.sub(r"\$bits\s*\(\s*" + re.escape(lhs) + r"\s*\)", str(pw), r1.group(1)), params)
                    w = k * int(r1.group(2)) if k is not None else None
                else:
                    nm = re.match(r"^~?\s*([A-Za-z_]\w*)$", el)
                    w = params.get("w_" + nm.group(1)) if nm else None
                if w is None:
                    pos = None
                    break
                below += w
            if pos is not None:
                w_lab = params.get("w_lab_led") or params.get("w_led") or (pw - below)
                for i in range(min(w_lab, pw - pos)):
                    put(i, "{}[{}]".format(lhs.upper(), pos + i), inv_el)
                continue
        od = _LED_OD.match(rhs)
        if od:
            lm = re.match(r"^([A-Za-z_]\w*)\s*(?:\[\s*(\d+)\s*\])?$", lhs)
            if lm and lm.group(1) in ports:
                key = lm.group(1).upper() + ("[{}]".format(lm.group(2)) if lm.group(2) is not None else "")
                put(int(od.group(1)), key, od.group(2) == "0", od=True)
            continue
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
            bits.append((None, False, False))
            continue
        key, inv, od = by_bit[i]
        ref = rev.get(sig_pins[key], "led")
        if ref is None:
            notes.append("led[{}] -> {}: pin {} not in the pinmap".format(i, key, sig_pins[key]))
        bits.append((ref, inv, od))
    return bits, notes


def led_attaches(bits, pinmap):
    """[(params, bind)] led_bank attaches, LSB-first, from [(ref, inverted)]."""
    out = []
    for run in _runs([b for b in bits if b[0] is not None], same=lambda a, b: a[1:] == b[1:]):
        if run["bank"] is None:
            continue
        ref, mirror = _run_bind(run, pinmap)
        inv = run["items"][0][1]
        od = run["items"][0][2]
        params = OrderedDict([("width", len(run["refs"]))])
        bank_active = ((pinmap.get("pinBanks") or {}).get(run["bank"]) or {}).get("active")
        want = "low" if inv else "high"
        if (bank_active or "high") != want:
            params["active"] = want
        if od:
            params["open_drain"] = True
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
        od = bool(params.get("open_drain", False))
        sig.extend((p, active == "low", od) for p in pins)
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
    # lab_bits (tools/sync_from_bgm.py --lab-bits) belongs to the attach; a
    # re-rendered block with the same bind keeps it
    kept_bits = {}
    for a in _yaml_attaches(lines):
        if a.get("peripheral") == pid and a.get("lab_bits"):
            kept_bits[_bind_key(a.get("bind") or {})] = a["lab_bits"]
    _remove_blocks(lines, pid)
    rendered = []
    for params, binds in wanted:
        rendered.extend(_render(pid, params, binds, comment, indent))
        bits = kept_bits.get(_bind_key(binds))
        if bits:
            rendered.extend(_render_lab_bits(indent, OrderedDict(bits)))
    if at is not None and rendered:
        lines[at:at] = rendered
    elif rendered:
        _append_after_attaches(lines, rendered, indent)
    changes.append("{} {} -> {}".format(label or pid, [b for _p, b in have] or "none",
                                        [b for _p, b in wanted] or "none"))


def _bind_key(bind):
    return json.dumps({k: (v if isinstance(v, list) else str(v).strip('"')) for k, v in bind.items()}, sort_keys=True)


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
    # pins another attach drives or reads; a bare header (gpio_header,
    # pmod_12pin) does not count: BGM ties GPIO [1] / GPIO [3] to GND / VCC
    # for the microphone module while still handing the whole header to
    # the lab, and the tie must win
    bound = set()
    for a in _yaml_attaches(lines):
        if a.get("peripheral") in _GPIO_PERIPHERALS:
            continue
        # a provider bit no lab bit reaches (lab_bits: [0..7, ~]) is not driven
        # by the attach: BGM's `LEDG = { 1'b0, lab_led }` ties LEDG [8] (de2_115)
        unmapped = set()
        for cap_bits in (a.get("lab_bits") or {}).values():
            unmapped |= {k for k, b in enumerate(cap_bits or []) if b is None}
        for ref in (a.get("bind") or {}).values():
            refs = ref if isinstance(ref, list) else [ref]
            pins_in_order = []
            for one in refs:
                pins_in_order.extend(_ref_pins_in_order(pinmap, one))
            for k, pin in enumerate(pins_in_order):
                if k in unmapped and len(refs) == 1 or (k in unmapped and isinstance(ref, list)):
                    continue
                bound.add(pin)
            for one in refs:
                # the P/N halves and anything _ref_pins_in_order simplified away
                for pn in sy._pins_of_ref(pinmap, one):
                    if pn not in pins_in_order or pins_in_order.index(pn) not in unmapped:
                        bound.add(pn)
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


# ---------------------------------------------------------------- lab bus composition (lab_bits)
#
# Which bits of the lab's key / sw / led / digit buses each board component
# carries, read from the active board_specific_top.sv. With a TM1638 BGM's
# default lab profile (DUPLICATE_TM1638_SIGNALS_WITH_REGULAR) makes the
# module *the* lab bus: `lab_key = tm_key`, `tm_led = lab_led`, `tm_digit =
# lab_digit`, the board LEDs show `w_led' (~ lab_led)` and the board keys
# serve only the reset. Without one, `.sw (lab_sw)` with `lab_sw = SW
# [w_lab_sw - 1:0]` keeps the reset switch out of the lab. Both are written
# as `lab_bits:` on the attaches (tools/codegen.py bit-mapped aggregation);
# the plain concatenation in attach order needs no entry.

_TM_PID = "tm1638_led_key"
_TM_WIDTH = 8
_LAB_BUS_CAP = (("key", "buttons"), ("sw", "switches"), ("led", "leds"), ("digit", "seven_segment"))


def _top_params(text):
    """{name: int} for the parameters and localparams of the board top that
    evaluate to integers (w_key, w_sw, w_lab_sw = w_sw - 1, ...)."""
    raw = OrderedDict()
    m = re.search(r"\bmodule\s+board_specific_top\s*#\s*\((.*?)\)\s*\(", text, re.S)
    if m:
        for pm in re.finditer(r"\b([A-Za-z_]\w*)\s*=\s*([^,\n]+)", m.group(1)):
            raw.setdefault(pm.group(1), pm.group(2).strip())
    for lm in re.finditer(r"\blocalparam\b([^;]*);", text):
        for pm in re.finditer(r"\b([A-Za-z_]\w*)\s*=\s*([^,]+)", lm.group(1)):
            raw.setdefault(pm.group(1), pm.group(2).strip())
    vals = {}
    for _ in range(6):
        for name, expr in raw.items():
            if name in vals:
                continue
            v = _eval_int(expr, vals)
            if v is not None:
                vals[name] = v
    return vals


def _eval_int(expr, vals):
    e = re.sub(r"\b([A-Za-z_]\w*)\b", lambda m: str(vals[m.group(1)]) if m.group(1) in vals else m.group(1), expr)
    e = re.sub(r"\d+'[dD]", "", e)
    if not re.match(r"^[\d\s+\-*/()]+$", e) or not e.strip():
        return None
    try:
        return int(eval(e, {"__builtins__": {}}, {}))       # digits and arithmetic only
    except Exception:
        return None


def _follow(text, expr, depth=0):
    """Resolve a lab connection through `assign x = ...;` / `wire x = ...;`
    / `SWAP_BITS (x, ...)` to what feeds it (keeps a trailing slice)."""
    e = " ".join(expr.split())
    m = re.match(r"^(~?)\s*([A-Za-z_]\w*)\s*(\[[^\]]*\])?$", e)
    if not m or depth > 6:
        return e
    inv, name, sl = m.group(1), m.group(2), m.group(3) or ""
    am = re.search(r"(?:\bassign\s+|\b(?:wire|logic)\s*(?:\[[^\]]*\])?\s*)" + re.escape(name) + r"\s*=\s*([^;]+);", text)
    if am:
        rhs = am.group(1)
    else:
        sm = re.search(r"SWAP_BITS\s*\(\s*" + re.escape(name) + r"\s*,\s*([^)]+)\)", text)
        if not sm:
            return e
        rhs = sm.group(1)
    inner = _follow(text, rhs, depth + 1)
    if inv:
        inner = "~ " + inner if not inner.startswith("~") else inner[1:].strip()
    return inner + (" " + sl if sl else "")


def _or_terms(text, name, ports, params, tm_names=None):
    """[(kind, port, (lo, hi) or None, target_lo)] for a lab bus BGM builds
    with `name = '0; name [hi:lo] |= X; ...` (arty, zybo: the board keys
    ORed into the TM1638's low bits) or `name = A | w' (B)`."""
    out = []
    for m in re.finditer(r"\b" + re.escape(name) + r"\s*\[\s*([^\]:]+?)\s*(?::\s*([^\]]+?))?\s*\]\s*\|=\s*([^;]+);", text):
        hi = _eval_int(m.group(1), params)
        lo = _eval_int(m.group(2), params) if m.group(2) is not None else hi
        if hi is None or lo is None:
            return None
        kind, port, rng = _source_kind(_follow(text, m.group(3)), ports, params, tm_names)
        out.append((kind, port, rng, min(lo, hi)))
    if out:
        return out
    m = re.search(r"(?:\bassign\s+|\b(?:wire|logic)\s*(?:\[[^\]]*\])?\s*)" + re.escape(name) + r"\s*=\s*([^;]+);", text)
    if m and "|" in m.group(1) and "||" not in m.group(1):
        for term in re.split(r"\s\|\s", " ".join(m.group(1).split())):
            term = re.sub(r"^\w+'\s*\(\s*(.*)\s*\)$", r"\1", term.strip())     # w' ( X ) cast
            kind, port, rng = _source_kind(_follow(text, term), ports, params, tm_names)
            out.append((kind, port, rng, 0))
        return out
    return None


_TM_NAMES = {"tm_key", "tm_led", "tm_digit"}


def _tm_signal_names(text):
    """The nets BGM's TM1638 controller drives or reads (`.keys (key_tm)`,
    `.ledr (tm_led)`, `.digit (...)`), whatever the board calls them."""
    names = set(_TM_NAMES)
    for inst in bgm_oracle.instantiations(text, "tm1638_board_controller"):
        for port, expr in inst["ports"]:
            if port in ("keys", "ledr", "digit"):
                m = re.match(r"^\s*([A-Za-z_]\w*)", expr)
                if m:
                    names.add(m.group(1))
    return names


def _source_kind(src, ports, params, tm_names=None):
    """('tm' | 'board' | 'other', port, (lo, hi) or None) for a resolved lab
    connection: tm_key / tm_key [7:0] -> tm; ~ KEY, SW [8:0] -> board."""
    cm = re.match(r"^~?\s*\{([^{}]*)\}$", src)
    if cm:
        names = [t.strip().lstrip("~ ").strip() for t in cm.group(1).split(",")]
        if names and all(re.match(r"^[A-Za-z_]\w*$", n) and n in ports for n in names):
            return "board", names, None            # MSB first, every element a port
        return "other", None, None
    m = re.match(r"^~?\s*([A-Za-z_]\w*)\s*(?:\[\s*([^\]:]+?)\s*(?::\s*([^\]]+?))?\s*\])?$", src)
    if not m:
        return "other", None, None
    name = m.group(1)
    rng = None
    if m.group(2) is not None:
        hi = _eval_int(m.group(2), params)
        lo = _eval_int(m.group(3), params) if m.group(3) is not None else hi
        if hi is None or lo is None:
            return "other", None, None
        rng = (min(lo, hi), max(lo, hi))
    if name in (tm_names or _TM_NAMES):
        return "tm", name, rng
    if name in ports:
        return "board", name, rng
    return "other", name, rng


def _attach_for_port(cfg, pinmap, sig_pins, port):
    """Index of the attach whose binds cover the pins of BGM port `port`
    (all of the port's constrained bits), or None."""
    names = {n.upper() for n in (port if isinstance(port, list) else [port])}
    port_pins = {sy._norm_pin(h) for k, p in sig_pins.items() if k.split("[", 1)[0] in names
                 for h in str(p).split(",")}
    if not port_pins:
        return None
    for i, a in enumerate(cfg.get("attach") or []):
        pins = set()
        for ref in (a.get("bind") or {}).values():
            for one in (ref if isinstance(ref, list) else [ref]):
                pins |= sy._pins_of_ref(pinmap, one)
        if pins and port_pins <= pins:
            return i
    return None


def _source_bits(port, rng, sig_pins, params):
    """LSB-first BGM port bits `PORT[i]` (upper-case keys of sig_pins) of a
    lab source: a whole port (width from the top's parameters when the
    constraint file names more bits than the port has), a slice, or a
    concatenation (last element = LSB)."""
    ports = port if isinstance(port, list) else [port]
    out = []
    for name in reversed(ports):
        up = name.upper()
        keys = sorted((k for k in sig_pins if k.split("[", 1)[0] == up),
                      key=lambda k: int(k[k.index("[") + 1:-1]) if "[" in k else -1)
        if keys == [up]:
            out.append(up)
            continue
        idxs = [int(k[k.index("[") + 1:-1]) for k in keys if "[" in k]
        if not idxs:
            continue
        w = params.get("w_" + name.lower()) if isinstance(port, str) else None
        lo, hi = rng if (rng and isinstance(port, str)) else (0, (w - 1) if w else max(idxs))
        for i in range(lo, hi + 1):
            out.append("{}[{}]".format(up, i))
    return out


def _provider_pin_bits(resolved, pinmap, plans, cap):
    """{norm pin: (provider index, provider bit)} over the board (driver-less)
    providers of `cap`, in their bind order."""
    from tools import codegen
    out = {}
    for pidx, perif, _p in plans[cap].providers:
        if perif.get("id") == _TM_PID:
            continue
        attach = resolved["peripherals"][pidx]
        sig = next((sg["name"] for sg in perif.get("signals", []) if sg.get("type") == "bus"), None)
        ref = (attach.get("bind") or {}).get(sig) if sig else None
        if ref is None:
            continue
        refs = ref if isinstance(ref, list) else [ref]
        pins = []
        for one in refs:
            pins.extend(_ref_pins_in_order(pinmap, one))
        if codegen._peripheral_mirror(attach, pinmap):
            pins = list(reversed(pins))         # bank bit i is provider bit w-1-i
        for bit, pin in enumerate(pins):
            out.setdefault(pin, (pidx, bit))
    return out


def _ref_pins_in_order(pinmap, ref):
    parts = _split_ref(ref)
    if parts is None:
        return []
    bank, sub, idx = parts
    vals = ((pinmap.get("pinBanks") or {}).get(bank) or {}).get("pins")
    if isinstance(vals, dict):
        vals = vals.get(sub) if sub else None
    if isinstance(vals, list):
        vals = vals if idx is None else [vals[idx]]
    else:
        vals = [vals]
    return [sy._norm_pin(str(v).split(",")[0]) for v in vals if v is not None]


def _lab_bits_wanted(text, cfg, resolved, plans, sig_pins, rev, notes):
    """{attach_index: {cap: [bits] or None-to-remove}} — the composition of
    the lab buses this configuration must have."""
    from tools import codegen
    inst = bgm_oracle.instantiations(text, "lab_top")
    if not inst:
        notes.append("no lab_top instantiation in the active top")
        return {}, {}
    conns = dict(inst[0]["ports"])
    ports = bgm_oracle.top_ports(text)
    params = _top_params(text)
    tm_names = _tm_signal_names(text)
    attaches = cfg.get("attach") or []
    tm_idx = next((i for i, a in enumerate(attaches) if a.get("peripheral") == _TM_PID), None)
    has_tm = tm_idx is not None and bool(re.search(r"\btm1638_board_controller\b", text))
    wanted = {i: {} for i in range(len(attaches))}
    param_changes = {}          # attach index -> {param: value} (button_array as_switches)
    pinmap = resolved["board_pinmap"]

    def keys_as_switches(port, rng, target_lo=0):
        """BGM feeds the lab's sw from the board keys: the button_array that
        owns that port provides switches too (as_switches) on these bits."""
        idx = _attach_for_port(cfg, pinmap, sig_pins, port)
        if idx is None or attaches[idx].get("peripheral") != "button_array":
            notes.append("sw: BGM feeds the lab's sw from {} but no button_array owns it".format(port))
            return False
        w = int((attaches[idx].get("params") or {}).get("width") or 1)
        param_changes.setdefault(idx, {})["as_switches"] = True
        by_pin = _provider_pin_bits(resolved, pinmap, plans, "buttons")
        bits = [None] * w
        for lab, key in enumerate(_source_bits(port, rng, sig_pins, params)):
            pin = sy._norm_pin(str(sig_pins.get(key, "")).split(",")[0]) if key in sig_pins else None
            hit = by_pin.get(pin) if pin else None
            if hit and hit[0] == idx and hit[1] < w:
                bits[hit[1]] = target_lo + lab
        wanted[idx]["switches"] = bits
        return True

    def board_providers(cap):
        out = []
        for pidx, perif, _p in plans[cap].providers:
            if perif.get("id") != _TM_PID:
                out.append((pidx, plans[cap].widths.get(pidx, 1)))
        return out

    def set_default(cap):
        # the concatenation in attach order: no entry anywhere
        for pidx, _perif, _p in plans[cap].providers:
            wanted[pidx][cap] = None

    # ---- key and sw: what the lab reads -----------------------------------
    for lab_port, cap in (("key", "buttons"), ("sw", "switches")):
        expr = conns.get(lab_port)
        if expr is None:
            continue
        if not plans[cap].providers and cap != "switches":
            continue                # (switches may still come from the keys)
        if not expr.strip() or re.match(r"^\s*(?:\d*'[bdh]?0+|'0|0)\s*$", expr):
            # `.sw ( )` / `.sw ( '0 )`: the lab reads nothing on this bus (colorlight, orangecrab)
            for pidx, _perif, _p in plans[cap].providers:
                wanted[pidx][cap] = []
            continue
        kind, port, rng = _source_kind(_follow(text, expr), ports, params, tm_names)
        if kind == "other":
            terms = _or_terms(text, " ".join(expr.split()), ports, params, tm_names)
            if terms and all(k in ("tm", "board") for k, _p, _r, _lo in terms):
                board = board_providers(cap)
                total = sum(w for _i, w in board)
                for pidx, _w in board:
                    wanted[pidx][cap] = []
                if tm_idx is not None and any(p == tm_idx for p, _perif, _pp in plans[cap].providers):
                    wanted[tm_idx][cap] = []
                for k, port, rng, target_lo in terms:
                    if k == "tm":
                        if tm_idx is None:
                            notes.append("{}: BGM ORs the TM1638 in but no tm1638_led_key attach".format(lab_port))
                            break
                        lo, hi = rng if rng else (0, _TM_WIDTH - 1)
                        wanted[tm_idx][cap] = [target_lo + (i - lo) if lo <= i <= hi else None for i in range(_TM_WIDTH)]
                    else:
                        if not board and cap == "switches":
                            if not keys_as_switches(port, rng, target_lo):
                                break
                            continue
                        if not board:
                            notes.append("{}: BGM ORs {} in but no board provider of {} is attached".format(lab_port, port, cap))
                            break
                        lo, hi = rng if rng else (0, total - 1)
                        n = 0
                        for pidx, w in board:
                            bits = []
                            for _b in range(w):
                                bits.append(target_lo + (n - lo) if lo <= n <= hi else None)
                                n += 1
                            wanted[pidx][cap] = bits
                continue
        if kind == "tm":
            if tm_idx is None:
                notes.append("{}: BGM feeds the lab from the TM1638 but no tm1638_led_key attach".format(lab_port))
                continue
            wanted[tm_idx][cap] = list(range(_TM_WIDTH))
            for pidx, _w in board_providers(cap):
                wanted[pidx][cap] = []
        elif kind == "board":
            board = board_providers(cap)
            owner = _attach_for_port(cfg, pinmap, sig_pins, port)
            owner_pid = attaches[owner].get("peripheral") if owner is not None else None
            if cap == "switches" and (not board or owner_pid == "button_array"):
                # the keys are the switches (`.sw ( lab_key )`, lab_key = ~ KEY):
                # the button_array provides them, a switch bank BGM does not
                # read (omdazz, ax7035b) carries no lab bit
                if keys_as_switches(port, rng):
                    for pidx, _w in board:
                        if pidx != owner:
                            wanted[pidx][cap] = []
                    if tm_idx is not None and any(p == tm_idx for p, _perif, _pp in plans[cap].providers):
                        wanted[tm_idx][cap] = []
                continue
            if not board:
                notes.append("{}: BGM feeds the lab from {} but no board provider of {} is attached".format(lab_port, port, cap))
                continue
            # BGM's source bits, LSB first, mapped onto our providers' bits by
            # pin: a whole port, a slice (`SW [w_lab_sw - 1:0]`), a subset or a
            # concatenation (`{ KEY2, KEY3, KEY4 }`, icebreaker's three of four
            # buttons); bits BGM does not read get no lab bit
            src_bits = _source_bits(port, rng, sig_pins, params)
            by_pin = _provider_pin_bits(resolved, pinmap, plans, cap)
            mapping = {pidx: [None] * w for pidx, w in board}
            unmatched = []
            for lab, key in enumerate(src_bits):
                pin = sy._norm_pin(str(sig_pins.get(key, "")).split(",")[0]) if key in sig_pins else None
                hit = by_pin.get(pin) if pin else None
                if hit is None:
                    unmatched.append(key)
                    continue
                pidx, bit = hit
                if bit < len(mapping[pidx]):
                    mapping[pidx][bit] = lab
            if unmatched:
                notes.append("{}: BGM reads {} on pins no {} provider covers".format(lab_port, unmatched[:4], cap))
            default = {}
            off = 0
            for pidx, w in board:
                default[pidx] = list(range(off, off + w))
                off += w
            if mapping == default and not has_tm:
                set_default(cap)
                continue
            for pidx, bits in mapping.items():
                wanted[pidx][cap] = bits
            if tm_idx is not None and any(p == tm_idx for p, _perif, _pp in plans[cap].providers):
                wanted[tm_idx][cap] = []
        else:
            notes.append("{}: lab reads {!r} (not a port or the TM1638); left as is".format(lab_port, expr))

    # ---- led and digit: what the lab drives ------------------------------
    for lab_port, cap, tm_sig in (("led", "leds", "tm_led"), ("digit", "seven_segment", "tm_digit")):
        if not plans[cap].providers:
            continue
        # the net the lab drives (`lab_led`, zybo's `led_top`) and whether a
        # TM1638 net is assigned from it
        lab_net = re.match(r"^\s*([A-Za-z_]\w*)", conns.get(lab_port, "") or "")
        lab_net = lab_net.group(1) if lab_net else "lab_" + lab_port
        shares = has_tm and any(re.search(r"\bassign\s+" + re.escape(n) + r"\s*=\s*" + re.escape(lab_net) + r"\b", text)
                                for n in tm_names)
        direct = conns.get(lab_port, "").strip()
        if has_tm and not shares and direct in tm_names:
            # `.led ( tm_led )`: the TM1638 is the lab's whole bus, the board's
            # own LEDs / digits show nothing (tang_primer_25k, zybo)
            wanted[tm_idx][cap] = list(range(_TM_WIDTH))
            for pidx, _w in board_providers(cap):
                wanted[pidx][cap] = []
            continue
        if not shares:
            if tm_idx is not None and any(p == tm_idx for p, _perif, _pp in plans[cap].providers) and has_tm:
                notes.append("{}: TM1638 present but BGM does not connect {} to lab_{}".format(lab_port, tm_sig, lab_port))
            if cap == "leds":
                # BGM may hand the lab only part of the LEDs (de2_115: LEDG [7:0];
                # LEDR is its own 7-seg dp emulation): map by BGM's led bits
                lbits, _n = led_bits(text, sig_pins, rev)
                if lbits:
                    by_ref = {}
                    for bit, (ref, _inv, _od) in enumerate(lbits):
                        by_ref.setdefault(str(ref), bit)
                    mapping, default, off = {}, {}, 0
                    for pidx, w in board_providers(cap):
                        bind = (resolved["peripherals"][pidx].get("bind") or {}).get("led")
                        refs = bind if isinstance(bind, list) else ([str(bind)] if w == 1 else ["{}[{}]".format(bind, i) for i in range(w)])
                        mapping[pidx] = [by_ref.get(str(r).strip('"')) for r in refs]
                        default[pidx] = list(range(off, off + w))
                        off += w
                    if mapping != default and any(b is not None for bits in mapping.values() for b in bits):
                        for pidx, bits in mapping.items():
                            wanted[pidx][cap] = bits
                        continue
            set_default(cap)
            continue
        wanted[tm_idx][cap] = list(range(_TM_WIDTH))
        if cap == "leds":
            lbits, lnotes = led_bits(text, sig_pins, rev)
            notes.extend("led: " + n for n in lnotes)
            if lbits is None:
                # a form led_bits() does not read (colorlight's open-drain
                # `LED[0] = lab_led[0] ? 1'b0 : 1'bz`): the board LEDs are
                # the low bits, as in every BGM top that shares with a TM1638
                for pidx, w in board_providers(cap):
                    wanted[pidx][cap] = list(range(w))
                notes.append("led: board LEDs taken as lab_led's low bits (assign form not parsed)")
                continue
            by_ref = {}
            for bit, (ref, _inv, _od) in enumerate(lbits or []):
                by_ref.setdefault(str(ref), bit)
            for pidx, w in board_providers(cap):
                bind = (resolved["peripherals"][pidx].get("bind") or {}).get("led")
                refs = bind if isinstance(bind, list) else None
                if refs is None:
                    bank = str(bind)
                    refs = [bank] if w == 1 else ["{}[{}]".format(bank, i) for i in range(w)]
                bits = [by_ref.get(str(r).strip('"')) for r in refs]
                if all(b is None for b in bits) and lbits:
                    # a whole-bank bind: BGM's led bits name the bank as one ref
                    if len(lbits) >= w and all(str(lbits[i][0]).split("[")[0] == str(bind).split("[")[0] for i in range(w)):
                        bits = list(range(w))
                wanted[pidx][cap] = bits
        else:
            for pidx, w in board_providers(cap):
                wanted[pidx][cap] = list(range(w))
    return wanted, param_changes


def _current_lab_bits(attaches):
    return {i: dict(a.get("lab_bits") or {}) for i, a in enumerate(attaches)}


def _render_lab_bits(indent, bits_by_cap):
    lines = [indent + "  lab_bits:   # bits of the lab bus this component carries (BGM's board top; tools/sync_from_bgm.py --lab-bits)"]
    for cap, bits in bits_by_cap.items():
        lines.append("{}    {}: [{}]".format(indent, cap, ", ".join("~" if b is None else str(b) for b in bits)))
    return lines


def _write_lab_bits(lines, attaches, wanted):
    """Rewrite every attach's lab_bits: block to `wanted` (cap -> bits, None
    = no entry). Returns the list of changes."""
    changes = []
    # blocks in file order, matched to attaches in file order
    order = []
    for pid in OrderedDict((a.get("peripheral"), None) for a in attaches):
        for k, blk in enumerate(sy._find_attach_blocks(lines, pid)):
            order.append((blk[0], pid, k))
    order.sort()
    idx_by_pid_occ = {}
    occ = {}
    for i, a in enumerate(attaches):
        pid = a.get("peripheral")
        idx_by_pid_occ[(pid, occ.get(pid, 0))] = i
        occ[pid] = occ.get(pid, 0) + 1
    # edit from the bottom so earlier line numbers stay valid
    for start, pid, k in reversed(order):
        i = idx_by_pid_occ.get((pid, k))
        if i is None:
            continue
        blk = sy._find_attach_blocks(lines, pid)[k]
        b0, b1, indent = blk
        cur = dict(attaches[i].get("lab_bits") or {})
        want = OrderedDict((c, b) for c, b in (wanted.get(i) or {}).items() if b is not None)
        keep = OrderedDict((c, b) for c, b in cur.items() if c not in (wanted.get(i) or {}))   # caps this slice does not own
        want_all = OrderedDict(list(keep.items()) + list(want.items()))
        if {c: list(b) for c, b in want_all.items()} == {c: list(b) for c, b in cur.items()}:
            continue
        # drop the old block
        key = indent + "  lab_bits:"
        for m in range(b0, b1):
            if lines[m].startswith(key):
                n = m + 1
                while n < b1 and (lines[n].startswith(indent + "    ") or lines[n].strip() == ""):
                    n += 1
                while n > m + 1 and lines[n - 1].strip() == "":
                    n -= 1
                del lines[m:n]
                b1 -= n - m
                break
        end = b1
        while end > b0 and lines[end - 1].strip() == "":
            end -= 1
        if want_all:
            lines[end:end] = _render_lab_bits(indent, want_all)
        changes.append("{}#{} lab_bits {} -> {}".format(pid, k, {c: list(b) for c, b in cur.items()} or "none",
                                                       {c: list(b) for c, b in want_all.items()} or "none"))
    return changes


def _set_attach_params(lines, attaches, changes_by_idx):
    """Add / replace `key: value` entries in the params: of the given attaches."""
    out = []
    order = {}
    for i, a in enumerate(attaches):
        pid = a.get("peripheral")
        order[i] = (pid, sum(1 for b in attaches[:i] if b.get("peripheral") == pid))
    for i in sorted(changes_by_idx, reverse=True):
        pid, occ = order[i]
        blocks = sy._find_attach_blocks(lines, pid)
        if occ >= len(blocks):
            continue
        b0, b1, indent = blocks[occ]
        for key, value in changes_by_idx[i].items():
            rendered = "{}    {}: {}".format(indent, key, "true" if value is True else value)
            done = False
            for k in range(b0, b1):
                if re.match(r"^{}    {}:".format(indent, re.escape(key)), lines[k]):
                    done = lines[k] != rendered
                    lines[k] = rendered
                    break
            else:
                for k in range(b0, b1):
                    if lines[k].strip() == "params:":
                        lines.insert(k + 1, rendered)
                        done = True
                        break
                else:
                    lines.insert(b0 + 1, indent + "  params:")
                    lines.insert(b0 + 2, rendered)
                    done = True
            if done:
                out.append("{}#{} params.{} = {}".format(pid, occ, key, value))
    return out


def apply_lab_bits(path, dry_run):
    original = open(path, encoding="utf-8").read()
    cfg = yaml.safe_load(original)["Configuration"]
    if cfg.get("manual"):
        return "skip (manual configuration)"
    vdir = bgm_oracle.variant_dir_for(cfg["id"], cfg["board"])
    if vdir is None:
        return "skip (no BGM variant)"
    from tools import codegen
    config_init.clear_cache()
    resolved = config_init.resolve_configuration(cfg["id"])
    try:
        plans = codegen.build_capability_plans(resolved)
    except codegen.CodegenError as exc:
        return "ERROR: {} (run --lab-bits after any slice that rewrites attaches)".format(exc)
    pinmap = resolved["board_pinmap"]
    pp = bgm_oracle.preprocess_variant(vdir)
    text = bgm_oracle.strip_comments(pp.text)
    sig_pins = sy._bgm_signal_pins(vdir)
    bound_banks = set()
    for a in cfg.get("attach") or []:
        for ref in (a.get("bind") or {}).values():
            for one in (ref if isinstance(ref, list) else [ref]):
                parts = _split_ref(one) if isinstance(one, str) else None
                if parts:
                    bound_banks.add(parts[0])
    rev = _Rev(pinmap, bound_banks)
    notes = []
    wanted, param_changes = _lab_bits_wanted(text, cfg, resolved, plans, sig_pins, rev, notes)
    lines = original.split("\n")
    # params first (they do not move the lab_bits blocks), then the bits
    changes = _set_attach_params(lines, cfg.get("attach") or [], {
        i: {k: v for k, v in ch.items() if (cfg["attach"][i].get("params") or {}).get(k) != v}
        for i, ch in param_changes.items()})
    changes = [c for c in changes if c] + _write_lab_bits(lines, cfg.get("attach") or [], wanted)
    if changes and not dry_run:
        new = "\n".join(lines)
        yaml.safe_load(new)                      # must still parse
        with open(path, "w", encoding="utf-8") as f:
            f.write(new)
    out = "; ".join(changes) if changes else "unchanged"
    if notes:
        out += " | " + "; ".join(notes)
    return out
