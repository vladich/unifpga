"""
Xilinx ISE 14.7 toolchain driver.

ISE 14.7 is the last version that supports the older Xilinx families:
Spartan-3 / 3A / 3AN / 3E / 3A-DSP, Spartan-6, Virtex-II / II Pro / 4 / 5 / 6,
CoolRunner-II / XPLA3 CPLDs, XC9500 / XL / XV CPLDs. Vivado dropped all
of these in favor of 7-series and newer.

Pipeline:

    sv2v <inputs> > merged.v        (SystemVerilog → Verilog 2001)
    xst -ifn build.xst -ofn build.syr
    ngdbuild -uc constraints.ucf build.ngc build.ngd
    map -p <PART> -o build_map.ncd build.ngd build.pcf
    par -w build_map.ncd build_par.ncd build.pcf
    trce -e 3 build_par.ncd build.pcf -o build.twr
    bitgen -w build_par.ncd build.bit build.pcf

Artifacts:
    <output>/top.sv             — codegen-produced top module
    <output>/merged.v           — sv2v output (all SV files concatenated as V2001)
    <output>/build.ucf          — UCF pin constraints
    <output>/build.xst          — xst options
    <output>/build.prj          — xst source-file list
    <output>/build.sh           — driver shell script (records the exact flow)
    <output>/build.ngc/.ngd/.ncd/.pcf/.bit — ISE intermediate + final artifacts

Critical caveats:
  - ISE doesn't natively grok SystemVerilog. We pre-pass through `sv2v`
    (from oss-cad-suite) to produce Verilog 2001 that xst can read. If
    sv2v is missing the driver will explain how to get it.
  - ISE 14.7 itself doesn't run on modern Linux without ancient glibc /
    libstdc++ shims; most users wrap it in a Docker container. The driver
    just looks for the binaries on $PATH (or under InstallDir) — if a
    container alias is set up the user's environment, this finds them.
  - Set $UNIFPGA_DRY_RUN=1 to generate every artifact without running
    tools, useful for CI and for machines without ISE installed.
"""

import logging
import os
import shutil
import subprocess

from tools import codegen
from tools import source_set


log = logging.getLogger(__name__)
REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.realpath(__file__))))

PROJECT_NAME = "unifpga_top"


def _resolve_ise_bin(toolchain, name):
    """Look up an ISE binary (xst, ngdbuild, map, par, trce, bitgen).
    Prefers `<InstallDir>/bin/lin64/<name>` then $PATH."""
    install_dir = os.path.expanduser(toolchain.get("install_dir") or "").rstrip("/")
    if install_dir:
        for sub in ("bin/lin64", "bin/lin", "bin"):
            cand = os.path.join(install_dir, sub, name)
            if os.path.exists(cand):
                return cand
    return shutil.which(name)


def _resolve_sv2v():
    """sv2v ships in oss-cad-suite; prefer that location."""
    oss = os.path.expanduser("~/oss-cad-suite/bin/sv2v")
    if os.path.exists(oss) and os.access(oss, os.X_OK):
        return oss
    return shutil.which("sv2v")


def _collect_sv_sources(repo, peripherals, user_design_top, generated_top, component_sources=()):
    """ISE, same shape as Vivado: .svh headers included, helpers/common ungated."""
    return source_set.collect_sources(
        repo, peripherals, user_design_top, generated_top,
        include_svh=True, gate_helpers=False, gate_common=False, component_sources=component_sources)


def _emit_xst_prj(merged_v_path):
    """The xst .prj file enumerates source files. After sv2v we have one
    consolidated .v file, so the project file has a single line."""
    return 'verilog work "{}"\n'.format(merged_v_path)


