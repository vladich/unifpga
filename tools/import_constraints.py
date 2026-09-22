#!/usr/bin/env python3
"""
Import vendor constraint files (XDC / QSF / CST / PCF / LPF) and emit a
normalized signal->pin mapping as YAML.

Usage:
    python -m tools.import_constraints <constraint-file> [--source <src>]
    python -m tools.import_constraints <dir>     # auto-detects board_specific.*

Output goes to stdout as YAML. Pipe to a file under config/boards/_raw/.

The output is *raw* harvested data — direct signal name -> pin -> iostandard.
A separate curation step groups raw signals into peripheral categories
(clock/switch/led/sevenSegment/button/pmod/...).
"""

import argparse
import os
import re
import sys
from collections import OrderedDict


_FORMAT_BY_EXT = {
    ".xdc": "xdc",
    ".qsf": "qsf",
    ".cst": "cst",
    ".pcf": "pcf",
    ".lpf": "lpf",
}


def detect_format(path):
    _, ext = os.path.splitext(path.lower())
    fmt = _FORMAT_BY_EXT.get(ext)
    if fmt is None:
        raise ValueError("Unknown constraint file extension: {p}".format(p=path))
    return fmt


def _strip_inline_comment(line, comment_chars=("#",)):
    """Strip inline comments but preserve the parts we need."""
    for c in comment_chars:
        i = line.find(c)
        if i >= 0:
            line = line[:i]
    return line


def parse_xdc(text):
    """
    Parse a Vivado/Xilinx XDC. Two common forms:

        set_property -dict { PACKAGE_PIN E3 IOSTANDARD LVCMOS33 } [get_ports { CLK }];
        set_property PACKAGE_PIN E3 [get_ports CLK]
        set_property IOSTANDARD LVCMOS33 [get_ports CLK]
    """
    pin_by_signal = OrderedDict()
    iostd_by_signal = {}

    # Strip line comments (#...) but keep the rest of the line.
    cleaned_lines = []
    for raw in text.splitlines():
        # XDC uses # for comments. Tcl is line-oriented enough that this is safe.
        cleaned_lines.append(_strip_inline_comment(raw, ("#",)))
    text = "\n".join(cleaned_lines)

    # The signal in [get_ports ...] may be braced (`{ SW[0] }`) or bare (`CLK`).
    # Braced form is needed when the signal name contains `[` or `]`.
    get_ports_pat = r"\[get_ports\s+(?:\{\s*([^}]+?)\s*\}|([^\s\]]+))\s*\]"

    def _signal_of(m_braced, m_bare):
        return m_braced if m_braced else m_bare

    # Form 1: set_property -dict { ... } [get_ports { SIG }]
    dict_re = re.compile(
        r"set_property\s+-dict\s*\{([^}]*)\}\s*" + get_ports_pat,
        re.IGNORECASE,
    )
    for m in dict_re.finditer(text):
        body = m.group(1)
        signal = _signal_of(m.group(2), m.group(3))
        pin_m = re.search(r"PACKAGE_PIN\s+(\S+)", body, re.IGNORECASE)
        iostd_m = re.search(r"IOSTANDARD\s+(\S+)", body, re.IGNORECASE)
        if pin_m:
            pin_by_signal[signal] = pin_m.group(1)
        if iostd_m:
            iostd_by_signal[signal] = iostd_m.group(1)

    # Form 2: set_property PACKAGE_PIN <pin> [get_ports <sig>]
    simple_pin_re = re.compile(
        r"set_property\s+PACKAGE_PIN\s+(\S+)\s+" + get_ports_pat,
        re.IGNORECASE,
    )
    for m in simple_pin_re.finditer(text):
        pin = m.group(1)
        signal = _signal_of(m.group(2), m.group(3))
        pin_by_signal.setdefault(signal, pin)

    # Form 3: set_property IOSTANDARD <std> [get_ports <sig>]
    simple_iostd_re = re.compile(
        r"set_property\s+IOSTANDARD\s+(\S+)\s+" + get_ports_pat,
        re.IGNORECASE,
    )
    for m in simple_iostd_re.finditer(text):
        std = m.group(1)
        signal = _signal_of(m.group(2), m.group(3))
        iostd_by_signal.setdefault(signal, std)

    return _merge(pin_by_signal, iostd_by_signal)


