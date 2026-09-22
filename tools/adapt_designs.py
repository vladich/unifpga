"""
Mechanically adapt every lab_top.sv from basics-graphics-music to the uni-fpga
virtual-device interface (written out as design_top.sv).

For each design:
  1. Read basics-graphics-music/labs/<section>/<lab>/lab_top.sv.
  2. Replace its module header (parameters + ports) with the uni-fpga
     canonical signature (matches rtl/peripherals/design_top_interface.sv).
  3. Inside the preserved body:
       - rename `key` -> `btn`, `w_key` -> `w_btn`
       - rename `mic` (24-bit) -> `mic_sample`; drop or tie off `mic_valid`
         references that the original didn't have
       - if the body references `slow_clk`, prepend an inline counter that
         derives a ~1 Hz slow_clk from the system clk
       - strip `\\`include "config.svh"` and friends
  4. Write to designs/<section>_<design>/design_top.sv.

Run with:
    python -m tools.adapt_designs           # adapt all
    python -m tools.adapt_designs --dry-run # report only

Skipped sections: 8_unfinished/, 99_experimental/ — incomplete in the source.
"""

import os
import re
import sys
from collections import defaultdict


REPO = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
# basics-graphics-music is expected to live as a sibling of this repo.
# Override with $UNIFPGA_BGM_DIR for a non-default layout.
BGM_DIR = os.environ.get(
    "UNIFPGA_BGM_DIR",
    os.path.normpath(os.path.join(REPO, "..", "basics-graphics-music")))
# Upstream keeps the examples under labs/<section>/<lab>/lab_top.sv.
BGM_LABS_DIR = os.path.join(BGM_DIR, "labs")
UPSTREAM_TOP = "lab_top.sv"
DESIGNS_OUT = os.path.join(REPO, "designs")

SKIP_SECTIONS = {"8_unfinished", "99_experimental"}

# Specific designs to skip — typically aggregator presentations that reference
# code in sibling directories (impractical to mechanically adapt).
SKIP_DESIGNS = {"2024_08_31_meetup_in_la"}


CANONICAL_HEADER = """\
module design_top
# (
    parameter int clk_mhz       = 50,
                  w_sw          = 0,
                  w_btn         = 0,
                  w_led         = 0,
                  w_digit       = 0,
                  w_rgb_led     = 0,
                  screen_width  = 0,
                  screen_height = 0,
                  w_red         = 0,
                  w_green       = 0,
                  w_blue        = 0,
                  w_gpio        = 0,
                  w_x = (screen_width  > 0) ? $clog2(screen_width ) : 1,
                  w_y = (screen_height > 0) ? $clog2(screen_height) : 1
)
(
    input                            clk,
    input                            rst,
    input        [w_sw     - 1 : 0]  sw,
    input        [w_btn    - 1 : 0]  btn,
    output logic [w_led    - 1 : 0]  led,
    output logic [          7 : 0]   abcdefgh,
    output logic [w_digit  - 1 : 0]  digit,
    output logic [w_rgb_led- 1 : 0]  rgb_r,
    output logic [w_rgb_led- 1 : 0]  rgb_g,
    output logic [w_rgb_led- 1 : 0]  rgb_b,
    input        [w_x      - 1 : 0]  x,
    input        [w_y      - 1 : 0]  y,
    output logic [w_red    - 1 : 0]  red,
    output logic [w_green  - 1 : 0]  green,
    output logic [w_blue   - 1 : 0]  blue,
    input        [         23 : 0]   mic_sample,
    input                            mic_valid,
    output logic [         15 : 0]   sound,
    input                            uart_rx,
    output logic                     uart_tx,
    inout        [w_gpio   - 1 : 0]  gpio
);
"""

