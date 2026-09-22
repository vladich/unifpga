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
_PLL = re.compile(r"\b(Gowin_rPLL(?:_\w+)?|Gowin_PLL|rPLL|clk_wiz\w*|SB_PLL40_\w+|altpll|MMCME2_BASE|PLLE2_BASE|EHXPLLL)\b")
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


_PORT_DECL = re.compile(
    r"^\s*(?:input|output|inout)\s+(?:logic\s+|wire\s+|reg\s+)?(?:\[[^\]]*\]\s*)?([A-Za-z_][A-Za-z0-9_]*)\s*(?:,|\)|$)",
    re.MULTILINE)


def top_ports(text):
    """Names declared as top-level ports in the preprocessed board top."""
    head = text.split(");", 1)[0]
    return set(_PORT_DECL.findall(head))


def port_polarity(text):
    """Per top-level port: does BGM invert it, and does it bit-swap it?

        assign LED = ~ lab_led;  assign LED = w_led' (~ lab_led);
        assign LED_N = ~ led;    `SWAP_BITS (LED, ~ lab_led)   -> LED inverted (+mirrored)
        .key ( ~ KEY )   lab_key = ~ KEY_N [..]   key = ~ { KEY2, KEY3 }  -> KEY* inverted

    Returns {PORT: {"inverted": bool, "mirrored": bool}} for the ports that
    appear in such expressions; ports used plainly are absent."""
    ports = top_ports(text)
    out = {}

    def mark(name, inverted=None, mirrored=None):
        if name not in ports:
            return
        d = out.setdefault(name, {"inverted": False, "mirrored": False})
        if inverted:
            d["inverted"] = True
        if mirrored:
            d["mirrored"] = True

    # Output side: assign PORT[...] = [cast (] ~ ...
    for m in re.finditer(r"\bassign\s+([A-Za-z_]\w*)\s*(?:\[[^\]]*\])?\s*=\s*(?:\w+'\s*\()?\s*(~?)", text):
        if m.group(2):
            mark(m.group(1), inverted=True)
    for m in re.finditer(r"SWAP_BITS\s*\(\s*([A-Za-z_]\w*)\s*,\s*(~?)", text):
        mark(m.group(1), inverted=bool(m.group(2)), mirrored=True)
    # Input side: ~ PORT, ~ PORT [..], ~ { P1, P2, ... }
    for m in re.finditer(r"~\s*([A-Za-z_]\w*)\b", text):
        mark(m.group(1), inverted=True)
    for m in re.finditer(r"~\s*\{([^}]*)\}", text):
        for name in re.findall(r"[A-Za-z_]\w*", m.group(1)):
            mark(name, inverted=True)
    return out


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

# ---------------------------------------------------------------------------
# PLL settings and the clocks BGM derives from them (P3.1)
# ---------------------------------------------------------------------------

_DEFPARAM = re.compile(r"defparam\s+\w+\.(\w+)\s*=\s*([^;]+);")
_PLL_MODELLED = ("Gowin_rPLL", "SB_PLL40_PAD", "SB_PLL40_CORE", "rPLL")


def _pll_wrapper_files(vdir, files, names):
    """Candidate wrapper files: the variant dir first, then every directory on
    the include chain (a `_yosys` twin ships its own gowin_rpll.v next to a
    two-line top that includes the sibling's); `<name>/` subdirectories too
    (marsohod3gw2 keeps gowin_rpll/gowin_rpll.v)."""
    dirs = [vdir]
    for f in files or []:
        d = os.path.dirname(f)
        if d not in dirs:
            dirs.append(d)
    out = []
    for d in dirs:
        for n in names:
            for cand in (os.path.join(d, n), os.path.join(d, os.path.splitext(n)[0], n)):
                if os.path.exists(cand) and cand not in out:
                    out.append(cand)
    return out


