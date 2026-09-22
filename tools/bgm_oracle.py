#!/usr/bin/env python3
"""
Read-only access to the basics-graphics-music (BGM) checkout as the oracle for
what a board must look like (PLAN.md, sections 1 and 7).

BGM keeps one directory per board *variant* under boards/<variant>/ with the
vendor constraint files and a hand-written `board_specific_top.sv`. Variants
such as `<board>_no_tm1638` consist of a few `` `define ``s followed by
`` `include "../<sibling>/board_specific_top.sv" ``; the sibling then selects
its reset source, LED polarity, TM1638 instantiation, LCD resolution and so on
with `` `ifdef `` blocks. To extract facts for one variant we therefore run a
small preprocessor over the file (define / undef / ifdef / ifndef / elsif /
else / endif / include-of-a-sibling-top) and analyse the surviving text.

The module is deliberately dependency-free (no PyYAML) so the parity tool and
the audit can import it from anywhere.
"""

import os
import re

REPO = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
BGM_DIR = os.environ.get(
    "UNIFPGA_BGM_DIR",
    os.path.normpath(os.path.join(REPO, "..", "basics-graphics-music")))
BGM_BOARDS = os.path.join(BGM_DIR, "boards")

TOP_FILE = "board_specific_top.sv"

# BGM enables optional blocks of board_specific_top.sv per *lab* through
# labs/<section>/<lab>/lab_specific_board_config.svh. For board-level facts we
# preprocess with the "full lab" profile (everything a lab can ask for); a
# variant that must not have a block says so itself with FORCE_NO_* defines,
# which the tops honour before these are consulted.
FULL_LAB_DEFINES = {
    "INSTANTIATE_TM1638_BOARD_CONTROLLER_MODULE": "",
    "INSTANTIATE_GRAPHICS_INTERFACE_MODULE": "",
    "INSTANTIATE_SOUND_OUTPUT_INTERFACE_MODULE": "",
    "INSTANTIATE_MICROPHONE_INTERFACE_MODULE": "",
}


# ---------------------------------------------------------------------------
# Locating the variant
# ---------------------------------------------------------------------------

def variant_dir(*candidates):
    """First existing boards/<candidate> directory, else None."""
    for cand in candidates:
        if not cand:
            continue
        d = os.path.join(BGM_BOARDS, cand)
        if os.path.isdir(d):
            return d
    return None


def has_bgm():
    return os.path.isdir(BGM_BOARDS)


# unifpga configuration ids that are open-flow twins of a vendor configuration
# carry one of these suffixes; BGM has no directory for the twin, so the
# oracle is the vendor variant's directory.
_TWIN_SUFFIXES = ("_openxc7", "_mistral", "_oxide", "_nextpnr")


def variant_dir_for(config_id, board_id=None):
    """BGM variant directory for a configuration: the id itself, the id
    without an open-flow twin suffix, then the board id."""
    cands = [config_id]
    for suf in _TWIN_SUFFIXES:
        if config_id.endswith(suf):
            cands.append(config_id[: -len(suf)])
    if board_id:
        cands.append(board_id)
    return variant_dir(*cands)


# ---------------------------------------------------------------------------
# Minimal SystemVerilog preprocessor
# ---------------------------------------------------------------------------

_PP_LINE = re.compile(r"^\s*`(ifdef|ifndef|elsif|else|endif|define|undef|include)\b\s*(.*)$")
_INCLUDE_SIBLING = re.compile(r'"\.\./([^/"]+)/' + re.escape(TOP_FILE) + '"')


class Preprocessed(object):
    """Result of preprocess(): the surviving source text, the macros that were
    defined, and the chain of files that were read (variant first)."""

    def __init__(self):
        self.text = ""
        self.defines = {}
        self.files = []
        self.branches = []      # (macro, taken) for every ifdef/ifndef seen