SLOW_CLK_INSERT = """
    // ---- slow_clk derivation (auto-inserted by adapt_designs.py) -------------
    // Original basics-graphics-music labs received `slow_clk` as a port. The
    // uni-fpga virtual device doesn't expose one, so derive a ~1 Hz tick from
    // the system clock.
    localparam int W_SLOW_CLK_DIV = $clog2(clk_mhz * 1_000_000);
    logic [W_SLOW_CLK_DIV - 1 : 0] slow_clk_div;
    logic                          slow_clk;
    always_ff @(posedge clk or posedge rst)
        if (rst) slow_clk_div <= '0;
        else     slow_clk_div <= slow_clk_div + 1'b1;
    assign slow_clk = slow_clk_div[W_SLOW_CLK_DIV - 1];

"""


def _read(path):
    with open(path) as f:
        return f.read()


def _strip_includes(text):
    # Strip per-board config / design-specific includes (handled by codegen).
    text = re.sub(
        r'^\s*`include\s+"(?:config|lab_specific[a-z_]*|swap_bits)\.svh"\s*\n',
        "", text, flags=re.MULTILINE)
    # Strip `include "<name>.v" / "<name>.sv" — these reference module
    # files that uni-fpga collects explicitly via the toolchain's source
    # list. Leaving them in causes Quartus to compile the module twice
    # (once via `include, once via the explicit file list).
    text = re.sub(
        r'^\s*`include\s+"[^"]+\.s?v"\s*\n',
        "", text, flags=re.MULTILINE)
    return text


def _strip_param_size_casts(text):
    """yosys 0.36 evaluates SV `<param>'(expr)` size-casts using the
    parameter's *default* value rather than the override. When a design uses
    `w_digit'(...)` and w_digit defaults to 0, yosys aborts with
    'Static cast with zero or negative size' even though the actual
    instantiation passes a non-zero value. The cast is functionally
    redundant — Verilog truncates/zero-extends on assignment to a
    fixed-width LHS — so strip it for portability across toolchains.
    Pattern: `w_<name>'(<expr>)` → `(<expr>)`."""
    out = []
    i = 0
    pat = re.compile(r"\b(w_\w+)\s*'\s*\(")
    for m in pat.finditer(text):
        # Skip casts where the RHS is a parameter declaration (param spec).
        # Heuristic: only rewrite casts that appear in expressions, i.e.
        # after an `=`, `?`, `:`, or `(` token nearby. Easier: just always
        # rewrite — the cast is semantically a no-op for assignments.
        end_paren = _consume_balanced_parens(text, m.end() - 1)
        if end_paren < 0:
            continue
        out.append(text[i:m.start()])
        out.append(text[m.end()-1:end_paren])  # the `(...)` itself
        i = end_paren
    out.append(text[i:])
    return "".join(out)


def _move_module_header_imports(text):
    """Quartus 23.1std rejects `import pkg::sym;` between `module FOO` and
    `(...)` — the SV ANSI port-list region. Move such imports inside the
    module body, just after the port list. Leaves other (non-import) header
    constructs (e.g. `# (parameter ...)`) untouched."""
    pat = re.compile(
        r"(module\s+\w+\s*\n)"
        r"((?:\s*import\s+\w+::[\w*]+\s*;\s*\n)+)"
        r"(\s*\()", re.MULTILINE)
    out = []
    last = 0
    for m in pat.finditer(text):
        head, imports, paren = m.group(1), m.group(2), m.group(3)
        # Find the matching `)` for this `(`.
        end = _consume_balanced_parens(text, m.end(2) + len(paren) - 1)
        if end is None:
            continue
        # Locate the trailing `;` after the port list close.
        semi = text.find(";", end)
        if semi == -1:
            continue
        out.append(text[last:m.start()])
        out.append(head + paren + text[m.end(3):semi+1] + "\n" + imports)
        last = semi + 1
    out.append(text[last:])
    return "".join(out)


def _consume_balanced_parens(text, start):
    """`start` points at '('; return the index just after the matching ')'."""
    depth = 0
    i = start
    while i < len(text):
        if text[i] == "(":
            depth += 1
        elif text[i] == ")":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return -1


