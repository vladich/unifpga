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

import glob
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
    pending = None          # (name, body) of a `define continued with backslashes
    with open(path, encoding="utf-8", errors="replace") as f:
        for raw in f:
            line = raw.rstrip("\n")
            if pending is not None:
                name, body = pending
                cont = line.rstrip()
                more = cont.endswith("\\")
                body = body + " " + (cont[:-1] if more else cont).strip()
                pending = (name, body) if more else None
                if pending is None and active:
                    pp.defines[name] = " ".join(body.split())
                continue
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
                parts = rest.split(None, 1)
                body = parts[1] if len(parts) > 1 else ""
                if parts and body.rstrip().endswith("\\"):
                    pending = (parts[0], body.rstrip()[:-1].strip())
                elif active and parts:
                    pp.defines[parts[0]] = body
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


def _expand_aliases(expr, text):
    """One level of `wire x = e;` / `assign x = e;` substituted into a reset
    expression (Tang Primer 25K: `rst = tm_rst | tm_key [..]`, `tm_rst =
    rst_on_power_up`) so classify_reset sees the sources behind the alias."""
    def sub(m):
        name = m.group(0)
        d = re.search(r"\b(?:wire|assign)\s+(?:\[[^\]]*\]\s*)?" + re.escape(name) + r"\s*=\s*([^;]+);", text)
        return "( {} )".format(" ".join(d.group(1).split())) if d and name != "rst" else name
    return re.sub(r"\b(?!rst_on_power_up\b)[a-z]\w*_rst\b", sub, expr)


def reset_exprs(text):
    """Every `wire rst = <expr>;` / `assign rst = <expr>;` left after
    preprocessing (normally one), plus the source of an xpm_cdc_async_rst
    whose dest_arst is rst (a7_lite: `.src_arst (~ RESETN)`)."""
    out = [_expand_aliases(x.strip(), text) for x in _RST.findall(text)]
    for inst in instantiations(text, "xpm_cdc_async_rst"):
        ports = dict(inst["ports"])
        if ports.get("dest_arst", "").strip() == "rst" and ports.get("src_arst", "").strip():
            out.append(" ".join(ports["src_arst"].split()))
    return out


def reset_sync_stages(text):
    """Flops between the reset pin releasing and `rst` deasserting: BGM
    c5gx's `logic [1:0] rstn_ff ... rst = ~rstn_ff[1]` (2), a7_lite's
    xpm_cdc_async_rst (DEST_SYNC_FF, default 4); None when rst follows the
    pin combinationally."""
    m = re.search(r"\b(?:logic|reg|wire)\s*\[\s*(\d+)\s*:\s*0\s*\]\s*(rstn?_ff\w*|rstn?_sync\w*)\b", text)
    if m and re.search(r"\brst\s*=\s*~?\s*" + re.escape(m.group(2)) + r"\s*\[", text):
        return int(m.group(1)) + 1
    for inst in instantiations(text, "xpm_cdc_async_rst"):
        if dict(inst["ports"]).get("dest_arst", "").strip() == "rst":
            params = dict(inst["params"])
            try:
                return int(params.get("DEST_SYNC_FF", "4"))
            except ValueError:
                return 4
    return None


def tm1638_reset(text):
    """The net BGM resets the TM1638 controller from when it is not the lab's
    rst (Tang Primer 25K: `.rst ( tm_rst )`, tm_rst = rst_on_power_up):
    'power_up', or None."""
    for inst in instantiations(text, "tm1638_board_controller"):
        net = dict(inst["ports"]).get("rst", "").strip()
        if net and net != "rst":
            e = _expand_aliases(net, text)
            return "power_up" if re.sub(r"[()\s]", "", e) == "rst_on_power_up" else e
    return None


def reset_sync_asserts(text):
    """True when BGM's synchroniser delays the reset's *assertion* instead of
    its release: a7_lite's `xpm_cdc_async_rst (.dest_arst (rst), .src_arst
    (~ RESETN))` keeps the default RST_ACTIVE_HIGH = 0, so src_arst and
    dest_arst are active low while the lab reads dest_arst as active-high rst
    — rst rises DEST_SYNC_FF clocks after the button goes down and falls with
    it (an upstream polarity slip; the overlay reproduces it)."""
    for inst in instantiations(text, "xpm_cdc_async_rst"):
        if dict(inst["ports"]).get("dest_arst", "").strip() == "rst":
            return str(dict(inst["params"]).get("RST_ACTIVE_HIGH", "0")).strip() in ("0", "1'b0", "0'b0")
    return False