def preprocess(path, defines=None, _pp=None, _depth=0):
    """Evaluate the conditional-compilation directives of `path`.

    Only the directives BGM's board tops use are understood. Text inside a
    false branch is dropped; directive lines themselves are dropped; a
    `` `include "../<sibling>/board_specific_top.sv" `` is expanded inline with
    the macros defined so far (that is how `_no_tm1638` variants work). Other
    includes are left in place as text."""
    pp = _pp or Preprocessed()
    if defines is not None and _pp is None:
        pp.defines.update(defines)
    if _depth > 4 or not os.path.exists(path):
        return pp
    pp.files.append(path)
    out = []
    # Stack of (parent_active, this_branch_taken_already, currently_active)
    stack = []
    active = True
    with open(path, encoding="utf-8", errors="replace") as f:
        for raw in f:
            line = raw.rstrip("\n")
            m = _PP_LINE.match(line)
            if not m:
                if active:
                    out.append(line)
                continue
            directive, rest = m.group(1), m.group(2).strip()
            if directive in ("ifdef", "ifndef"):
                macro = rest.split()[0] if rest else ""
                cond = (macro in pp.defines) if directive == "ifdef" else (macro not in pp.defines)
                pp.branches.append((macro, bool(active and cond)))
                stack.append((active, active and cond))
                active = active and cond
            elif directive == "elsif":
                if not stack:
                    continue
                parent_active, taken = stack[-1]
                macro = rest.split()[0] if rest else ""
                cond = macro in pp.defines
                new_active = parent_active and not taken and cond
                pp.branches.append((macro, bool(new_active)))
                stack[-1] = (parent_active, taken or new_active)
                active = new_active
            elif directive == "else":
                if not stack:
                    continue
                parent_active, taken = stack[-1]
                active = parent_active and not taken
                stack[-1] = (parent_active, True)
            elif directive == "endif":
                if stack:
                    parent_active, _ = stack.pop()
                    active = parent_active
            elif directive == "define":
                if active:
                    parts = rest.split(None, 1)
                    if parts:
                        pp.defines[parts[0]] = parts[1] if len(parts) > 1 else ""
            elif directive == "undef":
                if active and rest:
                    pp.defines.pop(rest.split()[0], None)
            elif directive == "include":
                if not active:
                    continue
                sib = _INCLUDE_SIBLING.search(rest)
                if sib:
                    sib_path = os.path.join(os.path.dirname(path), "..", sib.group(1), TOP_FILE)
                    sub = Preprocessed()
                    sub.defines = pp.defines           # share the macro table
                    preprocess(os.path.normpath(sib_path), None, sub, _depth + 1)
                    pp.files.extend(sub.files)
                    pp.branches.extend(sub.branches)
                    out.append(sub.text)
                else:
                    out.append(line)               # keep foreign includes as text
    pp.text = (pp.text + "\n" if pp.text else "") + "\n".join(out)
    return pp


def preprocess_variant(vdir, defines=None, full_lab=True):
    """Preprocess boards/<variant>/board_specific_top.sv. Returns Preprocessed
    (empty text when the file does not exist). With `full_lab` (default) the
    INSTANTIATE_* lab macros are predefined; the variant's own FORCE_NO_*
    defines still win because the tops test them first."""
    d = dict(FULL_LAB_DEFINES) if full_lab else {}
    if defines:
        d.update(defines)
    return preprocess(os.path.join(vdir, TOP_FILE), d)


# ---------------------------------------------------------------------------
# Fact extraction from the preprocessed top
# ---------------------------------------------------------------------------

_CLK_MHZ = re.compile(r"\bclk_mhz\s*=\s*([0-9.]+)")
_RST = re.compile(r"\b(?:wire\s+rst|assign\s+rst)\s*=\s*([^;]+);")
_PLL = re.compile(r"\b(Gowin_rPLL|Gowin_PLL|clk_wiz\w*|SB_PLL40_\w+|altpll|MMCME2_BASE|PLLE2_BASE|EHXPLLL)\b")
_MHZ_COMMENT = re.compile(r"//\s*([0-9]+(?:\.[0-9]+)?)\s*MHz", re.IGNORECASE)
# `Module inst (`  |  `Module # ( ... ) inst (`  — the instance name is followed
# by `(` and then a newline or a `.port` connection.
_MODULE_INST = re.compile(
    r"^[ \t]*([A-Za-z_][A-Za-z0-9_]*)[ \t]*(?:#[ \t]*\((?:[^()]|\([^()]*\))*\)[ \t\r\n]*)?"
    r"([A-Za-z_][A-Za-z0-9_]*)[ \t]*\([ \t]*(?=\r?\n|\.)",
    re.MULTILINE)
