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
from tools import source_set


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
    """yosys frontend: gate helpers/common by module-name match; synth_gowin has BUFG natively (stubs would redefine it)."""
    return source_set.collect_sources(
        repo, peripherals, user_design_top, generated_top,
        include_svh=False, gate_helpers=True, gate_common=True, compat_stubs=False)


# Map our boards.yml board id to (nextpnr-gowin --device, gowin_pack -d).
# nextpnr-gowin takes the part-line `GW1NR-LV9QN88PC6/I5` form;
# gowin_pack uses the family-only form `GW1N-9C` / `GW2A-18C`.


def _himbaechel_has_gowin(binary):
    """Does this nextpnr-himbaechel include the gowin uarch (`--list-uarch`)?"""
    try:
        out = subprocess.run([binary, "--list-uarch"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             universal_newlines=True, timeout=30).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return "gowin" in out.lower()


def _select_part(board, pinmap):
    """(nextpnr device, apicula family): the pinmap's
    `toolchain_options.yosys.device_part` (else the board's part) and
    `device_family`; None without a family."""
    yo = codegen.yosys_loader_settings(pinmap)
    family = yo.get("device_family")
    return (yo.get("device_part") or board.get("Part"), family) if family else None


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
            f.write(codegen.emit_top_sv(resolved, design=top))

    info = _select_part(board, board_pinmap)
    if info is None:
        log.error("Board %r: its pinmap gives no toolchain_options.yosys.device_family (the apicula "
                  "family, GW1N-9C ...)", board.get("Id"))
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
    # `-D __ICARUS__`: the labs use `\`ifdef __ICARUS__` to gate older Verilog
    # syntax against SV-2009 `'{ … }` array-init that yosys still rejects.
    read_cmds = ['read_verilog -sv -D __ICARUS__ "{}"'.format(sv) for sv in sv_files]
    yosys_script = "; ".join(
        read_cmds
        + ['{} -top top -json "{}"'.format(" ".join(["synth_gowin"] + codegen.yosys_synth_options(board_pinmap)), json_path)]
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

    # ---- place-and-route: the canonical command is nextpnr-himbaechel with
    # the gowin uarch (`--device $DEVICE_PART --vopt family=$DEVICE_FAMILY
    # --vopt cst=...`); a build without that uarch (mercury's oss-cad-suite)
    # keeps the legacy nextpnr-gowin ----
    yo = codegen.yosys_loader_settings(board_pinmap)
    himbaechel = _resolve_bin("nextpnr-himbaechel")
    if himbaechel is not None and not _himbaechel_has_gowin(himbaechel):
        himbaechel = None
    nextpnr = himbaechel or _resolve_bin("nextpnr-gowin")
    if nextpnr is None:
        log.error("Could not find nextpnr-himbaechel (gowin uarch) or nextpnr-gowin on $PATH.")
        return 1
    if himbaechel:
        family = yo.get("device_family") or gowin_pack_device
        cmd = [nextpnr, "--json", json_path, "--write", pack_path,
               "--device", yo.get("device_part") or nextpnr_device,
               "--vopt", "family=" + str(family), "--vopt", "cst=" + cst_path,
               "-q", "-l", nextpnr_log] + codegen.nextpnr_gui_args()
        log.info("Invoking nextpnr-himbaechel --device %s --vopt family=%s", yo.get("device_part") or nextpnr_device, family)
    else:
        cmd = [nextpnr, "--device", nextpnr_device,
               "--json", json_path, "--cst", cst_path,
               "--write", pack_path,
               "-q", "-l", nextpnr_log] + codegen.nextpnr_gui_args()
        log.info("Invoking nextpnr-gowin --device %s", nextpnr_device)
    rc = subprocess.run(cmd, cwd=output).returncode
    if rc != 0:
        log.error("%s exited with code %d (see %s)", os.path.basename(nextpnr), rc, nextpnr_log)
        return rc
    gowin_pack_device = yo.get("device_pack") or gowin_pack_device

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
    cmd = [pgm] + (codegen.openfpgaloader_args(board_pinmap)) + [fs]
    log.info("Programming via: %s", " ".join(cmd))
    rc = subprocess.run(cmd, cwd=output).returncode
    if rc != 0:
        log.error("Programming failed (exit %d). Is the board connected?", rc)
    return rc
