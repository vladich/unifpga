"""
Efinity (Efinix) toolchain driver.

Pipeline:
    efx_run.py -f map        # synthesis (this is the "elaborate" stage)
    efx_run.py -f full       # synth + place + route + bitstream

The Efinity tools are Python-driven via efx_run.py. Two modes:
  - direct: pass --device, --family, -v <sources>
  - project: pass --prj <project.xml> (ingests our codegen-emitted XML)

We use direct mode for `--step elaborate` (faster — no peri.xml processing
needed for synth-only) and project-XML mode for `--step full` (so the
peri.xml pin assignments are honoured).

Artifacts:
    <output>/top.sv               — codegen-produced top module
    <output>/unifpga_top.peri.xml — codegen-produced peripheral XML
    <output>/unifpga_top.sdc      — codegen-produced timing constraints
    <output>/unifpga_top.xml      — codegen-produced Efinity project XML
    <output>/efx_run.log          — efx_run / map / pnr / pgm log
    <output>/outflow/             — Efinity's own output dir (.bit + reports)

Set $UNIFPGA_DRY_RUN=1 to generate every artifact without invoking the tools.
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


def _resolve_install_dir(toolchain):
    return os.path.expanduser(toolchain.get("install_dir") or "").rstrip("/")


def _resolve_bin(toolchain, name, sub="bin"):
    install_dir = _resolve_install_dir(toolchain)
    if install_dir:
        candidate = os.path.join(install_dir, sub, name)
        if os.path.exists(candidate):
            return candidate
    return shutil.which(name)


def _efinity_env(install_dir):
    """What <install>/bin/setup.sh exports (Efinity 2023.2): the tool homes,
    PATH additions and the bundled Python (efx_run's helpers read
    EFXPGM_HOME etc. straight from the environment)."""
    env = dict(os.environ)
    h = install_dir
    env["EFINITY_HOME"] = h
    env["EFXPT_HOME"] = os.path.join(h, "pt")
    env["EFXPGM_HOME"] = os.path.join(h, "pgm")
    env["EFXDBG_HOME"] = os.path.join(h, "debugger")
    env["EFXIPM_HOME"] = os.path.join(h, "ipm")
    env["EFXIPMGR_HOME"] = os.path.join(h, "ipm", "bin", "ip_manager")
    env["EFXIPPKG_HOME"] = os.path.join(h, "ipm", "bin", "ip_packager")
    env["EFXSVF_HOME"] = os.path.join(h, "debugger", "svf_player")
    env["QT_LOGGING_CONF"] = os.path.join(h, "bin", "lc.ini")
    env["QT_PLUGIN_PATH"] = os.path.join(h, "lib", "plugins")
    env["PYTHONNOUSERSITE"] = "1"
    env["PYTHONPATH"] = os.path.join(h, "lib") + os.pathsep + env.get("PYTHONPATH", "")
    env["PYTHONHOME"] = h
    env["PATH"] = os.pathsep.join([os.path.join(h, "bin"), os.path.join(h, "scripts"),
                                   os.path.join(h, "pgm", "bin"), os.path.join(h, "debugger", "bin"),
                                   os.path.join(h, "debugger", "svf_player", "bin"), os.path.join(h, "ipm", "bin"),
                                   env.get("PATH", "")])
    return env


def _efx_run_script(toolchain):
    install_dir = _resolve_install_dir(toolchain)
    if install_dir:
        candidate = os.path.join(install_dir, "scripts", "efx_run.py")
        if os.path.exists(candidate):
            return candidate
    return None


def _collect_sv_sources(repo, peripherals, user_design_top, generated_top, component_sources=()):
    """Efinity: no .svh; helpers/common ungated."""
    return source_set.collect_sources(
        repo, peripherals, user_design_top, generated_top,
        include_svh=False, gate_helpers=False, gate_common=False, component_sources=component_sources)


def synthesize(*, dir, configuration, board, toolchain, peripherals,
               top, generated_top=None, include=None, component_sources=(), output, step="full", **_):
    """Synthesize through Efinity's efx_run.py. Returns 0 on success."""
    resolved = {
        "configuration": configuration,
        "board":         board,
        "toolchain":     toolchain,
        "peripherals":   peripherals,
    }

    if generated_top is None:
        generated_top = os.path.join(output, "top.sv")
        with open(generated_top, "w") as f:
            f.write(codegen.emit_top_sv(resolved, design=top))

    device = (board.get("part") or "").strip()
    family = ((board.get("family") or {}).get("name") or "Trion").strip()
    if not device:
        log.error("Board %s has no part — cannot drive Efinity.", board["id"])
        return 1

    sv_files = _collect_sv_sources(REPO, peripherals, top, generated_top, component_sources)
    sdc_path = os.path.join(output, PROJECT_NAME + ".sdc")
    peri_path = os.path.join(output, PROJECT_NAME + ".peri.xml")
    project_path = os.path.join(output, PROJECT_NAME + ".xml")
    log_path = os.path.join(output, "efx_run.log")

    with open(sdc_path, "w") as f:
        f.write(codegen.emit_sdc(resolved))
    log.info("Wrote %s", sdc_path)

    with open(peri_path, "w") as f:
        f.write(codegen.emit_peri_xml(resolved, device))
    log.info("Wrote %s", peri_path)

    with open(project_path, "w") as f:
        f.write(codegen.emit_efx_project_xml(resolved, device, sv_files,
                                              os.path.basename(sdc_path),
                                              os.path.basename(peri_path),
                                              project_name=PROJECT_NAME))
    log.info("Wrote %s", project_path)

    log.info("Source files (%d):", len(sv_files))
    for sv in sv_files:
        log.info("  - %s", os.path.relpath(sv, REPO) if sv.startswith(REPO) else sv)

    # Direct driver callers get the same staged ROM files as synthesize.main().
    source_set.stage_assets(os.path.dirname(os.path.abspath(top)), output)

    if os.environ.get("UNIFPGA_DRY_RUN"):
        log.info("[dry run] Efinity not invoked. Artifacts in %s", output)
        return 0

    efx_run = _efx_run_script(toolchain)
    if efx_run is None:
        log.error("Could not locate efx_run.py. Set toolchain.InstallDir in "
                  "config/toolchains.yml (e.g. ~/efinity/2023.2/).")
        return 1
    install_dir = _resolve_install_dir(toolchain)

    # Project mode: efx_run.py takes the project XML (the peri.xml next to it
    # feeds the Interface Designer, which writes the constraint file efx_pgm
    # needs); `--flow compile` is synthesis + place + route + bitstream
    # (`full` would also run the RTL simulation, which fails without a
    # simulator).
    flow = "map" if step == "elaborate" else "compile"
    cmd = _efx_command(efx_run, flow, project_path)
    env = _efinity_env(install_dir)
    python = os.path.join(install_dir, "bin", "python3")
    if os.path.exists(python):
        cmd[0] = python

    log.info("Invoking efx_run.py --flow %s %s (%s %s)", flow, os.path.basename(project_path), family, device)
    with open(log_path, "w") as logf:
        try:
            rc = subprocess.run(cmd, cwd=output, env=env,
                                stdout=logf, stderr=subprocess.STDOUT).returncode
        except FileNotFoundError as exc:
            log.error("efx_run invocation failed: %s", exc)
            return 1

    if rc != 0:
        log.error("efx_run exited with code %d (see %s)", rc, log_path)
        return rc

    if step != "elaborate":
        bit = _find_bitstream(output)
        if bit:
            log.info("Bitstream ready: %s", bit)
        else:
            log.error("efx_run finished but wrote no bitstream (expected work_pnr/%s.hex, see %s)", PROJECT_NAME, log_path)
            return 1
    return 0


