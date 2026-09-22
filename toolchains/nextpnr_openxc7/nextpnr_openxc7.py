"""
nextpnr-openxc7 toolchain driver — open-source flow for Xilinx 7-series
(Artix 7, Kintex 7, Spartan 7, Zynq 7000) via yosys + nextpnr-xilinx +
fasm2bit.

Pipeline:
    yosys -p "read_verilog -sv …; synth_xilinx -family xc7 -top top -json out.json"
    nextpnr-xilinx --chipdb <part>.bin --xdc in.xdc --json in.json --fasm out.fasm
    fasm2frames out.fasm > out.frames
    xc7frames2bit --part_file part.yaml --frm_file out.frames --output_file out.bit

For `--step elaborate`, runs yosys synth_xilinx only — fastest equivalent of
Vivado's `synth_design -rtl`.

Chipdbs are big (~90 MB per part) and slow to build (~10 min from prjxray-db
data). The driver caches them under ~/.cache/openxc7/<part>.bin and rebuilds
on demand. To pre-warm the cache, run:

    python3 -m toolchains.nextpnr_openxc7.nextpnr_openxc7 prebuild xc7a35tcsg324-1

Reuses the existing emit_xdc() — same XDC format Vivado reads.
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

# openxc7 snap layout
SNAP_OPENXC7 = "/snap/openxc7/current/opt/nextpnr-xilinx"
PRJXRAY_DB = SNAP_OPENXC7 + "/external/prjxray-db"
BBAEXPORT = SNAP_OPENXC7 + "/python/bbaexport.py"
CHIPDB_CACHE = os.path.expanduser("~/.cache/openxc7")

# Snap-bundled bitstream tools.
FASM2FRAMES = "openxc7.fasm2frames"
XC7FRAMES2BIT = "openxc7.xc7frames2bit"

# Map Xilinx part prefixes to prjxray-db family directory names.
_PART_TO_FAMILY = [
    ("xc7a", "artix7"),
    ("xc7k", "kintex7"),
    ("xc7s", "spartan7"),
    ("xc7z", "zynq7"),
]


_OSS_CAD = os.path.expanduser("~/oss-cad-suite/bin")


def _resolve_bin(name):
    """Prefer ~/oss-cad-suite/bin (newer yosys 0.41+ which accepts SV-2009
    multi-dim packed arrays). Falls back to $PATH so snap-bundled tools
    (openxc7.fasm2frames, openxc7.xc7frames2bit) still resolve."""
    cand = os.path.join(_OSS_CAD, name)
    if os.path.exists(cand) and os.access(cand, os.X_OK):
        return cand
    return shutil.which(name)


def _normalize_part(part):
    """Strip Vivado's optional package/temperature/voltage suffix to the
    bbaexport-acceptable form. Examples:
        xc7a35ticsg324-1l  -> xc7a35tcsg324-1
        xc7a100tcsg324-1   -> xc7a100tcsg324-1
        XC7A15T-CPG236I    -> xc7a15tcpg236-1   (best-effort)
    """
    p = part.lower()
    # Remove industrial 'i' before package designator (xc7a35ticsg → xc7a35tcsg)
    p = p.replace("ticsg", "tcsg").replace("ticpg", "tcpg").replace("tifgg", "tfgg").replace("tiffg", "tffg")
    # Strip trailing low-power 'l'
    if p.endswith("l"):
        p = p[:-1]
    # Some boards.yml entries use 'XC7A15T-CPG236I' style — normalize.
    if "-" in p:
        body, _, tail = p.partition("-")
        # Strip any trailing 'i' (industrial) on the speed grade.
        tail = tail.rstrip("i")
        # If tail looks like a package (cpg236) rather than speed grade, force -1.
        if tail and not tail.isdigit():
            p = body + tail.lower() + "-1"
        else:
            p = body + "-" + (tail or "1")
    elif "csg" not in p and "cpg" not in p and "fgg" not in p and "ffg" not in p:
        p = p + "-1"
    return p


def _family_for(part):
    p = part.lower()
    for prefix, fam in _PART_TO_FAMILY:
        if p.startswith(prefix):
            return fam
    return None


def _ensure_chipdb(part):
    """Build/cache a chipdb for `part` (e.g. 'xc7a35tcsg324-1'). Returns the
    path to the .bin or None on error."""
    bin_path = os.path.join(CHIPDB_CACHE, part + ".bin")
    if os.path.exists(bin_path):
        return bin_path

    family = _family_for(part)
    if family is None:
        log.error("openxc7: cannot derive family for part %r", part)
        return None

    os.makedirs(CHIPDB_CACHE, exist_ok=True)
    bba_path = os.path.join(CHIPDB_CACHE, part + ".bba")
    log.info("openxc7: building chipdb for %s (one-time, ~10 min)", part)
    env = dict(os.environ)
    env["PYTHONPATH"] = (SNAP_OPENXC7 + "/python:" + PRJXRAY_DB
                         + ":" + env.get("PYTHONPATH", ""))
    env["XRAY_DATABASE_DIR"] = PRJXRAY_DB
    env["XRAY_DATABASE"] = family

    rc = subprocess.run(
        ["python3", BBAEXPORT, "--device", part, "--bba", bba_path],
        env=env).returncode
    if rc != 0:
        log.error("bbaexport failed for %s (rc=%d)", part, rc)
        return None

    bbasm = _resolve_bin("openxc7.bbasm") or _resolve_bin("bbasm")
    if bbasm is None:
        log.error("could not find bbasm on $PATH (snap install openxc7?)")
        return None
    rc = subprocess.run([bbasm, "-l", bba_path, bin_path]).returncode
    try:
        os.remove(bba_path)   # discard the 265 MB intermediate
    except OSError:
        pass
    if rc != 0:
        return None
    log.info("openxc7: chipdb cached at %s", bin_path)
    return bin_path


def _collect_sv_sources(repo, peripherals, user_design_top, generated_top):
    """yosys frontend: gate helpers/common by module-name match; synth_xilinx has BUFG natively, no stubs."""
    return source_set.collect_sources(
        repo, peripherals, user_design_top, generated_top,
        include_svh=False, gate_helpers=True, gate_common=True, compat_stubs=False)


def _select_part(board, configuration):
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
    return part


def synthesize(*, dir, configuration, board, board_pinmap, toolchain, peripherals,
               top, generated_top=None, include=None, output, step="full", **_):
    """Synthesize through yosys + nextpnr-xilinx. Returns 0 on success."""
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

    raw_part = _select_part(board, configuration)
    if not raw_part:
        log.error("Board %s has no 'Part' field — cannot drive openxc7.", board["Id"])
        return 1
    part = _normalize_part(raw_part)

    sv_files = _collect_sv_sources(REPO, peripherals, top, generated_top)
    xdc_path = os.path.join(output, PROJECT_NAME + ".xdc")
    json_path = os.path.join(output, PROJECT_NAME + ".json")
    fasm_path = os.path.join(output, PROJECT_NAME + ".fasm")
    yosys_log = os.path.join(output, "yosys.log")
    nextpnr_log = os.path.join(output, "nextpnr.log")

    with open(xdc_path, "w") as f:
        # nextpnr-xilinx wants the simple 4-arg set_property form, not Vivado's
        # `-dict { … }` shorthand.
        f.write(codegen.emit_xdc_simple(resolved))
    log.info("Wrote %s", xdc_path)

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

    # ---- yosys synth_xilinx ----
    # `-D __ICARUS__`: BGM labs use `\`ifdef __ICARUS__` to gate older Verilog
    # syntax against SV-2009 `'{ … }` array-init that yosys still rejects.
    read_cmds = ['read_verilog -sv -D __ICARUS__ "{}"'.format(sv) for sv in sv_files]
    # synth_xilinx in yosys 0.36 doesn't take -json; emit via write_json.
    yosys_script = "; ".join(
        read_cmds
        + ['synth_xilinx -flatten -family xc7 -top top',
           'write_json "{}"'.format(json_path)]
    )
    cmd = [yosys, "-q", "-l", yosys_log, "-p", yosys_script]
    log.info("Invoking yosys synth_xilinx")
    rc = subprocess.run(cmd, cwd=output).returncode
    if rc != 0:
        log.error("yosys exited with code %d (see %s)", rc, yosys_log)
        return rc

    if step == "elaborate":
        log.info("[elaborate] yosys synth complete; skipping nextpnr/fasm.")
        return 0

    # ---- nextpnr place-and-route ----
    chipdb = _ensure_chipdb(part)
    if chipdb is None:
        return 1
    nextpnr = _resolve_bin("nextpnr-xilinx") or _resolve_bin("openxc7.nextpnr-xilinx")
    if nextpnr is None:
        log.error("Could not find nextpnr-xilinx on $PATH.")
        return 1
    cmd = [nextpnr, "--chipdb", chipdb,
           "--xdc", xdc_path, "--json", json_path,
           "--fasm", fasm_path, "-q", "-l", nextpnr_log]
    log.info("Invoking nextpnr-xilinx --chipdb %s", part + ".bin")
    rc = subprocess.run(cmd, cwd=output).returncode
    if rc != 0:
        log.error("nextpnr-xilinx exited with code %d (see %s)", rc, nextpnr_log)
        return rc

    if step == "pnr":
        log.info("[pnr] FASM ready: %s (skipping bitstream packing)", fasm_path)
        return 0

    # ---- fasm2frames + xc7frames2bit (bitstream) ----
    fasm2frames = _resolve_bin(FASM2FRAMES)
    xc7frames2bit = _resolve_bin(XC7FRAMES2BIT)
    if fasm2frames is None or xc7frames2bit is None:
        log.error("openxc7 bitstream tools missing (need %s + %s).",
                  FASM2FRAMES, XC7FRAMES2BIT)
        return 1
    family = _family_for(part) or "artix7"
    db_root = os.path.join(PRJXRAY_DB, family)
    part_yaml = os.path.join(db_root, part, "part.yaml")
    if not os.path.exists(part_yaml):
        log.error("Missing part.yaml: %s", part_yaml)
        return 1

    frames_path = os.path.join(output, PROJECT_NAME + ".frames")
    bit_path = os.path.join(output, PROJECT_NAME + ".bit")

    log.info("Invoking fasm2frames")
    rc = subprocess.run(
        [fasm2frames, "--db-root", db_root, "--part", part, fasm_path, frames_path],
        cwd=output).returncode
    if rc != 0:
        log.error("fasm2frames exited with code %d", rc)
        return rc

    log.info("Invoking xc7frames2bit")
    rc = subprocess.run(
        [xc7frames2bit, "--part_file", part_yaml, "--part_name", part,
         "--frm_file", frames_path, "--output_file", bit_path],
        cwd=output).returncode
    if rc != 0:
        log.error("xc7frames2bit exited with code %d", rc)
        return rc

    log.info("Bitstream ready: %s", bit_path)
    return 0


def program(*, board, board_pinmap=None, toolchain, output, **_):
    """Download the .bit to the connected board via openFPGALoader."""
    bit = os.path.join(output, PROJECT_NAME + ".bit")
    if not os.path.exists(bit) and not os.environ.get("UNIFPGA_DRY_RUN"):
        log.error("Bitstream not found: %s — run synthesis first", bit)
        return 1
    if os.environ.get("UNIFPGA_DRY_RUN"):
        log.info("[dry run] Would program %s", bit)
        return 0
    pgm = _resolve_bin("openFPGALoader")
    if pgm is None:
        log.error("Could not find openFPGALoader on $PATH (try ~/oss-cad-suite/).")
        return 1
    cmd = [pgm, bit]
    log.info("Programming via: %s", " ".join(cmd))
    rc = subprocess.run(cmd, cwd=output).returncode
    if rc != 0:
        log.error("Programming failed (exit %d). Is the board connected?", rc)
    return rc
