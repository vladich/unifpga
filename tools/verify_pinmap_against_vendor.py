#!/usr/bin/env python3
"""
Cross-check our `config/boards/<id>.yml` pinmaps against authoritative vendor
constraint files, and import boards we don't have yet.

Currently supports the **Digilent XDC repo** (github.com/Digilent/digilent-xdc,
MIT-licensed). Their `<Board>-Master.xdc` files document every board pin with
PACKAGE_PIN + IOSTANDARD + canonical port name — vendor-authoritative.

Usage
-----
    # check coverage against repo (all known boards)
    python -m tools.verify_pinmap_against_vendor --source digilent --check

    # check one board
    python -m tools.verify_pinmap_against_vendor --source digilent --check basys3

    # import a new board (XDC → YAML)
    python -m tools.verify_pinmap_against_vendor --source digilent \
        --import nexys_video --as nexys_video

Resolves Digilent's XDC files from `~/Projects/digilent-xdc` (clone the repo
there once, or set $DIGILENT_XDC_DIR).
"""

import argparse
import os
import re
import sys

import yaml


REPO = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
BOARDS_DIR = os.path.join(REPO, "config", "boards")

DIGILENT_DIR = os.environ.get(
    "DIGILENT_XDC_DIR",
    os.path.expanduser("~/Projects/digilent-xdc"),
)


# our board id -> Digilent XDC filename: config/vendor_constraints.yml
def _digilent_boards():
    with open(os.path.join(REPO, "config", "vendor_constraints.yml"), encoding="utf-8") as f:
        return (yaml.safe_load(f)["VendorConstraints"] or {}).get("digilent") or {}


DIGILENT_BOARDS = _digilent_boards()

# Map Digilent canonical port names → our pinBank names. Our convention
# prefixes most things with "onboard_"; Digilent uses uppercase abbreviations.
# An entry of the form ("BANK", "subkey") routes to a nested pin block.
DIGILENT_PORT_MAP = {
    # Clock(s).
    "CLK100MHZ":  "clk100mhz",
    "SYSCLK":     "clk",
    "GCLK":       "clk",

    # Switches / LEDs / buttons / reset.
    "SW":         "onboard_switches",     # bus
    "LED":        "onboard_leds",
    # Button order matches the pinmaps' onboard_buttons — C-U-L-R-D
    # (center, up, left, right, down — clockwise from up). NOT alphabetical.
    "BTNC":       ("onboard_buttons", 0),
    "BTNU":       ("onboard_buttons", 1),
    "BTNL":       ("onboard_buttons", 2),
    "BTNR":       ("onboard_buttons", 3),
    "BTND":       ("onboard_buttons", 4),
    "BTN":        "onboard_buttons",      # bus form (Arty/Cmod use BTN[N])
    "CPU_RESETN": "cpu_resetn",

    # 7-segment.
    "CA":         ("onboard_7seg", "ca"),
    "CB":         ("onboard_7seg", "cb"),
    "CC":         ("onboard_7seg", "cc"),
    "CD":         ("onboard_7seg", "cd"),
    "CE":         ("onboard_7seg", "ce"),
    "CF":         ("onboard_7seg", "cf"),
    "CG":         ("onboard_7seg", "cg"),
    "DP":         ("onboard_7seg", "dp"),
    "AN":         ("onboard_7seg", "anodes"),

    # VGA — Nexys4 / Basys3 form.
    "VGA_R":      ("onboard_vga", "r"),
    "VGA_G":      ("onboard_vga", "g"),
    "VGA_B":      ("onboard_vga", "b"),
    "VGA_HS":     ("onboard_vga", "hs"),
    "VGA_VS":     ("onboard_vga", "vs"),

    # Pmod headers.
    "JA":         "pmod_ja",
    "JB":         "pmod_jb",
    "JC":         "pmod_jc",
    "JD":         "pmod_jd",
    "JXADC":      "pmod_jxadc",

    # UART (USB-UART bridge).
    "UART_RXD_OUT": ("onboard_uart", "tx"),  # Digilent's name is from the FPGA's POV
    "UART_TXD_IN":  ("onboard_uart", "rx"),
    "UART_RTS":     ("onboard_uart", "rts"),
    "UART_CTS":     ("onboard_uart", "cts"),

    # On-board mic / amplifier (Nexys 4 family).
    "M_CLK":      ("onboard_mic", "clk"),
    "M_DATA":     ("onboard_mic", "data"),
    "M_LRSEL":    ("onboard_mic", "lrsel"),
    "AUD_PWM":    ("onboard_pwm_amp", "pwm"),
    "AUD_SD":     ("onboard_pwm_amp", "sd"),

    # RGB LEDs — Nexys 4 DDR style (named by LED number 16/17).
    "LED16_R":    ("onboard_rgb_led_16", "r"),
    "LED16_G":    ("onboard_rgb_led_16", "g"),
    "LED16_B":    ("onboard_rgb_led_16", "b"),
    "LED17_R":    ("onboard_rgb_led_17", "r"),
    "LED17_G":    ("onboard_rgb_led_17", "g"),
    "LED17_B":    ("onboard_rgb_led_17", "b"),

    # RGB LEDs — Arty / Cmod style (LED0..3 with R/G/B suffixes).
    "LED0_R":     ("onboard_rgb_led_0", "r"),
    "LED0_G":     ("onboard_rgb_led_0", "g"),
    "LED0_B":     ("onboard_rgb_led_0", "b"),
    "LED1_R":     ("onboard_rgb_led_1", "r"),
    "LED1_G":     ("onboard_rgb_led_1", "g"),
    "LED1_B":     ("onboard_rgb_led_1", "b"),
    "LED2_R":     ("onboard_rgb_led_2", "r"),
    "LED2_G":     ("onboard_rgb_led_2", "g"),
    "LED2_B":     ("onboard_rgb_led_2", "b"),
    "LED3_R":     ("onboard_rgb_led_3", "r"),
    "LED3_G":     ("onboard_rgb_led_3", "g"),
    "LED3_B":     ("onboard_rgb_led_3", "b"),

    # Arty-style USB-UART (the explicit USB_UART prefix).
    "USB_UART_TXD": ("onboard_uart", "rx"),  # Arty: TXD = "transmit by USB chip" → FPGA rx
    "USB_UART_RXD": ("onboard_uart", "tx"),
}