def classify_reset(exprs):
    """Map BGM reset expressions to the unifpga reset-policy source kinds.

    Returns a set drawn from {"pin", "switch_msb", "any_key", "key_msb",
    "key_0", "tm_key_msb", "power_up"}. `any_key` covers `| (~KEY)`; a single indexed key
    is reported as key_0 / key_msb so the policy can be exact."""
    kinds = set()
    for e in exprs:
        if "rst_on_power_up" in e:
            kinds.add("power_up")
        if re.search(r"\b(SW|sw)\s*\[\s*w_(?:lab_)?sw", e):
            kinds.add("switch_msb")
        if re.search(r"\(\s*\|\s*\(?\s*~?\s*(KEY|key|BTN|btn)\b", e):
            kinds.add("any_key")
        elif re.search(r"\btm_key\s*\[\s*w_\w*key\w*\s*-\s*1\s*\]", e):
            kinds.add("tm_key_msb")        # the TM1638's own last key (icebreaker, tang_primer_20k)
        elif re.search(r"\b(KEY\w*|key|BTN\w*|btn)\s*\[\s*w_\w*key\w*\s*-\s*1\s*\]", e):
            kinds.add("key_msb")
        elif re.search(r"\b(KEY\w*|key|BTN\w*|btn)\s*\[\s*w_\w*key\w*\s*\]", e):
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
    r"^\s*(?:input|output|inout)\s+(?:logic\s+|wire\s+|reg\s+)?(?:\[[^\]]*\]\s*)?([A-Za-z_][A-Za-z0-9_]*)\s*(?:,|\)|//|$)",
    re.MULTILINE)


def strip_comments(text):
    """The text without `//` line comments and `/* */` block comments (the
    board tops keep commented-out instantiations and assigns around)."""
    text = re.sub(r"/\*.*?\*/", " ", text, flags=re.S)
    return re.sub(r"//[^\n]*", "", text)


_PORT_WORDS = {"input", "output", "inout", "logic", "wire", "reg", "tri", "signed", "unsigned", "int", "integer", "bit"}


