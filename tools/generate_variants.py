"""
For every variant directory in basics-graphics-music/boards/, generate a
self-contained configuration YAML under config/configurations/<variant>.yml.

Peripheral bindings for external modules (TM1638, INMP441, LCD, ...) are
extracted from each variant's `board_specific_top.sv` by scanning module
instantiations and translating their port maps into bank references.

For each variant:
1. Determine the physical board from the manifest.
2. Determine the toolchain from the suffix (`_yosys` → open-source) and the
   board's FPGA family.
3. Parse the variant's own constraint file(s) to learn which signals are pinned.
4. Group signals by peripheral via the same classify() rules used for boards.
5. Emit explicit attach: blocks. Each peripheral binding references board
   pinBanks rather than absolute pins, so the configuration stays portable
   if the board's pin map is later corrected.

Heuristics for what's bound:
- On-board peripherals present in the variant's signal list  ->  attach.
- External peripherals inferred from variant directory suffixes
  (`_tm1638`, `_hdmi`, `_lcd_480_272`, ...)              ->  attach with TODO bind:
  for the user to fill in pin bindings if not auto-detected.
"""

import os
import re
import sys
from collections import OrderedDict, defaultdict

import yaml

from tools import board_manifest
from tools import curate_board
from tools import import_constraints


REPO = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
# basics-graphics-music is expected to live as a sibling of this repo.
# Override with $UNIFPGA_BGM_DIR for a non-default layout.
_BGM_DIR = os.environ.get(
    "UNIFPGA_BGM_DIR",
    os.path.normpath(os.path.join(REPO, "..", "basics-graphics-music")))
BGM_BOARDS = os.path.join(_BGM_DIR, "boards")
OUT_DIR = os.path.join(REPO, "config", "configurations")


# Family → vendor toolchain id. Mirrors the entries in config/toolchains.yml.
_VENDOR_TOOLCHAIN_BY_FAMILY = {
    "Artix 7": "vivado",
    "Spartan 7": "vivado",
    "Kintex 7": "vivado",
    "Zynq 7000": "vivado",
    "Zynq Ultrascale+": "vivado",
    "Spartan 6": "ise",
    "Cyclone": "quartus2",            # original Cyclone (not II/III/IV/V)
    "Cyclone II": "quartus2",
    "Cyclone III": "quartus2",
    "Cyclone IV": "quartus_prime",
    "Cyclone V": "quartus_prime",
    "Cyclone V SoC": "quartus_prime",
    "MAX 10": "quartus_prime",
    "MAX II CPLD": "quartus2",
    "MAX V CPLD": "quartus2",
    "ICE40": "nextpnr_icestorm",      # no vendor toolchain — yosys is the only option
    "ECP5": "lattice_diamond",
    "MachXO2": "lattice_diamond",
    "MachXO3": "lattice_diamond",
    "Trion": "efinity",
    "Titanium": "efinity",
    "LittleBee/GW1N": "gowin_eda",
    "LittleBee/GW1NR": "gowin_eda",
    "LittleBee/GW1NSR": "gowin_eda",
    "Arora/GW2A": "gowin_eda",
    "AroraV/GW5A": "gowin_eda",
    "AroraV/GW5AST": "gowin_eda",
}

_YOSYS_TOOLCHAIN_BY_FAMILY = {
    "ICE40": "nextpnr_icestorm",
    "ECP5": "nextpnr_trellis",
    "MachXO2": "nextpnr_trellis",
    "Artix 7": "nextpnr_openxc7",
    "Spartan 7": "nextpnr_openxc7",
    "Kintex 7": "nextpnr_openxc7",
    "Zynq 7000": "nextpnr_openxc7",
    "Cyclone V": "nextpnr_mistral",
    "Cyclone V SoC": "nextpnr_mistral",
    "LittleBee/GW1N": "nextpnr_apicula",
    "LittleBee/GW1NR": "nextpnr_apicula",
    "LittleBee/GW1NSR": "nextpnr_apicula",
    "Arora/GW2A": "nextpnr_apicula",
}


