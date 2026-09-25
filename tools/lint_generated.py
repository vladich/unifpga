#!/usr/bin/env python3
"""
Lint stage: generate `top.sv` for every
configuration and compile it together with the peripheral RTL and one design
using iverilog (`-g2012`). Catches undeclared identifiers, width mismatches,
missing modules and syntax slips in what codegen emits, without any vendor
tool.

Three sub-commands so the iverilog run can happen on a machine without PyYAML
(mercury's `agent` account):

    generate  [--all] [--design D | --design all] --out DIR
        Resolve each configuration, write DIR/tops/<cfg>/top.sv and
        DIR/manifest.json (per configuration: file list relative to the repo,
        include dirs, whether strict codegen accepted it). Needs PyYAML.
        Default: only configurations strict codegen accepts; --all lints the
        non-strict output too (useful to see what the refused ones would say).
        `--design all`: every design on every configuration it fits
        (DIR/tops/<cfg>/<design>/top.sv, entries `<cfg>/<design>`).

    run --out DIR [--repo R] [--jobs N] [--yosys]
        Compile every manifest entry with iverilog; write DIR/results.json and
        DIR/logs/<cfg>.log. No PyYAML needed. --yosys also synthesizes it
        (read, hierarchy, proc, flatten) and runs `check -assert`: a net with
        conflicting drivers, a used net nothing drives, a combinational loop
        fail the entry.

    remote --host H --out DIR [--all] [--design D]
        generate locally, rsync the repo and DIR to H:/tmp/unifpga-lint/, run
        `run` there, fetch results back into DIR, print the summary.

    summary --out DIR
        Print the PASS/FAIL table from DIR/results.json.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

REPO = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
DEFAULT_DESIGN = os.path.join("designs", "1_09_hex_counter", "design_top.sv")
REMOTE_ROOT = "/tmp/unifpga-lint"


# ---------------------------------------------------------------------------
# generate (needs PyYAML through config.init / tools.codegen)
# ---------------------------------------------------------------------------

def _source_list(resolved, design_top, generated_top):
    """The file set the rig's own toolchain compiles, as repo-relative paths.
    Headers (`.svh`) are left out: iverilog and yosys compile each listed file
    standalone and reach headers through `include (Vivado alone lists them)."""
    import importlib
    from tools import source_set
    tc = resolved["toolchain"]["Id"]
    try:
        driver = importlib.import_module("toolchains.{0}.{0}".format(tc))
        files = driver._collect_sv_sources(REPO, resolved["peripherals"], design_top, generated_top)
    except (ImportError, AttributeError):
        files = source_set.collect_sources(REPO, resolved["peripherals"], design_top, generated_top,
                                           gate_helpers=False, gate_common=False)
    out = []
    for f in files:
        if f.endswith(".svh"):
            continue
        f = os.path.abspath(f)
        out.append(os.path.relpath(f, REPO) if f.startswith(REPO + os.sep) else f)
    # Vendor primitives (rPLL, SB_PLL40_*, BUFG) only exist in the vendor
    # flows; the lint compiles the behavioural stand-ins instead, which also
    # cover the Quartus pass-through stubs.
    stubs = os.path.join("rtl", "sim", "vendor_stubs.sv")
    compat = os.path.join("rtl", "peripherals", "_quartus_compat") + os.sep
    out = [f for f in out if not f.startswith(compat)]
    out.append(stubs)
    return out


def cmd_generate(args):
    sys.path.insert(0, REPO)
    import logging
    logging.disable(logging.CRITICAL)
    from config import init as config_init
    from tools import codegen

    out = os.path.abspath(args.out)
    tops_dir = os.path.join(out, "tops")
    os.makedirs(tops_dir, exist_ok=True)
    every_design = args.design == "all"
    if not every_design and not os.path.exists(os.path.join(REPO, args.design)):
        print("design not found: {}".format(os.path.join(REPO, args.design)), file=sys.stderr)
        return 2
    manifest = {"design": args.design, "entries": []}
    ids = sorted(config_init.read_configurations())
    if args.only:
        ids = [c for c in ids if c in args.only]
    pairs = []
    for cfg_id in ids:
        if not every_design:
            pairs.append((cfg_id, args.design, cfg_id))
            continue
        from tools import studio
        fit = studio.design_fit(config_init.resolve_configuration(cfg_id))
        pairs += [(cfg_id, os.path.join("designs", d, "design_top.sv"), cfg_id + "/" + d)
                  for d, unmet in sorted(fit.items()) if not unmet]
    n_strict = n_loose = n_skipped = 0
    for cfg_id, design_rel, entry_id in pairs:
        design_top = os.path.join(REPO, design_rel)
        resolved = config_init.resolve_configuration(cfg_id)
        strict_ok = True
        try:
            text = codegen.emit_top_sv(resolved, strict=True, design=design_top)
        except codegen.CodegenError:
            strict_ok = False
            if not args.all:
                n_skipped += 1
                continue
            text = codegen.emit_top_sv(resolved, strict=False, design=design_top)
        d = os.path.join(tops_dir, entry_id)
        os.makedirs(d, exist_ok=True)
        top_path = os.path.join(d, "top.sv")
        with open(top_path, "w") as f:
            f.write(text)
        files = _source_list(resolved, design_top, top_path)
        # generated top is outside the repo: reference it relative to `out`
        files = [os.path.relpath(top_path, out) if os.path.isabs(p) and p.startswith(out) else p
                 for p in files]
        incdirs = [os.path.join("rtl", "peripherals"), os.path.dirname(design_rel)]
        manifest["entries"].append({
            "id": entry_id,
            "design": design_rel,
            "toolchain": resolved["toolchain"]["Id"],
            "strict_ok": strict_ok,
            "files": files,
            "incdirs": incdirs,
        })
        if strict_ok:
            n_strict += 1
        else:
            n_loose += 1
    with open(os.path.join(out, "manifest.json"), "w") as f:
        json.dump(manifest, f, indent=1)
    print("generated {} tops ({} strict, {} non-strict, {} skipped) -> {}".format(
        n_strict + n_loose, n_strict, n_loose, n_skipped, out))
    return 0


# ---------------------------------------------------------------------------
# run (iverilog only)
# ---------------------------------------------------------------------------

_BOXES = {}


def _vendor_blackboxes(repo):
    """A file of the vendor primitives' module headers only (rtl/sim/vendor_stubs.sv
    without bodies), written once next to the stand-ins' copy for yosys."""
    if repo not in _BOXES:
        text = open(os.path.join(repo, "rtl", "sim", "vendor_stubs.sv"), encoding="utf-8").read()
        text = re.sub(r"//[^\n]*", "", text)
        heads = re.findall(r"\bmodule\b.*?\);", text, re.S)
        path = os.path.join(repo, "rtl", "sim", ".vendor_blackboxes.v")
        with open(path, "w") as f:
            f.write("".join("(* blackbox *)\n{}\nendmodule\n\n".format(h) for h in heads))
        _BOXES[repo] = path
    return _BOXES[repo]


def _design_box(design_top, path):
    """design_top's header alone (parameters, ports with directions and widths)
    as a black box written to `path`; None when no header is found. The header
    ends at the `;` after the balanced port list."""
    text = re.sub(r"//[^\n]*", "", open(design_top, encoding="utf-8", errors="replace").read())
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    m = re.search(r"\bmodule\s+design_top\b", text)
    if not m:
        return None
    i, groups = m.end(), 0
    while i < len(text) and groups < 2:
        c = text[i]
        if c == ";" and groups == 1 and not re.match(r"\s*#", text[m.end():]):
            break                                     # no parameter list: one group
        if c == "(":
            depth, j = 0, i
            while j < len(text):
                depth += (text[j] == "(") - (text[j] == ")")
                if depth == 0:
                    break
                j += 1
            groups, i = groups + 1, j
        i += 1
    header = text[m.start():i].rstrip().rstrip(";")
    with open(path, "w") as f:
        f.write("(* blackbox *)\n{};\nendmodule\n".format(header))
    return path


def _yosys_check(entry, repo, files, yosys, box=None):
    """yosys: read the top, the design and the peripherals, elaborate,
    `check -assert`. Common helper modules (rtl/peripherals/designs_common) are
    read only when the design or the top instantiates them: some are not
    readable by every yosys and none is needed otherwise. With `box` (a
    design_top header from _design_box) the design's own sources are left out
    and design_top is that black box: the wiring is checked against its port
    directions and widths alone."""
    if box:
        files = [f for f in files if not _in_design(entry, repo, f)]
    texts = ""
    for f in files[:2]:
        try:
            texts += open(f, encoding="utf-8", errors="replace").read()
        except OSError:
            pass
    # to a fixed point: a common module another one instantiates is needed too
    common = [f for f in files if os.sep + "designs_common" + os.sep in f]
    needed, grew = set(), True
    while grew:
        grew = False
        for f in common:
            name = os.path.splitext(os.path.basename(f))[0]
            if f not in needed and re.search(r"\b" + re.escape(name) + r"\b", texts):
                needed.add(f)
                texts += open(f, encoding="utf-8", errors="replace").read()
                grew = True
    keep = [f for f in files if f not in common or f in needed]
    # vendor primitives (PLLs, differential buffers) are black boxes here: their
    # simulation stand-ins are timed models yosys cannot read, so only their
    # module headers (parameters, ports with directions) are given to it
    keep = [f for f in keep if not f.endswith(os.path.join("rtl", "sim", "vendor_stubs.sv"))]
    boxes = _vendor_blackboxes(repo) + (" " + box if box else "")
    incs = " ".join("-I" + os.path.join(repo, i) for i in entry["incdirs"])
    script = "read_verilog -sv -lib {}; read_verilog -sv {} {}; hierarchy -top top; proc; flatten; check -assert".format(
        boxes, incs, " ".join(keep))
    proc = subprocess.run([yosys, "-q", "-p", script], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    return proc.returncode, proc.stdout


def _in_design(entry, repo, path):
    """A source path of the design's own directory (not the generated top or rtl/)."""
    d = os.path.join(repo, os.path.dirname(entry["design"])) + os.sep
    return path.startswith(d) or ("/" + os.path.dirname(entry["design"]) + "/") in path


def _check_findings(text):
    """`check` findings, one string per finding (a warning with its indented lines)."""
    found = []
    for line in text.splitlines():
        if line.startswith(("Warning:", "ERROR:")):
            found.append(line)
        elif line.startswith("    ") and found:
            found[-1] += "\n" + line
    return [f for f in found if "conflicting driver" in f or "has no driver" in f or "logic loop" in f
            or "driving constant bits" in f]


def _classify_yosys(entry, repo, rc, text):
    """(status, wiring problems, design findings). A conflict anywhere is the
    wiring's: two masters on a net the top connects. An undriven net or a loop
    wholly inside the design is the design's own; so is a design file this
    yosys cannot parse (the wiring is then UNCHECKED). A claimed gpio bit
    (gpio_nc_N) is a pad-less inout: a design reading it reads nothing, by
    rule, and no driver can be added without fighting a design that drives it."""
    if rc == 0:
        return "PASS", [], []
    parse = [l for l in text.splitlines() if "ERROR" in l and re.search(r"\.s?v\w*:\d+:", l)]
    if parse:
        path = parse[0].split(":")[0]
        return ("UNCHECKED", [], parse[:1]) if _in_design(entry, repo, path) else ("FAIL", parse[:1], [])
    wiring, design, allowed = [], [], []
    for f in _check_findings(text):
        if "conflicting driver" in f or "driving constant bits" in f:
            wiring.append(f)
        elif "gpio_nc_" in f.split("\n")[0] and "has no driver" in f:
            allowed.append(f)
        elif "i_design_top." in f:
            design.append(f)
        else:
            wiring.append(f)
    if wiring:
        return "FAIL", wiring, design
    if design:
        return "DESIGN", [], design
    if allowed:
        return "PASS", [], []
    return "FAIL", ["yosys check failed (see log)"], []


def _compile_one(entry, repo, out, iverilog, yosys=None):
    cfg_id = entry["id"]
    log_dir = os.path.join(out, "logs")
    os.makedirs(log_dir, exist_ok=True)
    files = []
    for p in entry["files"]:
        if os.path.isabs(p):
            files.append(p)
        elif p.startswith("tops" + os.sep) or p.startswith("tops/"):
            files.append(os.path.join(out, p))
        else:
            files.append(os.path.join(repo, p))
    cmd = [iverilog, "-g2012", "-Wall", "-Wno-timescale", "-grelative-include", "-s", "top", "-o", os.devnull]
    for inc in entry["incdirs"]:
        cmd += ["-I", os.path.join(repo, inc)]
    cmd += files
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    log_path = os.path.join(log_dir, cfg_id.replace("/", "__") + ".log")
    with open(log_path, "w") as f:
        f.write(" ".join(cmd) + "\n\n" + proc.stdout)
    lines = [l for l in proc.stdout.splitlines() if l.strip()]
    errors = [l for l in lines if ": error" in l or "error:" in l.lower() or "syntax error" in l]
    warnings = [l for l in lines if ": warning" in l]
    rc = proc.returncode
    status = "PASS" if rc == 0 else "FAIL"
    findings = []
    if rc != 0:
        # the first located error (or missing include) says whose source it is;
        # warnings and their continuation lines do not, and an iverilog "sorry"
        # (an unsupported construct) only when there is no error
        located = [l for l in lines if re.match(r"\S+\.(sv|v|svh|vh):\d+:", l)]
        first = next((l for l in located if re.search(r"error|Include file .* not found", l)),
                     next((l for l in located if ": sorry:" in l), ""))
        if first and _in_design(entry, repo, first.split(":")[0]):
            status, findings, errors = "UNCHECKED", errors[:1], []
    if yosys and rc == 0:
        yrc, ylog = _yosys_check(entry, repo, files, yosys)
        with open(log_path, "a") as f:
            f.write("\n\n---- yosys check -assert ----\n" + ylog)
        status, wiring, findings = _classify_yosys(entry, repo, yrc, ylog)
        errors = ["yosys: " + p for p in wiring]
    if yosys and status == "UNCHECKED":
        # the design is unreadable here: check the wiring with design_top as
        # a black box of its header
        box = _design_box(os.path.join(repo, entry["design"]), log_path[:-4] + ".design_box.v")
        if box:
            yrc, ylog = _yosys_check(entry, repo, files, yosys, box=box)
            with open(log_path, "a") as f:
                f.write("\n\n---- yosys check -assert, design_top a black box ----\n" + ylog)
            bstatus, wiring, _ = _classify_yosys(entry, repo, yrc, ylog)
            if bstatus == "PASS":
                status = "BOXED"
            elif bstatus == "FAIL":
                status, errors = "FAIL", ["yosys (design boxed): " + p for p in wiring]
    return {
        "id": cfg_id,
        "toolchain": entry.get("toolchain"),
        "strict_ok": entry.get("strict_ok"),
        "rc": rc,
        "status": status,
        "errors": errors[:8],
        "n_errors": len(errors),
        "design_findings": [f.split("\n")[0] for f in findings[:4]],
        "n_warnings": len(warnings),
        "first_warnings": warnings[:5],
        "log": os.path.relpath(log_path, out),
    }


def cmd_run(args):
    out = os.path.abspath(args.out)
    repo = os.path.abspath(args.repo or REPO)
    with open(os.path.join(out, "manifest.json")) as f:
        manifest = json.load(f)
    iverilog = shutil.which("iverilog")
    if iverilog is None:
        print("iverilog not found on PATH", file=sys.stderr)
        return 2
    entries = manifest["entries"]
    yosys = shutil.which("yosys") if args.yosys else None
    if args.yosys and yosys is None:
        print("yosys not found on PATH", file=sys.stderr)
        return 2
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        results = list(pool.map(lambda e: _compile_one(e, repo, out, iverilog, yosys), entries))
    with open(os.path.join(out, "results.json"), "w") as f:
        json.dump({"design": manifest["design"], "results": results}, f, indent=1)
    _print_summary(results)
    return 0 if all(r["status"] != "FAIL" for r in results) else 1


# ---------------------------------------------------------------------------
# remote
# ---------------------------------------------------------------------------

def cmd_remote(args):
    rc = cmd_generate(args)
    if rc:
        return rc
    out = os.path.abspath(args.out)
    host = args.host
    remote_repo = REMOTE_ROOT + "/repo/"
    remote_out = REMOTE_ROOT + "/out/"
    excludes = ["--exclude", ".git", "--exclude", "__pycache__", "--exclude", ".venv",
                "--exclude", "build", "--exclude", "*.pyc"]
    subprocess.check_call(["ssh", host, "mkdir -p {} {}".format(remote_repo, remote_out)])
    subprocess.check_call(["rsync", "-a", "--delete"] + excludes + [REPO + "/", "{}:{}".format(host, remote_repo)])
    subprocess.check_call(["rsync", "-a", "--delete", out + "/", "{}:{}".format(host, remote_out)])
    remote_cmd = "cd {r} && python3 tools/lint_generated.py run --out {o} --repo {r} --jobs {j}{y}".format(
        r=remote_repo.rstrip("/"), o=remote_out.rstrip("/"), j=args.jobs, y=" --yosys" if args.yosys else "")
    proc = subprocess.run(["ssh", host, remote_cmd], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    subprocess.check_call(["rsync", "-a", "{}:{}".format(host, remote_out), out + "/"])
    sys.stdout.write(proc.stdout)
    return proc.returncode


def _print_summary(results):
    """PASS: wiring checked clean. DESIGN: wiring clean, the design has its own
    findings. BOXED: the design's sources could not be read; the wiring was
    checked clean against design_top's header. UNCHECKED: not even the header
    could be read, the wiring was not checked. FAIL: a problem in the
    generated top or the drivers."""
    count = {k: sum(1 for r in results if r["status"] == k)
             for k in ("PASS", "DESIGN", "BOXED", "UNCHECKED", "FAIL")}
    print("lint: {PASS} PASS, {DESIGN} DESIGN (the design's own findings), {BOXED} BOXED "
          "(design unreadable, wiring checked against its header), {UNCHECKED} UNCHECKED, "
          "{FAIL} FAIL of ".format(**count) + str(len(results)))
    for r in results:
        if r["status"] == "FAIL":
            print("  FAIL {:44s} [{}] {}".format(r["id"], r.get("toolchain"),
                                                (r["errors"][0] if r["errors"] else "(see log)")[:110]))
    for kind in ("UNCHECKED", "BOXED", "DESIGN"):
        designs = {}
        for r in results:
            if r["status"] == kind:
                designs.setdefault(r["id"].split("/")[-1], []).append(r)
        for d, rs in sorted(designs.items()):
            print("  {} {:44s} on {} rig(s): {}".format(kind, d, len(rs), (rs[0]["design_findings"] or ["?"])[0][:100]))
    warn = [r for r in results if r["status"] == "PASS" and r["n_warnings"]]
    if warn:
        print("  warnings on {} passing configurations (see logs)".format(len(warn)))


def cmd_summary(args):
    with open(os.path.join(os.path.abspath(args.out), "results.json")) as f:
        _print_summary(json.load(f)["results"])
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("generate")
    g.add_argument("--out", required=True)
    g.add_argument("--all", action="store_true")
    g.add_argument("--design", default=DEFAULT_DESIGN)
    g.add_argument("--only", nargs="*")
    r = sub.add_parser("run")
    r.add_argument("--out", required=True)
    r.add_argument("--repo")
    r.add_argument("--jobs", type=int, default=8)
    r.add_argument("--yosys", action="store_true")
    m = sub.add_parser("remote")
    m.add_argument("--host", required=True)
    m.add_argument("--out", required=True)
    m.add_argument("--all", action="store_true")
    m.add_argument("--design", default=DEFAULT_DESIGN)
    m.add_argument("--only", nargs="*")
    m.add_argument("--jobs", type=int, default=16)
    m.add_argument("--yosys", action="store_true")
    s = sub.add_parser("summary")
    s.add_argument("--out", required=True)
    args = p.parse_args(argv)
    return {"generate": cmd_generate, "run": cmd_run, "remote": cmd_remote, "summary": cmd_summary}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