def top_ports(text):
    """Names declared as top-level ports in the preprocessed board top,
    including the continuation names of `input KEY2, KEY3, KEY4,`."""
    head = text.split(");", 1)[0]
    names = set(_PORT_DECL.findall(head))
    # the port list proper: after the module's `(` that follows the parameter block
    m = re.search(r"\bmodule\s+[A-Za-z_]\w*", head)
    if m:
        i = m.end()
        while i < len(head) and head[i].isspace():
            i += 1
        if head.startswith("#", i):
            j = head.find("(", i)
            i = _balanced(head, j) if j >= 0 else i
            while i < len(head) and head[i].isspace():
                i += 1
        if head.startswith("(", i):
            body = head[i + 1:]
            cur_dir, depth, chunk, chunks = None, 0, [], []
            for ch in body:
                if ch in "([{":
                    depth += 1
                elif ch in ")]}":
                    depth -= 1
                if ch == "," and depth == 0:
                    chunks.append("".join(chunk))
                    chunk = []
                else:
                    chunk.append(ch)
            chunks.append("".join(chunk))
            for c in chunks:
                c = c.strip()
                dm = re.match(r"(input|output|inout)\b", c)
                if dm:
                    cur_dir = dm.group(1)
                if not cur_dir:
                    continue
                ids = [w for w in re.findall(r"[A-Za-z_]\w*", re.sub(r"\[[^\]]*\]", " ", c)) if w not in _PORT_WORDS]
                if ids:
                    names.add(ids[-1])
    return names


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
    # open drain: `assign LED [0] = ( lab_led [0] ? 1'b0 : 1'bz );` drives 0 when on
    for m in re.finditer(r"\bassign\s+([A-Za-z_]\w*)\s*(?:\[[^\]]*\])?\s*=\s*\(?\s*\w+\s*(?:\[[^\]]*\])?\s*\?\s*1'b([01])\s*:\s*1'bz", text):
        if m.group(2) == "0":
            mark(m.group(1), inverted=True)
    for m in re.finditer(r"SWAP_BITS\s*\(\s*([A-Za-z_]\w*)\s*,\s*(~?)\s*([A-Za-z_]\w*)", text):
        if m.group(1) in ports:
            mark(m.group(1), inverted=bool(m.group(2)), mirrored=True)
        else:
            # `SWAP_BITS (lab_key, ~ key_in)` (ax7035b): the port is the source
            mark(m.group(3), inverted=bool(m.group(2)), mirrored=True)
    # Input side: ~ PORT, ~ PORT [..], ~ { P1, P2, ... }, ! PORT
    for m in re.finditer(r"[~!]\s*([A-Za-z_]\w*)\b", text):
        mark(m.group(1), inverted=True)
    # through an alias: `wire arst_n = CPU_RESET_n; ... if (!arst_n)` (c5gx)
    for m in re.finditer(r"\b(?:wire|logic)\s+([A-Za-z_]\w*)\s*=\s*([A-Za-z_]\w*)\s*;", text):
        if m.group(2) in ports and re.search(r"[~!]\s*" + re.escape(m.group(1)) + r"\b", text):
            mark(m.group(2), inverted=True)
    for m in re.finditer(r"~\s*\{([^}]*)\}", text):
        for name in re.findall(r"[A-Za-z_]\w*", m.group(1)):
            mark(name, inverted=True)
    # under a negated group, directly or through a one-level alias
    # (marsohod3gw2: `key_rst_n = KEY0 & KEY1; rst = ~ ( key_rst_n & pll_lock )`)
    for m in re.finditer(r"~\s*\(([^()]*)\)", text):
        for name in re.findall(r"[A-Za-z_]\w*", m.group(1)):
            mark(name, inverted=True)
            am = re.search(r"\b(?:assign\s+|(?:wire|logic)\s+)" + re.escape(name) + r"\s*=\s*([^;~!]+);", text)
            if am and name not in ports:
                for inner in re.findall(r"[A-Za-z_]\w*", am.group(1)):
                    mark(inner, inverted=True)
    return out


def polarity_hints(text):
    """Which user-level signal classes BGM inverts or mirrors for this variant."""
    hints = set()
    if re.search(r"\.key\s*\(\s*~", text) or re.search(r"~\s*KEY\b", text) \
            or re.search(r"SWAP_BITS\s*\(\s*\w*key\w*\s*,\s*~", text, re.I):
        hints.add("keys~")
    if re.search(r"SWAP_BITS\s*\(\s*\w*key\w*\s*,", text, re.I):
        hints.add("keys-mirrored")
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
    if not opts:
        # BGM's gw_sh tcl for some boards (marsohod3gw2) carries no set_option
        # while the IDE project settings free the dual-purpose pins the CST
        # uses; take those (`"SSPI" : true` -> -use_sspi_as_gpio).
        for cand in sorted(glob.glob(os.path.join(vdir, "project_process_config_*.json"))):
            try:
                with open(cand, encoding="utf-8", errors="replace") as f:
                    body = f.read()
            except OSError:
                continue
            # union over the project files BGM ships (01/02 are two IDE
            # projects of the same variant): freeing a pin more never breaks
            # a build, and the CST needs every pin some project frees
            for key, opt in _PROCESS_CONFIG_GPIO:
                if re.search(r'"{}"\s*:\s*true'.format(key), body):
                    if opt not in opts:
                        opts.append(opt)
    return opts, device