def _split_module(text):
    """Return (preamble, header_text, body_text, post_endmodule).

    Walks the module declaration as: `module design_top` [optional `# ( params )`]
    `( ports )` `;`. Each parenthesized group is matched with paren-depth
    counting; this handles nested parens inside parameter expressions like
    `$clog2 ( screen_width )`.
    """
    # Upstream names the module `lab_top`; already-adapted files say `design_top`.
    m = re.search(r"^module\s+(?:lab_top|design_top)\b", text, flags=re.MULTILINE)
    if not m:
        return None
    preamble_end = m.start()
    i = m.end()

    # Skip whitespace and an optional `# ( params )` block.
    while i < len(text) and text[i].isspace():
        i += 1
    if i < len(text) and text[i] == "#":
        i += 1
        while i < len(text) and text[i].isspace():
            i += 1
        if i >= len(text) or text[i] != "(":
            return None
        i = _consume_balanced_parens(text, i)
        if i < 0:
            return None

    # Skip whitespace; now expect the port-list `(...)`.
    while i < len(text) and text[i].isspace():
        i += 1
    if i >= len(text) or text[i] != "(":
        return None
    i = _consume_balanced_parens(text, i)
    if i < 0:
        return None

    # Consume optional whitespace + `;`.
    while i < len(text) and text[i].isspace():
        i += 1
    if i >= len(text) or text[i] != ";":
        return None
    header_end = i + 1

    end_m = re.search(r"^\s*endmodule\b", text[header_end:], flags=re.MULTILINE)
    if end_m is None:
        return None
    body_end = header_end + end_m.start()
    post_start = header_end + end_m.end()
    return (
        text[:preamble_end],
        text[preamble_end:header_end],
        text[header_end:body_end],
        text[post_start:],
    )


# Body substitutions: ordered, bounded with word breaks.
_BODY_SUBS = [
    (re.compile(r"\bw_key\b"),  "w_btn"),
    (re.compile(r"\bkey\b"),    "btn"),
    (re.compile(r"\bmic\s*\["), "mic_sample["),
    (re.compile(r"\bmic\b(?!_)"), "mic_sample"),  # bare `mic` → mic_sample, but don't touch mic_*
    # Quartus 23.1std rejects SystemVerilog `export pkg::sym;` — convert each
    # to a localparam alias re-declared in the importing package's scope.
    (re.compile(r"^(\s*)export\s+([A-Za-z_][\w]*)::(\w+)\s*;",
                re.MULTILINE),
     r"\1localparam \3 = \2::\3;"),
]