# Variant-name suffix patterns -> (peripheral_id, params) to attach.
_PERIPHERAL_BY_SUFFIX = [
    (r"_lcd_480_272", "lcd_480_272", {}),
    (r"_lcd_800_480", "lcd_800_480", {}),
    (r"_lcd_ml6485",  "lcd_ml6485",  {}),
    (r"_dvi_12b",     "dvi_12bit",   {}),
    (r"_dvi_24b",     "dvi_24bit",   {}),
    (r"_hdmi",        "hdmi_tmds",   {}),
    (r"_pmod_hdmi",   "hdmi_tmds",   {}),
    (r"_pmod_vga",    "vga_4bit",    {}),
    (r"_pmod_hub75e_led_matrix", "hub75e_led_matrix", {}),
    (r"_pmod_mic3",   "pmod_mic3",   {}),
    (r"_tm1638(?!_virtual)", "tm1638_led_key", {}),
    (r"_vga666",      "vga_4bit",    {}),
    (r"_vga_pmod",    "vga_4bit",    {}),
]


# Suffixes that are pure variant tags, not peripheral attachments.
_VARIANT_TAGS = ("_yosys", "_alt", "_hackathon", "_no_ip", "_bare", "_virtual_switches",
                 "_sd", "_no_dvi", "_no_hdmi", "_no_tm1638", "_quartus_13_1_or_older",
                 "_wo_dig_0")


# Peripherals that provide an "exclusive" capability — only one allowed per
# configuration. Used to deduplicate when SV inference + suffix inference
# both add audio_in / screen / etc.
_EXCLUSIVE_BY_PERIPHERAL = {
    "pdm_mic":         "audio_in",
    "inmp441_i2s_mic": "audio_in",
    "pmod_mic3":       "audio_in",
    "pwm_amp":         "audio_out",
    "vga_4bit":        "screen",
    "lcd_480_272":     "screen",
    "lcd_800_480":     "screen",
    "lcd_ml6485":      "screen",
    "hdmi_tmds":       "screen",
    "dvi_12bit":       "screen",
    "dvi_24bit":       "screen",
    "dvi_pmod_ddr_24b":"screen",
    "hub75e_led_matrix": "screen",
    "uart_2wire":      "serial_console",
    "uart_4wire":      "serial_console",
}


def infer_toolchain(family, variant_name):
    if "_yosys" in variant_name:
        return _YOSYS_TOOLCHAIN_BY_FAMILY.get(family, "<UNKNOWN>")
    return _VENDOR_TOOLCHAIN_BY_FAMILY.get(family, "<UNKNOWN>")


def infer_external_peripherals(variant_name, pin_banks, sv_bindings):
    """
    Return [(peripheral_id, params, bind), ...] for every external peripheral
    we identified. Sources of binding (highest precedence first):

      1. MANUAL_BINDINGS[variant][peripheral]
      2. peripheral module instantiated in board_specific_top.sv
      3. obvious bank match (e.g., lcd_480_272 + onboard_lcd)
      4. empty (TODO comment)
    """
    manual = MANUAL_BINDINGS.get(variant_name, {})
    replace_pair = manual.get("_replace_peripheral")  # (old_id, new_id) optional

    out = []
    seen = set()
    used_exclusive = set()

    def maybe_add(perip, params, bind):
        if perip in seen:
            return
        cap = _EXCLUSIVE_BY_PERIPHERAL.get(perip)
        if cap and cap in used_exclusive:
            return
        seen.add(perip)
        if cap:
            used_exclusive.add(cap)
        out.append((perip, params, bind))

    for pattern, perip, params in _PERIPHERAL_BY_SUFFIX:
        if not re.search(pattern, variant_name):
            continue
        if replace_pair and perip == replace_pair[0]:
            perip = replace_pair[1]
        bind = manual.get(perip) or sv_bindings.get(perip) or _auto_bind(perip, pin_banks)
        maybe_add(perip, params, bind)

    for perip, bind in sv_bindings.items():
        maybe_add(perip, {}, manual.get(perip) or bind)

    for perip, bind in manual.items():
        if perip.startswith("_"):
            continue
        maybe_add(perip, {}, bind)

    return out