_PROCESS_CONFIG_GPIO = (
    ("MSPI", "-use_mspi_as_gpio"), ("SSPI", "-use_sspi_as_gpio"), ("READY", "-use_ready_as_gpio"),
    ("DONE", "-use_done_as_gpio"), ("RECONFIG_N", "-use_reconfign_as_gpio"), ("JTAG", "-use_jtag_as_gpio"),
    ("I2C", "-use_i2c_as_gpio"), ("MODE_IO", "-use_mode_as_gpio"), ("CPU", "-use_cpu_as_gpio"),
)


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
    # Lattice ECP5 EHXPLLL behind a board-local wrapper module (colorlight
    # clock.v: 25 MHz -> CLKOP 125 / CLKOS 250 / CLKOS2 25 MHz)
    for mod, st in ehxplll_wrappers(vdir, files).items():
        for inst in instantiations(text, mod):
            conns = dict(inst["ports"])
            if fin is None:
                unmodelled.append(mod + "(no clk_mhz)")
                continue
            f_vco = fin / st["clki_div"] * st["clkfb_div"] * st["clkop_div"]
            for port, expr in inst["ports"]:
                div = st["outputs"].get(port)
                if div and expr and _net_used(text, expr, ports):
                    outs.append((expr, f_vco / div, "EHXPLLL"))
    # Gowin Arora V Gowin_PLL wrapper (gowin_pll.v, primitive PLL or PLLA):
    # f_vco = FCLKIN / IDIV_SEL * FBDIV_SEL * MDIV_SEL; clkout<i> = f_vco / ODIV<i>_SEL
    # (BGM tang_mega_138k*: 50 MHz -> VCO 800 -> clkout0 8 MHz, per gowin_pll.ipc)
    gw5_done = False
    for inst in instantiations(text, "Gowin_PLL"):
        st = gw5_pll_settings(vdir, files)
        if st is None:
            unmodelled.append("Gowin_PLL(no gowin_pll.v)")
            continue
        f_vco = st["FCLKIN"] / st["IDIV_SEL"] * st["FBDIV_SEL"] * st["MDIV_SEL"]
        for port, expr in inst["ports"]:
            m = re.match(r"^clkout(\d)$", port)
            if not m or not expr:
                continue
            div = st["outputs"].get(int(m.group(1)))
            if div and _net_used(text, expr, ports):
                outs.append((expr, f_vco / div, port))
        gw5_done = True
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
                wiz_done and name.startswith("clk_wiz")) and not (gw5_done and name == "Gowin_PLL"):
            unmodelled.append(name)
    return outs, unmodelled


def ehxplll_wrappers(vdir, files=None):
    """Lattice ECP5 PLL wrappers among the variant's sources (BGM colorlight
    clock.v): {module: {"clki": input port, "clki_div", "clkfb_div",
    "clkop_div", "outputs": {output port: divider}}}. Feedback path CLKOP:
    f_vco = f_in / CLKI_DIV * CLKFB_DIV * CLKOP_DIV, each output f_vco / DIV."""
    dirs = [vdir] + [os.path.dirname(f) for f in files or [] if os.path.dirname(f) != vdir]
    found = {}
    for d in dirs:
        if not os.path.isdir(d):
            continue
        for name in sorted(os.listdir(d)):
            if not name.endswith((".v", ".sv")) or name == TOP_FILE:
                continue
            text = strip_comments(open(os.path.join(d, name), encoding="utf-8", errors="replace").read())
            for m in re.finditer(r"\bmodule\s+(\w+)(.*?)\bendmodule", text, re.S):
                mod, body = m.group(1), m.group(2)
                insts = instantiations(body, "EHXPLLL")
                if not insts:
                    continue
                params = dict(insts[0]["params"])
                ports = dict(insts[0]["ports"])
                try:
                    clki_div = int(params.get("CLKI_DIV", "1"))
                    clkfb_div = int(params.get("CLKFB_DIV", "1"))
                    clkop_div = int(params.get("CLKOP_DIV", "1"))
                except ValueError:
                    continue
                if params.get("FEEDBK_PATH", '"CLKOP"').strip('"') != "CLKOP":
                    continue
                outputs = {}
                for port, div_key in (("CLKOP", "CLKOP_DIV"), ("CLKOS", "CLKOS_DIV"), ("CLKOS2", "CLKOS2_DIV"),
                                      ("CLKOS3", "CLKOS3_DIV")):
                    net = ports.get(port, "").strip()
                    try:
                        div = int(params.get(div_key, "0"))
                    except ValueError:
                        div = 0
                    if net and div > 0 and params.get(port + "_ENABLE", '"ENABLED"').strip('"') == "ENABLED":
                        outputs[net] = div
                if outputs:
                    found[mod] = {"clki": ports.get("CLKI", "").strip(), "clki_div": clki_div,
                                  "clkfb_div": clkfb_div, "clkop_div": clkop_div, "outputs": outputs}
    return found


