"""
nextpnr-icestorm toolchain driver (yosys + nextpnr-ice40 + icepack).

Pipeline:
    yosys -p "read_verilog -sv …; synth_ice40 -top top -json out.json"
    nextpnr-ice40 --json in.json --pcf in.pcf --asc out.asc --<chip>
    icepack out.asc out.bin

Artifacts:
    <output>/top.sv             — codegen-produced top module
    <output>/unifpga_top.pcf    — codegen-produced PCF
    <output>/yosys.log          — yosys log
    <output>/nextpnr.log        — nextpnr log
    <output>/unifpga_top.json   — yosys netlist
    <output>/unifpga_top.asc    — nextpnr placed-and-routed ascii bitstream
    <output>/unifpga_top.bin    — final bitstream (icepack output)

Set $UNIFPGA_DRY_RUN=1 to generate every artifact without running tools.
"""

import logging
import os
import re
import shutil
import subprocess

from tools import codegen


log = logging.getLogger(__name__)
REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.realpath(__file__))))

PROJECT_NAME = "unifpga_top"


_OSS_CAD = os.path.expanduser("~/oss-cad-suite/bin")


def _resolve_bin(name):
    """Prefer ~/oss-cad-suite/bin (newer yosys 0.41+ which accepts SV-2009
    multi-dim packed arrays — fixes most of our SV-feature gap fails)
    before falling back to $PATH."""
    cand = os.path.join(_OSS_CAD, name)
    if os.path.exists(cand) and os.access(cand, os.X_OK):
        return cand
    return shutil.which(name)


def _collect_sv_sources(repo, peripherals, user_design_top, generated_top):
    """Same logic as the other drivers — keep symmetrical."""
    files = [generated_top, os.path.abspath(user_design_top)]
    seen = {os.path.abspath(p) for p in files}

    design_dir = os.path.dirname(os.path.abspath(user_design_top))
    if os.path.isdir(design_dir):
        for root, _dirs, names in os.walk(design_dir):
            for name in sorted(names):
                # Exclude .vh/.svh — included via `\`include`, not compiled standalone.
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

    # Helpers — include only when the generated top.sv references the
    # module name. yosys 0.36 doesn't accept SV-2009 multi-dim packed
    # arrays in port declarations (e.g. tm1638_registers.sv), so always
    # adding them breaks every design on iCE40 even when unused.
    try:
        with open(generated_top) as f:
            top_text = f.read()
    except Exception:
        top_text = ""
    helper_modules = {
        "tm1638_registers.sv":         ("tm1638_registers", "tm1638_board_controller"),
        "slow_clk_gen.sv":             ("slow_clk_gen",),
        "imitate_reset_on_power_up.sv": ("imitate_reset_on_power_up",),
    }
    for helper, modules in helper_modules.items():
        full = os.path.join(repo, "rtl", "peripherals", helper)
        if not os.path.exists(full) or full in seen:
            continue
        if any(m in top_text for m in modules):
            files.append(full)
            seen.add(full)

    # designs_common helpers — same gating as above. Include only files whose
    # module name appears in top.sv or in any sibling SV the design pulls in.
    # Some helpers use SV-2009 features yosys 0.36 rejects (multi-dim packed
    # arrays), so unconditionally adding them breaks unrelated designs.
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
            module_name = name[:-3]   # strip .sv
            if module_name not in sibling_text:
                continue
            full = os.path.join(designs_common_dir, name)
            if full not in seen:
                files.append(full)
                seen.add(full)

    # iCE40 doesn't have BUFG; provide stubs as the Quartus driver does.
    compat_dir = os.path.join(repo, "rtl", "peripherals", "_quartus_compat")
    if os.path.isdir(compat_dir):
        for name in sorted(os.listdir(compat_dir)):
            if not name.endswith(".sv"):
                continue
            full = os.path.join(compat_dir, name)
            if full not in seen:
                files.append(full)
                seen.add(full)

    # Clock-tree wrappers (rtl/pll) the generated top instantiates and the
    # drivers' extra `files:` (P3.1 / P3.2).
    for full in codegen.pll_source_paths(repo, generated_top, peripherals):
        if full not in seen:
            files.append(full)
            seen.add(full)

    return files


_PART_TO_NEXTPNR = {
    # iCE40 LP/HX/UP family identifiers used by nextpnr-ice40's --<chip> flag.
    "iCE40UP5K-SG48": ("up5k", "sg48"),
    "iCE40HX8K-CT256": ("hx8k", "ct256"),
    "iCE40HX8K-CB132": ("hx8k", "cb132"),
    "iCE40HX1K-VQ100": ("hx1k", "vq100"),
    "iCE40LP1K-QN84":  ("lp1k", "qn84"),
}


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