# Mapping: peripheral SV module name -> (peripheral catalog id, port_to_signal map).
# port_to_signal renames the SV port name to the catalog peripheral's signal name.
SV_MODULE_TO_PERIPHERAL = {
    "tm1638_board_controller": ("tm1638_led_key",
                                 {"sio_data": "dio", "sio_clk": "clk", "sio_stb": "stb"}),
    "tm1638_sio": ("tm1638_led_key",
                   {"sio_data": "dio", "sio_clk": "clk", "sio_stb": "stb"}),
    "inmp441_mic_i2s_receiver": ("inmp441_i2s_mic",
                                  {"sck": "sck", "ws": "ws", "sd": "sd", "lr": "lr"}),
    "inmp441_mic_i2s_receiver_alt": ("inmp441_i2s_mic",
                                      {"sck": "sck", "ws": "ws", "sd": "sd", "lr": "lr"}),
    "digilent_pmod_mic3_spi_receiver": ("pmod_mic3",
                                         {"cs": "cs", "sclk": "sclk", "miso": "miso"}),
    # Module ports == peripheral signal names (ck/oe/st, a..e). The system
    # `clk`/`rst` ports are context, not pins, and must not be mapped.
    "hub75e_led_matrix": ("hub75e_led_matrix",
                          {"r1": "r1", "g1": "g1", "b1": "b1",
                           "r2": "r2", "g2": "g2", "b2": "b2",
                           "a": "a", "b": "b", "c": "c", "d": "d", "e": "e",
                           "ck": "ck", "oe": "oe", "st": "st"}),
}

_PORT_RE = re.compile(
    r"\.\s*(?P<port>[a-z_][a-z0-9_]*)\s*\(\s*(?P<expr>[^()]*?)\s*\)",
    re.IGNORECASE,
)


def _match_balanced(text, start):
    """Given text and an index pointing at '(', return the index just after
    the matching ')'. Returns -1 on mismatch."""
    if start >= len(text) or text[start] != "(":
        return -1
    depth = 0
    i = start
    while i < len(text):
        c = text[i]
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return -1


def _find_instantiations(text, module_names):
    """Yield (module_name, port_body_text) for every instantiation in `text`
    whose module name is in `module_names`."""
    pos = 0
    name_re = re.compile(
        r"\b(" + "|".join(re.escape(m) for m in module_names) + r")\b",
        re.IGNORECASE,
    )
    while True:
        m = name_re.search(text, pos)
        if m is None:
            return
        module = m.group(1).lower()
        i = m.end()
        # Skip whitespace.
        while i < len(text) and text[i].isspace():
            i += 1
        # Optional parameter block: '#' '(' ... ')'
        if i < len(text) and text[i] == "#":
            i += 1
            while i < len(text) and text[i].isspace():
                i += 1
            if i < len(text) and text[i] == "(":
                end = _match_balanced(text, i)
                if end < 0:
                    pos = m.end()
                    continue
                i = end
        # Skip whitespace.
        while i < len(text) and text[i].isspace():
            i += 1
        # Instance name (identifier).
        ident_m = re.match(r"[A-Za-z_][A-Za-z0-9_]*", text[i:])
        if ident_m:
            i += ident_m.end()
        # Skip whitespace.
        while i < len(text) and text[i].isspace():
            i += 1
        # Port-binding block: '(' ... ')'
        if i < len(text) and text[i] == "(":
            end = _match_balanced(text, i)
            if end < 0:
                pos = m.end()
                continue
            body = text[i + 1:end - 1]
            yield module, body
            pos = end
        else:
            pos = m.end()


def _strip_sv_comments(text):
    """Strip // line comments and /* */ block comments — naive but enough."""
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    text = re.sub(r"//[^\n]*", "", text)
    return text


def _normalize_pin_expr(expr):
    """`GPIO [3]` -> `GPIO[3]`. Returns None if expr is not a pin reference."""
    expr = expr.strip()
    expr = re.sub(r"\s*\[\s*", "[", expr)
    expr = re.sub(r"\s*\]\s*", "]", expr)
    # Plain identifier or identifier[index]
    if re.match(r"^[A-Za-z_][A-Za-z0-9_]*(?:\[\d+\])?$", expr):
        return expr
    return None


def _resolve_pin_to_bank(pin_signal, pin_banks):
    """
    Given a pin-signal reference like 'GPIO[2]' or 'JA[3]' and the curated
    pin_banks of the target board, return a bank-relative reference (e.g.,
    'gpio[2]' or 'pmod_ja[2]') or None if it can't be resolved.
    """
    from tools.curate_board import classify
    dest = classify(pin_signal)
    if dest is None:
        return None
    kind = dest[0]
    if kind == "scalar":
        return dest[1] if dest[1] in pin_banks else None
    if kind == "list":
        bank, idx = dest[1], dest[2]
        if bank in pin_banks:
            return "{}[{}]".format(bank, idx)
        return None
    if kind == "subkey":
        bank, sub = dest[1], dest[2]
        if bank in pin_banks:
            return "{}.{}".format(bank, sub)
        return None
    if kind == "sublist":
        bank, sub, idx = dest[1], dest[2], dest[3]
        if bank in pin_banks:
            return "{}.{}[{}]".format(bank, sub, idx)
        return None
    return None