# ----------------------------------------------------------------------------
# XDC parser — extract (port, index, pin, iostd) from `set_property -dict ...`
# ----------------------------------------------------------------------------

# Match the standard Digilent line shape:
#   set_property -dict { PACKAGE_PIN <pin>  IOSTANDARD <std> ... } [get_ports { <name>[<i>] }]
# Comments (lines starting with `#`) are skipped (master XDC ships everything
# commented; users uncomment what they need — we treat both as authoritative
# documentation).
_LINE_RE = re.compile(
    r"^\s*#?\s*set_property\s+-dict\s*\{\s*"
    r"PACKAGE_PIN\s+(?P<pin>\S+)\s+"
    r"IOSTANDARD\s+(?P<std>\S+)"
    r"[^}]*\}\s*"
    r"\[get_ports\s+\{?\s*(?P<port>[A-Za-z_][A-Za-z0-9_]*)"
    r"(?:\s*\[\s*(?P<idx>\d+)\s*\])?",
)


def parse_digilent_xdc(path):
    """Return list of (canonical_port, idx_or_None, pin, iostd) tuples."""
    out = []
    with open(path) as f:
        for line in f:
            m = _LINE_RE.match(line)
            if not m:
                continue
            port = m.group("port")
            idx = int(m.group("idx")) if m.group("idx") is not None else None
            pin = m.group("pin")
            iostd = m.group("std")
            out.append((port, idx, pin, iostd))
    return out


# ----------------------------------------------------------------------------
# YAML pinmap loader — flatten our pinBanks: { name → list/dict/str of pins }
# ----------------------------------------------------------------------------

