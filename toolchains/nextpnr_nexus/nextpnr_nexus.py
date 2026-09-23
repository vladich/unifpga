"""
nextpnr-nexus toolchain driver (yosys + nextpnr-nexus + prjoxide pack).

Pipeline:
    yosys -p "read_verilog -sv …; synth_nexus -family lifcl
              -top top -json out.json"
    nextpnr-nexus --device <PART> --json in.json
                  --pdc in.pdc --fasm out.fasm
    prjoxide pack out.fasm out.bit

Artifacts:
    <output>/top.sv             — codegen-produced top module
    <output>/unifpga_top.pdc    — codegen-produced PDC (Physical Design Constraints)
    <output>/yosys.log, nextpnr.log
    <output>/unifpga_top.json   — yosys netlist
    <output>/unifpga_top.fasm   — nextpnr placed-and-routed FASM
    <output>/unifpga_top.bit    — final Nexus bitstream

Both nextpnr-nexus and prjoxide ship in oss-cad-suite; no extra build steps.

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
    """Prefer ~/oss-cad-suite/bin (where nextpnr-nexus and prjoxide live)
    before falling back to $PATH."""
    cand = os.path.join(_OSS_CAD, name)
    if os.path.exists(cand) and os.access(cand, os.X_OK):
        return cand
    return shutil.which(name)


def _collect_sv_sources(repo, peripherals, user_design_top, generated_top):
    """yosys frontend: gate helpers/common by module-name match; synth_nexus is native, no compat stubs."""
    return source_set.collect_sources(
        repo, peripherals, user_design_top, generated_top,
        include_svh=False, gate_helpers=True, gate_common=True, compat_stubs=False)


# Map our boards.yml board id to nextpnr-nexus --device.
_BOARD_TO_NEXUS = {
    "lattice_crosslink_nx_evn": "LIFCL-40-9BG400C",
    "lattice_crosslink_nx_vip": "LIFCL-40-9BG400C",
}


def _select_part(board, configuration):
    bid = board.get("Id") or ""
    if bid in _BOARD_TO_NEXUS:
        return _BOARD_TO_NEXUS[bid]
    # Fallback: use the board's Part field as-is if it looks like a Nexus part.
    part = board.get("Part") or ""
    if part.startswith(("LIFCL-", "LFD2NX-")):
        return part
    return None


def _yosys_synth_family(part):
    """LIFCL = CrossLink-NX / Certus-NX; LFD2NX = CertusPro-NX."""
    if part.startswith("LFD2NX"):
        return "lfd2nx"
    return "lifcl"


def synthesize(*, dir, configuration, board, board_pinmap, toolchain, peripherals,
               top, generated_top=None, include=None, output, step="full", **_):
    """Synthesize through yosys + nextpnr-nexus + prjoxide pack. Returns 0 on success."""
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

    device = _select_part(board, configuration)
    if device is None:
        log.error("Unrecognized Nexus board %r — extend _BOARD_TO_NEXUS.", board.get("Id"))
        return 1

    family = _yosys_synth_family(device)

    sv_files = _collect_sv_sources(REPO, peripherals, top, generated_top)
    pdc_path = os.path.join(output, PROJECT_NAME + ".pdc")
    json_path = os.path.join(output, PROJECT_NAME + ".json")
    fasm_path = os.path.join(output, PROJECT_NAME + ".fasm")
    bit_path = os.path.join(output, PROJECT_NAME + ".bit")
    yosys_log = os.path.join(output, "yosys.log")
    nextpnr_log = os.path.join(output, "nextpnr.log")

    with open(pdc_path, "w") as f:
        f.write(codegen.emit_pdc(resolved))
    log.info("Wrote %s", pdc_path)

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

    # ---- yosys synth_nexus ----
    # `-D __ICARUS__`: the designs use `\`ifdef __ICARUS__` to gate older
    # Verilog syntax against SV-2009 `'{ … }` array-init that yosys still
    # rejects.
    # `setundef -undriven -zero`: same fix as nextpnr-mistral / gatemate —
    # nextpnr rejects IO with `'x` constants.
    read_cmds = ['read_verilog -sv -D __ICARUS__ "{}"'.format(sv) for sv in sv_files]
    yosys_script = "; ".join(
        read_cmds
        + ['hierarchy -top top',
           'proc',
           'setundef -undriven -zero',
           'synth_nexus -family {} -top top -json "{}"'.format(family, json_path)]
    )
    cmd = [yosys, "-q", "-l", yosys_log, "-p", yosys_script]
    log.info("Invoking yosys synth_nexus -family %s", family)
    try:
        rc = subprocess.run(cmd, cwd=output).returncode
    except FileNotFoundError as exc:
        log.error("yosys invocation failed: %s", exc)
        return 1
    if rc != 0:
        log.error("yosys exited with code %d (see %s)", rc, yosys_log)
        return rc

    if step == "elaborate":
        log.info("[elaborate] yosys synth complete; skipping nextpnr/prjoxide.")
        return 0

    # ---- nextpnr-nexus place-and-route ----
    nextpnr = _resolve_bin("nextpnr-nexus")
    if nextpnr is None:
        log.error("Could not find nextpnr-nexus on $PATH.")
        return 1
    cmd = [nextpnr, "--device", device,
           "--json", json_path,
           "--pdc", pdc_path,
           "--fasm", fasm_path,
           "-q", "-l", nextpnr_log] + codegen.nextpnr_gui_args()
    log.info("Invoking nextpnr-nexus --device %s", device)
    rc = subprocess.run(cmd, cwd=output).returncode
    if rc != 0:
        log.error("nextpnr-nexus exited with code %d (see %s)", rc, nextpnr_log)
        return rc

    # ---- prjoxide pack bitstream ----
    prjoxide = _resolve_bin("prjoxide")
    if prjoxide is None:
        log.error("Could not find prjoxide on $PATH.")
        return 1
    rc = subprocess.run([prjoxide, "pack", fasm_path, bit_path], cwd=output).returncode
    if rc != 0:
        log.error("prjoxide pack exited with code %d", rc)
        return rc

    log.info("Bitstream ready: %s", bit_path)
    return 0


def program(*, board, board_pinmap=None, toolchain, output, **_):
    """Download the .bit to the connected board via openFPGALoader.
    The CrossLink-NX EVN enumerates as an FT4232H — openFPGALoader has a
    `crosslink-nx-evn` cable profile."""
    bit = os.path.join(output, PROJECT_NAME + ".bit")
    if not os.path.exists(bit) and not os.environ.get("UNIFPGA_DRY_RUN"):
        log.error("Bitstream not found: %s — run synthesis first", bit)
        return 1
    if os.environ.get("UNIFPGA_DRY_RUN"):
        log.info("[dry run] Would program %s", bit)
        return 0
    pgm = _resolve_bin("openFPGALoader")
    if pgm is None:
        log.error("Could not find openFPGALoader on $PATH.")
        return 1
    cmd = [pgm, "-c", "crosslink-nx-evn", bit]
    log.info("Programming via: %s", " ".join(cmd))
    rc = subprocess.run(cmd, cwd=output).returncode
    if rc != 0:
        log.error("Programming failed (exit %d). Is the board connected?", rc)
    return rc
