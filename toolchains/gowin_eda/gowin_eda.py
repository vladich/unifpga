"""
Gowin EDA toolchain driver.

Drives `gw_sh` (Gowin Tcl batch shell) to synthesize, place-and-route, and
emit a bitstream for Gowin LittleBee (GW1N), Arora (GW2A), Mega (GW5A), and
Primer (GW1NS) parts. Artifacts:

    <output>/top.sv             — codegen-produced top module
    <output>/unifpga_top.cst    — codegen-produced pin constraints
    <output>/unifpga_top.sdc    — codegen-produced timing constraints
    <output>/build.tcl          — Gowin batch script
    <output>/gw_sh.log          — Gowin log
    <output>/impl/pnr/unifpga_top.fs — final bitstream

The Gowin EDA install ships an older libfreetype that conflicts with newer
Linux distros' fontconfig (FT_Done_MM_Var symbol). The driver works around
this by LD_PRELOAD-ing the system freetype and pointing Qt at Gowin's
bundled platform plugins (offscreen, no display required).

Set $UNIFPGA_DRY_RUN=1 to generate every artifact without invoking gw_sh.
"""

import logging
import os
import shutil
import subprocess

from tools import codegen


log = logging.getLogger(__name__)
REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.realpath(__file__))))

PROJECT_NAME = "unifpga_top"


def _resolve_gowin_bin(toolchain, name, sub="IDE/bin"):
    install_dir = os.path.expanduser(toolchain.get("InstallDir") or "").rstrip("/")
    if install_dir:
        candidate = os.path.join(install_dir, sub, name)
        if os.path.exists(candidate):
            return candidate
    return shutil.which(name)


def _gowin_env(install_dir):
    """Environment overrides needed to launch gw_sh headless on modern Linux:
      - LD_PRELOAD system freetype (Gowin's bundled freetype is too old for
        the system fontconfig — missing FT_Done_MM_Var).
      - Point Qt platform plugin path to Gowin's bundled offscreen plugin so
        the Qt library version matches the one gw_sh was linked against."""
    env = dict(os.environ)
    plugin_root = os.path.join(install_dir, "IDE", "plugins", "qt")
    plugins_path = os.path.join(plugin_root, "platforms")
    env.setdefault("QT_PLUGIN_PATH", plugin_root)
    env.setdefault("QT_QPA_PLATFORM_PLUGIN_PATH", plugins_path)
    env.setdefault("QT_QPA_PLATFORM", "offscreen")
    lib_dir = os.path.join(install_dir, "IDE", "lib")
    existing_ld = env.get("LD_LIBRARY_PATH", "")
    env["LD_LIBRARY_PATH"] = lib_dir + (":" + existing_ld if existing_ld else "")
    sys_freetype = "/lib/x86_64-linux-gnu/libfreetype.so.6"
    if os.path.exists(sys_freetype):
        existing_pre = env.get("LD_PRELOAD", "")
        env["LD_PRELOAD"] = sys_freetype + (":" + existing_pre if existing_pre else "")
    return env


def _collect_sv_sources(repo, peripherals, user_design_top, generated_top):
    """Same shape as the Vivado / Quartus drivers — keep symmetrical."""
    files = [generated_top, os.path.abspath(user_design_top)]
    seen = {os.path.abspath(p) for p in files}

    design_dir = os.path.dirname(os.path.abspath(user_design_top))
    if os.path.isdir(design_dir):
        for root, _dirs, names in os.walk(design_dir):
            for name in sorted(names):
                # Exclude .vh/.svh — Gowin auto-discovers modules in
                # SEARCH_PATH like Quartus does, causing duplicate
                # declarations. Headers come in via `\`include`.
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

    for helper in ("tm1638_registers.sv", "slow_clk_gen.sv",
                   "imitate_reset_on_power_up.sv"):
        full = os.path.join(repo, "rtl", "peripherals", helper)
        if os.path.exists(full) and full not in seen:
            files.append(full)
            seen.add(full)

    designs_common_dir = os.path.join(repo, "rtl", "peripherals", "designs_common")
    if os.path.isdir(designs_common_dir):
        for name in sorted(os.listdir(designs_common_dir)):
            if not name.endswith(".sv"):
                continue
            full = os.path.join(designs_common_dir, name)
            if full not in seen:
                files.append(full)
                seen.add(full)

    # Same Xilinx-primitive stubs as the Quartus driver: BUFG / IBUFG /
    # BUFGCE pass-through. Some designs (5_4_yrv_plus) instantiate BUFG
    # directly, which Gowin doesn't have a primitive for.
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