def load_yaml_pins(board_id):
    """Return dict mapping our canonical pin token to its physical pin.
    Tokens look like:
        clk
        onboard_leds[3]
        onboard_7seg.anodes[5]
        onboard_uart.tx
    """
    path = os.path.join(BOARDS_DIR, board_id + ".yml")
    if not os.path.exists(path):
        return None
    with open(path) as f:
        data = yaml.safe_load(f)
    banks = ((data.get("Board") or {}).get("pinBanks") or {})

    out = {}
    for bank, body in banks.items():
        if isinstance(body, str):
            out[bank] = body
            continue
        if not isinstance(body, dict):
            continue
        pins = body.get("pins")
        if pins is None:
            # virtual: clock or similar
            continue
        if isinstance(pins, str):
            out[bank] = pins
        elif isinstance(pins, list):
            for i, p in enumerate(pins):
                if p is None:
                    continue
                out["{}[{}]".format(bank, i)] = p
        elif isinstance(pins, dict):
            for sub, val in pins.items():
                if isinstance(val, str):
                    out["{}.{}".format(bank, sub)] = val
                elif isinstance(val, list):
                    for i, p in enumerate(val):
                        if p is None:
                            continue
                        out["{}.{}[{}]".format(bank, sub, i)] = p
    return out


# ----------------------------------------------------------------------------
# Port-name mapping: Digilent → our token form
# ----------------------------------------------------------------------------

def map_digilent_port(port, idx, pmod_zero_based=False):
    """Return the our-format token for a Digilent (port, idx) tuple, or None
    if we have no canonical mapping (caller can decide to keep, drop, or
    flag). Match is case-insensitive — Digilent uses both lowercase
    (Arty: `led`, `sw`, `btn`) and uppercase (Nexys 4: `LED`, `SW`, `BTN`)
    in different XDCs.

    Pmod numbering: the pinmaps normalise both 0-based-contiguous
    (Arty/Zybo: ja[0..7]) and 1-based-with-gaps (Nexys/Basys: JA[1..4,7..10])
    onto a single 0-based-contiguous form. To match, we re-apply that
    normalisation here when comparing — the caller passes `pmod_zero_based`
    after pre-scanning the XDC for any `JA[0]` appearance.
    """
    mapping = DIGILENT_PORT_MAP.get(port.upper())
    if mapping is None:
        return None
    # mapping: str → flat name; tuple(bank, subkey) → composite
    if isinstance(mapping, str):
        if idx is None:
            return mapping
        # Pmod silkscreen → our 0-based contiguous index.
        if mapping.startswith("pmod_") and idx is not None:
            if pmod_zero_based:
                normalised = idx
            else:
                # 1-based silkscreen with GND/VCC at 5,6.
                _PMOD_INDEX = {1: 0, 2: 1, 3: 2, 4: 3, 7: 4, 8: 5, 9: 6, 10: 7}
                if idx not in _PMOD_INDEX:
                    return None  # GND / VCC pin, not user-visible
                normalised = _PMOD_INDEX[idx]
            return "{}[{}]".format(mapping, normalised)
        return "{}[{}]".format(mapping, idx)
    bank, subkey = mapping
    if isinstance(subkey, int):
        # Subkey is a fixed bus index (e.g. BTNC → onboard_buttons[0]).
        if idx is not None:
            # Digilent shouldn't have BTNC[1], but if so we ignore the idx.
            return "{}[{}]".format(bank, subkey)
        return "{}[{}]".format(bank, subkey)
    # subkey is a string (e.g. CA → onboard_7seg.ca).
    if idx is None:
        return "{}.{}".format(bank, subkey)
    return "{}.{}[{}]".format(bank, subkey, idx)


# ----------------------------------------------------------------------------
# Cross-check
# ----------------------------------------------------------------------------

def check_one(board_id, xdc_path, verbose=False):
    """Compare config/boards/<board_id>.yml against the XDC.
    Returns (match_count, mismatch_list, our_only, vendor_only)."""
    ours = load_yaml_pins(board_id)
    if ours is None:
        print("  [{}] NO YAML — vendor file present, our pinmap missing"
              .format(board_id), file=sys.stderr)
        return None
    vendor_tuples = parse_digilent_xdc(xdc_path)

    # Pre-scan: a JA[0] / JB[0] etc. anywhere in the file means this XDC uses
    # 0-based-contiguous Pmod numbering (Arty / Zybo / Eclypse). Otherwise it
    # uses 1-based-with-gaps (Nexys / Basys / Genesys).
    pmod_zero_based = any(
        idx == 0 and re.match(r"^J[A-E]$", port.upper())
        for port, idx, _, _ in vendor_tuples
    )

    vendor_map = {}                # our-format-token → pin
    unmapped = []                  # Digilent ports that don't fit DIGILENT_PORT_MAP
    for port, idx, pin, _ in vendor_tuples:
        token = map_digilent_port(port, idx, pmod_zero_based=pmod_zero_based)
        if token is None:
            unmapped.append((port, idx, pin))
            continue
        # Dedup duplicates (Digilent files have commented + uncommented copies
        # of the same line — both parse).
        vendor_map[token] = pin

    matches, mismatches = [], []
    for tok, our_pin in ours.items():
        v = vendor_map.get(tok)
        if v is None:
            continue
        if v == our_pin:
            matches.append(tok)
        else:
            mismatches.append((tok, our_pin, v))

    our_only = sorted(set(ours) - set(vendor_map))
    vendor_only = sorted(set(vendor_map) - set(ours))

    return matches, mismatches, our_only, vendor_only, unmapped