def _efx_command(efx_run, flow, project_path):
    """The efx_run.py call shape: --pgm_opts source=<lbf> --pgm_opts
    dest=<hex> --flow <flow> <project.xml> (`program`: source=<hex>)."""
    lbf = os.path.join("work_pnr", PROJECT_NAME + ".lbf")
    hexfile = os.path.join("work_pnr", PROJECT_NAME + ".hex")
    if flow == "program":
        return ["python3", efx_run, "--pgm_opts", "source=" + hexfile, "--flow", "program", project_path]
    return ["python3", efx_run, "--pgm_opts", "source=" + lbf, "--pgm_opts", "dest=" + hexfile,
            "--flow", flow, project_path]


def _find_bitstream(output):
    for rel in (os.path.join("work_pnr", PROJECT_NAME + ".hex"), os.path.join("outflow", PROJECT_NAME + ".hex"),
                os.path.join("outflow", PROJECT_NAME + ".bit")):
        if os.path.exists(os.path.join(output, rel)):
            return os.path.join(output, rel)
    return None


def program(*, board, toolchain, output, **_):
    """Download the bitstream with efx_run.py --flow program on the project
    (the programmer settings live in the project XML); openFPGALoader is the
    fallback without Efinity."""
    bit = _find_bitstream(output)
    if bit is None and not os.environ.get("UNIFPGA_DRY_RUN"):
        log.error("Bitstream not found: work_pnr/%s.hex in %s — run synthesis first", PROJECT_NAME, output)
        return 1
    if os.environ.get("UNIFPGA_DRY_RUN"):
        log.info("[dry run] Would program %s", bit or os.path.join(output, "work_pnr", PROJECT_NAME + ".hex"))
        return 0
    efx_run = _efx_run_script(toolchain)
    project_path = os.path.join(output, PROJECT_NAME + ".xml")
    if efx_run is not None and os.path.exists(project_path):
        install_dir = _resolve_install_dir(toolchain)
        cmd = _efx_command(efx_run, "program", project_path)
        python = os.path.join(install_dir, "bin", "python3")
        if os.path.exists(python):
            cmd[0] = python
        log.info("Programming via: %s", " ".join(cmd))
        rc = subprocess.run(cmd, cwd=output, env=_efinity_env(install_dir)).returncode
        if rc != 0:
            log.error("Programming failed (exit %d). Is the board connected?", rc)
        return rc
    pgm = shutil.which("openFPGALoader")
    if pgm is None:
        log.error("Could not find efx_run.py or openFPGALoader.")
        return 1
    cmd = [pgm, bit]
    log.info("Programming via: %s", " ".join(cmd))
    rc = subprocess.run(cmd, cwd=output).returncode
    if rc != 0:
        log.error("Programming failed (exit %d). Is the board connected?", rc)
    return rc
