"""
The virtual device interface, rendered from data, in two places:

  * rtl/peripherals/design_top_interface.sv — the whole device, with its
    prose: what config/design_top.yml (the sections, in order) and the
    capabilities' `design:` blocks (parameters, derived widths, ports;
    `description` and `comment` for the prose) say;
  * designs/<name>/design_top_interface.svh — a design's module header, the
    same parameters and ports without the optional capabilities the design
    does not require (its `// requires:` names the ones it uses). The design
    file holds no header of its own:

        // requires:
        //   memory
        module design_top
        `include "design_top_interface.svh"
            ... the design ...
        endmodule

    The include is a build product: every build, simulation and lint renders
    it first (tools/source_set.py design_inputs); it is not committed.

    ./unifpga interface                      is the interface file current, does every design include its header?
    ./unifpga interface --write [design…]    render the file and the designs' includes; a design still carrying
                                             a hand-written header is converted to the include
"""

import glob
import os
import re
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

from config import init as config_init            # noqa: E402
from tools import codegen, design_requirements    # noqa: E402

INTERFACE = codegen.DESIGN_INTERFACE
DESIGNS_DIR = os.path.join(REPO, "designs")
INCLUDE_NAME = "design_top_interface.svh"
DIRECTIVE = '`include "{}"'.format(INCLUDE_NAME)
WIDTH = 80

PREAMBLE = """\
// =============================================================================
// THE VIRTUAL DEVICE INTERFACE
//
// This file defines the canonical port list every user-written `design_top`
// targets. The interface is identical on every supported configuration. Per-
// configuration widths (number of switches, presence of a screen, etc.) come
// from `parameter` overrides set by the codegen-generated top module.
//
// Capabilities a particular board lacks are declared with width 0; SystemVerilog
// vectors of width 0 are zero-element arrays (no driver, no consumer), which
// silently optimize away. User code that references e.g. `led[3]` on a board
// with `w_led = 2` produces a synthesis error — which is the correct behaviour:
// the design requires more than the board provides, surface the mismatch.
//
// A design does not copy this header: its file says
//
//     module design_top
//     `include "design_top_interface.svh"
//
// and the include, rendered next to it by every build (or by `./unifpga
// interface --write`), is this header without the optional capabilities the
// design's `// requires:` block does not name. Never rename the ports or
// change their directions — every board adapter binds to these names.
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
    """A design parameter's value when nothing sets it (a design compiled on
    its own): its `default`, else 0."""
    d = spec.get("default")
    return d if isinstance(d, (int, float)) and not isinstance(d, bool) else 0


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


def optional_capabilities():
    return {cid for cid, cap in codegen._capabilities().items() if cap.get("optional")}


def render_lists(uses=None, prose=False):
    """The `# ( … )` parameter list and `( … );` port list of design_top,
    without the optional capabilities not in `uses` (None: all of them).
    With `prose`, the section headings, the parameters' descriptions and the
    capabilities' comments."""
    capabilities = codegen._capabilities()
    sections = config_init.read_design_top().get("sections") or []
    params, derived, ports = codegen.design_contract()
    optional = optional_capabilities()
    keep = lambda cid: cid not in optional or uses is None or cid in uses
    out = ["# ("]
    lines_all = []
    for sec in sections:
        lines = []
        for cid in sec["capabilities"]:
            if not keep(cid):
                continue
            for p in params:
                if p.capability != cid:
                    continue
                desc = p.spec.get("description") if prose else None
                lines.append("    parameter int {:<13} = {:<7}{}".format(p.name, str(_default(p.spec)) + ",", "// " + desc if desc else "").rstrip())
        if not lines:
            continue
        if prose:
            if lines_all:
                lines_all.append("")
            lines_all += _comment(sec["title"])
        lines_all += lines
    kept_derived = [d for d in derived if keep(d.capability)]
    if kept_derived:
        if prose:
            lines_all.append("")
            lines_all += _comment("Derived widths (do not override)")
        lines_all += ["    parameter int {} = {},".format(d.name, _derived_expr(d.spec)) for d in kept_derived]
    lines_all[-1] = lines_all[-1].rstrip(",")
    out += lines_all + [")", "("]
    lines_all = []
    for sec in sections:
        for cid in sec["capabilities"]:
            mine = [p for p in ports if p.capability == cid]
            if not mine or not keep(cid):
                continue
            if prose:
                if lines_all:
                    lines_all.append("")
                cap = capabilities[cid]
                lines_all += _comment((cap.get("design") or {}).get("comment") or cap.get("description") or cid)
            for p in mine:
                lines_all.append("    {} {:<20} {},".format(_direction(capabilities, p), _range(p.width), p.name).rstrip())
    lines_all[-1] = lines_all[-1].rstrip(",")
    out += lines_all + [");"]
    return "\n".join(out)


