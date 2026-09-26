"""
The virtual device interface, rendered from data: rtl/peripherals/
design_top_interface.sv is what config/design_top.yml (the sections, in
order) and the capabilities' `design:` blocks (their parameters, derived
widths and ports, with `description` and `comment` for the prose) say.

    ./unifpga interface            is the file what the data renders to?
    ./unifpga interface --write    render it (./unifpga check reports a stale file)

A design copies the module header of that file (all of it, or the part it
uses: an optional capability reaches a design only through the ports it
declares).
"""

import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

from config import init as config_init   # noqa: E402
from tools import codegen                # noqa: E402

INTERFACE = codegen.DESIGN_INTERFACE

PREAMBLE = """\
// =============================================================================
// THE VIRTUAL DEVICE INTERFACE
//
// This file defines the canonical port list every user-written `design_top`
// targets. The interface is identical on every supported configuration. Per-
// configuration widths (number of switches, presence of a screen, etc.) come
// from `parameter` overrides set by the codegen-generated top module.
//
// A capability a particular board lacks gets the parameter values below (its
// `absent` values in config/capabilities/: most widths 0, so the vectors are
// zero-element arrays that optimize away; a screen 640x480 so pixel code still
// compiles). User code that references e.g. `led[3]` on a board with
// `w_led = 2` produces a synthesis error — which is the correct behaviour:
// the design requires more than the board provides, surface the mismatch.
//
// To write a new design, copy the body of this file into your project as
// `design_top.sv` and add your logic. Never rename the ports or change their
// directions — every board adapter binds to these names. An optional
// capability (its section says so) reaches a design only through the ports
// the design declares: leave them out when you do not use it.
//
// To declare hard capability requirements that synthesize.py should check
// before building, add a `// requires:` block before the module keyword.
// Example:
//
//     // requires:
//     //   switches >= 4
//     //   leds     >= 4
//     //   buttons  >= 2
//     //   screen   >= 640x480
//     //   audio_in
//     //   serial_console
//
// `synthesize.py` parses that block and fails fast if the chosen configuration
// doesn't meet the requirements.
//
// Rendered by tools/design_top.py from config/design_top.yml and the
// capabilities' design: blocks (config/capabilities/*.yml): edit those, then
// `./unifpga interface --write`.
// =============================================================================
"""


WIDTH = 80


def _comment(text):
    """A `// ---- text ----` heading, wrapped at WIDTH, the dashes on its last line."""
    lines, line = [], "    // ---- "
    for w in text.split():
        if len(line) + len(w) > WIDTH - 2 and line.strip() not in ("// ----", "//"):
            lines.append(line.rstrip())
            line = "    // "
        line += w + " "
    return lines + [line + "-" * max(0, WIDTH - len(line))]


def _default(spec):
    """The interface's default of a design parameter: its value on a rig
    without a provider."""
    absent = spec.get("absent")
    return absent if isinstance(absent, (int, float)) and not isinstance(absent, bool) else 0


def _derived_expr(spec):
    if "clog2" in spec:
        p = spec["clog2"]
        return "({} > 1) ? $clog2({}) : 1".format(p, p)
    return " * ".join(str(x) for x in spec["multiply"])


def _range(width):
    """The vector range of a port: none for width 1, [N-1:0] for a number,
    [name - 1 : 0] for a parameter."""
    if width == 1:
        return ""
    if isinstance(width, int):
        return "[{:>10} : 0]".format(width - 1)
    return "[{:<9}- 1 : 0]".format(width)


def _direction(capabilities, port):
    sig = next((s for s in (capabilities[port.capability].get("signals") or []) if s["name"] == port.signal), {})
    return {"hw_to_user": "input       ", "user_to_hw": "output logic", "inout": "inout       "}[sig.get("direction")]


def render():
    """The interface file's text."""
    capabilities = codegen._capabilities()
    sections = config_init.read_design_top().get("sections") or []
    params, derived, ports = codegen.design_contract()
    by_cap_params = {}
    for p in params:
        by_cap_params.setdefault(p.capability, []).append(p)
    out = [PREAMBLE, "module design_top", "# ("]
    first = True
    for sec in sections:
        lines = []
        for cid in sec["capabilities"]:
            for p in by_cap_params.get(cid, []):
                desc = p.spec.get("description")
                lines.append("    parameter int {:<13} = {:<7}{}".format(p.name, str(_default(p.spec)) + ",", "// " + desc if desc else "").rstrip())
        if not lines:
            continue
        if not first:
            out.append("")
        first = False
        out += _comment(sec["title"])
        out += lines
    out.append("")
    out += _comment("Derived widths (do not override)")
    for d in derived:
        out.append("    parameter int {} = {},".format(d.name, _derived_expr(d.spec)))
    out[-1] = out[-1].rstrip(",")
    out += [")", "("]
    by_cap_ports = {}
    for p in ports:
        by_cap_ports.setdefault(p.capability, []).append(p)
    first = True
    for sec in sections:
        for cid in sec["capabilities"]:
            mine = by_cap_ports.get(cid)
            if not mine:
                continue
            if not first:
                out.append("")
            first = False
            cap = capabilities[cid]
            out += _comment((cap.get("design") or {}).get("comment") or cap.get("description") or cid)
            for p in mine:
                out.append("    {} {:<20} {},".format(_direction(capabilities, p), _range(p.width), p.name).rstrip())
    out[-1] = out[-1].rstrip(",")
    out += [");", "", "    // -------------------------------------------------------------------------",
            "    // Default tie-offs. Override below as needed.",
            "    // -------------------------------------------------------------------------"]
    for p in ports:
        if _direction(capabilities, p).startswith("output"):
            out.append("    assign {:<9} = {};".format(p.name, p.spec.get("idle", "'0")))
    out += ["", "    // -------------------------------------------------------------------------",
            "    // User logic goes here.",
            "    // -------------------------------------------------------------------------", "", "endmodule", ""]
    return "\n".join(out)


def current_text():
    with open(INTERFACE, encoding="utf-8") as f:
        return f.read()


def is_current():
    return os.path.exists(INTERFACE) and current_text() == render()


def write():
    """Render the interface into its file; True when the file changed."""
    text = render()
    old = current_text() if os.path.exists(INTERFACE) else None
    if old != text:
        with open(INTERFACE, "w", encoding="utf-8") as f:
            f.write(text)
    return old != text