def _emit_xst_opts(prj_path, part_name, top_module, output_dir):
    """xst options file (build.xst). The full set of switches is large
    but most stay at sensible defaults; we set part, top, optimization
    target, and a handful of synthesis preferences."""
    return (
        'set -tmpdir "{tmp}"\n'
        'set -xsthdpdir "{hdp}"\n'
        'run\n'
        '-ifn {prj}\n'
        '-ifmt mixed\n'
        '-ofn {ofn}\n'
        '-ofmt NGC\n'
        '-p {part}\n'
        '-top {top}\n'
        '-opt_mode Speed\n'
        '-opt_level 1\n'
        '-iuc NO\n'
        '-keep_hierarchy No\n'
        '-netlist_hierarchy as_optimized\n'
        '-rtlview Yes\n'
        '-glob_opt AllClockNets\n'
        '-read_cores YES\n'
        '-write_timing_constraints NO\n'
        '-cross_clock_analysis NO\n'
        '-hierarchy_separator /\n'
        '-bus_delimiter <>\n'
        '-case maintain\n'
        '-slice_utilization_ratio 100\n'
        '-bram_utilization_ratio 100\n'
        '-dsp_utilization_ratio 100\n'
        '-fsm_extract YES -fsm_encoding Auto\n'
        '-safe_implementation No\n'
        '-fsm_style lut\n'
        '-ram_extract Yes\n'
        '-ram_style Auto\n'
        '-rom_extract Yes\n'
        '-rom_style Auto\n'
        '-auto_bram_packing NO\n'
        '-shreg_extract YES\n'
        '-resource_sharing YES\n'
        '-async_to_sync NO\n'
        '-shreg_min_size 2\n'
        '-use_dsp48 auto\n'
        '-iobuf YES\n'
        '-max_fanout 100000\n'
        '-bufg 16\n'
        '-register_duplication YES\n'
        '-register_balancing No\n'
        '-optimize_primitives NO\n'
        '-use_clock_enable Auto\n'
        '-use_sync_set Auto\n'
        '-use_sync_reset Auto\n'
        '-iob auto\n'
        '-equivalent_register_removal YES\n'
        '-slice_utilization_ratio_maxmargin 5\n'
    ).format(
        tmp=os.path.join(output_dir, "xst", "tmp"),
        hdp=os.path.join(output_dir, "xst"),
        prj=prj_path,
        ofn=os.path.join(output_dir, PROJECT_NAME),
        part=part_name,
        top=top_module,
    )


def _emit_build_script(part_name, top_module, ucf_path, output_dir,
                       do_synth=True, do_pnr=True, do_bitstream=True):
    """Shell script that runs the ISE flow end-to-end. We emit this whether
    or not we plan to execute it — it documents exactly what the driver
    intends and lets users inspect / re-run interactively."""
    proj = os.path.join(output_dir, PROJECT_NAME)
    lines = ["#!/bin/sh", "# ISE 14.7 build flow — auto-generated by uni-fpga.", "set -e", "cd " + output_dir, ""]
    if do_synth:
        lines += [
            "# 1. Synthesis (xst)",
            "xst -intstyle ise -ifn build.xst -ofn build.syr",
            "",
        ]
    if do_pnr:
        lines += [
            "# 2. Translate (ngdbuild)",
            'ngdbuild -intstyle ise -uc "{ucf}" "{proj}.ngc" "{proj}.ngd"'.format(ucf=ucf_path, proj=proj),
            "",
            "# 3. Map",
            'map -intstyle ise -p {part} -o "{proj}_map.ncd" "{proj}.ngd" "{proj}.pcf"'.format(
                part=part_name, proj=proj),
            "",
            "# 4. Place and route",
            'par -intstyle ise -w "{proj}_map.ncd" "{proj}_par.ncd" "{proj}.pcf"'.format(proj=proj),
            "",
            "# 5. Timing analysis",
            'trce -intstyle ise -e 3 "{proj}_par.ncd" "{proj}.pcf" -o "{proj}.twr"'.format(proj=proj),
            "",
        ]
    if do_bitstream:
        lines += [
            "# 6. Bitstream generation",
            'bitgen -intstyle ise -w "{proj}_par.ncd" "{proj}.bit" "{proj}.pcf"'.format(proj=proj),
            "",
        ]
    return "\n".join(lines) + "\n"