def rpll_settings(vdir, files=None, filename="gowin_rpll.v"):
    """Gowin rPLL dividers from the variant's gowin_rpll.v: {"FCLKIN",
    "IDIV_SEL", "FBDIV_SEL", "ODIV_SEL", "DYN_SDIV_SEL", "DEVICE", "file"}
    or None. CLKOUT = FCLKIN / (IDIV_SEL + 1) * (FBDIV_SEL + 1);
    CLKOUTD = CLKOUT / DYN_SDIV_SEL."""
    for cand in _pll_wrapper_files(vdir, files, (filename,)):
        with open(cand, encoding="utf-8", errors="replace") as f:
            vals = {k: v.strip().strip('"') for k, v in _DEFPARAM.findall(f.read())}
        if "IDIV_SEL" in vals and "FBDIV_SEL" in vals:
            return {"FCLKIN": float(vals.get("FCLKIN", "27")),
                    "IDIV_SEL": int(vals["IDIV_SEL"]),
                    "FBDIV_SEL": int(vals["FBDIV_SEL"]),
                    "ODIV_SEL": int(vals.get("ODIV_SEL", "8")),
                    "DYN_SDIV_SEL": int(vals.get("DYN_SDIV_SEL", "2")),
                    "DEVICE": vals.get("DEVICE"),
                    "file": cand}
    return None


def _vlit(s):
    """Verilog integer literal (`4'b0000`, `7'b1000010`, `12`) -> int."""
    s = s.strip().replace("_", "")
    m = re.match(r"^(?:\d+)?'([bBdDhHoO])([0-9a-fA-F]+)$", s)
    if m:
        return int(m.group(2), {"b": 2, "d": 10, "h": 16, "o": 8}[m.group(1).lower()])
    return int(s)


_DECL_LINE = re.compile(r"^\s*(?:wire|logic|reg)\b", re.M)


def _net_used(text, net, ports):
    """A PLL output is 'used' when it is a top port or appears in at least
    two non-declaration places (the PLL connection plus one consumer):
    `high_clk` on the Tang Nano 20K alt variant is connected but dead,
    a7_lite_35t's implicit `serial_clk` net is connected and consumed."""
    if net in ports:
        return True
    uses = 0
    for line in text.splitlines():
        if _DECL_LINE.match(line):
            continue
        uses += len(re.findall(r"(?<![A-Za-z0-9_])%s(?![A-Za-z0-9_])" % re.escape(net), line))
    return uses >= 2