def _infer_requires(body, has_slow_clk):
    """Scan the design body and return capability requirements implied by access
    patterns. Conservative: only emit a requirement when the design clearly
    *uses* the capability (indexed access or width-arithmetic), not when it
    merely ties the port off. The result is a multiline `// requires:` block
    (or empty string when nothing specific is needed)."""
    needs = {}

    # Strip out trivial tie-offs (`assign X = '0;`, `assign X = '1;`, etc.)
    # before pattern matching, so a design that only writes `assign sound = '0`
    # isn't flagged as needing `audio_out`.
    cleaned = re.sub(
        r"assign\s+\w+\s*=\s*'?[01]+\s*;",
        "",
        body,
    )

    def max_index(name):
        """Largest constant index used with `name[N]` or `name[N:M]`."""
        ms = list(re.finditer(
            r"\b" + re.escape(name) + r"\s*\[\s*(\d+)(?:\s*:\s*\d+)?\s*\]",
            cleaned,
        ))
        return max((int(x.group(1)) for x in ms), default=-1)

    led_max  = max_index("led")
    btn_max  = max_index("btn")
    sw_max   = max_index("sw")
    digit_max = max_index("digit")
    rgb_max = max_index("rgb_r")

    if led_max  >= 0:  needs["leds"]     = "leds >= {}".format(led_max + 1)
    if btn_max  >= 0:  needs["buttons"]  = "buttons >= {}".format(btn_max + 1)
    if sw_max   >= 0:  needs["switches"] = "switches >= {}".format(sw_max + 1)
    if digit_max >= 0: needs["seven_segment"] = "seven_segment >= {}".format(digit_max + 1)
    if rgb_max  >= 0:  needs["rgb_leds"] = "rgb_leds >= {}".format(rgb_max + 1)

    # 7-seg usage without an explicit indexed access — the design uses `w_digit`
    # in width arithmetic, instantiates `seven_segment_display`, or passes
    # `w_digit` as a parameter to a sibling module. With w_digit=0 these all
    # become zero-width which Gowin (and Quartus) reject.
    if (re.search(r"\bw_digit\s*'", cleaned)
            or re.search(r"\[\s*w_digit\s*-\s*1\s*:", cleaned)
            or re.search(r"\bw_digit\s*\*", cleaned)
            or re.search(r"\bseven_segment_display\b", cleaned)
            or re.search(r"#\s*\(\s*w_digit\s*\)", cleaned)):
        needs.setdefault("seven_segment", "seven_segment >= 1")

    # Screen: emit when the design clearly paints pixels — indexed RGB access,
    # screen-coordinate arithmetic, references to color-channel widths
    # (`w_red + w_green + w_blue`), x/y passed as arguments to functions, or
    # references to `display_on`.
    if (re.search(r"\b(red|green|blue)\s*\[", cleaned)
            or re.search(r"\b(x|y)\s*[<>=]+\s*screen_", cleaned)
            or re.search(r"screen_(width|height)\s*[<>=*/+-]", cleaned)
            or re.search(r"\bw_(red|green|blue)\b", cleaned)
            or re.search(r"\(\s*x\s*,\s*y\b", cleaned)
            or re.search(r"\bdisplay_on\b", cleaned)):
        needs["screen"] = "screen >= 320x240"

    # GPIO: detect width-based usage. Labs that use `gpio[N]` need at least
    # N+1 wires; designs using `gpio[N +: K]` need N+K.
    gpio_max_idx = -1
    for m in re.finditer(r"\bgpio\s*\[\s*(\d+)\s*\]", cleaned):
        gpio_max_idx = max(gpio_max_idx, int(m.group(1)))
    for m in re.finditer(r"\bgpio\s*\[\s*(\d+)\s*\+:\s*(\d+)\s*\]", cleaned):
        gpio_max_idx = max(gpio_max_idx, int(m.group(1)) + int(m.group(2)) - 1)
    if gpio_max_idx >= 0:
        needs["gpio"] = "gpio >= {}".format(gpio_max_idx + 1)

    # Audio / serial requirements are deliberately not auto-emitted —
    # designs commonly tie these off with constants, which would yield false
    # positives. Authors add `// requires: audio_in` etc. by hand when needed.

    if not needs:
        return ""

    lines = ["// requires:"]
    for cap_id, line in needs.items():
        lines.append("//   " + line)
    return "\n".join(lines) + "\n"


# Per-design body patches applied AFTER the standard substitutions, by design name.
# Each entry is a list of (regex_pattern, replacement) pairs. Used to encode
# board-portability tweaks that survive re-adaptation.
_DESIGN_BODY_PATCHES = {
    "6_1_geiger_muller_radiation_counter": [
        # Original assumed w_gpio = 100 with shield at offset 36; reduce to 0
        # so signals fit in 32-pin GPIO (typical for Nexys 4 DDR / Basys 3).
        (re.compile(r"shield_gpio_base\s*=\s*36"), "shield_gpio_base = 0"),
    ],
    "1_06_binary_counter": [
        # Original assumed clog2(clk_mhz*1e6) >= w_led, so cnt[$left-:w_led]
        # was always valid. Boards with > 26 LEDs (DE2-115, terasic_sockit)
        # break this; widen cnt to max(clog2(...), w_led).
        (re.compile(r"localparam\s+w_cnt\s*=\s*\$clog2\s*\(\s*clk_mhz\s*\*\s*1000\s*\*\s*1000\s*\)\s*;"),
         "localparam w_cnt = ($clog2(clk_mhz * 1000 * 1000) > w_led) ? "
         "$clog2(clk_mhz * 1000 * 1000) : w_led;"),
    ],
}