def _merge_sv_files_via_sv2v(sv_files, merged_v_path, dry_run=False):
    """Run sv2v on the SV inputs, producing a single Verilog 2001 file
    that ISE's xst can parse. In dry-run mode we just write a stub."""
    if dry_run:
        with open(merged_v_path, "w") as f:
            f.write("// [dry run] sv2v not invoked. Would have merged:\n")
            for s in sv_files:
                f.write("//   - {}\n".format(s))
        return 0

    sv2v_bin = _resolve_sv2v()
    if sv2v_bin is None:
        log.error("sv2v not found. ISE doesn't support SystemVerilog directly; "
                  "uni-fpga uses sv2v as a pre-pass. Install oss-cad-suite "
                  "(ships sv2v at ~/oss-cad-suite/bin/sv2v) or `cargo install sv2v` "
                  "or set UNIFPGA_DRY_RUN=1 to skip the actual run.")
        return 1
    cmd = [sv2v_bin] + sv_files
    log.info("Invoking sv2v: %s ... > %s", sv2v_bin, merged_v_path)
    with open(merged_v_path, "w") as f:
        rc = subprocess.run(cmd, stdout=f).returncode
    if rc != 0:
        log.error("sv2v failed (exit %d). Inspect %s for partial output.", rc, merged_v_path)
    return rc