def pll_outputs(vdir, text, files=None):
    """(outputs, unmodelled). outputs = [(net, mhz, via)] for every PLL output
    BGM connects and uses: Gowin_rPLL clkout/clkoutd computed from
    gowin_rpll.v, SB_PLL40_* from its DIVR/DIVF/DIVQ on the board clock.
    unmodelled = PLL modules whose frequency this oracle cannot compute yet
    (GW5 Gowin_PLL, Xilinx clk_wiz, altpll, ...) and rPLLs without a wrapper file."""
    ports = top_ports(text)
    outs, unmodelled = [], []
    # Gowin_rPLL wrappers (gowin_rpll.v), including BGM's suffixed ones
    # (Gowin_rPLL_250 / gowin_rpll_250.v on tang_nano_9k_hdmi_no_ip_tm1638).
    # A wrapper clocked from another PLL's output (directly or through a
    # BUFG) runs at that output's real frequency, not at its FCLKIN string.
    aliases = {o: i for i, o in re.findall(r"\bBUFG\s+\w+\s*\(\s*\.I\s*\(\s*(\w+)\s*\)\s*,\s*\.O\s*\(\s*(\w+)\s*\)", text)}
    pending = []
    for wrapper in sorted(set(re.findall(r"\bGowin_rPLL(_\w+)?\b", text))):
        module = "Gowin_rPLL" + (wrapper or "")
        for inst in instantiations(text, module):
            st = rpll_settings(vdir, files, "gowin_rpll{}.v".format((wrapper or "").lower()))
            if st is None:
                unmodelled.append("{}(no gowin_rpll{}.v)".format(module, (wrapper or "").lower()))
                continue
            clkin = dict(inst["ports"]).get("clkin", "")
            pending.append((inst, st, aliases.get(clkin, clkin)))
    known = {}
    progress = True
    while pending and progress:
        progress = False
        for item in list(pending):
            inst, st, clkin = item
            if clkin in known:
                fin = known[clkin]
            elif any(clkin == other_in for _i, _s, other_in in pending if _i is not inst) or \
                    any(clkin == o_net for o_net in _pending_outputs(pending, inst)):
                continue            # wait for the feeding PLL
            else:
                fin = st["FCLKIN"]
            f_clkout = fin / (st["IDIV_SEL"] + 1) * (st["FBDIV_SEL"] + 1)
            for port, expr in inst["ports"]:
                if not expr:
                    continue
                f = f_clkout if port == "clkout" else f_clkout / st["DYN_SDIV_SEL"] if port == "clkoutd" else None
                if f is None:
                    continue
                known[expr] = f
                for alias_out, alias_in in aliases.items():
                    if alias_in == expr:
                        known[alias_out] = f
                if _net_used(text, expr, ports):
                    outs.append((expr, f, port))
            pending.remove(item)
            progress = True
    # the raw rPLL primitive with inline parameters (BGM _yosys variants)
    for inst in instantiations(text, "rPLL"):
        params = dict(inst["params"])
        try:
            fclkin = float(params.get("FCLKIN", '"27"').strip('"'))
            idiv, fbdiv = int(params.get("IDIV_SEL", "0")), int(params.get("FBDIV_SEL", "0"))
            sdiv = int(params.get("DYN_SDIV_SEL", "2"))
        except ValueError:
            unmodelled.append("rPLL(params)")
            continue
        f_clkout = fclkin / (idiv + 1) * (fbdiv + 1)
        for port, expr in inst["ports"]:
            if not expr or not _net_used(text, expr, ports):
                continue
            if port == "CLKOUT":
                outs.append((expr, f_clkout, "clkout"))
            elif port == "CLKOUTD":
                outs.append((expr, f_clkout / sdiv, "clkoutd"))
    fin = clk_mhz(text)
    for mod in ("SB_PLL40_PAD", "SB_PLL40_CORE"):
        for inst in instantiations(text, mod):
            params = dict(inst["params"])
            try:
                divr, divf, divq = (_vlit(params[k]) for k in ("DIVR", "DIVF", "DIVQ"))
            except (KeyError, ValueError):
                unmodelled.append(mod + "(params)")
                continue
            if fin is None:
                unmodelled.append(mod + "(no clk_mhz)")
                continue
            f = fin / (divr + 1) * (divf + 1) / (2 ** divq)
            for port, expr in inst["ports"]:
                if port in ("PLLOUTCORE", "PLLOUTGLOBAL") and expr:
                    outs.append((expr, f, mod))
    # Xilinx clk_wiz: MMCME2_ADV parameters in the generated clk_wiz_clk_wiz.v,
    # wrapper ports clk_out1..3 = CLKOUT0..2 (a7_lite_35t: 250 / 50 / 25 MHz)
    wiz_done = False
    for inst in instantiations(text, "clk_wiz"):
        st = clk_wiz_settings(vdir, files)
        if st is None:
            unmodelled.append("clk_wiz(no clk_wiz_clk_wiz.v)")
            continue
        f_vco = st["f_in"] / st["DIVCLK_DIVIDE"] * st["CLKFBOUT_MULT_F"]
        for port, expr in inst["ports"]:
            m = re.match(r"^clk_out(\d)$", port)
            if not m or not expr:
                continue
            div = st["outputs"].get(int(m.group(1)) - 1)
            if div and _net_used(text, expr, ports):
                outs.append((expr, f_vco / div, port))
        wiz_done = True
    for name in sorted(set(pll_instances(text))):
        if name not in _PLL_MODELLED and not name.startswith("Gowin_rPLL_") and not (
                wiz_done and name.startswith("clk_wiz")):
            unmodelled.append(name)
    return outs, unmodelled