_INCLUDE_RE = re.compile(r'`include\s+"([^"]+)"')


def _read_sv_with_includes(sv_path, seen=None):
    r"""Read an SV file, inlining ``\`include "..."`` directives by appending
    the included file's contents. Cyclic includes are guarded by `seen`."""
    seen = seen or set()
    real = os.path.realpath(sv_path)
    if real in seen or not os.path.exists(sv_path):
        return ""
    seen.add(real)
    with open(sv_path) as f:
        text = f.read()
    out = []
    base_dir = os.path.dirname(sv_path)
    for line in text.splitlines():
        m = _INCLUDE_RE.search(line)
        if m:
            inc_path = os.path.join(base_dir, m.group(1))
            out.append(_read_sv_with_includes(inc_path, seen))
        else:
            out.append(line)
    return "\n".join(out)


def parse_sv_bindings(sv_path, pin_banks):
    """
    Read a board_specific_top.sv and return a dict
        peripheral_id -> { peripheral_signal_name: bank_reference }
    for every peripheral module instantiation we recognize.
    """
    if not os.path.exists(sv_path):
        return {}
    text = _strip_sv_comments(_read_sv_with_includes(sv_path))

    bindings = {}
    for mod, body in _find_instantiations(text, list(SV_MODULE_TO_PERIPHERAL.keys())):
        peripheral_id, port_to_signal = SV_MODULE_TO_PERIPHERAL[mod]
        bind = {}
        for pm in _PORT_RE.finditer(body):
            port = pm.group("port").lower()
            if port not in port_to_signal:
                continue
            expr = _normalize_pin_expr(pm.group("expr"))
            if expr is None:
                continue
            bank_ref = _resolve_pin_to_bank(expr, pin_banks)
            if bank_ref is None:
                continue
            bind[port_to_signal[port]] = bank_ref
        if bind:
            # If the same peripheral appears multiple times (rare), keep the last.
            bindings[peripheral_id] = bind
    return bindings


