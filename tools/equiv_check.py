#!/usr/bin/env python3
"""
tools/equiv_check.py -- is unifpga's generated top the same circuit as BGM's
board_specific_top.sv? Proved by co-simulation on the physical pins.

For every configuration with a BGM oracle, two simulations are built with
Icarus Verilog (each side in its own compilation, so the identically named
modules of the two trees never meet):

    gold:  BGM's boards/<variant>/board_specific_top.sv + BGM's lab_top
    gate:  the top codegen emits for the configuration + our design_top

Both tops are wrapped in a module whose ports are the *physical pins* of the
board: BGM's pins come from the variant's constraint files, ours from the
pinmap banks the constraint emitters walk. One testbench (identical text on
both sides) drives every input pin with the same deterministic schedule
(all 0, all 1, each pin toggling alone, then per-pin random holds of 1..32768
cycles), toggles the oscillator pins at the pinmap frequency, pulls every
bidirectional pin with a weak random value so a driven pin shows on top of
it, and prints the compared pins whenever they change. The runner merges
the two streams cycle by cycle and reports every pin that ever differed,
with the port name on each side, the first cycle and how many cycles.

The vendor primitives both trees instantiate (PLLs, differential buffers,
clock dividers, Gowin's DVI_TX IP) are the shared behavioural stand-ins in
rtl/sim/vendor_stubs.sv and rtl/sim/equiv_stubs.sv: the PLL models make
their output clock from the parameters the vendor tool reads, so a
parameter difference is a frequency difference on the pins.

The lab on both sides is tools/equiv_lab (every output bit a distinct XOR of
input bits and a slow counter), or `--lab <bgm lab name>` for a BGM lab and
its adapted design (both must exist).

Steps (`generate` runs where BGM and this repository are, `run` where Icarus
is; `remote` does both through ssh + rsync):

    python3 tools/equiv_check.py remote --host mercury --out <scratch>/equiv [--only ids]
    python3 tools/equiv_check.py summary --out <scratch>/equiv
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REMOTE_ROOT = "/tmp/unifpga-equiv"

STUB_FILES = [os.path.join("rtl", "sim", "vendor_stubs.sv"),
              os.path.join("rtl", "sim", "equiv_stubs.sv")]
TIMESCALE_FILE = os.path.join("rtl", "sim", "bgm_timescale.sv")
EQUIV_LAB_DIR = os.path.join("tools", "equiv_lab")
EQUIV_LAB_BGM = os.path.join(EQUIV_LAB_DIR, "bgm")
EQUIV_LAB_OURS = os.path.join(EQUIV_LAB_DIR, "unifpga", "design_top.sv")

GOLD_TOP = "board_specific_top"
GATE_TOP = "top"

DEFAULT_CYCLES = 300000
VVP_TIMEOUT_S = 1800

_SV_NON_PORT_WORDS = {"input", "output", "inout", "logic", "wire", "reg", "tri", "signed",
                      "unsigned", "int", "integer", "bit", "var"}


# ---------------------------------------------------------------------------
# Port lists and pin maps
# ---------------------------------------------------------------------------

def _balanced(text, start):
    """Index just past the parenthesis group opening at text[start] == '('."""
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "(":
            depth += 1
        elif text[i] == ")":
            depth -= 1
            if depth == 0:
                return i + 1
    return len(text)


def _split_top_level(body):
    parts, depth, cur = [], 0, []
    for ch in body:
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    parts.append("".join(cur))
    return parts


def parse_ports(text, module):
    """{port: {"dir": input|output|inout, "width": int or None}} from the
    header of `module` in comment-free SystemVerilog text. Widths are known
    only for numeric ranges (codegen's tops); BGM's tops use parameters and
    get None (the pins decide, and the wrapper checks $bits at run time)."""
    m = re.search(r"\bmodule\s+" + re.escape(module) + r"\b", text)
    if not m:
        return OrderedDict()
    i = m.end()
    while i < len(text) and text[i].isspace():
        i += 1
    if text.startswith("#", i):
        j = text.index("(", i)
        i = _balanced(text, j)
        while i < len(text) and text[i].isspace():
            i += 1
    if not text.startswith("(", i):
        return OrderedDict()
    body = text[i + 1:_balanced(text, i) - 1]
    ports = OrderedDict()
    cur_dir = None
    for chunk in _split_top_level(body):
        chunk = chunk.strip()
        if not chunk:
            continue
        dm = re.match(r"(input|output|inout)\b", chunk)
        if dm:
            cur_dir = dm.group(1)
        width = None
        rm = re.search(r"\[\s*(\d+)\s*:\s*(\d+)\s*\]", chunk)
        if rm:
            width = abs(int(rm.group(1)) - int(rm.group(2))) + 1
        stripped = re.sub(r"\[[^\]]*\]", " ", chunk)
        idents = [w for w in re.findall(r"[A-Za-z_]\w*", stripped) if w not in _SV_NON_PORT_WORDS]
        if idents and cur_dir:
            ports[idents[-1]] = {"dir": cur_dir, "width": width}
    return ports


_PIN_KEY = re.compile(r"^([A-Za-z_]\w*)(?:\[(\d+)\])?$")


def _pair_halves(pin, norm):
    halves = [norm(h) for h in str(pin).split(",")]
    return halves[0], (halves[1] if len(halves) > 1 else None)


def assign_pins(port_pins, ports, norm):
    """port_pins: iterable of (port_key, pin) where port_key is `NAME` or
    `NAME[idx]` (any case) and pin may be a `P,N` pair. Returns
    {norm_pin: (port, idx)} for the ports declared in `ports`, plus the list
    of keys that name no declared port. The N half of a pair goes to the
    `_n` sibling of a `_p` port when that sibling has no pin of its own."""
    by_upper = {p.upper(): p for p in ports}
    out, unknown, n_pending = OrderedDict(), [], []
    for key, pin in port_pins:
        km = _PIN_KEY.match(str(key).replace(" ", ""))
        if not km:
            unknown.append(str(key))
            continue
        port = by_upper.get(km.group(1).upper())
        if port is None:
            unknown.append(str(key))
            continue
        idx = int(km.group(2)) if km.group(2) is not None else None
        p_half, n_half = _pair_halves(pin, norm)
        out.setdefault(p_half, (port, idx))
        if n_half:
            n_pending.append((port, idx, n_half))
    taken = {(p, i) for p, i in out.values()}
    for port, idx, n_half in n_pending:
        m = re.match(r"^(.*)_[pP]$", port)
        sibling = None
        if m:
            for cand in (m.group(1) + "_n", m.group(1) + "_N"):
                if cand in ports:
                    sibling = cand
                    break
        if sibling and (sibling, idx) not in taken:
            out.setdefault(n_half, (sibling, idx))
            taken.add((sibling, idx))
    return out, unknown


def port_bits(pin_map):
    """{port: {idx or None: pin}} inverted from {pin: (port, idx)}."""
    out = OrderedDict()
    for pin, (port, idx) in pin_map.items():
        out.setdefault(port, OrderedDict())[idx] = pin
    return out


# ---------------------------------------------------------------------------
# Wrapper and testbench text
# ---------------------------------------------------------------------------

def _pname(pin):
    return "p_" + re.sub(r"[^A-Za-z0-9]", "_", pin)


def wrapper_text(wrap_name, inner, ports, bits, pins, klass):
    """Module `wrap_name` with one port per physical pin (union of both
    sides, direction from its class) instantiating `inner` with each port
    connected bit by bit to its pins."""
    lines = ["`timescale 1 ns / 1 ps", "", "module {} (".format(wrap_name)]
    decls = []
    for pin in pins:
        d = {"CLOCK": "input", "INPUT": "input", "OUTPUT": "output", "INOUT": "inout"}[klass[pin]]
        decls.append("    {:<6} {}".format(d, _pname(pin)))
    lines.append(",\n".join(decls))
    lines.append(");")
    lines.append("")
    conns, checks, ncs = [], [], []
    for port, info in ports.items():
        pb = bits.get(port)
        if not pb:
            checks.append('        $display ("EQUIV-UNPINNED {}");'.format(port))
            continue
        if list(pb.keys()) == [None]:
            conns.append("        .{} ({})".format(port, _pname(pb[None])))
            expect = 1
        else:
            idxs = [i for i in pb if i is not None]
            width = info.get("width") or (max(idxs) + 1)
            parts = []
            for i in range(width - 1, -1, -1):
                if i in pb:
                    parts.append(_pname(pb[i]))
                else:
                    nc = "nc_{}_{}".format(port, i)
                    ncs.append(nc)
                    parts.append(nc)
            conns.append("        .{} ({{ {} }})".format(port, ", ".join(parts)))
            expect = width
        checks.append('        if ($bits (u.{p}) != {e}) $display ("EQUIV-WIDTH {p} expected {e} got %0d", $bits (u.{p}));'
                      .format(p=port, e=expect))
    for nc in ncs:
        lines.append("    wire {};".format(nc))
    if ncs:
        lines.append("")
    lines.append("    {} u (".format(inner))
    lines.append(",\n".join(conns))
    lines.append("    );")
    lines.append("")
    lines.append("    initial begin")
    lines.extend(checks)
    lines.append("    end")
    lines.append("")
    lines.append("endmodule")
    return "\n".join(lines) + "\n"


def testbench_text(wrap_name, pins, klass, clocks, main_pin, cmp_pins, cycles):
    """The stimulus / trace testbench; identical text on both sides except
    the wrapper module name."""
    in_pins = [p for p in pins if klass[p] in ("INPUT", "INOUT")]
    n_in = len(in_pins)
    n_cmp = len(cmp_pins)
    main_half = 500.0 / clocks[main_pin]
    L = ["`timescale 1 ns / 1 ps", "", "module equiv_tb;", ""]
    L.append("    localparam integer N_CYCLES = {};".format(cycles))
    L.append("    localparam integer N_IN = {};".format(max(n_in, 1)))
    L.append("    localparam integer N_CMP = {};".format(max(n_cmp, 1)))
    L.append("    localparam integer WALK = 64;")
    L.append("")
    for pin in pins:
        k = klass[pin]
        if k == "CLOCK":
            L.append("    reg  {} = 1'b0;".format(_pname(pin)))
        elif k == "INPUT":
            L.append("    wire {};".format(_pname(pin)))
        else:
            L.append("    wire {};".format(_pname(pin)))
    L.append("")
    for pin, mhz in clocks.items():
        L.append("    always #({:.6f}) {} = ~ {};".format(500.0 / mhz, _pname(pin), _pname(pin)))
    L.append("")
    L.append("    reg [N_IN - 1:0] in_v = '0;")
    for k, pin in enumerate(in_pins):
        if klass[pin] == "INPUT":
            L.append("    assign {} = in_v [{}];".format(_pname(pin), k))
        else:
            L.append("    assign (weak1, weak0) {} = in_v [{}];".format(_pname(pin), k))
    L.append("")
    L.append("    {} dut (".format(wrap_name))
    L.append(",\n".join("        .{0} ({0})".format(_pname(p)) for p in pins))
    L.append("    );")
    L.append("")
    if cmp_pins:
        L.append("    wire [N_CMP - 1:0] cmp = { " + ", ".join(_pname(p) for p in cmp_pins) + " };")
    else:
        L.append("    wire [N_CMP - 1:0] cmp = 1'b0;")
    L.append("""
    integer in_h [0:N_IN - 1];
    reg [31:0] lfsr = 32'h2545_f491;
    reg [N_CMP - 1:0] prev;
    reg first = 1'b1;
    integer cycle = 0;
    integer k;

    function [31:0] xorshift (input [31:0] v);
        reg [31:0] t;
        begin
            t = v ^ (v << 13);
            t = t ^ (t >> 17);
            t = t ^ (t << 5);
            xorshift = t;
        end
    endfunction

    initial for (k = 0; k < N_IN; k = k + 1) in_h [k] = 0;

    // Sample the compared pins a half period after the DUT's clock edge,
    // then change the inputs a quarter period later so the DUT sees them at
    // its next edge; the same instants on both sides.
    always @ (negedge %s) begin
        if (first || cmp !== prev) begin
            $fwrite (32'h8000_0001, "%%0d %%b\\n", cycle, cmp);
            prev  = cmp;
            first = 1'b0;
        end
        cycle = cycle + 1;
        if (cycle >= N_CYCLES) begin
            $fwrite (32'h8000_0001, "END %%0d\\n", cycle);
            $finish;
        end
        #(%.6f);
        if (cycle %% 4 == 0) begin
            for (k = 0; k < N_IN; k = k + 1) begin
                if (cycle < 1000)
                    in_v [k] = 1'b0;
                else if (cycle < 2000)
                    in_v [k] = 1'b1;
                else if (cycle < 2000 + N_IN * WALK)
                    in_v [k] = ((cycle - 2000) / WALK == k) ? cycle [3] : 1'b0;
                else if (in_h [k] <= 0) begin
                    lfsr = xorshift (lfsr);
                    in_v [k] = lfsr [0];
                    in_h [k] = 1 << lfsr [6:3];
                end else
                    in_h [k] = in_h [k] - 4;
            end
        end
    end

endmodule
""" % (_pname(main_pin), main_half / 2.0))
    return "\n".join(L)


# ---------------------------------------------------------------------------
# generate
# ---------------------------------------------------------------------------

def _is_protected(path):
    """Vendor-encrypted IP (Gowin DVI_TX): `pragma protect` in the head."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return "pragma protect" in f.read(4096)
    except OSError:
        return False


def _bgm_sources(dirs):
    """BGM's file set (find -name '*.sv' -or -name '*.v', not tb.sv), minus
    black-box declarations (*_bb.v) and encrypted IP, both of which would
    clash with the real definition or the stub."""
    files = []
    for d in dirs:
        for root, _subdirs, names in os.walk(d):
            for name in sorted(names):
                if not name.endswith((".sv", ".v")) or name == "tb.sv" or name.endswith("_bb.v"):
                    continue
                path = os.path.join(root, name)
                if _is_protected(path):
                    continue
                files.append(path)
    return files


_ALWAYS_NO_EVENT = re.compile(r"^(\s*)always\s*(begin\b|$)", re.M)


def _stage_bgm_patches(files, out_dir):
    """Icarus refuses Terasic's `always begin case (...)` look-up tables (an
    always without event control); the vendor tools read them as
    combinational. Such files are copied next to the run with `always @*`,
    the same edit rtl/peripherals carries for our copies. Returns the file
    list with the copies substituted and the names of the patched files."""
    staged, patched = [], []
    for path in files:
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                text = f.read()
        except OSError:
            staged.append(path)
            continue
        if not _ALWAYS_NO_EVENT.search(text):
            staged.append(path)
            continue
        new = _ALWAYS_NO_EVENT.sub(lambda m: m.group(1) + "always @*" + (" " + m.group(2) if m.group(2) else ""), text)
        pdir = os.path.join(out_dir, "bgm_patched")
        os.makedirs(pdir, exist_ok=True)
        dst = os.path.join(pdir, os.path.basename(path))
        with open(dst, "w") as f:
            f.write(new)
        staged.append(dst)
        patched.append(os.path.basename(path))
    return staged, patched


_MODULE_DECL = re.compile(r"^\s*module\s+([A-Za-z_]\w*)", re.M)
_MODULE_BLOCK = re.compile(r"^module\s+([A-Za-z_]\w*).*?^endmodule[ \t]*$", re.M | re.S)


def _defined_modules(files):
    out = set()
    for path in files:
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                out.update(_MODULE_DECL.findall(f.read()))
        except OSError:
            pass
    return out


def _stub_text_without(defined):
    """The shared stub library minus the modules a side already defines
    (BGM's colorlight ships its own OBUFDS model, a7_lite its clk_wiz)."""
    parts, dropped = [], []
    for stub in STUB_FILES:
        with open(os.path.join(REPO, stub)) as f:
            text = f.read()
        def keep(m):
            if m.group(1) in defined:
                dropped.append(m.group(1))
                return ""
            return m.group(0)
        parts.append(_MODULE_BLOCK.sub(keep, text))
    return "\n".join(parts), dropped


def _find_bgm_lab(bgm_root, lab):
    labs = os.path.join(bgm_root, "labs")
    for section in sorted(os.listdir(labs)):
        cand = os.path.join(labs, section, lab)
        if os.path.isfile(os.path.join(cand, "lab_top.sv")):
            return cand
    return None


def _rel(path, roots):
    """{"root": name, "path": relative} for the first root containing path."""
    path = os.path.abspath(path)
    for name, root in roots:
        root = os.path.abspath(root)
        if path == root or path.startswith(root + os.sep):
            return {"root": name, "path": os.path.relpath(path, root)}
    return {"root": "abs", "path": path}


def cmd_generate(args):
    sys.path.insert(0, REPO)
    import logging
    logging.disable(logging.CRITICAL)
    from config import init as config_init
    from tools import codegen, bgm_oracle, import_constraints as ic, audit_configs, sync_from_bgm, lint_generated

    bgm_root = bgm_oracle.BGM_DIR
    if not os.path.isdir(os.path.join(bgm_root, "boards")):
        print("BGM not found at {} (set UNIFPGA_BGM_DIR)".format(bgm_root), file=sys.stderr)
        return 2
    out = os.path.abspath(args.out)
    os.makedirs(out, exist_ok=True)
    roots = [("repo", REPO), ("bgm", bgm_root), ("out", out)]

    if args.lab == "equiv":
        bgm_lab_dir = os.path.join(REPO, EQUIV_LAB_BGM)
        our_design = os.path.join(REPO, EQUIV_LAB_OURS)
        extra_inc = [os.path.join(REPO, EQUIV_LAB_DIR)]
    else:
        bgm_lab_dir = _find_bgm_lab(bgm_root, args.lab)
        our_design = os.path.join(REPO, "designs", args.lab, "design_top.sv")
        extra_inc = []
        if bgm_lab_dir is None or not os.path.exists(our_design):
            print("lab {} not found on both sides".format(args.lab), file=sys.stderr)
            return 2

    ids = sorted(config_init.read_configurations())
    if args.only:
        ids = [c for c in ids if c in args.only]
    manifest = {"lab": args.lab, "cycles": args.cycles, "entries": []}
    counts = {"ready": 0, "NO-ORACLE": 0, "NO-CLOCK": 0}
    for cfg_id in ids:
        resolved = config_init.resolve_configuration(cfg_id)
        board_id = resolved["board"]["Id"]
        entry = {"id": cfg_id, "toolchain": resolved["toolchain"]["Id"], "board": board_id}
        vdir = bgm_oracle.variant_dir_for(cfg_id, board_id)
        if vdir is None:
            entry["status"] = "NO-ORACLE"
            counts["NO-ORACLE"] += 1
            manifest["entries"].append(entry)
            continue
        entry["variant"] = os.path.basename(vdir)
        d = os.path.join(out, cfg_id)
        os.makedirs(d, exist_ok=True)

        # ---- gate: the generated top and its sources
        try:
            gate_text = codegen.emit_top_sv(resolved, strict=True)
            entry["strict_ok"] = True
        except codegen.CodegenError:
            gate_text = codegen.emit_top_sv(resolved, strict=False)
            entry["strict_ok"] = False
        gate_top_path = os.path.join(d, "gate_top.sv")
        with open(gate_top_path, "w") as f:
            f.write(gate_text)
        gate_files = [os.path.join(REPO, TIMESCALE_FILE)]
        for p in lint_generated._source_list(resolved, our_design, gate_top_path):
            if p.endswith(os.path.join("rtl", "sim", "vendor_stubs.sv")):
                continue            # the per-side stub file below replaces it
            gate_files.append(p if os.path.isabs(p) else os.path.join(REPO, p))
        seen, uniq = set(), []
        for p in gate_files:
            ap = os.path.abspath(p)
            if ap not in seen:
                seen.add(ap)
                uniq.append(ap)
        gate_files = uniq
        gate_stub_text, gate_dropped = _stub_text_without(_defined_modules(gate_files))
        with open(os.path.join(d, "gate_stubs.sv"), "w") as f:
            f.write(gate_stub_text)
        gate_files.append(os.path.join(d, "gate_stubs.sv"))
        gate_inc = [os.path.join(REPO, "rtl", "peripherals"), os.path.dirname(our_design)] + extra_inc

        # ---- gold: BGM's variant, lab, peripherals and common
        pp = bgm_oracle.preprocess_variant(vdir)
        gold_text = bgm_oracle.strip_comments(pp.text)
        if not bgm_oracle.instantiations(gold_text, "lab_top"):
            entry["status"] = "NO-LAB-TOP"          # hackathon_top, or nothing
            counts["NO-ORACLE"] += 1
            manifest["entries"].append(entry)
            continue
        chain_dirs = []
        for fpath in pp.files:
            d0 = os.path.dirname(os.path.abspath(fpath))
            if d0 != os.path.abspath(vdir) and d0.startswith(os.path.abspath(bgm_root)) and d0 not in chain_dirs:
                chain_dirs.append(d0)
        gold_files = _bgm_sources([bgm_lab_dir, vdir] + chain_dirs +
                                  [os.path.join(bgm_root, "peripherals"), os.path.join(bgm_root, "labs", "common")])
        gold_files, patched = _stage_bgm_patches(gold_files, d)
        gold_stub_text, gold_dropped = _stub_text_without(_defined_modules(gold_files))
        with open(os.path.join(d, "gold_stubs.sv"), "w") as f:
            f.write(gold_stub_text)
        gold_files.append(os.path.join(d, "gold_stubs.sv"))
        gold_inc = [bgm_lab_dir, os.path.join(bgm_root, "peripherals"), os.path.join(bgm_root, "labs", "common"),
                    vdir] + extra_inc
        defines = ["SIMULATION"] + sorted(bgm_oracle.FULL_LAB_DEFINES)

        # ---- ports and pins
        gold_ports = parse_ports(gold_text, GOLD_TOP)
        gate_ports = parse_ports(bgm_oracle.strip_comments(gate_text), GATE_TOP)
        norm = sync_from_bgm._norm_pin
        gold_port_pins = []
        for path in ic.find_all_constraint_files(vdir):
            try:
                signals, _fmt = ic.parse_file(path)
            except Exception:
                continue
            for name, info in signals.items():
                gold_port_pins.append((name, info["pin"]))
        gold_map, gold_unknown = assign_pins(gold_port_pins, gold_ports, norm)
        pins_by_port, _nulls = audit_configs._pins_by_port(resolved)
        gate_port_pins = []
        for pin, names in pins_by_port.items():
            for name in names:
                gate_port_pins.append((name, pin))
        gate_map, gate_unknown = assign_pins(gate_port_pins, gate_ports, norm)

        # ---- clocks: every oscillator the pinmap knows, at its frequency
        pinmap = resolved["board_pinmap"]
        clocks = OrderedDict()
        for _bank, b in (pinmap.get("pinBanks") or {}).items():
            if isinstance(b, dict) and b.get("frequency_mhz") and isinstance(b.get("pins"), str):
                clocks[norm(b["pins"])] = float(b["frequency_mhz"])
        clk = codegen.resolve_clock(resolved)
        main_pin = None
        if clk:
            for pin, (port, _idx) in gate_map.items():
                if port == clk["port"]:
                    main_pin = pin
                    if clk.get("mhz") and pin not in clocks:
                        clocks[pin] = float(clk["mhz"])
                    break
        if main_pin is None or main_pin not in clocks:
            entry["status"] = "NO-CLOCK"
            counts["NO-CLOCK"] += 1
            manifest["entries"].append(entry)
            continue

        # ---- union of pins and their class
        all_pins = sorted(set(gold_map) | set(gate_map) | set(clocks))
        klass, table = OrderedDict(), []
        for pin in all_pins:
            g = gold_map.get(pin)
            t = gate_map.get(pin)
            dirs = []
            if g:
                dirs.append(gold_ports[g[0]]["dir"])
            if t:
                dirs.append(gate_ports[t[0]]["dir"])
            if pin in clocks:
                k = "CLOCK"
            elif all(x == "input" for x in dirs):
                k = "INPUT"
            elif any(x == "output" for x in dirs):
                k = "OUTPUT"
            else:
                k = "INOUT"
            klass[pin] = k
            table.append({"pin": pin, "class": k,
                          "gold": ("{}[{}]".format(g[0], g[1]) if g[1] is not None else g[0]) if g else None,
                          "gate": ("{}[{}]".format(t[0], t[1]) if t[1] is not None else t[0]) if t else None,
                          "gold_dir": gold_ports[g[0]]["dir"] if g else None,
                          "gate_dir": gate_ports[t[0]]["dir"] if t else None})
        cmp_pins = [p for p in all_pins if klass[p] in ("OUTPUT", "INOUT") and p in gold_map and p in gate_map]

        for side, wrap, inner, ports, pmap in (("gold", "gold_w", GOLD_TOP, gold_ports, gold_map),
                                               ("gate", "gate_w", GATE_TOP, gate_ports, gate_map)):
            with open(os.path.join(d, side + "_w.sv"), "w") as f:
                f.write(wrapper_text(wrap, inner, ports, port_bits(pmap), all_pins, klass))
            with open(os.path.join(d, side + "_tb.sv"), "w") as f:
                f.write(testbench_text(wrap, all_pins, klass, clocks, main_pin, cmp_pins, args.cycles))

        entry.update({
            "status": "ready",
            "pins": table,
            "cmp_pins": cmp_pins,
            "clocks": clocks,
            "main_pin": main_pin,
            "gold_only": [p for p in all_pins if p in gold_map and p not in gate_map],
            "gate_only": [p for p in all_pins if p in gate_map and p not in gold_map],
            "stubs_dropped": {"gold": sorted(gold_dropped), "gate": sorted(gate_dropped)},
            "bgm_patched": patched,
            "gold_unknown_keys": sorted(set(gold_unknown)),
            "gate_unknown_keys": sorted(set(gate_unknown)),
            "gold": {"files": [_rel(p, roots) for p in gold_files], "incdirs": [_rel(p, roots) for p in gold_inc],
                     "defines": defines, "wrapper": "gold_w.sv", "tb": "gold_tb.sv"},
            "gate": {"files": [_rel(p, roots) for p in gate_files], "incdirs": [_rel(p, roots) for p in gate_inc],
                     "defines": ["SIMULATION"], "wrapper": "gate_w.sv", "tb": "gate_tb.sv"},
        })
        counts["ready"] += 1
        manifest["entries"].append(entry)

    with open(os.path.join(out, "manifest.json"), "w") as f:
        json.dump(manifest, f, indent=1)
    print("generated {} equivalence runs ({} without oracle, {} without a clock pin) -> {}".format(
        counts["ready"], counts["NO-ORACLE"], counts["NO-CLOCK"], out))
    return 0


# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------

def _resolve(ref, roots):
    if ref["root"] == "abs":
        return ref["path"]
    return os.path.join(roots[ref["root"]], ref["path"])


class _Trace(object):
    """Reads `<cycle> <bits>` / `END <cycle>` lines from a pipe; keeps the
    other lines (width checks, $display output) aside."""

    def __init__(self, stream):
        self.stream = stream
        self.other = []
        self.end = None

    def next(self):
        for line in self.stream:
            line = line.rstrip("\n")
            if not line:
                continue
            if line[0].isdigit():
                c, _sp, bits = line.partition(" ")
                return int(c), bits
            if line.startswith("END "):
                self.end = int(line[4:].strip())
                continue
            if len(self.other) < 200:
                self.other.append(line)
        return None


def _merge(gold, gate, n_cmp):
    """Walk both change streams; per compared bit: cycles differing, first
    differing cycle, and whether it ever moved on each side."""
    INF = float("inf")
    mism = [0] * n_cmp
    first = [None] * n_cmp
    moved = [[set(), set()] for _ in range(n_cmp)]
    gv = tv = None
    eg, et = gold.next(), gate.next()
    cur = None
    while eg is not None or et is not None:
        c = min(eg[0] if eg else INF, et[0] if et else INF)
        if eg and eg[0] == c:
            gv = eg[1]
            eg = gold.next()
        if et and et[0] == c:
            tv = et[1]
            et = gate.next()
        nxt = min(eg[0] if eg else INF, et[0] if et else INF)
        cur = c
        if gv is not None and tv is not None and len(gv) == n_cmp and len(tv) == n_cmp:
            for i in range(n_cmp):
                moved[i][0].add(gv[i])
                moved[i][1].add(tv[i])
            if gv != tv:
                span = (nxt if nxt != INF else c + 1) - c
                for i in range(n_cmp):
                    if gv[i] != tv[i]:
                        mism[i] += span
                        if first[i] is None:
                            first[i] = c
        elif gv is not None and len(gv) != n_cmp:
            return None, "gold trace width {} != {}".format(len(gv), n_cmp)
        elif tv is not None and len(tv) != n_cmp:
            return None, "gate trace width {} != {}".format(len(tv), n_cmp)
    # the last segment runs to the end of the simulation
    end = min(x for x in (gold.end, gate.end) if x is not None) if (gold.end or gate.end) else None
    if end is not None and cur is not None and gv is not None and tv is not None and gv != tv:
        for i in range(n_cmp):
            if gv[i] != tv[i]:
                mism[i] += max(end - cur - 1, 0)
    return (mism, first, moved), None


def _compile(side, entry, roots, d, log):
    s = entry[side]
    cmd = ["iverilog", "-g2012", "-Wno-timescale", "-o", os.path.join(d, side + ".vvp")]
    for df in s["defines"]:
        cmd.append("-D" + df)
    for inc in s["incdirs"]:
        cmd += ["-I", _resolve(inc, roots)]
    cmd += [_resolve(f, roots) for f in s["files"]]
    cmd += [os.path.join(d, s["wrapper"]), os.path.join(d, s["tb"])]
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)
    log.write("$ " + " ".join(cmd) + "\n" + proc.stdout + "\n")
    lines = [l for l in proc.stdout.splitlines() if l.strip()]
    errors = [l for l in lines if ": error" in l or "error:" in l.lower() or "syntax error" in l]
    widths = [l for l in lines if "expects" in l and "bits" in l]
    return proc.returncode, errors, widths


def _run_one(entry, roots, out, keep_logs=True):
    cfg_id = entry["id"]
    res = {"id": cfg_id, "toolchain": entry.get("toolchain"), "variant": entry.get("variant")}
    if entry.get("status") != "ready":
        res["status"] = entry.get("status", "SKIPPED")
        return res
    res["bgm_patched"] = entry.get("bgm_patched") or []
    d = os.path.join(out, cfg_id)
    t0 = time.time()
    with open(os.path.join(d, "run.log"), "w") as log:
        notes = []
        for side in ("gold", "gate"):
            rc, errors, widths = _compile(side, entry, roots, d, log)
            if rc != 0:
                res.update({"status": "COMPILE-FAIL-" + side, "errors": errors[:8], "seconds": round(time.time() - t0, 1)})
                return res
            notes += ["{}: {}".format(side, w.strip()) for w in widths[:6]]
        timeout = shutil.which("timeout")
        procs = {}
        for side in ("gold", "gate"):
            cmd = ([timeout, str(VVP_TIMEOUT_S)] if timeout else []) + ["vvp", "-n", os.path.join(d, side + ".vvp")]
            procs[side] = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                           universal_newlines=True, bufsize=1 << 16)
        gold, gate = _Trace(procs["gold"].stdout), _Trace(procs["gate"].stdout)
        n_cmp = len(entry["cmp_pins"])
        merged, err = _merge(gold, gate, n_cmp)
        rcs = {s: p.wait() for s, p in procs.items()}
        for s, tr in (("gold", gold), ("gate", gate)):
            log.write("---- {} vvp rc={} end={}\n".format(s, rcs[s], tr.end) + "\n".join(tr.other) + "\n")
        res["seconds"] = round(time.time() - t0, 1)
        res["notes"] = notes
        for s, tr in (("gold", gold), ("gate", gate)):
            res[s + "_messages"] = [l for l in tr.other if l.startswith("EQUIV-")][:20]
        if err or gold.end is None or gate.end is None or any(rc != 0 for rc in rcs.values()):
            res["status"] = "RUN-FAIL"
            res["errors"] = [err] if err else []
            res["errors"] += ["{} rc={} end={}".format(s, rcs[s], tr.end) for s, tr in (("gold", gold), ("gate", gate))]
            res["errors"] += (gold.other[:4] + gate.other[:4])
            return res
        mism, first, moved = merged
        res["cycles"] = min(gold.end, gate.end)
        res["n_cmp"] = n_cmp
        diffs = []
        for i, pin in enumerate(entry["cmp_pins"]):
            if mism[i]:
                row = next(r for r in entry["pins"] if r["pin"] == pin)
                diffs.append({"pin": pin, "gold": row["gold"], "gate": row["gate"], "class": row["class"],
                              "cycles": mism[i], "first": first[i],
                              "gold_values": "".join(sorted(moved[i][0])), "gate_values": "".join(sorted(moved[i][1]))})
        diffs.sort(key=lambda r: (-r["cycles"], r["pin"]))
        res["mismatch_pins"] = diffs
        res["constant_pins"] = [entry["cmp_pins"][i] for i in range(n_cmp)
                                if len(moved[i][0]) <= 1 and len(moved[i][1]) <= 1]
        res["gold_only"] = entry["gold_only"]
        res["gate_only"] = entry["gate_only"]
        res["status"] = "DIFF" if diffs else "PASS"
    for side in ("gold", "gate"):
        try:
            os.remove(os.path.join(d, side + ".vvp"))
        except OSError:
            pass
    return res


def cmd_run(args):
    out = os.path.abspath(args.out)
    roots = {"repo": os.path.abspath(args.repo or REPO), "bgm": os.path.abspath(args.bgm), "out": out}
    with open(os.path.join(out, "manifest.json")) as f:
        manifest = json.load(f)
    if shutil.which("iverilog") is None or shutil.which("vvp") is None:
        print("iverilog / vvp not found on PATH", file=sys.stderr)
        return 2
    entries = manifest["entries"]
    if args.only:
        entries = [e for e in entries if e["id"] in args.only]
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        results = list(pool.map(lambda e: _run_one(e, roots, out), entries))
    with open(os.path.join(out, "results.json"), "w") as f:
        json.dump({"lab": manifest["lab"], "cycles": manifest["cycles"], "results": results}, f, indent=1)
    _print_summary(results, verbose=args.verbose)
    return 0 if all(r["status"] == "PASS" for r in results if r["status"] not in ("NO-ORACLE", "NO-LAB-TOP")) else 1


# ---------------------------------------------------------------------------
# remote / summary
# ---------------------------------------------------------------------------

def cmd_remote(args):
    rc = cmd_generate(args)
    if rc:
        return rc
    sys.path.insert(0, REPO)
    from tools import bgm_oracle
    out = os.path.abspath(args.out)
    host = args.host
    remote_repo = REMOTE_ROOT + "/repo/"
    remote_bgm = REMOTE_ROOT + "/bgm/"
    remote_out = REMOTE_ROOT + "/out/"
    excludes = ["--exclude", ".git", "--exclude", "__pycache__", "--exclude", ".venv",
                "--exclude", "build", "--exclude", "*.pyc", "--exclude", "run", "--exclude", ".ater-tmp"]
    subprocess.check_call(["ssh", host, "mkdir -p {} {} {}".format(remote_repo, remote_bgm, remote_out)])
    subprocess.check_call(["rsync", "-a", "--delete"] + excludes + [REPO + "/", "{}:{}".format(host, remote_repo)])
    bgm_excl = ["--exclude", "*.jpg", "--exclude", "*.jpeg", "--exclude", "*.png", "--exclude", "*.pdf",
                "--exclude", "*.gif", "--exclude", "*.zip", "--exclude", "*.bin", "--exclude", "*.fs",
                "--exclude", "*.sof", "--exclude", "*.bit"]
    for sub in ("boards", "labs", "peripherals"):
        subprocess.check_call(["rsync", "-a", "--delete"] + bgm_excl +
                              [os.path.join(bgm_oracle.BGM_DIR, sub) + "/", "{}:{}{}/".format(host, remote_bgm, sub)])
    subprocess.check_call(["rsync", "-a", "--delete", "--exclude", "*.vvp", out + "/", "{}:{}".format(host, remote_out)])
    remote_cmd = "cd {r} && python3 tools/equiv_check.py run --out {o} --repo {r} --bgm {b} --jobs {j}{v}{only}".format(
        r=remote_repo.rstrip("/"), o=remote_out.rstrip("/"), b=remote_bgm.rstrip("/"), j=args.jobs,
        v=" --verbose" if args.verbose else "",
        only=(" --only " + " ".join(args.only)) if args.only else "")
    proc = subprocess.run(["ssh", host, remote_cmd], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          universal_newlines=True)
    subprocess.check_call(["rsync", "-a", "--exclude", "*.vvp", "{}:{}".format(host, remote_out), out + "/"])
    sys.stdout.write(proc.stdout)
    return proc.returncode


def _print_summary(results, verbose=False):
    counts = OrderedDict()
    for r in results:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    print("equivalence: " + ", ".join("{} {}".format(v, k) for k, v in counts.items()))
    for r in results:
        st = r["status"]
        if st == "PASS" and not verbose:
            continue
        if st in ("NO-ORACLE", "NO-LAB-TOP") and not verbose:
            continue
        line = "  {:<44} {:<18}".format(r["id"], st)
        if st == "PASS":
            line += " {} pins, {} cycles, {}s".format(r.get("n_cmp"), r.get("cycles"), r.get("seconds"))
        elif st == "DIFF":
            line += " {} of {} pins differ ({} cycles, {}s)".format(len(r["mismatch_pins"]), r["n_cmp"],
                                                                     r.get("cycles"), r.get("seconds"))
        elif r.get("errors"):
            line += " " + (r["errors"][0] or "")[:110]
        print(line)
        if st == "DIFF":
            for m in r["mismatch_pins"][:12]:
                print("      {:<10} gold {:<28} gate {:<28} {:>8} cycles from {} (gold {} / gate {})".format(
                    m["pin"], str(m["gold"]), str(m["gate"]), m["cycles"], m["first"],
                    m["gold_values"], m["gate_values"]))
            if len(r["mismatch_pins"]) > 12:
                print("      ... {} more".format(len(r["mismatch_pins"]) - 12))
        if verbose and st in ("PASS", "DIFF"):
            if r.get("gold_only"):
                print("      BGM-only pins: {}".format(" ".join(r["gold_only"][:12])))
            if r.get("gate_only"):
                print("      ours-only pins: {}".format(" ".join(r["gate_only"][:12])))
            if r.get("constant_pins"):
                print("      constant on both sides: {}".format(" ".join(r["constant_pins"][:12])))
            for s in ("gold", "gate"):
                for msg in r.get(s + "_messages") or []:
                    print("      {}: {}".format(s, msg))


def cmd_summary(args):
    with open(os.path.join(os.path.abspath(args.out), "results.json")) as f:
        data = json.load(f)
    _print_summary(data["results"], verbose=args.verbose)
    return 0


# ---------------------------------------------------------------------------

def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="cmd")

    def common(sp):
        sp.add_argument("--out", required=True, help="work directory (manifest, wrappers, testbenches, results)")
        sp.add_argument("--only", nargs="*", help="configuration ids")
        sp.add_argument("--verbose", "-v", action="store_true")

    g = sub.add_parser("generate", help="write wrappers, testbenches and the manifest")
    common(g)
    g.add_argument("--lab", default="equiv", help="'equiv' (tools/equiv_lab) or a BGM lab name present under designs/")
    g.add_argument("--cycles", type=int, default=DEFAULT_CYCLES)

    r = sub.add_parser("run", help="compile and co-simulate every ready entry (needs iverilog)")
    common(r)
    r.add_argument("--repo", default=None)
    r.add_argument("--bgm", required=True, help="BGM root (boards/, labs/, peripherals/)")
    r.add_argument("--jobs", type=int, default=8)

    m = sub.add_parser("remote", help="generate here, run on --host, fetch the results")
    common(m)
    m.add_argument("--host", required=True)
    m.add_argument("--lab", default="equiv")
    m.add_argument("--cycles", type=int, default=DEFAULT_CYCLES)
    m.add_argument("--jobs", type=int, default=32)

    s = sub.add_parser("summary", help="print results.json")
    common(s)

    args = p.parse_args(argv)
    if args.cmd is None:
        p.print_help()
        return 2
    return {"generate": cmd_generate, "run": cmd_run, "remote": cmd_remote, "summary": cmd_summary}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
