"""
nextpnr-himbaechel (gatemate uarch) toolchain driver.

Pipeline:
    yosys -p "read_verilog -sv …; synth_gatemate -top top -json out.json"
    nextpnr-himbaechel --device CCGM1A1 --json in.json
                       --vopt ccf=in.ccf --vopt out=out.cfg
    gmpack out.cfg out.bit

Artifacts:
    <output>/top.sv             — codegen-produced top module
    <output>/unifpga_top.ccf    — codegen-produced CCF (Cologne Chip Constraints File)
    <output>/yosys.log, nextpnr.log
    <output>/unifpga_top.json   — yosys netlist
    <output>/unifpga_top.cfg    — nextpnr textual config (input to gmpack)
    <output>/unifpga_top.bit    — final GateMate bitstream

Build prerequisites (one-time):
  1. Build nextpnr-himbaechel with the gatemate uarch from
     ~/Projects/nextpnr (-DARCH=himbaechel -DHIMBAECHEL_UARCH=gatemate
     -DHIMBAECHEL_PEPPERCORN_PATH=~/Projects/prjpeppercorn).
  2. Build gmpack from ~/Projects/prjpeppercorn/libgm.

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
_PEPPERCORN_BUILD = os.path.expanduser("~/Projects/prjpeppercorn/libgm/build")


def _resolve_bin(name):
    """nextpnr-himbaechel and gmpack aren't shipped in oss-cad-suite, so
    we look in the local nextpnr/peppercorn build trees too."""
    for d in (_OSS_CAD, _NEXTPNR_BUILD, _PEPPERCORN_BUILD):
        cand = os.path.join(d, name)
        if os.path.exists(cand) and os.access(cand, os.X_OK):
            return cand
    return shutil.which(name)


def _collect_sv_sources(repo, peripherals, user_design_top, generated_top):
    """yosys frontend: gate helpers/common by module-name match; synth_gatemate has CC_BUFG natively, no stubs."""
    return source_set.collect_sources(
        repo, peripherals, user_design_top, generated_top,
        include_svh=False, gate_helpers=True, gate_common=True, compat_stubs=False)


# Map our boards.yml board id to (nextpnr-himbaechel --device).
# CCGM1A1 = single-die  CCGM1 (40k ALU eq.)
# CCGM1A2 = dual-die    CCGM1 (80k ALU eq.)
_BOARD_TO_GATEMATE = {
    "gatemate_evb_a1":    "CCGM1A1",
    "olimex_gatemateevb": "CCGM1A1",
}


def _select_part(board, configuration):
    bid = board.get("Id") or ""
    return _BOARD_TO_GATEMATE.get(bid)


def synthesize(*, dir, configuration, board, board_pinmap, toolchain, peripherals,
               top, generated_top=None, include=None, output, step="full", **_):
    """Synthesize through yosys + nextpnr-himbaechel + gmpack. Returns 0 on success."""
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
        log.error("Unrecognized GateMate board %r — extend _BOARD_TO_GATEMATE.", board.get("Id"))
        return 1

    sv_files = _collect_sv_sources(REPO, peripherals, top, generated_top)
    ccf_path = os.path.join(output, PROJECT_NAME + ".ccf")
    json_path = os.path.join(output, PROJECT_NAME + ".json")
    cfg_path = os.path.join(output, PROJECT_NAME + ".cfg")
    bit_path = os.path.join(output, PROJECT_NAME + ".bit")
    yosys_log = os.path.join(output, "yosys.log")
    nextpnr_log = os.path.join(output, "nextpnr.log")

    with open(ccf_path, "w") as f:
        f.write(codegen.emit_ccf(resolved))
    log.info("Wrote %s", ccf_path)

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

    # ---- yosys synth_gatemate ----
    # `-D __ICARUS__`: BGM labs use `\`ifdef __ICARUS__` to gate older Verilog
    # syntax against SV-2009 `'{ … }` array-init that yosys still rejects.
    read_cmds = ['read_verilog -sv -D __ICARUS__ "{}"'.format(sv) for sv in sv_files]
    # `setundef -undriven -zero`: same fix as nextpnr-mistral — undriven IO
    # bits propagate as 1'x and nextpnr-himbaechel rejects constant-driven IOs.
    yosys_script = "; ".join(
        read_cmds
        + ['hierarchy -top top',
           'proc',
           'setundef -undriven -zero',
           'synth_gatemate -top top -json "{}"'.format(json_path)]
    )
    cmd = [yosys, "-q", "-l", yosys_log, "-p", yosys_script]
    log.info("Invoking yosys synth_gatemate")
    try:
        rc = subprocess.run(cmd, cwd=output).returncode
    except FileNotFoundError as exc:
        log.error("yosys invocation failed: %s", exc)
        return 1
    if rc != 0:
        log.error("yosys exited with code %d (see %s)", rc, yosys_log)
        return rc

    if step == "elaborate":
        log.info("[elaborate] yosys synth complete; skipping nextpnr/gmpack.")
        return 0

    # ---- nextpnr-himbaechel place-and-route ----
    nextpnr = _resolve_bin("nextpnr-himbaechel")
    if nextpnr is None:
        log.error("Could not find nextpnr-himbaechel — build it from "
                  "~/Projects/nextpnr (-DARCH=himbaechel -DHIMBAECHEL_UARCH=gatemate).")
        return 1
    cmd = [nextpnr, "--device", device,
           "--json", json_path,
           "--vopt", "ccf={}".format(ccf_path),
           "--vopt", "out={}".format(cfg_path),
           "-q", "-l", nextpnr_log]
    log.info("Invoking nextpnr-himbaechel --device %s", device)
    rc = subprocess.run(cmd, cwd=output).returncode
    if rc != 0:
        log.error("nextpnr-himbaechel exited with code %d (see %s)", rc, nextpnr_log)
        return rc

    # ---- gmpack bitstream ----
    gmpack = _resolve_bin("gmpack")
    if gmpack is None:
        log.error("Could not find gmpack — build it from ~/Projects/prjpeppercorn/libgm.")
        return 1
    rc = subprocess.run([gmpack, cfg_path, bit_path], cwd=output).returncode
    if rc != 0:
        log.error("gmpack exited with code %d", rc)
        return rc

    log.info("Bitstream ready: %s", bit_path)
    return 0


def program(*, board, board_pinmap=None, toolchain, output, **_):
    """Download the .bit to the connected GateMate EVB via openFPGALoader.
    The GateMate EVB-A1 enumerates as a generic FT2232 JTAG; openFPGALoader
    has a `gatemate_evb_jtag` cable profile."""
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
    cmd = [pgm, "-c", "gatemate_evb_jtag", bit]
    log.info("Programming via: %s", " ".join(cmd))
    rc = subprocess.run(cmd, cwd=output).returncode
    if rc != 0:
        log.error("Programming failed (exit %d). Is the board connected?", rc)
    return rc