def _gowin_options(board_pinmap):
    """`toolchain_options.gowin` from the pinmap: {set_device: str, options: [..]}
    written from BGM's board_specific.tcl by tools/sync_from_bgm.py --gowin-options."""
    return ((board_pinmap or {}).get("toolchain_options") or {}).get("gowin") or {}


def _select_set_device_args(board, configuration, board_pinmap=None):
    """Pick the args for `set_device`. Precedence: the pinmap's
    `toolchain_options.gowin.set_device` (BGM's exact `<part> -name <name>
    -device_version <ver>`), the board's `GowinDeviceArgs`, then the bare Part."""
    args = _gowin_options(board_pinmap).get("set_device")
    if args:
        return args
    args = board.get("GowinDeviceArgs")
    if args:
        return args
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
        part_name = chosen.get("Part") or ""
    return part_name


def _emit_tcl(device_args, sv_files, cst_path, sdc_path, output_dir, step, options=()):
    """Generate the gw_sh batch script. The Gowin TCL flow is:
        set_device <part> [-name <name>] [-device_version <ver>]
        set_option -use_<pin-group>_as_gpio 1   (per board, from BGM's .tcl)
        add_file <each .sv .v>
        add_file -type cst <cst>
        add_file -type sdc <sdc>
        run syn   |   run pnr   |   run all
    """
    lines = []
    lines.append("# Gowin batch script — auto-generated by uni-fpga.")
    lines.append("set_device {}".format(device_args))
    # Treat all .v / .sv files as SystemVerilog (sysv2017). Without this,
    # GowinSynthesis defaults to Verilog-2001 and rejects `logic`,
    # `always_ff`, `'0`, etc.
    lines.append("set_option -synthesis_tool gowinsynthesis")
    lines.append("set_option -verilog_std sysv2017")
    lines.append("set_option -top_module top")
    lines.append("set_option -output_base_name {}".format(PROJECT_NAME))
    # Configuration pins reused as user I/O (MSPI/SSPI flash lines, DONE,
    # READY, CPU, I2C): without these the LCD/HDMI/TM1638 pins BGM uses on
    # the Tang boards are illegal for the placer.
    for opt in options:
        lines.append("set_option -{} 1".format(str(opt).lstrip("-")))
    for sv in sv_files:
        # `add_file <path>` auto-detects file type by extension.
        lines.append("add_file {{{}}}".format(sv))
    if cst_path:
        lines.append("add_file -type cst {{{}}}".format(cst_path))
    if sdc_path:
        lines.append("add_file -type sdc {{{}}}".format(sdc_path))
    # Pick the run target. `run syn` is RTL synthesis only — the closest
    # equivalent of Vivado's `synth_design -rtl`, used for elaborate sweeps.
    target = "syn" if step == "elaborate" else "all"
    lines.append("run {}".format(target))
    return "\n".join(lines) + "\n"


