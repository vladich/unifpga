#!/usr/bin/env python3
"""
Lint stage: generate `top.sv` for every
configuration and compile it together with the peripheral RTL and one design
using iverilog (`-g2012`). Catches undeclared identifiers, width mismatches,
missing modules and syntax slips in what codegen emits, without any vendor
tool.

Three sub-commands so the iverilog run can happen on a machine without PyYAML
(mercury's `agent` account):

    generate  [--all] [--design D] --out DIR
        Resolve each configuration, write DIR/tops/<cfg>/top.sv and
        DIR/manifest.json (per configuration: file list relative to the repo,
        include dirs, whether strict codegen accepted it). Needs PyYAML.
        Default: only configurations strict codegen accepts; --all lints the
        non-strict output too (useful to see what the refused ones would say).

    run --out DIR [--repo R] [--jobs N]
        Compile every manifest entry with iverilog; write DIR/results.json and
        DIR/logs/<cfg>.log. No PyYAML needed.

    remote --host H --out DIR [--all] [--design D]
        generate locally, rsync the repo and DIR to H:/tmp/unifpga-lint/, run
        `run` there, fetch results back into DIR, print the summary.

    summary --out DIR
        Print the PASS/FAIL table from DIR/results.json.
"""

import argparse
import json
import os
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
    """Same file set the Vivado driver compiles, as repo-relative paths."""
    from toolchains.vivado import vivado
    files = vivado._collect_sv_sources(REPO, resolved["peripherals"], design_top, generated_top)
    out = []
    for f in files:
        f = os.path.abspath(f)
        out.append(os.path.relpath(f, REPO) if f.startswith(REPO + os.sep) else f)
    # Vendor PLL primitives (rPLL, SB_PLL40_*) only exist in the vendor flows;
    # the lint compiles the behavioural stand-ins instead.
    if any(rel.startswith(os.path.join("rtl", "pll") + os.sep) for rel in out):
        out.append(os.path.join("rtl", "sim", "vendor_stubs.sv"))
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
    design_top = os.path.join(REPO, args.design)
    if not os.path.exists(design_top):
        print("design not found: {}".format(design_top), file=sys.stderr)
        return 2
    manifest = {"design": args.design, "entries": []}
    ids = sorted(config_init.read_configurations())
    if args.only:
        ids = [c for c in ids if c in args.only]
    n_strict = n_loose = n_skipped = 0
    for cfg_id in ids:
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
        d = os.path.join(tops_dir, cfg_id)
        os.makedirs(d, exist_ok=True)
        top_path = os.path.join(d, "top.sv")
        with open(top_path, "w") as f:
            f.write(text)
        files = _source_list(resolved, design_top, top_path)
        # generated top is outside the repo: reference it relative to `out`
        files = [os.path.relpath(top_path, out) if os.path.isabs(p) and p.startswith(out) else p
                 for p in files]
        incdirs = [os.path.join("rtl", "peripherals"), os.path.dirname(args.design)]
        manifest["entries"].append({
            "id": cfg_id,
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

def _compile_one(entry, repo, out, iverilog):
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
    cmd = [iverilog, "-g2012", "-Wall", "-Wno-timescale", "-o", os.devnull]
    for inc in entry["incdirs"]:
        cmd += ["-I", os.path.join(repo, inc)]
    cmd += files
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    log_path = os.path.join(log_dir, cfg_id + ".log")
    with open(log_path, "w") as f:
        f.write(" ".join(cmd) + "\n\n" + proc.stdout)
    lines = [l for l in proc.stdout.splitlines() if l.strip()]
    errors = [l for l in lines if ": error" in l or "error:" in l.lower() or "syntax error" in l]
    warnings = [l for l in lines if ": warning" in l]
    return {
        "id": cfg_id,
        "toolchain": entry.get("toolchain"),
        "strict_ok": entry.get("strict_ok"),
        "rc": proc.returncode,
        "status": "PASS" if proc.returncode == 0 else "FAIL",
        "errors": errors[:8],
        "n_errors": len(errors),
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
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        results = list(pool.map(lambda e: _compile_one(e, repo, out, iverilog), entries))
    with open(os.path.join(out, "results.json"), "w") as f:
        json.dump({"design": manifest["design"], "results": results}, f, indent=1)
    _print_summary(results)
    return 0 if all(r["status"] == "PASS" for r in results) else 1


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
    remote_cmd = "cd {r} && python3 tools/lint_generated.py run --out {o} --repo {r} --jobs {j}".format(
        r=remote_repo.rstrip("/"), o=remote_out.rstrip("/"), j=args.jobs)
    proc = subprocess.run(["ssh", host, remote_cmd], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    subprocess.check_call(["rsync", "-a", "{}:{}".format(host, remote_out), out + "/"])
    sys.stdout.write(proc.stdout)
    return proc.returncode


def _print_summary(results):
    n_pass = sum(1 for r in results if r["status"] == "PASS")
    print("lint: {} PASS, {} FAIL of {}".format(n_pass, len(results) - n_pass, len(results)))
    for r in results:
        if r["status"] != "PASS":
            print("  FAIL {:44s} [{}] {}".format(r["id"], r.get("toolchain"),
                                                (r["errors"][0] if r["errors"] else "(see log)")[:110]))
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
    m = sub.add_parser("remote")
    m.add_argument("--host", required=True)
    m.add_argument("--out", required=True)
    m.add_argument("--all", action="store_true")
    m.add_argument("--design", default=DEFAULT_DESIGN)
    m.add_argument("--only", nargs="*")
    m.add_argument("--jobs", type=int, default=16)
    s = sub.add_parser("summary")
    s.add_argument("--out", required=True)
    args = p.parse_args(argv)
    return {"generate": cmd_generate, "run": cmd_run, "remote": cmd_remote, "summary": cmd_summary}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