_WIZ_PARAM = re.compile(r"\.(CLKIN1_PERIOD|DIVCLK_DIVIDE|CLKFBOUT_MULT_F|CLKOUT0_DIVIDE_F|CLKOUT([1-6])_DIVIDE)\s*\(\s*([0-9.]+)\s*\)")


def clk_wiz_settings(vdir, files=None):
    """MMCM settings of a Vivado clk_wiz core: {"f_in", "DIVCLK_DIVIDE",
    "CLKFBOUT_MULT_F", "outputs": {index: divide}} or None."""
    for cand in _pll_wrapper_files(vdir, files, ("clk_wiz_clk_wiz.v",)):
        with open(cand, encoding="utf-8", errors="replace") as f:
            body = f.read()
        vals, outs = {}, {}
        for name, idx, val in _WIZ_PARAM.findall(body):
            if name == "CLKOUT0_DIVIDE_F":
                outs[0] = float(val)
            elif idx:
                outs[int(idx)] = float(val)
            else:
                vals[name] = float(val)
        if "CLKIN1_PERIOD" in vals and "CLKFBOUT_MULT_F" in vals and outs:
            return {"f_in": 1000.0 / vals["CLKIN1_PERIOD"], "DIVCLK_DIVIDE": vals.get("DIVCLK_DIVIDE", 1.0),
                    "CLKFBOUT_MULT_F": vals["CLKFBOUT_MULT_F"], "outputs": outs, "file": cand}
    return None



def _pending_outputs(pending, skip):
    for inst, _st, _in in pending:
        if inst is skip:
            continue
        for port, expr in inst["ports"]:
            if port in ("clkout", "clkoutd") and expr:
                yield expr


def lab_clock_source(text):
    """'pixel' when the lab runs on the PLL pixel clock (`localparam lab_mhz =
    pixel_mhz; assign clk = pixel_clk` — iCEBreaker DVI, Tang Primer 20K Dock
    LCD/HDMI), else 'board'."""
    m = re.search(r"localparam\s+lab_mhz\s*=\s*([A-Za-z_]\w*)", text)
    return "pixel" if m and m.group(1) == "pixel_mhz" else "board"


def lab_mhz(text):
    """The clk_mhz the lab (lab_top) receives: pixel_mhz or clk_mhz."""
    if lab_clock_source(text) == "pixel":
        m = re.search(r"\bpixel_mhz\s*=\s*([0-9.]+)", text)
        return float(m.group(1)) if m else None
    return clk_mhz(text)


def screen_size(text):
    """(screen_width, screen_height) parameters after preprocessing, or None."""
    w = re.search(r"\bscreen_width\s*=\s*(\d+)", text)
    h = re.search(r"\bscreen_height\s*=\s*(\d+)", text)
    return (int(w.group(1)), int(h.group(1))) if w and h else None


_ASSIGN = re.compile(r"\bassign\s+([A-Za-z_]\w*)\s*=\s*([^;]+);")


def port_assigns(text):
    """{PORT: expr} for every `assign PORT = expr;` of a top-level port
    (`LCD_BL = 1'b0`, `LCD_BL = ~ rst`, `LCD_INIT = 1'b0`)."""
    ports = top_ports(text)
    return {p: " ".join(e.split()) for p, e in _ASSIGN.findall(text) if p in ports}


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
        "pll_outputs": pll_outputs(vdir, text, pp.files)[0],
        "pll_unmodelled": pll_outputs(vdir, text, pp.files)[1],
        "lab_clock": lab_clock_source(text),
        "lab_mhz": lab_mhz(text),
        "screen_size": screen_size(text),
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