# Manual bindings derived from each variant's `board_specific_top.sv` for
# peripherals whose pin map cannot be inferred automatically (carrier-card
# layouts, DDR-encoded buses, SV-level bit packing, etc.). The generator
# applies these on top of the heuristic-driven bindings so it stays idempotent.
MANUAL_BINDINGS = {
    # DE0-Nano / DE0-Nano-SoC VGA carriers — pin map from the
    # `assign GPIO_1[N] = ...` block in board_specific_top.sv
    "de0_nano_vga666": {
        "vga_4bit": {
            "r":  ["gpio_1[11]", "gpio_1[17]", "gpio_1[3]",  "gpio_1[1]"],
            "g":  ["gpio_1[5]",  "gpio_1[19]", "gpio_1[15]", "gpio_1[13]"],
            "b":  ["gpio_1[21]", "gpio_1[7]",  "gpio_1[9]",  "gpio_1[23]"],
            "hs": "gpio_1[31]",
            "vs": "gpio_1[33]",
        },
    },
    "de0_nano_vga_pmod": {
        "vga_4bit": {
            "r":  ["gpio_1[33]", "gpio_1[31]", "gpio_1[29]", "gpio_1[27]"],
            "g":  ["gpio_1[23]", "gpio_1[21]", "gpio_1[19]", "gpio_1[17]"],
            "b":  ["gpio_1[15]", "gpio_1[13]", "gpio_1[11]", "gpio_1[9]"],
            "hs": "gpio_1[3]",
            "vs": "gpio_1[5]",
        },
    },
    "de0_nano_soc_vga666": {
        "vga_4bit": {
            "r":  ["gpio_1[13]", "gpio_1[19]", "gpio_1[5]",  "gpio_1[3]"],
            "g":  ["gpio_1[7]",  "gpio_1[21]", "gpio_1[17]", "gpio_1[15]"],
            "b":  ["gpio_1[23]", "gpio_1[9]",  "gpio_1[11]", "gpio_1[25]"],
            "hs": "gpio_1[33]",
            "vs": "gpio_1[35]",
        },
    },
    "de0_nano_soc_vga_pmod": {
        "vga_4bit": {
            "r":  ["gpio_1[35]", "gpio_1[33]", "gpio_1[31]", "gpio_1[29]"],
            "g":  ["gpio_1[25]", "gpio_1[23]", "gpio_1[21]", "gpio_1[19]"],
            "b":  ["gpio_1[17]", "gpio_1[15]", "gpio_1[13]", "gpio_1[11]"],
            "hs": "gpio_1[5]",
            "vs": "gpio_1[7]",
        },
    },

    # 1BitSquared DVI 12b Pmod — derived from `assign dvi_data = {...}` in
    # icebreaker_dvi_24b_tm1638_yosys/board_specific_top.sv (the 12b variant
    # `\`include`s that file).
    "icebreaker_dvi_12b_no_tm1638_yosys": {
        "dvi_12bit": {
            "r":  ["pmod_p1a[5]", "pmod_p1a[1]", "pmod_p1a[4]", "pmod_p1a[0]"],
            "g":  ["pmod_p1a[7]", "pmod_p1a[3]", "pmod_p1a[6]", "pmod_p1a[2]"],
            "b":  ["pmod_p1b[5]", "pmod_p1b[2]", "pmod_p1b[4]", "pmod_p1b[0]"],
            "hs": "pmod_p1b[3]",
            "vs": "pmod_p1b[7]",
            "de": "pmod_p1b[6]",
            "ck": "pmod_p1b[1]",
        },
    },
    "icebreaker_dvi_12b_tm1638_yosys": {
        "dvi_12bit": {
            "r":  ["pmod_p1a[5]", "pmod_p1a[1]", "pmod_p1a[4]", "pmod_p1a[0]"],
            "g":  ["pmod_p1a[7]", "pmod_p1a[3]", "pmod_p1a[6]", "pmod_p1a[2]"],
            "b":  ["pmod_p1b[5]", "pmod_p1b[2]", "pmod_p1b[4]", "pmod_p1b[0]"],
            "hs": "pmod_p1b[3]",
            "vs": "pmod_p1b[7]",
            "de": "pmod_p1b[6]",
            "ck": "pmod_p1b[1]",
        },
    },

    # 1BitSquared DVI 24b Pmod — DDR-encoded, no parallel R/G/B mapping.
    # Replace dvi_24bit with the DDR-aware peripheral.
    "icebreaker_dvi_24b_no_tm1638_yosys": {
        "_replace_peripheral": ("dvi_24bit", "dvi_pmod_ddr_24b"),
        "dvi_pmod_ddr_24b": {
            "pmod_a": "pmod_p1a",
            "pmod_b": "pmod_p1b",
        },
    },
    "icebreaker_dvi_24b_tm1638_yosys": {
        "_replace_peripheral": ("dvi_24bit", "dvi_pmod_ddr_24b"),
        "dvi_pmod_ddr_24b": {
            "pmod_a": "pmod_p1a",
            "pmod_b": "pmod_p1b",
        },
    },

    # Tang Primer 25k carriers
    "tang_primer_25k_pmod_hdmi": {
        # Bind to the on-board HDMI bank (pin values are Gowin diff pairs "P,N").
        "hdmi_tmds": {
            "clk_p": "onboard_hdmi_0.clk_p",
            "d_p":   "onboard_hdmi_0.d_p",
        },
    },
    "tang_primer_25k_pmod_vga": {
        # PMOD layout from board_specific_top.sv:
        #   PMOD_0 = { green[3:0], 2'b0, vsync, hsync }
        #   PMOD_1 = { red[3:0], blue[3:0] }
        "vga_4bit": {
            "r":  ["pmod_1[4]", "pmod_1[5]", "pmod_1[6]", "pmod_1[7]"],
            "g":  ["pmod_0[4]", "pmod_0[5]", "pmod_0[6]", "pmod_0[7]"],
            "b":  ["pmod_1[0]", "pmod_1[1]", "pmod_1[2]", "pmod_1[3]"],
            "hs": "pmod_0[0]",
            "vs": "pmod_0[1]",
        },
    },
}