def gw5_pll_settings(vdir, files=None):
    """Arora V PLL / PLLA settings from gowin_pll.v: {"FCLKIN", "IDIV_SEL",
    "FBDIV_SEL", "MDIV_SEL", "outputs": {i: ODIVi_SEL for enabled outputs}}."""
    for cand in _pll_wrapper_files(vdir, files, ("gowin_pll.v",)):
        with open(cand, encoding="utf-8", errors="replace") as f:
            vals = {k: v.strip().strip('"') for k, v in _DEFPARAM.findall(f.read())}
        if "IDIV_SEL" in vals and "MDIV_SEL" in vals:
            outs = {}
            for i in range(7):
                if vals.get("CLKOUT{}_EN".format(i), "FALSE").upper() == "TRUE":
                    outs[i] = float(vals.get("ODIV{}_SEL".format(i), "8"))
            return {"FCLKIN": float(vals.get("FCLKIN", "50")), "IDIV_SEL": int(vals["IDIV_SEL"]),
                    "FBDIV_SEL": int(vals.get("FBDIV_SEL", "1")), "MDIV_SEL": int(vals["MDIV_SEL"]),
                    "outputs": outs, "file": cand}
    return None


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


# the LHS: a concatenation, a name, a bit or a slice (`LEDR [w_lab_led - 1:0]`)
_ASSIGN_ANY = re.compile(r"\bassign\s+(\{[^}]*\}|[A-Za-z_]\w*(?:\s*\[[^\]]*\])?)\s*=\s*([^;]+);")
_BUS_NAMES = {"abcdefgh": "seg", "digit": "dig", "lab_digit": "dig", "hgfedcba": "seg"}
_REVERSED_BUSES = {"hgfedcba"}     # BGM: `SWAP_BITS (hgfedcba, abcdefgh)`: hgfedcba[i] = abcdefgh[7 - i]