# ---------------------------------------------------------------------------
# rtl/peripherals/design_top_interface.sv
# ---------------------------------------------------------------------------

def render():
    """The interface file's text: the preamble, the whole header with its
    prose, the outputs' default tie-offs."""
    capabilities = codegen._capabilities()
    _params, _derived, ports = codegen.design_contract()
    out = [PREAMBLE + "module design_top", render_lists(None, prose=True), "",
           "    // -------------------------------------------------------------------------",
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


# ---------------------------------------------------------------------------
# the designs' includes
# ---------------------------------------------------------------------------

def design_files():
    return sorted(glob.glob(os.path.join(DESIGNS_DIR, "*", "design_top.sv")))


def uses_of(text):
    """The optional capabilities a design's `// requires:` block names."""
    try:
        required = set(design_requirements.parse_text(text))
    except Exception:          # a requires block the parser rejects: the build reports it
        required = set()
    return required & optional_capabilities()


def includes_header(text):
    """Does the design file take its header from the include?"""
    return DIRECTIVE in text


def render_include(text):
    """The include file of a design whose text this is."""
    uses = uses_of(text)
    optional = sorted(uses) or None
    return "\n".join([
        "// design_top's module header, rendered by ./unifpga interface --write (and by every build)",
        "// from config/design_top.yml, the capabilities' design: blocks and this design's",
        "// // requires: block" + (" (optional capabilities: {})".format(", ".join(optional)) if optional else "") +
        ". Not for editing; not committed.",
        render_lists(uses), ""])


def resolve(text):
    """A design's text with the include directive replaced by the header it
    renders to (what codegen reads the declared ports from)."""
    if not includes_header(text):
        return text
    return text.replace(DIRECTIVE, render_lists(uses_of(text)), 1)


def include_path(design_path):
    return os.path.join(os.path.dirname(os.path.abspath(design_path)), INCLUDE_NAME)


def write_include(design_path):
    """Render a design's include next to it (when the design includes its
    header); True when the file changed."""
    with open(design_path, encoding="utf-8") as f:
        text = f.read()
    if not includes_header(text):
        return False
    path = include_path(design_path)
    new = render_include(text)
    old = None
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            old = f.read()
    if old != new:
        with open(path, "w", encoding="utf-8") as f:
            f.write(new)
    return old != new


def _header_span(text):
    """(start, end) of a hand-written `module design_top … );` header, or None."""
    m = re.search(r"^module\s+design_top\b", text, re.M)
    if not m:
        return None
    pos, depth, seen = m.end(), 0, 0
    while pos < len(text):
        ch = text[pos]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                seen += 1
        elif ch == ";" and depth == 0 and seen:
            return m.start(), pos + 1
        pos += 1
    return None


def convert(design_path):
    """A design carrying a hand-written header: the header replaced by the
    include directive and the include rendered. Returns whether the design
    file changed; raises when the file has no header at all."""
    with open(design_path, encoding="utf-8") as f:
        text = f.read()
    if includes_header(text):
        return write_include(design_path) and False
    span = _header_span(text)
    if span is None:
        raise codegen.CodegenError("{}: no `module design_top` header to convert".format(design_path))
    new = text[:span[0]] + "module design_top\n" + DIRECTIVE + text[span[1]:]
    with open(design_path, "w", encoding="utf-8") as f:
        f.write(new)
    write_include(design_path)
    return True