def parse_qsf(text):
    """
    Quartus QSF.

        set_location_assignment PIN_E3 -to CLK
        set_instance_assignment -name IO_STANDARD "3.3-V LVTTL" -to *
        set_instance_assignment -name IO_STANDARD "3.3 V Schmitt Trigger" -to KEY[0]

    `-to *` is a project-wide default; we apply it to any signal that doesn't
    have its own per-signal IO_STANDARD.
    """
    pin_by_signal = OrderedDict()
    iostd_by_signal = {}
    default_iostd = None

    pin_re = re.compile(
        r"^\s*set_location_assignment\s+PIN_(\S+)\s+-to\s+(\S+)",
        re.IGNORECASE | re.MULTILINE,
    )
    for m in pin_re.finditer(text):
        pin, signal = m.group(1), m.group(2)
        pin_by_signal[signal] = pin

    iostd_re = re.compile(
        r"^\s*set_instance_assignment\s+-name\s+IO_STANDARD\s+\"([^\"]+)\"\s+-to\s+(\S+)",
        re.IGNORECASE | re.MULTILINE,
    )
    # Quartus applies assignments in file order, the last matching one wins;
    # `-to KEY*` / `-to HEX0*` are wildcards over the located signal names
    # (BGM de2_115 / de1_soc / c5gx), `-to *` alone the project-wide default.
    for m in iostd_re.finditer(text):
        std, pattern = m.group(1), m.group(2)
        if pattern == "*":
            default_iostd = std
        elif "*" in pattern or "?" in pattern:
            # Quartus wildcards: `*` any text, `?` one character; `[` `]` are
            # literal bus brackets (`HDMI_TX_D[*]` is every index, not a
            # character class as in fnmatch)
            rx = re.compile("^" + re.escape(pattern).replace(r"\*", ".*").replace(r"\?", ".") + "$")
            for signal in pin_by_signal:
                if rx.match(signal):
                    iostd_by_signal[signal] = std
        else:
            # `-to seven_seg_sel` names a bus as a whole too (dk_dev_3c120n)
            iostd_by_signal[pattern] = std
            for signal in pin_by_signal:
                if signal.startswith(pattern + "["):
                    iostd_by_signal[signal] = std

    if default_iostd is not None:
        for signal in pin_by_signal:
            iostd_by_signal.setdefault(signal, default_iostd)

    return _merge(pin_by_signal, iostd_by_signal)


def parse_cst(text):
    """
    Gowin CST. The format is loose — terminating `;` is sometimes omitted,
    multiple IO_LOC clauses can appear on one line, and differential-pair
    pins are written as comma-separated pairs (`28,27`). Comments use `//`
    or `#`.

        IO_LOC "CLK"        52;
        IO_LOC "KEY[0]"     4
        IO_LOC "GPIO[0]"    K2 ; IO_LOC "GPIO[1]"  K1
        IO_LOC "TMDS_CLK_P" 28,27;
        IO_PORT "KEY[0]" PULL_MODE=UP;
    """
    pin_by_signal = OrderedDict()
    iostd_by_signal = {}

    text = "\n".join(_strip_inline_comment(line, ("//", "#")) for line in text.splitlines())

    # Pin can be a single token or a comma-separated diff pair (kept verbatim).
    loc_re = re.compile(
        r'IO_LOC\s+"([^"]+)"\s+([^\s;,]+(?:\s*,\s*[^\s;,]+)*)',
        re.IGNORECASE,
    )
    for m in loc_re.finditer(text):
        signal = m.group(1)
        pin = re.sub(r"\s+", "", m.group(2))  # collapse whitespace inside diff pair
        pin_by_signal[signal] = pin

    port_re = re.compile(
        r'IO_PORT\s+"([^"]+)"\s+([^;\n]+)',
        re.IGNORECASE,
    )
    for m in port_re.finditer(text):
        signal, attrs = m.group(1), m.group(2)
        io_type_m = re.search(r"IO_TYPE\s*=\s*(\S+)", attrs, re.IGNORECASE)
        if io_type_m:
            iostd_by_signal.setdefault(signal, io_type_m.group(1).rstrip(",;"))

    return _merge(pin_by_signal, iostd_by_signal)


def parse_pcf(text):
    """
    Project IceStorm PCF.

        set_io CLK 35
        set_io -nowarn TX 9
    """
    pin_by_signal = OrderedDict()

    # Strip line comments first.
    text = "\n".join(_strip_inline_comment(line, ("#",)) for line in text.splitlines())

    set_io_re = re.compile(
        r"^\s*set_io(?:\s+-nowarn)?\s+(\S+)\s+(\S+)",
        re.IGNORECASE | re.MULTILINE,
    )
    for m in set_io_re.finditer(text):
        signal, pin = m.group(1), m.group(2)
        pin_by_signal[signal] = pin

    return _merge(pin_by_signal, {})