def seven_seg_map(text):
    """{PORT or PORT[i] (upper-cased): (kind, bit, inverted)} for every
    top-level port bit BGM drives from the shared seven-segment buses:
    kind "seg" with the abcdefgh bit (7 = a, 0 = dp/h), "dig" with the digit
    bit, or "const" with the driven value. Handles `assign SEG_DATA = ~
    abcdefgh;`, `assign {CA, ..., DP} = ~ abcdefgh;`, `assign {DN, CN, BN,
    AN} = ~ digit;`, `assign seg_a = ~ abcdefgh [7];`, `assign an = ~ digit;`,
    `assign DIGIT_N = ~ { lab_digit, 1'b0 };`, `assign seg_sel = { digit[0],
    digit[1] };` and `assign IO[14] = digit[0];`. A bus of parametric width
    is reported as `NAME[*]` with an LSB-first item list that
    expand_seven_seg_map() resolves over the located pins. Per-digit HEXn
    boards yield nothing here."""
    ports = top_ports(text)
    width, lsb = {}, {}
    for m in re.finditer(r"^\s*(?:input|output|inout)\s+(?:logic\s+|wire\s+|reg\s+)?\[\s*([^:\]]+?)\s*:\s*(\d+)\s*\]\s*([A-Za-z_]\w*)",
                         text.split(");", 1)[0], re.M):
        lo = int(m.group(2))
        try:
            hi = int(m.group(1))
        except ValueError:
            hi = None                   # parametric (`[w_digit : 1]`): expanded over the located pins
        if hi is not None and hi < lo:
            continue                    # ascending (`[0:7] SEG`, omdazz): as before, by located pins
        lsb[m.group(3)] = lo
        width[m.group(3)] = hi + 1 if (hi is not None and lo == 0) else None

    def item(part, inv_outer):
        """One rhs element -> ("bit", kind, bit, inv) | ("bus", kind, inv) | ("const", value)."""
        r = " ".join(part.split())
        m = re.match(r"^(~?)\s*(abcdefgh|digit|lab_digit|hgfedcba)\s*$", r)
        if m:
            kind = "busrev" if m.group(2) in _REVERSED_BUSES else "bus"
            return (kind, _BUS_NAMES[m.group(2)], bool(m.group(1)) != inv_outer)
        m = re.match(r"^(~?)\s*(abcdefgh|digit|lab_digit|hgfedcba)\s*\[\s*(\d+)\s*\]$", r)
        if m:
            bit = int(m.group(3))
            if m.group(2) in _REVERSED_BUSES:
                bit = 7 - bit
            return ("bit", _BUS_NAMES[m.group(2)], bit, bool(m.group(1)) != inv_outer)
        m = re.match(r"^(~?)\s*1'b([01])$", r)
        if m:
            v = int(m.group(2))
            return ("const", v ^ (1 if (bool(m.group(1)) != inv_outer) else 0))
        return None

    def items_of(rhs):
        """LSB-first item list for an rhs, or None."""
        r = " ".join(rhs.split())
        m = re.match(r"^(~?)\s*\{(.*)\}$", r)
        if m:
            inv = bool(m.group(1))
            parts = [item(p, inv) for p in m.group(2).split(",")]
            if any(p is None for p in parts):
                return None
            return list(reversed(parts))
        it = item(r, False)
        return [it] if it else None

    def lsb_items_to_map(name, items, w):
        """Expand LSB-first items over bits 0..w-1 (w known) into `out`."""
        idx = 0
        for it in items:
            if it[0] in ("bus", "busrev"):
                n = w - idx
                for k in range(n):
                    bit = (n - 1 - k) if it[0] == "busrev" else k
                    out["{}[{}]".format(name, idx + k)] = (it[1], bit, it[2])
                idx += n
            elif it[0] == "bit":
                out["{}[{}]".format(name, idx)] = (it[1], it[2], it[3])
                idx += 1
            else:
                out["{}[{}]".format(name, idx)] = ("const", it[1], False)
                idx += 1

    out = {}
    # `SWAP_BITS (SMG_Data, ~ abcdefgh)` (swap_bits.svh): lhs[i] = rhs[w-1-i]
    for m in re.finditer(r"`SWAP_BITS\s*\(\s*([A-Za-z_]\w*)\s*,\s*(~?)\s*(abcdefgh|digit|lab_digit)\s*\)", text):
        name, inv, kind = m.group(1), bool(m.group(2)), _BUS_NAMES[m.group(3)]
        if name not in ports:
            continue
        w = width.get(name)
        if w:
            for i in range(w):
                out["{}[{}]".format(name.upper(), i)] = (kind, w - 1 - i, inv)
        else:
            out[name.upper() + "[*]"] = ("items", [("busrev", kind, inv)], lsb.get(name))
    for m in _ASSIGN_ANY.finditer(text):
        lhs, rhs = m.group(1).strip(), m.group(2)
        items = items_of(rhs)
        if items is None:
            continue
        if lhs.startswith("{"):
            names = [x.strip() for x in lhs[1:-1].split(",")]
            names = list(reversed(names))                     # LSB first
            # a single bus item on the rhs spans all the names
            if len(items) == 1 and items[0][0] in ("bus", "busrev"):
                n = len(names)
                items = [("bit", items[0][1], (n - 1 - k) if items[0][0] == "busrev" else k, items[0][2])
                         for k in range(n)]
            if len(items) != len(names):
                continue
            for name, it in zip(names, items):
                nm = re.match(r"^([A-Za-z_]\w*)(?:\s*\[\s*(\d+)\s*\])?$", name)
                if not nm or nm.group(1) not in ports:
                    continue
                key = nm.group(1).upper() + ("[{}]".format(nm.group(2)) if nm.group(2) else "")
                out[key] = (it[1], it[2], it[3]) if it[0] == "bit" else ("const", it[1], False)
            continue
        nm = re.match(r"^([A-Za-z_]\w*)(?:\s*\[\s*(\d+)\s*\])?$", lhs)
        if not nm or nm.group(1) not in ports:
            continue
        name, idx = nm.group(1), nm.group(2)
        if idx is not None:
            if len(items) == 1 and items[0][0] == "bit":
                it = items[0]
                out["{}[{}]".format(name.upper(), idx)] = (it[1], it[2], it[3])
            continue
        w = width.get(name)
        if len(items) == 1 and items[0][0] == "bit" and name not in width:
            it = items[0]
            out[name.upper()] = (it[1], it[2], it[3])          # scalar port
        elif w:
            lsb_items_to_map(name.upper(), items, w)
        else:
            out[name.upper() + "[*]"] = ("items", items, lsb.get(name))  # parametric width: expand over pins
    return out


