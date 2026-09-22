"""
nextpnr-apicula toolchain driver (yosys + nextpnr-gowin + gowin_pack).

Pipeline:
    yosys -p "read_verilog -sv …; synth_gowin -top top -json out.json"
    nextpnr-gowin --device <DEV> --json in.json --cst in.cst --write out_pack.json
    gowin_pack -d <DEV> -o out.fs out_pack.json

Artifacts:
    <output>/top.sv             — codegen-produced top module
    <output>/unifpga_top.cst    — codegen-produced CST (reused from Gowin EDA)
    <output>/yosys.log, nextpnr.log — per-stage logs
    <output>/unifpga_top.json   — yosys netlist
    <output>/unifpga_top_pack.json — nextpnr packed netlist
    <output>/unifpga_top.fs     — final Gowin bitstream

Set $UNIFPGA_DRY_RUN=1 to generate every artifact without running tools.
"""

import logging
import os
import shutil
import subprocess

from tools import codegen


log = logging.getLogger(__name__)
REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.realpath(__file__))))

PROJECT_NAME = "unifpga_top"


_OSS_CAD = os.path.expanduser("~/oss-cad-suite/bin")


def _resolve_bin(name):
    """Prefer ~/oss-cad-suite/bin (newer yosys 0.41+) before falling back
    to $PATH."""
    cand = os.path.join(_OSS_CAD, name)
    if os.path.exists(cand) and os.access(cand, os.X_OK):
        return cand
    return shutil.which(name)


def _collect_sv_sources(repo, peripherals, user_design_top, generated_top):
    """Same module-name-gating pattern as the icestorm/trellis drivers."""
    files = [generated_top, os.path.abspath(user_design_top)]
    seen = {os.path.abspath(p) for p in files}

    design_dir = os.path.dirname(os.path.abspath(user_design_top))
    if os.path.isdir(design_dir):
        for root, _dirs, names in os.walk(design_dir):
            for name in sorted(names):
                if not (name.endswith(".sv") or name.endswith(".v")):
                    continue
                if name in ("design_top.sv", "tb.sv"):
                    continue
                full = os.path.join(root, name)
                if full not in seen:
                    files.append(full)
                    seen.add(full)

    for attach in peripherals:
        drv = (attach.get("peripheral") or {}).get("driver") or {}
        f = drv.get("file")
        if f:
            full = os.path.join(repo, f)
            if os.path.exists(full) and full not in seen:
                files.append(full)
                seen.add(full)

    try:
        with open(generated_top) as f:
            top_text = f.read()
    except Exception:
        top_text = ""

    helper_modules = {
        "tm1638_registers.sv":          ("tm1638_registers", "tm1638_board_controller"),
        "slow_clk_gen.sv":              ("slow_clk_gen",),
        "imitate_reset_on_power_up.sv": ("imitate_reset_on_power_up",),
    }
    for helper, modules in helper_modules.items():
        full = os.path.join(repo, "rtl", "peripherals", helper)
        if not os.path.exists(full) or full in seen:
            continue
        if any(m in top_text for m in modules):
            files.append(full)
            seen.add(full)

    sibling_text = top_text
    for f in list(files):
        try:
            with open(f) as fh:
                sibling_text += "\n" + fh.read()
        except Exception:
            pass

    designs_common_dir = os.path.join(repo, "rtl", "peripherals", "designs_common")
    if os.path.isdir(designs_common_dir):
        for name in sorted(os.listdir(designs_common_dir)):
            if not name.endswith(".sv"):
                continue
            module_name = name[:-3]
            if module_name not in sibling_text:
                continue
            full = os.path.join(designs_common_dir, name)
            if full not in seen:
                files.append(full)
                seen.add(full)

    # Skip _quartus_compat stubs (BUFG/IBUFG): yosys's synth_gowin already
    # provides those primitives natively, so adding our pass-through stubs
    # causes redefinition errors.

    # Clock-tree wrappers (rtl/pll) the generated top instantiates and the
    # drivers' extra `files:` (P3.1 / P3.2).
    for full in codegen.pll_source_paths(repo, generated_top, peripherals):
        if full not in seen:
            files.append(full)
            seen.add(full)

    return files


# Map our boards.yml board id to (nextpnr-gowin --device, gowin_pack -d).
# nextpnr-gowin takes the part-line `GW1NR-LV9QN88PC6/I5` form;
# gowin_pack uses the family-only form `GW1N-9C` / `GW2A-18C`.
_BOARD_TO_APICULA = {
    "tang_nano_9k":         ("GW1NR-LV9QN88PC6/I5", "GW1N-9C"),
    "tang_primer_20k_dock": ("GW2A-LV18PG256C8/I7", "GW2A-18C"),
    # Tang Nano 4K, 1K, 20K could be added when needed.
}


def _select_part(board, configuration):
    bid = board.get("Id") or ""
    return _BOARD_TO_APICULA.get(bid)