_SV_KEYWORDS = {"module", "if", "for", "always", "always_ff", "always_comb", "assign", "wire",
                "logic", "input", "output", "inout", "generate", "case", "else", "begin",
                "initial", "function", "task", "localparam", "parameter", "return", "while",
                "endmodule", "genvar", "reg", "integer", "int", "bit", "typedef", "import",
                "end", "endgenerate", "endcase", "default"}


def clk_mhz(text):
    m = _CLK_MHZ.search(text)
    return float(m.group(1)) if m else None


def reset_exprs(text):
    """Every `wire rst = <expr>;` / `assign rst = <expr>;` left after
    preprocessing (normally one)."""
    return [x.strip() for x in _RST.findall(text)]


def classify_reset(exprs):
    """Map BGM reset expressions to the unifpga reset-policy source kinds.

    Returns a set drawn from {"pin", "switch_msb", "any_key", "key_msb",
    "key_0", "power_up"}. `any_key` covers `| (~KEY)`; a single indexed key
    is reported as key_0 / key_msb so the policy can be exact."""
    kinds = set()
    for e in exprs:
        if "rst_on_power_up" in e:
            kinds.add("power_up")
        if re.search(r"\b(SW|sw)\s*\[\s*w_(?:lab_)?sw", e):
            kinds.add("switch_msb")
        if re.search(r"\(\s*\|\s*\(?\s*~?\s*(KEY|key|BTN|btn)\b", e):
            kinds.add("any_key")
        elif re.search(r"\b(KEY\w*|key|BTN\w*|btn|tm_key)\s*\[\s*w_\w*key\w*\s*-\s*1\s*\]", e):
            kinds.add("key_msb")
        elif re.search(r"\b(KEY\w*|key|BTN\w*|btn|tm_key)\s*\[\s*w_\w*key\w*\s*\]", e):
            kinds.add("key_msb")           # BGM's `KEY [w_lab_key]`: first key above the lab's range
        elif re.search(r"\b(KEY\w*|key|BTN\w*|btn)\s*\[\s*0\s*\]", e):
            kinds.add("key_0")
        elif re.search(r"\bBTN_N\b", e):
            kinds.add("key_0")
        stripped = re.sub(r"rst_on_power_up|tm_key\s*\[[^\]]*\]", "", e)
        if re.search(r"(?i)\b(reset\w*|rst\w*|resetn|cpu_resetn|ck_rst|rstn_ff\s*\[\s*1\s*\])\b", stripped):
            kinds.add("pin")
    return kinds


def pll_instances(text):
    return _PLL.findall(text)


def pll_output_mhz(text):
    """Frequencies BGM annotates next to PLL outputs (`// 33.33 MHz`)."""
    return sorted({float(x) for x in _MHZ_COMMENT.findall(text)})


def instantiated_modules(text):
    """Module names instantiated in the preprocessed top (best effort)."""
    names = []
    for m in _MODULE_INST.finditer(text):
        mod, inst = m.group(1), m.group(2)
        if mod in _SV_KEYWORDS or inst in _SV_KEYWORDS or mod.startswith("`"):
            continue
        names.append(mod)
    return names


_INST_HEAD = re.compile(
    r"^[ \t]*([A-Za-z_][A-Za-z0-9_]*)[ \t\r\n]*(#[ \t\r\n]*\()?", re.MULTILINE)


def _balanced(text, start):
    """Index just past the `)` matching the `(` at `start`, or -1."""
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


_PORT_CONN = re.compile(r"\.\s*([A-Za-z_][A-Za-z0-9_]*)\s*\(", re.MULTILINE)


def _split_connections(body):
    """`.a ( X ), .b ( Y [3] ) ...` -> OrderedDict-like list of (port, expr)."""
    out = []
    i = 0
    while True:
        m = _PORT_CONN.search(body, i)
        if not m:
            break
        end = _balanced(body, m.end() - 1)
        if end < 0:
            break
        expr = body[m.end():end - 1]
        expr = re.sub(r"//[^\n]*", "", expr)          # strip trailing comments
        out.append((m.group(1), " ".join(expr.split())))
        i = end
    return out