def synthesize(*, dir, configuration, board, board_pinmap, toolchain, peripherals,
               top, generated_top=None, include=None, output, step="full", **_):
    """Synthesize through Gowin EDA. Returns 0 on success."""
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

    device_args = _select_set_device_args(board, configuration, board_pinmap)
    if not device_args:
        log.error("Board %s has no 'Part'/'GowinDeviceArgs' — cannot drive Gowin.", board["Id"])
        return 1
    gowin_opts = _gowin_options(board_pinmap).get("options") or []
    # Use the bare part token (first whitespace-delimited word) wherever a
    # raw part-name is needed (e.g. for emit_cst / pin lookup).
    part_name = device_args.split()[0]

    sv_files = _collect_sv_sources(REPO, peripherals, top, generated_top)

    cst_path = os.path.join(output, PROJECT_NAME + ".cst")
    sdc_path = os.path.join(output, PROJECT_NAME + ".sdc")
    tcl_path = os.path.join(output, "build.tcl")

    with open(cst_path, "w") as f:
        f.write(codegen.emit_cst(resolved))
    log.info("Wrote %s", cst_path)

    with open(sdc_path, "w") as f:
        f.write(codegen.emit_sdc(resolved))
    log.info("Wrote %s", sdc_path)

    with open(tcl_path, "w") as f:
        f.write(_emit_tcl(device_args, sv_files, cst_path, sdc_path, output, step, gowin_opts))
    log.info("Wrote %s", tcl_path)

    log.info("Source files (%d):", len(sv_files))
    for sv in sv_files:
        log.info("  - %s", os.path.relpath(sv, REPO) if sv.startswith(REPO) else sv)

    if os.environ.get("UNIFPGA_DRY_RUN"):
        log.info("[dry run] gw_sh not invoked. Artifacts in %s", output)
        return 0

    install_dir = os.path.expanduser(toolchain.get("InstallDir") or "").rstrip("/")
    gw_sh = _resolve_gowin_bin(toolchain, "gw_sh")
    if gw_sh is None:
        log.error("Could not locate gw_sh. Set toolchain.InstallDir in "
                  "config/toolchains.yml or put gw_sh on $PATH.")
        return 1

    env = _gowin_env(install_dir)
    log_path = os.path.join(output, "gw_sh.log")
    cmd = [gw_sh, tcl_path]
    log.info("Invoking gw_sh: %s", " ".join(cmd))
    with open(log_path, "w") as logf:
        try:
            rc = subprocess.run(cmd, cwd=output, env=env,
                                stdout=logf, stderr=subprocess.STDOUT).returncode
        except FileNotFoundError as exc:
            log.error("gw_sh invocation failed: %s", exc)
            return 1

    if rc != 0:
        log.error("gw_sh exited with code %d (see %s)", rc, log_path)
        return rc

    bit = os.path.join(output, "impl", "pnr", PROJECT_NAME + ".fs")
    if os.path.exists(bit):
        log.info("Bitstream ready: %s", bit)
    return 0


def program(*, board, board_pinmap=None, toolchain, output, **_):
    """Download the .fs bitstream over JTAG via programmer_cli."""
    bit = os.path.join(output, "impl", "pnr", PROJECT_NAME + ".fs")
    if not os.path.exists(bit) and not os.environ.get("UNIFPGA_DRY_RUN"):
        log.error("Bitstream not found: %s — run synthesis first", bit)
        return 1

    if os.environ.get("UNIFPGA_DRY_RUN"):
        log.info("[dry run] Would program %s", bit)
        return 0

    pgm = _resolve_gowin_bin(toolchain, "programmer_cli", sub="Programmer/bin")
    if pgm is None:
        log.error("Could not locate programmer_cli.")
        return 1

    install_dir = os.path.expanduser(toolchain.get("InstallDir") or "").rstrip("/")
    env = _gowin_env(install_dir)
    cmd = [pgm, "--device", "GW1N-9", "--operation_index", "2", "--fsFile", bit]
    log.info("Programming via: %s", " ".join(cmd))
    rc = subprocess.run(cmd, cwd=output, env=env).returncode
    if rc != 0:
        log.error("Programming failed (exit %d). Is the board connected?", rc)
    return rc