def _auto_bind(peripheral_id, pin_banks):
    """Look for an obvious matching bank in the board's pinBanks; return a
    bind: dict (peripheral signal -> bank reference) or {} if not found."""
    if peripheral_id in ("lcd_480_272", "lcd_800_480"):
        if "onboard_lcd" not in pin_banks:
            return {}
        bind = {}
        for sig in ("r", "g", "b", "hs", "vs", "de", "ck", "bl", "init"):
            sub = "onboard_lcd.{}".format(sig)
            bind[sig] = sub
        return bind
    if peripheral_id == "lcd_ml6485":
        # BGM drives the ML6485 panel through the LARGE_LCD_* pins (its
        # SMALL_LCD_* pins are declared but never driven).
        if "onboard_lcd" not in pin_banks:
            return {}
        return {sig: "onboard_lcd.{}".format(sig)
                for sig in ("r", "g", "b", "hs", "vs", "de", "ck", "bl", "init")}
    if peripheral_id == "hdmi_tmds":
        if "onboard_hdmi" not in pin_banks:
            return {}
        return {"clk_p": "onboard_hdmi.clk_p",
                "clk_n": "onboard_hdmi.clk_n",
                "d_p": "onboard_hdmi.d_p",
                "d_n": "onboard_hdmi.d_n"}
    return {}


# ---------------------------------------------------------------------------
# Per-variant signal -> bank lookup
# ---------------------------------------------------------------------------

def _bank_lookup_from_board_yaml(board_id):
    """
    Returns a dict mapping {pin -> (bank_name, sub_path)} where sub_path is a
    list of nested keys (or None for scalar bank, or [int] for flat-list index).
    Used to translate a variant's pin assignments to bank references.
    """
    path = os.path.join(REPO, "config", "boards", board_id + ".yml")
    if not os.path.exists(path):
        return {}
    with open(path) as f:
        d = yaml.safe_load(f)
    pin_banks = (d.get("Board") or {}).get("pinBanks") or {}
    out = {}
    for bank_name, bank in pin_banks.items():
        pins = bank.get("pins") if isinstance(bank, dict) else None
        if pins is None:
            continue
        if isinstance(pins, str):
            out[str(pins)] = (bank_name, None)
        elif isinstance(pins, list):
            for i, p in enumerate(pins):
                if p is None:
                    continue
                out[str(p)] = (bank_name, [i])
        elif isinstance(pins, dict):
            for sub, val in pins.items():
                if isinstance(val, list):
                    for i, p in enumerate(val):
                        if p is None:
                            continue
                        out[str(p)] = (bank_name, [sub, i])
                else:
                    out[str(val)] = (bank_name, [sub])
    return out


def _bank_ref(bank_name, sub_path):
    """Render a bank reference like `pmod_jc[2]` or `onboard_7seg.anodes[0]`."""
    if sub_path is None:
        return bank_name
    parts = [bank_name]
    for el in sub_path:
        if isinstance(el, int):
            parts[-1] = "{}[{}]".format(parts[-1], el)
        else:
            parts.append(el)
    return ".".join(parts)


# ---------------------------------------------------------------------------
# Peripheral-binding emission for the on-board peripherals classified out of
# the variant's pin map.
# ---------------------------------------------------------------------------