def parse_lpf(text):
    """
    Lattice ECP5 LPF (used by nextpnr-trellis / Diamond).

        LOCATE COMP "CLK" SITE "A9";
        IOBUF PORT "CLK" IO_TYPE=LVCMOS33;
    """
    pin_by_signal = OrderedDict()
    iostd_by_signal = {}

    # LPF supports // line comments.
    text = "\n".join(_strip_inline_comment(line, ("//", "#")) for line in text.splitlines())

    locate_re = re.compile(
        r'LOCATE\s+COMP\s+"([^"]+)"\s+SITE\s+"([^"]+)"\s*;',
        re.IGNORECASE,
    )
    for m in locate_re.finditer(text):
        signal, pin = m.group(1), m.group(2)
        pin_by_signal[signal] = pin

    iobuf_re = re.compile(
        r'IOBUF\s+PORT\s+"([^"]+)"\s+([^;]+);',
        re.IGNORECASE,
    )
    for m in iobuf_re.finditer(text):
        signal, attrs = m.group(1), m.group(2)
        std_m = re.search(r"IO_TYPE\s*=\s*(\S+)", attrs, re.IGNORECASE)
        if std_m:
            iostd_by_signal[signal] = std_m.group(1).rstrip(",;")

    return _merge(pin_by_signal, iostd_by_signal)


_PARSERS = {
    "xdc": parse_xdc,
    "qsf": parse_qsf,
    "cst": parse_cst,
    "pcf": parse_pcf,
    "lpf": parse_lpf,
}


def _merge(pin_by_signal, iostd_by_signal):
    out = OrderedDict()
    for signal, pin in pin_by_signal.items():
        entry = OrderedDict([("pin", pin)])
        if signal in iostd_by_signal:
            entry["iostandard"] = iostd_by_signal[signal]
        out[signal] = entry
    return out


def parse_file(path, fmt=None):
    if fmt is None:
        fmt = detect_format(path)
    with open(path) as f:
        text = f.read()
    return _PARSERS[fmt](text), fmt


def find_constraint_file(directory):
    """
    Locate the primary constraint file in a directory. Prefer
    `board_specific.<ext>`; fall back to any *.<ext> when no `board_specific.*`
    is present (some boards ship a single file named after the board, e.g.,
    icebreaker.pcf).
    """
    paths = find_all_constraint_files(directory)
    return paths[0] if paths else None


def find_all_constraint_files(directory):
    """
    Return *every* constraint file in a directory, ordered so the primary
    `board_specific.<ext>` comes first and supplementary reference files
    (master XDCs, per-peripheral QSFs) follow. Used to merge data from
    multiple authoritative files into one harvested signal map.
    """
    priority = {".xdc": 0, ".qsf": 1, ".cst": 2, ".lpf": 3, ".pcf": 4}

    primary = []
    fallback = []
    for name in sorted(os.listdir(directory)):
        ext = os.path.splitext(name)[1].lower()
        if ext not in _FORMAT_BY_EXT:
            continue
        path = os.path.join(directory, name)
        if name.startswith("board_specific."):
            primary.append(path)
        else:
            fallback.append(path)

    primary.sort(key=lambda p: priority.get(os.path.splitext(p)[1].lower(), 99))
    fallback.sort(key=lambda p: priority.get(os.path.splitext(p)[1].lower(), 99))
    return primary + fallback


def _quote_for_flow(v):
    """Quote a value if it contains characters that break YAML flow context
    (comma, brace) or starts with a digit (parsed as int otherwise)."""
    s = str(v)
    if "," in s or "{" in s or "}" in s or s.startswith(("'", '"')):
        return '"{}"'.format(s)
    return s


def emit_yaml(signals, source, fmt):
    lines = []
    lines.append("# Generated by tools/import_constraints.py — DO NOT EDIT")
    lines.append("# Re-run the importer to refresh.")
    lines.append("source: {s}".format(s=source))
    lines.append("format: {f}".format(f=fmt))
    lines.append("signals:")
    for signal, info in signals.items():
        # Quote signals that contain non-identifier chars (e.g. "SW[0]").
        if re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", signal):
            sk = signal
        else:
            sk = '"{s}"'.format(s=signal)
        pin = _quote_for_flow(info["pin"])
        if "iostandard" in info:
            iostd = _quote_for_flow(info["iostandard"])
            lines.append("  {k}: {{ pin: {p}, iostandard: {s} }}".format(
                k=sk, p=pin, s=iostd))
        else:
            lines.append("  {k}: {{ pin: {p} }}".format(k=sk, p=pin))
    lines.append("")
    return "\n".join(lines)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("path", help="Path to a constraint file or a directory containing board_specific.*")
    p.add_argument("--source", help="Override the recorded source string")
    p.add_argument("--format", choices=list(_FORMAT_BY_EXT.values()),
                   help="Force format (overrides extension detection)")
    args = p.parse_args(argv)

    if os.path.isdir(args.path):
        path = find_constraint_file(args.path)
        if path is None:
            print("No board_specific.* constraint file in {d}".format(d=args.path), file=sys.stderr)
            return 1
    else:
        path = args.path

    signals, fmt = parse_file(path, fmt=args.format)
    source = args.source if args.source else path
    sys.stdout.write(emit_yaml(signals, source=source, fmt=fmt))
    return 0


if __name__ == "__main__":
    sys.exit(main())