def synthesize(*, dir, configuration, board, board_pinmap, toolchain, peripherals,
               top, generated_top=None, include=None, component_sources=(), output, step="full", **_):
    """Synthesize a configuration through ISE 14.7. Returns 0 on success."""
    resolved = {
        "configuration": configuration,
        "board":         board,
        "board_pinmap":  board_pinmap,
        "toolchain":     toolchain,
        "peripherals":   peripherals,
    }

    if generated_top is None:
        generated_top = os.path.join(output, "top.sv")
        with open(generated_top, "w") as f:
            f.write(codegen.emit_top_sv(resolved, design=top))

    ucf_path = os.path.join(output, "build.ucf")
    with open(ucf_path, "w") as f:
        f.write(codegen.emit_ucf(resolved))
    log.info("Wrote %s", ucf_path)

    sv_files = _collect_sv_sources(REPO, peripherals, top, generated_top, component_sources)
    log.info("Source files (%d):", len(sv_files))
    for sv in sv_files:
        log.info("  - %s", os.path.relpath(sv, REPO) if sv.startswith(REPO) else sv)

    part_name = board.get("Part") or ""
    if not part_name and isinstance(board.get("Parts"), list):
        wanted = (configuration.get("part") or "").lower()
        chosen = None
        for entry in board["Parts"]:
            if wanted and entry.get("Name", "").lower() == wanted:
                chosen = entry
                break
        if chosen is None:
            chosen = board["Parts"][0]
            log.info("Board %s has multiple Parts; defaulting to %s",
                     board["Id"], chosen.get("Name") or chosen.get("Part"))
        part_name = chosen.get("Part") or ""
    if not part_name:
        log.error("Board %s has no 'Part' field — cannot drive ISE.", board["Id"])
        return 1
    # ISE xst expects the part name without the leading 'XC' uppercase prefix
    # variance; normalize to lowercase since the rest of the flow accepts both.
    part_name = part_name.lower()

    do_synth     = step in ("elaborate", "pnr", "full")
    do_pnr       = step in ("pnr", "full")
    do_bitstream = step == "full"

    # SV → V conversion via sv2v
    merged_v = os.path.join(output, "merged.v")
    dry_run = bool(os.environ.get("UNIFPGA_DRY_RUN"))
    rc = _merge_sv_files_via_sv2v(sv_files, merged_v, dry_run=dry_run)
    if rc != 0:
        return rc

    # xst project + options
    prj_path = os.path.join(output, "build.prj")
    with open(prj_path, "w") as f:
        f.write(_emit_xst_prj(merged_v))
    xst_path = os.path.join(output, "build.xst")
    with open(xst_path, "w") as f:
        f.write(_emit_xst_opts(prj_path, part_name, "top", output))
    log.info("Wrote %s and %s", prj_path, xst_path)

    # Build script
    sh_path = os.path.join(output, "build.sh")
    with open(sh_path, "w") as f:
        f.write(_emit_build_script(part_name, "top", ucf_path, output,
                                   do_synth=do_synth, do_pnr=do_pnr, do_bitstream=do_bitstream))
    os.chmod(sh_path, 0o755)
    log.info("Wrote %s", sh_path)

    if dry_run:
        log.info("[dry run] ISE not invoked. Artifacts in %s", output)
        return 0

    # Resolve all the binaries we need before we start
    needed = []
    if do_synth:
        needed.append("xst")
    if do_pnr:
        needed.extend(["ngdbuild", "map", "par", "trce"])
    if do_bitstream:
        needed.append("bitgen")
    bins = {n: _resolve_ise_bin(toolchain, n) for n in needed}
    missing = [n for n, p in bins.items() if p is None]
    if missing:
        log.error("Missing ISE binaries on $PATH: %s. ISE 14.7 typically needs "
                  "a docker wrapper on modern Linux; set toolchain.InstallDir "
                  "in config/toolchains.yml or activate `settings64.sh` from "
                  "your ISE install. Set UNIFPGA_DRY_RUN=1 to skip the run.",
                  ", ".join(missing))
        return 1

    # Run the flow step by step. We don't `subprocess.run` build.sh because
    # we want clean per-step return codes.
    proj = os.path.join(output, PROJECT_NAME)
    log_path = os.path.join(output, "ise.log")
    log_fh = open(log_path, "w")

    def _run(label, cmd):
        log.info("[%s] %s", label, " ".join(cmd))
        log_fh.write("# {}\n# {}\n".format(label, " ".join(cmd)))
        log_fh.flush()
        return subprocess.run(cmd, cwd=output, stdout=log_fh, stderr=subprocess.STDOUT).returncode

    try:
        if do_synth:
            rc = _run("xst", [bins["xst"], "-intstyle", "ise", "-ifn", xst_path,
                              "-ofn", os.path.join(output, "build.syr")])
            if rc:
                log.error("xst failed (rc=%d). See %s", rc, log_path)
                return rc

        if do_pnr:
            rc = _run("ngdbuild", [bins["ngdbuild"], "-intstyle", "ise",
                                   "-uc", ucf_path,
                                   proj + ".ngc", proj + ".ngd"])
            if rc:
                log.error("ngdbuild failed (rc=%d). See %s", rc, log_path)
                return rc
            rc = _run("map", [bins["map"], "-intstyle", "ise", "-p", part_name,
                              "-o", proj + "_map.ncd",
                              proj + ".ngd", proj + ".pcf"])
            if rc:
                log.error("map failed (rc=%d). See %s", rc, log_path)
                return rc
            rc = _run("par", [bins["par"], "-intstyle", "ise", "-w",
                              proj + "_map.ncd", proj + "_par.ncd", proj + ".pcf"])
            if rc:
                log.error("par failed (rc=%d). See %s", rc, log_path)
                return rc
            rc = _run("trce", [bins["trce"], "-intstyle", "ise", "-e", "3",
                               proj + "_par.ncd", proj + ".pcf",
                               "-o", proj + ".twr"])
            if rc:
                log.error("trce failed (rc=%d). See %s", rc, log_path)
                return rc

        if do_bitstream:
            rc = _run("bitgen", [bins["bitgen"], "-intstyle", "ise", "-w",
                                 proj + "_par.ncd", proj + ".bit", proj + ".pcf"])
            if rc:
                log.error("bitgen failed (rc=%d). See %s", rc, log_path)
                return rc
    finally:
        log_fh.close()

    bit = proj + ".bit"
    if os.path.exists(bit):
        log.info("Bitstream ready: %s", bit)
    return 0


def program(*, board, board_pinmap=None, toolchain, output, **_):
    """Program the connected board with the previously-built bitstream.

    Programmer choice depends on the board:
      - Mojo v3 / Alchitry: uses `mojoload` (AVR-based ISP boot loader),
        not JTAG. The .bit needs an addressing-mode tweak via the AVR
        firmware. We don't ship mojoload — users grab it from
        embeddedmicro.com or use openFPGALoader's mojo profile.
      - Generic ISE board with JTAG: ISE ships `iMPACT`; openFPGALoader
        also works for many Spartan-6 boards.

    Stub: report that programming is unavailable. Concrete programmers can
    be added per board family as use cases land.
    """
    bit = os.path.join(output, PROJECT_NAME + ".bit")
    if not os.path.exists(bit) and not os.environ.get("UNIFPGA_DRY_RUN"):
        log.error("Bitstream not found: %s — run synthesis first", bit)
        return 1
    log.error("[ise] cannot program %s to %s; programming logic is not implemented "
              "(use openFPGALoader or vendor iMPACT manually).",
              bit, board.get("Id"))
    return 2