def synthesize(*, dir, configuration, board, board_pinmap, toolchain, peripherals,
               top, generated_top=None, include=None, output, step="full", **_):
    """Synthesize through yosys + nextpnr-gowin + gowin_pack. Returns 0 on success."""
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
            f.write(codegen.emit_top_sv(resolved))

    info = _select_part(board, configuration)
    if info is None:
        log.error("Unrecognized Gowin board %r — extend _BOARD_TO_APICULA.", board.get("Id"))
        return 1
    nextpnr_device, gowin_pack_device = info

    sv_files = _collect_sv_sources(REPO, peripherals, top, generated_top)
    cst_path = os.path.join(output, PROJECT_NAME + ".cst")
    json_path = os.path.join(output, PROJECT_NAME + ".json")
    pack_path = os.path.join(output, PROJECT_NAME + "_pack.json")
    fs_path = os.path.join(output, PROJECT_NAME + ".fs")
    yosys_log = os.path.join(output, "yosys.log")
    nextpnr_log = os.path.join(output, "nextpnr.log")

    with open(cst_path, "w") as f:
        f.write(codegen.emit_cst(resolved))
    log.info("Wrote %s", cst_path)

    log.info("Source files (%d):", len(sv_files))
    for sv in sv_files:
        log.info("  - %s", os.path.relpath(sv, REPO) if sv.startswith(REPO) else sv)

    if os.environ.get("UNIFPGA_DRY_RUN"):
        log.info("[dry run] tools not invoked. Artifacts in %s", output)
        return 0

    yosys = _resolve_bin("yosys")
    if yosys is None:
        log.error("Could not find yosys on $PATH.")
        return 1

    # ---- yosys synth_gowin ----
    # `-D __ICARUS__`: BGM labs use `\`ifdef __ICARUS__` to gate older Verilog
    # syntax against SV-2009 `'{ … }` array-init that yosys still rejects.
    read_cmds = ['read_verilog -sv -D __ICARUS__ "{}"'.format(sv) for sv in sv_files]
    yosys_script = "; ".join(
        read_cmds
        + ['synth_gowin -top top -json "{}"'.format(json_path)]
    )
    cmd = [yosys, "-q", "-l", yosys_log, "-p", yosys_script]
    log.info("Invoking yosys synth_gowin")
    try:
        rc = subprocess.run(cmd, cwd=output).returncode
    except FileNotFoundError as exc:
        log.error("yosys invocation failed: %s", exc)
        return 1
    if rc != 0:
        log.error("yosys exited with code %d (see %s)", rc, yosys_log)
        return rc

    if step == "elaborate":
        log.info("[elaborate] yosys synth complete; skipping nextpnr/gowin_pack.")
        return 0

    # ---- nextpnr-gowin place-and-route ----
    nextpnr = _resolve_bin("nextpnr-gowin") or _resolve_bin("nextpnr-himbaechel")
    if nextpnr is None:
        log.error("Could not find nextpnr-gowin or nextpnr-himbaechel on $PATH.")
        return 1
    cmd = [nextpnr, "--device", nextpnr_device,
           "--json", json_path, "--cst", cst_path,
           "--write", pack_path,
           "-q", "-l", nextpnr_log]
    log.info("Invoking nextpnr-gowin --device %s", nextpnr_device)
    rc = subprocess.run(cmd, cwd=output).returncode
    if rc != 0:
        log.error("nextpnr-gowin exited with code %d (see %s)", rc, nextpnr_log)
        return rc

    # ---- gowin_pack bitstream ----
    gowin_pack = _resolve_bin("gowin_pack")
    if gowin_pack is None:
        log.error("Could not find gowin_pack on $PATH (pip install apycula).")
        return 1
    rc = subprocess.run([gowin_pack, "-d", gowin_pack_device,
                         "-o", fs_path, pack_path], cwd=output).returncode
    if rc != 0:
        log.error("gowin_pack exited with code %d", rc)
        return rc

    log.info("Bitstream ready: %s", fs_path)
    return 0


def program(*, board, board_pinmap=None, toolchain, output, **_):
    """Download the .fs to the connected board via openFPGALoader."""
    fs = os.path.join(output, PROJECT_NAME + ".fs")
    if not os.path.exists(fs) and not os.environ.get("UNIFPGA_DRY_RUN"):
        log.error("Bitstream not found: %s — run synthesis first", fs)
        return 1
    if os.environ.get("UNIFPGA_DRY_RUN"):
        log.info("[dry run] Would program %s", fs)
        return 0
    pgm = _resolve_bin("openFPGALoader")
    if pgm is None:
        log.error("Could not find openFPGALoader on $PATH.")
        return 1
    cmd = [pgm, "-b", "tangnano9k", fs]
    log.info("Programming via: %s", " ".join(cmd))
    rc = subprocess.run(cmd, cwd=output).returncode
    if rc != 0:
        log.error("Programming failed (exit %d). Is the board connected?", rc)
    return rc
