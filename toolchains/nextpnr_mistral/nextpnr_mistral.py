"""
nextpnr-mistral toolchain driver (yosys + nextpnr-mistral).

Pipeline:
    yosys -p "read_verilog -sv …; synth_intel_alm -family cyclonev -top top;
              write_json out.json"
    nextpnr-mistral --device <PART> --qsf in.qsf --json in.json --rbf out.rbf

The `--qsf` flag consumes the same QSF that the Quartus driver emits (only the
set_location_assignment / set_instance_assignment subset is read; the rest is
ignored), so we reuse codegen.emit_qsf directly.

Artifacts:
    <output>/top.sv             — codegen-produced top module
    <output>/unifpga_top.qsf    — codegen-produced QSF (location + IO_STANDARD)
    <output>/yosys.log, nextpnr.log
    <output>/unifpga_top.json   — yosys netlist
    <output>/unifpga_top.rbf    — final raw binary bitstream

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
_NEXTPNR_BUILD = os.path.expanduser("~/Projects/nextpnr/build")


def _resolve_bin(name):
    """Prefer ~/oss-cad-suite/bin (newer yosys with synth_intel_alm) and the
    locally built ~/Projects/nextpnr/build (where nextpnr-mistral lives, since
    it isn't shipped in oss-cad-suite) before falling back to $PATH."""
    for d in (_OSS_CAD, _NEXTPNR_BUILD):
        cand = os.path.join(d, name)
        if os.path.exists(cand) and os.access(cand, os.X_OK):
            return cand
    return shutil.which(name)


def _collect_sv_sources(repo, peripherals, user_design_top, generated_top):
    """yosys frontend: gate helpers/common by module-name match; synth_intel_alm handles globals natively, no stubs."""
    return source_set.collect_sources(
        repo, peripherals, user_design_top, generated_top,
        include_svh=False, gate_helpers=True, gate_common=True)


def _select_part(board, configuration):
    """Pull the Cyclone V model name. mistral wants the part without the
    trailing 'N' (RoHS marker), and rejects a few -prefix forms; strip both
    if present."""
    part = board.get("Part") or ""
    if not part and isinstance(board.get("Parts"), list):
        wanted = (configuration.get("part") or "").lower()
        chosen = None
        for entry in board["Parts"]:
            if wanted and entry.get("Name", "").lower() == wanted:
                chosen = entry
                break
        if chosen is None:
            chosen = board["Parts"][0]
        part = chosen.get("Part") or ""
    if part.endswith("N"):
        part = part[:-1]
    return part


def synthesize(*, dir, configuration, board, board_pinmap, toolchain, peripherals,
               top, generated_top=None, include=None, output, step="full", **_):
    """Synthesize through yosys + nextpnr-mistral. Returns 0 on success."""
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

    part = _select_part(board, configuration)
    if not part:
        log.error("No Cyclone V part on board %r", board.get("Id"))
        return 1

    sv_files = _collect_sv_sources(REPO, peripherals, top, generated_top)
    qsf_path = os.path.join(output, PROJECT_NAME + ".qsf")
    json_path = os.path.join(output, PROJECT_NAME + ".json")
    rbf_path = os.path.join(output, PROJECT_NAME + ".rbf")
    yosys_log = os.path.join(output, "yosys.log")
    nextpnr_log = os.path.join(output, "nextpnr.log")

    with open(qsf_path, "w") as f:
        f.write(codegen.emit_qsf(resolved, part))
    log.info("Wrote %s", qsf_path)

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

    # ---- yosys synth_intel_alm ----
    # `-D __ICARUS__`: the labs use `\`ifdef __ICARUS__` to gate older Verilog
    # syntax against SV-2009 `'{ … }` array-init that yosys still rejects.
    read_cmds = ['read_verilog -sv -D __ICARUS__ "{}"'.format(sv) for sv in sv_files]
    # `setundef -zero`: nextpnr-mistral refuses any IO whose output port is
    # tied to a constant — including `1'x`. Labs that drive only some bits
    # of a wider output (e.g. led[1:0] on a 10-LED board) leave the rest
    # undriven, which yosys propagates as 1'x. Tying undriven bits to zero
    # before write_json keeps the IO buffers as real registered outputs.
    yosys_script = "; ".join(
        read_cmds
        + ['hierarchy -top top',
           'proc',
           'setundef -undriven -zero',
           'synth_intel_alm -family cyclonev -top top',
           'write_json "{}"'.format(json_path)]
    )
    cmd = [yosys, "-q", "-l", yosys_log, "-p", yosys_script]
    log.info("Invoking yosys synth_intel_alm")
    try:
        rc = subprocess.run(cmd, cwd=output).returncode
    except FileNotFoundError as exc:
        log.error("yosys invocation failed: %s", exc)
        return 1
    if rc != 0:
        log.error("yosys exited with code %d (see %s)", rc, yosys_log)
        return rc

    if step == "elaborate":
        log.info("[elaborate] yosys synth complete; skipping nextpnr.")
        return 0

    # ---- nextpnr-mistral place-and-route + bitstream ----
    nextpnr = _resolve_bin("nextpnr-mistral")
    if nextpnr is None:
        log.error("Could not find nextpnr-mistral on $PATH or in ~/Projects/nextpnr/build.")
        return 1
    cmd = [nextpnr, "--device", part,
           "--qsf", qsf_path,
           "--json", json_path,
           "--rbf", rbf_path,
           "-q", "-l", nextpnr_log] + codegen.nextpnr_gui_args()
    log.info("Invoking nextpnr-mistral --device %s", part)
    rc = subprocess.run(cmd, cwd=output).returncode
    if rc != 0:
        log.error("nextpnr-mistral exited with code %d (see %s)", rc, nextpnr_log)
        return rc

    log.info("Bitstream ready: %s", rbf_path)
    return 0


def program(*, board, board_pinmap=None, toolchain, output, **_):
    """Download the .rbf to the connected board via openFPGALoader.
    Cyclone V SoC dev boards typically use USB-BlasterII; openFPGALoader
    supports it via libftdi."""
    rbf = os.path.join(output, PROJECT_NAME + ".rbf")
    if not os.path.exists(rbf) and not os.environ.get("UNIFPGA_DRY_RUN"):
        log.error("Bitstream not found: %s — run synthesis first", rbf)
        return 1
    if os.environ.get("UNIFPGA_DRY_RUN"):
        log.info("[dry run] Would program %s", rbf)
        return 0
    pgm = _resolve_bin("openFPGALoader")
    if pgm is None:
        log.error("Could not find openFPGALoader on $PATH.")
        return 1
    cmd = [pgm, "-c", "usb-blaster", rbf]
    log.info("Programming via: %s", " ".join(cmd))
    rc = subprocess.run(cmd, cwd=output).returncode
    if rc != 0:
        log.error("Programming failed (exit %d). Is the board connected?", rc)
    return rc