# Per-design requirements that the body-scanning heuristic misses (e.g. when
# index expressions use named offsets rather than literals). These are merged
# into the auto-generated `// requires:` block.
_DESIGN_EXTRA_REQUIRES = {
    "6_1_geiger_muller_radiation_counter":              ["gpio >= 10"],
    "6_1_geiger_muller_radiation_counter_on_tang_nano_9k": ["gpio >= 8"],
    # 3_12 indexes gpio[w_gpio-1] and gpio[w_gpio-2] (sk9822 LED strip).
    # Quartus rejects gpio[-1] / gpio[-2] when w_gpio=0; gate the design
    # behind the actual GPIO requirement.
    "3_12_acoustic_locator":                            ["gpio >= 2"],
    # These designs use w_digit as a depth parameter, indexing arrays as
    # [w_digit-1:0]. Quartus rejects [-1:0] when w_digit=0.
    "1_08_7segment_word":                               ["seven_segment >= 1"],
    "4_2_1_start_with_shift_register":                  ["seven_segment >= 1"],
    "4_2_3_ring_buffer_with_single_pointer_view_2_data_layout": ["seven_segment >= 1"],
    "4_2_5_optimized_ff_fifo_view_2_data_layout":       ["seven_segment >= 1"],
    "4_2_6_fifo_view_3_interface_only":                 ["seven_segment >= 1"],
    "9_5_make_shift_register_low_power":                ["seven_segment >= 1"],
}


def _apply_lab_patches(lab_name, body):
    for pat, rep in _DESIGN_BODY_PATCHES.get(lab_name, []):
        body = pat.sub(rep, body)
    return body


def _adapt_body(body, uses_slow_clk):
    out = body
    out = _strip_param_size_casts(out)
    for pat, rep in _BODY_SUBS:
        out = pat.sub(rep, out)
    if uses_slow_clk:
        # Insert the slow_clk synthesizer at the top of the body.
        out = SLOW_CLK_INSERT + out
    return out


def _detect_slow_clk(body):
    return bool(re.search(r"\bslow_clk\b", body))


def _section_and_lab(path):
    """Use the directory containing design_top.sv as the design name. Some sections
    nest designs in subgroups (4_microarchitecture/4_1_pipelines_1/<design>/), so
    we always pick the leaf directory."""
    rel = os.path.relpath(path, BGM_LABS_DIR)
    parts = rel.split(os.sep)
    if len(parts) < 2:
        return None
    return parts[0], parts[-2]