def expand_seven_seg_map(seg_map, signal_pins):
    """Resolve `NAME[*]` entries of seven_seg_map() over the `NAME[i]` signals
    the constraint files locate; returns {SIGNAL_KEY: (kind, bit, inverted)}."""
    out = {}
    for key, val in seg_map.items():
        if not key.endswith("[*]"):
            out[key] = val
            continue
        base = key[:-3]
        idxs = sorted(int(m.group(1)) for sig in signal_pins
                      for m in [re.match(r"^{}\[(\d+)\]$".format(re.escape(base)), sig)] if m)
        if not idxs:
            continue
        # the port's declared LSB (dk_dev_3c120n: `seven_seg_sel [w_digit : 1]`)
        lo = val[2] if len(val) > 2 and val[2] is not None else 0
        w = max(idxs) + 1
        items = val[1]
        idx = lo
        for it in items:
            if it[0] == "bus":
                for k in range(w - idx):
                    out["{}[{}]".format(base, idx + k)] = (it[1], k, it[2])
                idx = w
            elif it[0] == "busrev":
                for k in range(w - idx):
                    out["{}[{}]".format(base, idx + k)] = (it[1], w - 1 - idx - k, it[2])
                idx = w
            elif it[0] == "bit":
                out["{}[{}]".format(base, idx)] = (it[1], it[2], it[3])
                idx += 1
            else:
                out["{}[{}]".format(base, idx)] = ("const", it[1], False)
                idx += 1
    return out


_PLL_MODULE = re.compile(r"clk_wiz|pll|mmcm|dcm", re.I)


def lab_clock_pll(text):
    """(module, port) when lab_top's clk is an output of a PLL instance (a7_lite:
    `clk_wiz i_clk_wiz (.clk_out2 ( clk ), .clk_in1 ( CLK_50M ))`), else None."""
    t = strip_comments(text)
    labs = instantiations(t, "lab_top")
    clk = next((e for p, e in labs[0]["ports"] if p == "clk"), "").strip() if labs else ""
    if not re.match(r"^[A-Za-z_]\w*$", clk):
        return None
    for m in re.finditer(r"\b([A-Za-z_]\w*)\s*(?:#\s*\((?:[^()]|\([^()]*\))*\)\s*)?[A-Za-z_]\w*\s*\(([^;]*?)\)\s*;", t):
        if not _PLL_MODULE.search(m.group(1)):
            continue
        pm = re.search(r"\.(\w*out\w*)\s*\(\s*" + re.escape(clk) + r"\s*\)", m.group(2), re.I)
        if pm:
            return m.group(1), pm.group(1)
    return None


def lab_clock_source(text):
    """'pixel' when the lab runs on the PLL pixel clock (`localparam lab_mhz =
    pixel_mhz; assign clk = pixel_clk` — iCEBreaker DVI, Tang Primer 20K Dock
    LCD/HDMI), 'pll' when its clk is another PLL output at clk_mhz (a7_lite's
    clk_wiz), else 'board'."""
    m = re.search(r"localparam\s+lab_mhz\s*=\s*([A-Za-z_]\w*)", text)
    if m and m.group(1) == "pixel_mhz":
        return "pixel"
    return "pll" if lab_clock_pll(text) else "board"


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


_ASSIGN_BIT = re.compile(r"\bassign\s+([A-Za-z_]\w*)\s*\[\s*(\d+)\s*\]\s*=\s*([^;]+);")


def port_bit_assigns(text):
    """port_assigns() plus the single bits: `assign GPIO [1] = 1'b0;` ->
    {"GPIO[1]": "1'b0"} (BGM grounds and powers the microphone module
    through header pins)."""
    ports = top_ports(text)
    out = dict(port_assigns(text))
    for p, i, e in _ASSIGN_BIT.findall(text):
        if p in ports:
            out["{}[{}]".format(p, int(i))] = " ".join(e.split())
    return out


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
        "reset_sync": reset_sync_stages(text),
        "reset_sync_asserts": reset_sync_asserts(text),
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