# (bank_name_pattern, peripheral_id, signal_name_or_signal_map, params_fn)
# When a variant exposes a bank we recognize, attach the matching peripheral.
_BANK_TO_PERIPHERAL = [
    # (regex on bank name, peripheral, signal_to_bank_subkey, params_from_bank)
    (r"^clk(\d+)mhz$", "clock_input", lambda bank: {"clk": bank},
                                   lambda meta: {"frequency_mhz": meta.get("frequency_mhz")} if meta.get("frequency_mhz") else {}),
    (r"^clk$",         "clock_input", lambda bank: {"clk": bank}, lambda _: {}),
    (r"^cpu_resetn$",  "reset_button", lambda bank: {"rst": bank}, lambda _: {}),
    (r"^cpu_reset$",   "reset_button", lambda bank: {"rst": bank}, lambda _: {}),
    (r"^onboard_switches$", "sw_bank", lambda bank: {"sw": bank},
                                    lambda _: None),  # width filled in below
    (r"^onboard_leds$",     "led_bank", lambda bank: {"led": bank},
                                    lambda _: None),
    (r"^onboard_leds_green$", "led_bank", lambda bank: {"led": bank},
                                    lambda _: None),
    (r"^onboard_buttons$",  "button_array", lambda bank: {"btn": bank},
                                    lambda _: None),
    (r"^onboard_button$",   "button_array", lambda bank: {"btn": bank}, lambda _: {"width": 1}),
    (r"^onboard_rgb_led_(\d+)$", "rgb_led",
        lambda bank: {"r": bank + ".r", "g": bank + ".g", "b": bank + ".b"}, lambda _: {}),
    # Shared-segment 7-segment with explicit segment fan-out + anode select.
    (r"^onboard_7seg$", "seven_segment_8digit_shared",
        lambda bank: {
            "segments": [bank + ".ca", bank + ".cb", bank + ".cc",
                         bank + ".cd", bank + ".ce", bank + ".cf", bank + ".cg"],
            "dp":       bank + ".dp",
            "digits":   bank + ".anodes",
        }, lambda _: {}),
    (r"^onboard_vga$", "vga_4bit",
        lambda bank: {"r": bank + ".r", "g": bank + ".g", "b": bank + ".b",
                      "hs": bank + ".hs", "vs": bank + ".vs"}, lambda _: {}),
    (r"^onboard_uart$", "uart_2wire",
        lambda bank: {"tx": bank + ".tx", "rx": bank + ".rx"}, lambda _: {}),
    (r"^onboard_mic$", "pdm_mic",
        lambda bank: {"clk": bank + ".clk", "data": bank + ".data", "lrsel": bank + ".lrsel"},
        lambda _: {}),
    (r"^onboard_pwm_amp$", "pwm_amp",
        lambda bank: {"pwm": bank + ".pwm", "sd": bank + ".sd"}, lambda _: {}),
    # Unbound Pmod headers expose 8 GPIO bits each.
    (r"^pmod_j[a-e]$",     "pmod_12pin", lambda bank: {"io": bank}, lambda _: {}),
    (r"^pmod_p\d+[ab]?$",  "pmod_12pin", lambda bank: {"io": bank}, lambda _: {}),
    (r"^pmod_\d+$",        "pmod_12pin", lambda bank: {"io": bank}, lambda _: {}),
]


def _bank_width(board_pin_banks, bank_name):
    bank = board_pin_banks.get(bank_name) or {}
    pins = bank.get("pins")
    if isinstance(pins, list):
        return sum(1 for p in pins if p is not None)
    return None


def _attaches_for_board(board_pin_banks):
    """For every recognized bank in the board, emit the standard peripheral attachment."""
    attaches = []
    for bank_name, bank in board_pin_banks.items():
        for pat, perip_id, bind_fn, params_fn in _BANK_TO_PERIPHERAL:
            m = re.match(pat, bank_name)
            if not m:
                continue
            entry = OrderedDict()
            entry["peripheral"] = perip_id
            params = params_fn(bank) or {}
            if perip_id in ("sw_bank", "led_bank", "button_array"):
                w = _bank_width(board_pin_banks, bank_name)
                if w is not None:
                    params = {"width": w}
            if params:
                entry["params"] = params
            entry["bind"] = bind_fn(bank_name)
            attaches.append(entry)
            break
    return attaches


# ---------------------------------------------------------------------------
# YAML emission
# ---------------------------------------------------------------------------

def _format_value(v):
    if isinstance(v, bool):
        return "true" if v else "false"
    if v is None:
        return "null"
    return str(v)


def _quote_ref(s):
    """Quote a bank reference if it contains characters that would break YAML
    flow context (`[`, `]`, `,`)."""
    s = str(s)
    if any(c in s for c in "[],"):
        return '"{}"'.format(s)
    return s


def emit_configuration(variant_name, board_id, toolchain, attaches, externals, sources):
    lines = []
    lines.append("# Configuration for variant '{}'.".format(variant_name))
    lines.append("# Generated by tools/generate_variants.py — re-run to refresh.")
    lines.append("# Source(s):")
    for s in sources:
        lines.append("#   - " + s)
    lines.append("")
    lines.append("Configuration:")
    lines.append("  id: " + variant_name)
    lines.append("  board: " + board_id)
    lines.append("  toolchain: " + toolchain)
    lines.append("")
    lines.append("  attach:")

    for entry in attaches:
        first = True
        prefix = "    - "
        for k, v in entry.items():
            indent = prefix if first else "      "
            first = False
            if isinstance(v, dict):
                lines.append(indent + k + ":")
                for sk, sv in v.items():
                    lines.append("        {}: {}".format(sk, _format_value(sv)))
            elif isinstance(v, str):
                lines.append("{}{}: {}".format(indent, k, v))
            else:
                lines.append("{}{}: {}".format(indent, k, _format_value(v)))

    if externals:
        lines.append("")
        lines.append("  # External peripherals inferred from the variant directory name.")
        for perip_id, params, bind in externals:
            lines.append("    - peripheral: {}".format(perip_id))
            if params:
                lines.append("      params:")
                for k, v in params.items():
                    lines.append("        {}: {}".format(k, _format_value(v)))
            if bind:
                lines.append("      bind:")
                for sk, sv in bind.items():
                    if isinstance(sv, list):
                        rendered = ", ".join(_quote_ref(x) for x in sv)
                        lines.append("        {}: [{}]".format(sk, rendered))
                    else:
                        lines.append("        {}: {}".format(sk, _quote_ref(sv)))
            else:
                lines.append("      bind: {}    # TODO: fill in pin bindings")

    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