# ----------------------------------------------------------------------------
# Import — write a fresh config/boards/<id>.yml from a Digilent XDC
# ----------------------------------------------------------------------------

def import_xdc_as(xdc_path, board_id, family, part, producer="Xilinx"):
    """Generate a config/boards/<id>.yml from a Digilent XDC. Groups bus
    members into list pins; non-bus ports become single-pin banks.
    """
    tuples = parse_digilent_xdc(xdc_path)

    # Same Pmod normalisation as --check: detect 0-based vs 1-based per file.
    pmod_zero_based = any(
        idx == 0 and re.match(r"^J[A-E]$", port.upper())
        for port, idx, _, _ in tuples
    )

    # Collect: token → pin. Then re-bin into pinBanks structure.
    flat = {}
    iostd = "LVCMOS33"
    for port, idx, pin, std in tuples:
        token = map_digilent_port(port, idx, pmod_zero_based=pmod_zero_based)
        if token is None:
            continue
        flat[token] = pin
        iostd = std        # last seen wins; OK because Xilinx boards usually all-LVCMOS33

    # Rebuild banks. Token shapes:
    #   "clk"                            → simple string
    #   "onboard_leds[3]"                → list per index
    #   "onboard_7seg.anodes[5]"         → nested dict[list]
    #   "onboard_uart.tx"                → nested dict[str]
    banks = {}
    for tok, pin in sorted(flat.items()):
        m = re.match(r"^([a-z_][a-z0-9_]*)(?:\.([a-z_][a-z0-9_]*))?(?:\[(\d+)\])?$", tok)
        if not m:
            continue
        bank, sub, idx = m.group(1), m.group(2), m.group(3)
        if sub is None and idx is None:
            banks.setdefault(bank, {})["__pin"] = pin
        elif sub is None and idx is not None:
            banks.setdefault(bank, {}).setdefault("__list", {})[int(idx)] = pin
        elif sub is not None and idx is None:
            banks.setdefault(bank, {}).setdefault("__sub", {})[sub] = pin
        else:
            banks.setdefault(bank, {}).setdefault("__sub_list", {}).setdefault(sub, {})[int(idx)] = pin

    # Materialize into the on-disk YAML form.
    out = {}
    for bank, body in banks.items():
        block = {}
        if "__pin" in body:
            block["pins"] = body["__pin"]
        if "__list" in body:
            mx = max(body["__list"].keys()) + 1
            block["pins"] = [body["__list"].get(i) for i in range(mx)]
        if "__sub" in body or "__sub_list" in body:
            inner = {}
            for sub, p in (body.get("__sub") or {}).items():
                inner[sub] = p
            for sub, items in (body.get("__sub_list") or {}).items():
                mx = max(items.keys()) + 1
                inner[sub] = [items.get(i) for i in range(mx)]
            block["pins"] = inner
        out[bank] = block

    # Render YAML with comments.
    lines = []
    lines.append("# {} pin map.".format(board_id))
    lines.append("#")
    lines.append("# Source: Digilent {} (github.com/Digilent/digilent-xdc, MIT-licensed)".format(os.path.basename(xdc_path)))
    lines.append("# Imported via tools/verify_pinmap_against_vendor.py.")
    lines.append("")
    lines.append("Board:")
    lines.append("  id: {}".format(board_id))
    lines.append("  fpga:")
    lines.append("    producer: {}".format(producer))
    lines.append("    family: {}".format(family))
    lines.append("    part: {}".format(part))
    lines.append("  defaults:")
    lines.append("    iostandard: {}".format(iostd))
    lines.append("")
    lines.append("  pinBanks:")
    for bank in sorted(out.keys()):
        block = out[bank]
        pins = block["pins"]
        if isinstance(pins, str):
            lines.append('    {}: {{ pins: "{}" }}'.format(bank, pins))
        elif isinstance(pins, list):
            lines.append("    {}:".format(bank))
            elems = ['"{}"'.format(p) if p is not None else "null" for p in pins]
            lines.append("      pins: [" + ", ".join(elems) + "]")
        elif isinstance(pins, dict):
            lines.append("    {}:".format(bank))
            lines.append("      pins:")
            for sub, val in pins.items():
                if isinstance(val, str):
                    lines.append('        {}: "{}"'.format(sub, val))
                elif isinstance(val, list):
                    elems = ['"{}"'.format(p) if p is not None else "null" for p in val]
                    lines.append("        {}: [{}]".format(sub, ", ".join(elems)))
    lines.append("")
    return "\n".join(lines)