def _nextpnr_chip_args(part):
    """Map our boards.yml part string to nextpnr-ice40's command-line flags.
    Returns a list like ['--up5k', '--package', 'sg48']."""
    info = _PART_TO_NEXTPNR.get(part)
    if info is None:
        # Best-effort: try to parse iCE40<family>-<package> from the part name.
        m = re.match(r"^iCE40(LP|HX|UP)(\d+K)-(\w+)$", part)
        if m:
            family = (m.group(1) + m.group(2)).lower()
            pkg = m.group(3).lower()
            return ["--{}".format(family), "--package", pkg]
        return None
    family, pkg = info
    return ["--{}".format(family), "--package", pkg]


def _yosys_synth_family(part):
    """yosys synth target for the given iCE40 part. UP5K uses UltraPlus."""
    if part.startswith("iCE40UP"):
        return "synth_ice40 -device u"
    return "synth_ice40"


def synthesize(*, dir, configuration, board, board_pinmap, toolchain, peripherals,
               top, generated_top=None, include=None, output, step="full", **_):
    """Synthesize through yosys + nextpnr-ice40 + icepack. Returns 0 on success."""
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

    part = _select_part(board, configuration)
    chip_args = _nextpnr_chip_args(part)
    if chip_args is None:
        log.error("Unrecognized iCE40 part %r — extend _PART_TO_NEXTPNR.", part)
        return 1

    sv_files = _collect_sv_sources(REPO, peripherals, top, generated_top)
    pcf_path = os.path.join(output, PROJECT_NAME + ".pcf")
    json_path = os.path.join(output, PROJECT_NAME + ".json")
    asc_path = os.path.join(output, PROJECT_NAME + ".asc")
    bin_path = os.path.join(output, PROJECT_NAME + ".bin")
    yosys_log = os.path.join(output, "yosys.log")
    nextpnr_log = os.path.join(output, "nextpnr.log")

    with open(pcf_path, "w") as f:
        f.write(codegen.emit_pcf(resolved))
    log.info("Wrote %s", pcf_path)

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

    # ---- yosys synth ----
    # `-D __ICARUS__`: BGM labs use `\`ifdef __ICARUS__` to gate older Verilog
    # syntax against SV-2009 `'{ … }` array-init that yosys still rejects.
    # Telling yosys it's "Icarus" picks the older-syntax branch.
    read_cmds = ['read_verilog -sv -D __ICARUS__ "{}"'.format(sv) for sv in sv_files]
    synth_cmd = _yosys_synth_family(part)
    yosys_script = "; ".join(
        read_cmds
        + ['{} -top top -json "{}"'.format(synth_cmd, json_path)]
    )
    cmd = [yosys, "-q", "-l", yosys_log, "-p", yosys_script]
    log.info("Invoking yosys")
    try:
        rc = subprocess.run(cmd, cwd=output).returncode
    except FileNotFoundError as exc:
        log.error("yosys invocation failed: %s", exc)
        return 1
    if rc != 0:
        log.error("yosys exited with code %d (see %s)", rc, yosys_log)
        return rc

    if step == "elaborate":
        # Synthesis-only: skip place-and-route + bitstream. Closest to
        # Vivado's `synth_design -rtl` for sweep purposes.
        log.info("[elaborate] yosys synth complete; skipping nextpnr/icepack.")
        return 0

    # ---- nextpnr place-and-route ----
    nextpnr = _resolve_bin("nextpnr-ice40")
    if nextpnr is None:
        log.error("Could not find nextpnr-ice40 on $PATH.")
        return 1
    cmd = [nextpnr] + chip_args + ["--json", json_path, "--pcf", pcf_path,
                                    "--asc", asc_path, "-q",
                                    "-l", nextpnr_log]
    log.info("Invoking nextpnr-ice40 %s", " ".join(chip_args))
    rc = subprocess.run(cmd, cwd=output).returncode
    if rc != 0:
        log.error("nextpnr-ice40 exited with code %d (see %s)", rc, nextpnr_log)
        return rc

    # ---- icepack bitstream ----
    icepack = _resolve_bin("icepack")
    if icepack is None:
        log.error("Could not find icepack on $PATH.")
        return 1
    rc = subprocess.run([icepack, asc_path, bin_path], cwd=output).returncode
    if rc != 0:
        log.error("icepack exited with code %d", rc)
        return rc

    log.info("Bitstream ready: %s", bin_path)
    return 0


def program(*, board, board_pinmap=None, toolchain, output, **_):
    """Download the .bin to the connected board over USB via iceprog."""
    bit = os.path.join(output, PROJECT_NAME + ".bin")
    if not os.path.exists(bit) and not os.environ.get("UNIFPGA_DRY_RUN"):
        log.error("Bitstream not found: %s — run synthesis first", bit)
        return 1
    if os.environ.get("UNIFPGA_DRY_RUN"):
        log.info("[dry run] Would program %s", bit)
        return 0
    iceprog = _resolve_bin("iceprog")
    if iceprog is None:
        log.error("Could not find iceprog on $PATH.")
        return 1
    cmd = [iceprog, bit]
    log.info("Programming via: %s", " ".join(cmd))
    rc = subprocess.run(cmd, cwd=output).returncode
    if rc != 0:
        log.error("Programming failed (exit %d). Is the board connected?", rc)
    return rc