def instantiations(text, module):
    """Every instantiation of `module` in the preprocessed text as
    {"instance": name, "params": [(p, expr)], "ports": [(port, expr)]}."""
    found = []
    for m in re.finditer(r"(?<![A-Za-z0-9_])" + re.escape(module) + r"(?![A-Za-z0-9_])", text):
        i = m.end()
        # optional parameter block
        j = i
        while j < len(text) and text[j] in " \t\r\n":
            j += 1
        params = []
        if j < len(text) and text[j] == "#":
            k = text.find("(", j)
            end = _balanced(text, k)
            if end < 0:
                continue
            params = _split_connections(text[k + 1:end - 1])
            j = end
            while j < len(text) and text[j] in " \t\r\n":
                j += 1
        im = re.match(r"([A-Za-z_][A-Za-z0-9_]*)[ \t\r\n]*(\[[^\]]*\])?[ \t\r\n]*\(", text[j:])
        if not im:
            continue
        k = j + im.end() - 1
        end = _balanced(text, k)
        if end < 0:
            continue
        found.append({"instance": im.group(1), "params": params,
                      "ports": _split_connections(text[k + 1:end - 1])})
    return found


def polarity_hints(text):
    """Which user-level signal classes BGM inverts or mirrors for this variant."""
    hints = set()
    if re.search(r"\.key\s*\(\s*~", text) or re.search(r"~\s*KEY\b", text):
        hints.add("keys~")
    if re.search(r"\.sw\s*\(\s*~", text):
        hints.add("sw~")
    if re.search(r"\bLED\w*\s*=\s*~", text) or re.search(r"assign\s+LED\w*\s*=\s*~", text):
        hints.add("leds~")
    if re.search(r"SWAP_BITS\s*\(\s*LED", text):
        hints.add("leds-mirrored")
    if re.search(r"\.(abcdefgh|hgfedcba)\s*\(\s*~|HEX\d*\s*=\s*.*~\s*hgfedcba", text):
        hints.add("segments~")
    return hints


def gowin_options(vdir):
    """`set_option -use_*_as_gpio` flags and the set_device args from the
    variant's board_specific.tcl (falls back to the included sibling's)."""
    opts, device = [], None
    for cand in _tcl_candidates(vdir):
        with open(cand, encoding="utf-8", errors="replace") as f:
            for line in f:
                s = line.strip()
                if s.startswith("#"):
                    continue
                m = re.match(r"set_option\s+(-use_\w+)\s+1", s)
                if m:
                    opts.append(m.group(1))
                m = re.match(r"set_device\s+(.*)$", s)
                if m:
                    device = m.group(1).strip()
        if opts or device:
            break
    return opts, device


def _tcl_candidates(vdir):
    out = []
    tcl = os.path.join(vdir, "board_specific.tcl")
    if os.path.exists(tcl):
        out.append(tcl)
    top = os.path.join(vdir, TOP_FILE)
    if os.path.exists(top):
        with open(top, encoding="utf-8", errors="replace") as f:
            head = f.read(4000)
        m = _INCLUDE_SIBLING.search(head)
        if m:
            sib = os.path.join(vdir, "..", m.group(1), "board_specific.tcl")
            if os.path.exists(sib):
                out.append(os.path.normpath(sib))
    return out


# ---------------------------------------------------------------------------
# One-call summary
# ---------------------------------------------------------------------------

def summarize(vdir):
    """Facts about one BGM variant directory after preprocessing its top."""
    pp = preprocess_variant(vdir)
    text = pp.text
    opts, device = gowin_options(vdir)
    rst = reset_exprs(text)
    return {
        "dir": vdir,
        "files": pp.files,
        "defines": sorted(pp.defines),
        "clk_mhz": clk_mhz(text),
        "reset_exprs": rst,
        "reset_kinds": sorted(classify_reset(rst)),
        "pll_instances": pll_instances(text),
        "pll_output_mhz": pll_output_mhz(text),
        "modules": instantiated_modules(text),
        "polarity": sorted(polarity_hints(text)),
        "gowin_options": opts,
        "gowin_set_device": device,
    }


if __name__ == "__main__":
    import json
    import sys
    for name in sys.argv[1:]:
        d = variant_dir(name)
        if d is None:
            print("{}: no such BGM variant".format(name), file=sys.stderr)
            continue
        print(json.dumps(summarize(d), indent=2))