# ----------------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------------

def cmd_check(args):
    rc = 0
    only = args.only
    for board_id, xdc_name in sorted(DIGILENT_BOARDS.items()):
        if only and board_id not in only:
            continue
        xdc_path = os.path.join(DIGILENT_DIR, xdc_name)
        if not os.path.exists(xdc_path):
            print("[{}] missing vendor XDC at {}".format(board_id, xdc_path))
            continue
        result = check_one(board_id, xdc_path, verbose=args.verbose)
        if result is None:
            print("[{}] no local pinmap (consider --import)".format(board_id))
            continue
        matches, mism, our_only, vendor_only, unmapped = result
        status = "OK" if not mism else "MISMATCH"
        print("[{}] {} — {} match, {} mismatch, {} our-only, {} vendor-only, {} unmapped"
              .format(board_id, status, len(matches), len(mism), len(our_only), len(vendor_only), len(unmapped)))
        if mism:
            rc = 1
            for tok, our_pin, v_pin in mism:
                print("  WRONG  {}: ours={}  vendor={}".format(tok, our_pin, v_pin))
        if args.verbose:
            for tok in vendor_only:
                print("  +VENDOR {}: {}".format(tok, "(see XDC)"))
            for tok in our_only:
                print("  -OURS   {}".format(tok))
            for port, idx, pin in unmapped:
                ix = "[{}]".format(idx) if idx is not None else ""
                print("  ?UNMAP  {}{} → {}".format(port, ix, pin))
    return rc


def cmd_import(args):
    xdc_name = DIGILENT_BOARDS.get(args.import_)
    if xdc_name is None:
        print("Unknown Digilent board id '{}'. Supported: {}"
              .format(args.import_, ", ".join(sorted(DIGILENT_BOARDS))))
        return 2
    xdc_path = os.path.join(DIGILENT_DIR, xdc_name)
    if not os.path.exists(xdc_path):
        print("Vendor file not found: {}".format(xdc_path))
        return 2
    target_id = args.as_ or args.import_
    out_path = os.path.join(BOARDS_DIR, target_id + ".yml")
    if os.path.exists(out_path) and not args.overwrite:
        print("Refusing to overwrite {} (use --overwrite)".format(out_path))
        return 2
    text = import_xdc_as(xdc_path, target_id, args.family, args.part)
    with open(out_path, "w") as f:
        f.write(text)
    print("Wrote {}".format(out_path))
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--source", choices=["digilent"], default="digilent")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--check", action="store_true", help="diff our YAML against vendor file(s)")
    g.add_argument("--import", dest="import_",
                   help="generate a fresh config/boards/<id>.yml from the vendor file")
    p.add_argument("--only", nargs="*", help="restrict --check to these board ids")
    p.add_argument("--as", dest="as_", help="target board id for --import (defaults to vendor id)")
    p.add_argument("--family", help="family to write into the imported YAML (e.g. 'Artix 7')")
    p.add_argument("--part",   help="part to write into the imported YAML (e.g. 'XC7A35TICSG324-1L')")
    p.add_argument("--overwrite", action="store_true",
                   help="allow --import to overwrite an existing file")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args(argv)

    if args.check:
        return cmd_check(args)
    if args.import_:
        if not args.family or not args.part:
            print("--import requires --family and --part", file=sys.stderr)
            return 2
        return cmd_import(args)
    return 2


if __name__ == "__main__":
    sys.exit(main())
