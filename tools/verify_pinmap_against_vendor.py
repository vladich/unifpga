#!/usr/bin/env python3
"""
Cross-check a board's banks against the vendor's constraint file.

Supports the Digilent XDC repository (github.com/Digilent/digilent-xdc,
MIT-licensed): a `<Board>-Master.xdc` documents every board pin with
PACKAGE_PIN + IOSTANDARD + a canonical port name. A board that has one lists
it among its documents (kind constraints, a digilent-xdc URL); the check reads
it from the document cache (./unifpga sources fetch <board> downloads it).

    python -m tools.verify_pinmap_against_vendor            # every board with such a document
    python -m tools.verify_pinmap_against_vendor basys3 -v  # one board, with the unmapped ports

A mismatch is a finding to look into (the banks, the XDC or the port-name
mapping below may be wrong); the check changes nothing.
"""

import argparse
import os
import re
import sys

REPO = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

from config import init as config_init     # noqa: E402
from tools import board_sources            # noqa: E402


def xdc_document(board):
    """The board's Digilent XDC document, or None."""
    for d in board.get("documents") or []:
        if d.get("kind") == "constraints" and "digilent-xdc" in (d.get("url") or ""):
            return d
    return None


def boards_with_xdc():
    return {b: bd for b, bd in config_init.read_boards().items() if xdc_document(bd)}


# Map Digilent canonical port names → our pinBank names. Our convention
# prefixes most things with "onboard_"; Digilent uses uppercase abbreviations.
# An entry of the form ("BANK", "subkey") routes to a nested pin block.
DIGILENT_PORT_MAP = {
    # Clock(s).
    "CLK100MHZ":  "clk100mhz",
    "CLK":        "clk",                 # Basys 3, Nexys 4
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


# The older two-statement form (Nexys 4, ZedBoard):
#   set_property PACKAGE_PIN <pin> [get_ports { <name>[<i>] }]   (IOSTANDARD on a line of its own)
_PLAIN_RE = re.compile(
    r"^\s*#?\s*set_property\s+PACKAGE_PIN\s+(?P<pin>\S+)\s+"
    r"\[get_ports\s+\{?\s*(?P<port>[A-Za-z_][A-Za-z0-9_]*)"
    r"(?:\s*\[\s*(?P<idx>\d+)\s*\])?",
)


def parse_digilent_xdc_text(text):
    """[(canonical port, idx or None, pin, iostd or None)] of an XDC's text,
    in either of Digilent's forms."""
    out = []
    for line in text.splitlines():
        m = _LINE_RE.match(line) or _PLAIN_RE.match(line)
        if m:
            out.append((m.group("port"), int(m.group("idx")) if m.group("idx") is not None else None,
                        m.group("pin"), m.groupdict().get("std")))
    return out


# ----------------------------------------------------------------------------
# YAML pinmap loader — flatten our pinBanks: { name → list/dict/str of pins }
# ----------------------------------------------------------------------------

def pins_of(board):
    """{token: FPGA pin} of a board's banks. Tokens: clk, onboard_leds[3],
    onboard_7seg.anodes[5], onboard_uart.tx."""
    out = {}
    for bank, body in (board.get("banks") or {}).items():
        if isinstance(body, str):
            out[bank] = body
            continue
        if not isinstance(body, dict):
            continue
        pins = body.get("pins")
        if pins is None:
            continue                                   # a virtual clock or alike
        if isinstance(pins, (str, int)):
            out[bank] = str(pins)
        elif isinstance(pins, list):
            for i, p in enumerate(pins):
                if p is not None:
                    out["{}[{}]".format(bank, i)] = str(p)
        elif isinstance(pins, dict):
            for sub, val in pins.items():
                if isinstance(val, (str, int)):
                    out["{}.{}".format(bank, sub)] = str(val)
                elif isinstance(val, list):
                    for i, p in enumerate(val):
                        if p is not None:
                            out["{}.{}[{}]".format(bank, sub, i)] = str(p)
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

def compare(ours, vendor_tuples):
    """(matches, mismatches [(token, our pin, vendor pin)], our_only,
    vendor_only, unmapped [(port, idx, pin)]) of our {token: pin} against the
    XDC's (port, idx, pin, iostd) tuples."""
    # A JA[0] / JB[0] anywhere means this XDC numbers Pmod pins 0-based and
    # contiguous (Arty / Zybo / Eclypse); otherwise 1-based with gaps (Nexys / Basys).
    pmod_zero_based = any(idx == 0 and re.match(r"^J[A-E]$", port.upper()) for port, idx, _, _ in vendor_tuples)
    vendor_map, unmapped = {}, []
    for port, idx, pin, _ in vendor_tuples:
        token = map_digilent_port(port, idx, pmod_zero_based=pmod_zero_based)
        if token is None:
            unmapped.append((port, idx, pin))
            continue
        vendor_map[token] = pin                        # commented and live copies of a line both parse
    matches, mismatches = [], []
    for tok, our_pin in ours.items():
        v = vendor_map.get(tok)
        if v is None:
            continue
        (matches if v == our_pin else mismatches).append(tok if v == our_pin else (tok, our_pin, v))
    return matches, mismatches, sorted(set(ours) - set(vendor_map)), sorted(set(vendor_map) - set(ours)), unmapped


def check_board(board_id):
    """compare() of a board's banks with its Digilent XDC document (fetched
    into the cache when it is not there yet)."""
    board = config_init.read_board(board_id)
    if board is None:
        raise board_sources.SourcesError("no board '{}'".format(board_id))
    doc = xdc_document(board)
    if doc is None:
        raise board_sources.SourcesError("board '{}' lists no Digilent XDC among its documents".format(board_id))
    got = board_sources.fetch(board_id, doc)
    with open(got["path"], encoding="utf-8", errors="replace") as f:
        tuples = parse_digilent_xdc_text(f.read())
    return compare(pins_of(board), tuples)


# ----------------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------------

def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("boards", nargs="*", help="board ids (default: every board with a Digilent XDC document)")
    p.add_argument("-v", "--verbose", action="store_true", help="list the vendor-only, our-only and unmapped ports")
    args = p.parse_args(argv)
    with_xdc = boards_with_xdc()
    ids = args.boards or sorted(with_xdc)
    rc = 0
    for board_id in ids:
        try:
            matches, mism, our_only, vendor_only, unmapped = check_board(board_id)
        except board_sources.SourcesError as exc:
            print("[{}] {}".format(board_id, exc))
            rc = 2
            continue
        status = "OK" if not mism else "MISMATCH"
        print("[{}] {} — {} match, {} mismatch, {} our-only, {} vendor-only, {} unmapped"
              .format(board_id, status, len(matches), len(mism), len(our_only), len(vendor_only), len(unmapped)))
        if mism:
            rc = 1
            for tok, our_pin, v_pin in mism:
                print("  WRONG  {}: ours={}  vendor={}".format(tok, our_pin, v_pin))
        if args.verbose:
            for tok in vendor_only:
                print("  +VENDOR {}".format(tok))
            for tok in our_only:
                print("  -OURS   {}".format(tok))
            for port, idx, pin in unmapped:
                print("  ?UNMAP  {}{} -> {}".format(port, "[{}]".format(idx) if idx is not None else "", pin))
    return rc


if __name__ == "__main__":
    sys.exit(main())