def variant_to_board():
    out = {}
    for board_id, _, variants in board_manifest.BOARDS:
        for v in variants:
            out[v] = board_id
    return out


def main():
    os.makedirs(OUT_DIR, exist_ok=True)

    v2b = variant_to_board()
    # Load the board catalog via config.init, which knows about the
    # hierarchical layout (config/boards/<producer>/<family>.yml).
    sys.path.insert(0, REPO)
    from config import init as _ci
    _catalog = _ci.read_boards_catalog()
    fpga_family = {bid: b["PartFamily"] for bid, b in _catalog.items() if "PartFamily" in b}

    # Cache board pinBanks for fast lookups (delegates to read_board_pinmap
    # which resolves the new <producer>/<family>/<board>.yml path).
    board_pin_banks_cache = {}
    def _board_pin_banks(board_id):
        if board_id not in board_pin_banks_cache:
            pm = _ci.read_board_pinmap(board_id)
            board_pin_banks_cache[board_id] = (pm or {}).get("pinBanks") or {}
        return board_pin_banks_cache[board_id]

    successes = []
    failures = []

    for variant_name, board_id in sorted(v2b.items()):
        family = fpga_family.get(board_id, "")
        toolchain = infer_toolchain(family, variant_name)

        vdir = os.path.join(BGM_BOARDS, variant_name)
        if not os.path.isdir(vdir):
            failures.append("{v}: directory not found".format(v=variant_name))
            continue
        sources = [os.path.relpath(p, os.path.dirname(os.path.dirname(BGM_BOARDS)))
                   for p in import_constraints.find_all_constraint_files(vdir)]

        # Standard on-board attachments based on the *board's* pin banks.
        # If the variant doesn't actually use a particular bank, the bank still
        # exists on the physical board, so attaching is harmless and matches the
        # user's stated principle (configurations are self-contained).
        pin_banks = _board_pin_banks(board_id)
        attaches = _attaches_for_board(pin_banks)

        # SV-file bindings: parse board_specific_top.sv for peripheral module
        # instantiations and extract port-to-pin mappings.
        sv_path = os.path.join(vdir, "board_specific_top.sv")
        sv_bindings = parse_sv_bindings(sv_path, pin_banks)

        # External peripherals inferred from the variant directory suffix.
        externals = infer_external_peripherals(variant_name, pin_banks, sv_bindings)

        # Cross-bucket dedup: if an on-board peripheral and an external peripheral
        # both provide the same exclusive capability (e.g. PDM mic on-board + an
        # external INMP441), keep the on-board one and drop the external duplicate.
        used_exclusive = set()
        for entry in attaches:
            cap = _EXCLUSIVE_BY_PERIPHERAL.get(entry.get("peripheral"))
            if cap:
                used_exclusive.add(cap)
        externals = [
            (pid, params, bind) for (pid, params, bind) in externals
            if (_EXCLUSIVE_BY_PERIPHERAL.get(pid) is None
                or _EXCLUSIVE_BY_PERIPHERAL[pid] not in used_exclusive)
        ]

        out = emit_configuration(variant_name, board_id, toolchain, attaches, externals, sources)
        out_path = os.path.join(OUT_DIR, variant_name + ".yml")
        with open(out_path, "w") as f:
            f.write(out)
        successes.append((variant_name, board_id, toolchain, len(attaches), len(externals)))

    print("Generated {n} configurations".format(n=len(successes)))
    if failures:
        print("Failures:")
        for f in failures:
            print("  -", f)
    return 0


if __name__ == "__main__":
    sys.exit(main())