def adapt_one(src_path, dry_run=False):
    section_lab = _section_and_lab(src_path)
    if section_lab is None:
        return ("skipped", src_path, "outside designs/")
    section, lab_name = section_lab
    if section in SKIP_SECTIONS:
        return ("skipped", lab_name, "section excluded")
    if lab_name in SKIP_DESIGNS:
        return ("skipped", lab_name, "explicitly skipped (aggregator)")
    text = _read(src_path)
    text = _strip_includes(text)
    parts = _split_module(text)
    if parts is None:
        return ("failed", lab_name, "could not split module header/body")
    preamble, _hdr, body, post = parts
    uses_slow_clk = _detect_slow_clk(body)
    body_new = _adapt_body(body, uses_slow_clk)
    body_new = _apply_lab_patches(lab_name, body_new)
    # Inject `localparam` shims for legacy basics-graphics-music parameters
    # that the original design_top declared but our canonical signature omits.
    legacy_params = []
    for pname, default in (("pixel_mhz", 25),
                            ("w_sound", 16),
                            ("strobe_to_update_xy_counter_width", 23)):
        if re.search(r"\b" + pname + r"\b", body_new):
            legacy_params.append((pname, default))
    injection = ""
    if legacy_params:
        injection = "    // Legacy basics-graphics-music parameters re-injected as localparams\n"
        for pname, default in legacy_params:
            injection += "    localparam int {p} = {d};\n".format(p=pname, d=default)
        injection += "\n"

    # Also include sibling helper SV files when inferring requirements —
    # game_sprite_display.sv, spectrum.sv, etc. use RGB / x / y indirectly.
    siblings_text = ""
    src_dir = os.path.dirname(src_path)
    for name in os.listdir(src_dir):
        if not name.endswith(".sv") or name in (UPSTREAM_TOP, "design_top.sv", "tb.sv"):
            continue
        try:
            with open(os.path.join(src_dir, name)) as f:
                siblings_text += "\n" + f.read()
        except OSError:
            pass
    requires_block = _infer_requires(body_new + siblings_text, uses_slow_clk)

    # Merge in any per-design extra requirements that the heuristic missed.
    extra = _DESIGN_EXTRA_REQUIRES.get(lab_name)
    if extra:
        if requires_block:
            # Insert before trailing newline.
            requires_block = requires_block.rstrip() + "\n"
            for line in extra:
                requires_block += "//   " + line + "\n"
        else:
            requires_block = "// requires:\n"
            for line in extra:
                requires_block += "//   " + line + "\n"

    out_text = (
        "// =============================================================================\n"
        "// {design} — auto-adapted by tools/adapt_designs.py from\n"
        "//   basics-graphics-music/labs/{section}/{design}/lab_top.sv\n"
        "// =============================================================================\n"
        "//\n"
        "{requires}"
        "\n"
        "{preamble}"
        "{header}"
        "{injection}"
        "{body}\n"
        "endmodule\n"
        "{post}"
    ).format(design=lab_name, section=section,
             preamble=preamble.strip() + ("\n\n" if preamble.strip() else ""),
             header=CANONICAL_HEADER,
             injection=injection,
             body=body_new.rstrip(),
             requires=requires_block,
             post=post)
    out_dir = os.path.join(DESIGNS_OUT, lab_name)
    if dry_run:
        return ("ok", lab_name, "would write {}".format(out_dir))
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "design_top.sv"), "w") as f:
        f.write(out_text)
    # Copy sibling SV/SVH/V helpers (recursively, so cpu/ subdirs etc. are
    # included), applying the same body substitutions so helper modules use
    # uni-fpga's vocabulary.
    src_dir = os.path.dirname(src_path)
    for root, _dirs, names in os.walk(src_dir):
        rel_root = os.path.relpath(root, src_dir)
        for name in names:
            if not (name.endswith(".sv") or name.endswith(".svh") or name.endswith(".v")):
                continue
            if name in (UPSTREAM_TOP, "design_top.sv", "tb.sv"):
                continue
            src_file = os.path.join(root, name)
            with open(src_file) as f:
                content = f.read()
            content = _strip_includes(content)
            content = _move_module_header_imports(content)
            content = _strip_param_size_casts(content)
            for pat, rep in _BODY_SUBS:
                content = pat.sub(rep, content)
            dest_dir = out_dir if rel_root == "." else os.path.join(out_dir, rel_root)
            os.makedirs(dest_dir, exist_ok=True)
            with open(os.path.join(dest_dir, name), "w") as f:
                f.write(content)
    return ("ok", lab_name, "wrote " + out_dir)


def main(argv=None):
    import argparse
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--section", help="adapt only this section (e.g. 1_basics)")
    args = p.parse_args(argv)

    src_paths = []
    for root, _dirs, files in os.walk(BGM_LABS_DIR):
        if UPSTREAM_TOP in files:
            src_paths.append(os.path.join(root, UPSTREAM_TOP))
    src_paths.sort()

    counts = defaultdict(int)
    failures = []
    for src in src_paths:
        sec_lab = _section_and_lab(src)
        if args.section and (sec_lab is None or sec_lab[0] != args.section):
            continue
        status, name, msg = adapt_one(src, dry_run=args.dry_run)
        counts[status] += 1
        if status == "failed":
            failures.append((name, msg))

    print("Adapted: ok={ok}  skipped={sk}  failed={fl}".format(
        ok=counts["ok"], sk=counts["skipped"], fl=counts["failed"]))
    if failures:
        print("Failures:")
        for n, m in failures:
            print("  - {}: {}".format(n, m))
    return 0 if counts["failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
