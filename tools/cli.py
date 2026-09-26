#!/usr/bin/env python3
"""
`./unifpga` -- the short command line.

Choose a board once, then build / program / clean inside a design directory
with no parameters; the output lands in the design's run/ directory:

    ./unifpga board                # numbered menu; remembered in settings.yml
    cd designs/1_06_binary_counter
    ../../unifpga build            # -> run/<configuration id>/
    ../../unifpga program          # synthesize (full) and load the bitstream
    ../../unifpga program --no-build  # load the last build's bitstream again
    ../../unifpga clean            # remove run/

Every subcommand only assembles synthesize.py's argument list and calls
synthesize.main(). The remembered choice is the same settings.yml
({ConfigurationId: <id>}) that config/init.py read_all() reads, so the two
entry points never disagree; $UNIFPGA_BOARD or -b/--board override it for
one command.

The logic lives here so tests/test_cli.py can import it; `unifpga` at the
repo root is a five-line launcher.
"""

import argparse
import difflib
import glob
import json
import logging
import os
import platform
import re
import shutil
import subprocess
import sys
import textwrap

try:
    import yaml
except ImportError:                         # requirements.txt; the ./unifpga launcher makes .venv with it
    sys.exit("unifpga: PyYAML is not installed for {py}.\n"
             "Run ./unifpga (it makes .venv with requirements.txt and runs with it), or install the requirements for "
             "this Python ({py} -m pip install -r requirements.txt).".format(py=sys.executable))

REPO = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
if REPO not in sys.path:                    # `python3 tools/cli.py` without the launcher
    sys.path.insert(0, REPO)

import config.init                          # noqa: E402
import program                              # noqa: E402  (called, never shelled out)
import synthesize                           # noqa: E402  (called, never shelled out)
from tools import codegen, source_set, toolchain_detect  # noqa: E402

DESIGNS_DIR = os.path.join(REPO, "designs")
TOP_NAME = "design_top.sv"
RUN_DIR_NAME = "run"
ENV_BOARD = "UNIFPGA_BOARD"
# The per-user choice: the file config/init.py read_all() reads and init()
# writes. This module reads and writes that same file, same document.
SETTINGS_PATH = os.path.join(REPO, "settings.yml")

QUICK_START = """\
Quick start:
  ./unifpga board                 # pick your board once (remembered in settings.yml)
  cd designs/1_06_binary_counter
  ../../unifpga build             # synthesize into run/<configuration>/
  ../../unifpga program           # synthesize and load the bitstream onto the board
  ../../unifpga program --no-build   # load the last build's bitstream again

Other commands:
  unifpga clean [--all]            remove run/ (of every design with --all)
  unifpga sim                      simulate tb.sv with Icarus Verilog, open a waveform viewer
  unifpga gui                      open the last build in the vendor GUI
  unifpga prepare [--all]          write the run directories without running the tools
  unifpga check [entity...]        every configuration file against its schema, every reference resolved
"""


class CliError(Exception):
    """A user-facing error: the message goes to stderr, the exit status is 1."""


# ---------------------------------------------------------------------------
# The remembered board (settings.yml)
# ---------------------------------------------------------------------------

def read_settings(path=None):
    """The configuration id remembered in settings.yml, or None when the file
    is absent or empty. Raises CliError for a malformed or legacy file."""
    path = path or SETTINGS_PATH
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except yaml.YAMLError as exc:
        raise CliError("{p}: YAML parse error: {e}\n"
                       "Fix or delete the file, then run ./unifpga board.".format(p=path, e=exc))
    if not data:
        return None
    if not isinstance(data, dict):
        raise CliError("{p}: expected a mapping {{ConfigurationId: <id>}}; "
                       "run ./unifpga board to rewrite it.".format(p=path))
    cfg_id = data.get("ConfigurationId")
    if cfg_id is None:
        if "BoardId" in data:
            raise CliError("{p} uses the legacy {{BoardId, Toolchain}} format. "
                           "Run ./unifpga board to pick a configuration (it rewrites the file)."
                           .format(p=path))
        return None
    return str(cfg_id)


def write_settings(cfg_id, path=None):
    """Persist the choice exactly the way config/init.py init() does."""
    path = path or SETTINGS_PATH
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump({"ConfigurationId": cfg_id}, f)


def configurations():
    """{build-target id: Configuration dict as that target builds it}: every rig
    (config/setups/) with each toolchain and chip it is checked with
    (config/init.py build_targets())."""
    try:
        rigs = config.init.read_configurations()
        return {t["id"]: config.init.for_target(rigs[t["rig"]], t["toolchain"], t["part"])
                for t in config.init.build_targets(rigs)}
    except config.init.ConfigError as exc:
        raise CliError(str(exc))


def target_configuration(cfg_id):
    """The Configuration dict a build-target id (an alias too) builds, or None."""
    t = config.init.target_of(cfg_id)
    if t is None:
        return None
    return config.init.for_target(config.init.read_configurations()[t[0]], t[1], t[2])


def _unknown_configuration(cfg_id, origin, ids):
    close = difflib.get_close_matches(cfg_id, ids, n=5, cutoff=0.5)
    return ("Unknown configuration '{c}' (from {o}). ./unifpga board -l lists the ids{hint}"
            .format(c=cfg_id, o=origin,
                    hint="; did you mean: " + ", ".join(close) + "?" if close else "."))


def chosen_configuration(override=None):
    """The configuration id to build for: -b/--board, else $UNIFPGA_BOARD,
    else settings.yml. Validated against the rigs (config/setups/)."""
    if override:
        cfg_id, origin = override, "-b/--board"
    elif os.environ.get(ENV_BOARD):
        cfg_id, origin = os.environ[ENV_BOARD], "$" + ENV_BOARD
    else:
        cfg_id, origin = read_settings(), SETTINGS_PATH
    if not cfg_id:
        raise CliError("No board chosen yet. Run ./unifpga board "
                       "(./unifpga board -l lists the configurations).")
    if config.init.target_of(cfg_id) is None:
        raise CliError(_unknown_configuration(cfg_id, origin, sorted(configurations())))
    return cfg_id


# ---------------------------------------------------------------------------
# Designs
# ---------------------------------------------------------------------------

def _has_top(directory):
    return os.path.isfile(os.path.join(directory, TOP_NAME))


def list_designs():
    """Names of the designs/<name>/ directories that contain design_top.sv."""
    if not os.path.isdir(DESIGNS_DIR):
        return []
    return sorted(n for n in os.listdir(DESIGNS_DIR) if _has_top(os.path.join(DESIGNS_DIR, n)))


def _candidates(query, names):
    """'Did you mean' text: substring and fuzzy matches of `query`, else all."""
    if not names:
        return "  (no designs with {t} under {d})".format(t=TOP_NAME, d=DESIGNS_DIR)
    picks = []
    if query:
        q = os.path.basename(os.path.normpath(query)).lower()
        picks = [n for n in names if q in n.lower()]
        for m in difflib.get_close_matches(q, names, n=8, cutoff=0.5):
            if m not in picks:
                picks.append(m)
    head = "Did you mean:" if picks else "Designs (./unifpga designs):"
    body = textwrap.fill(" ".join(picks[:10] if picks else names), width=78,
                         initial_indent="  ", subsequent_indent="  ",
                         break_long_words=False, break_on_hyphens=False)
    return head + "\n" + body


def resolve_design(design=None, cwd=None):
    """Absolute path of the design directory.

    design None: the current directory, which must contain design_top.sv.
    Otherwise a directory path (relative to cwd or absolute), the path of a
    design_top.sv, or the name of a designs/<name>/ subdirectory."""
    cwd = os.path.abspath(cwd or os.getcwd())
    names = list_designs()
    if design is None:
        if _has_top(cwd):
            return cwd
        raise CliError("No design given and {cwd} has no {top}.\n"
                       "Run from a design directory or name one: ./unifpga build <design>\n{c}"
                       .format(cwd=cwd, top=TOP_NAME, c=_candidates(os.path.basename(cwd), names)))
    as_path = os.path.normpath(os.path.join(cwd, os.path.expanduser(design)))
    if os.path.isfile(as_path) and os.path.basename(as_path) == TOP_NAME:
        return os.path.dirname(as_path)
    if os.path.isdir(as_path) and _has_top(as_path):
        return as_path
    under_designs = os.path.normpath(os.path.join(DESIGNS_DIR, design))
    if os.path.isdir(under_designs) and _has_top(under_designs):
        return under_designs
    if os.path.isdir(as_path):
        raise CliError("{p} has no {top}, so it is not a design directory.\n{c}"
                       .format(p=as_path, top=TOP_NAME, c=_candidates(design, names)))
    raise CliError("No design '{d}': not a directory and not one of designs/.\n{c}"
                   .format(d=design, c=_candidates(design, names)))


def run_dir(design_dir, cfg_id=None):
    """<design>/run[/<configuration id>]: where build output goes."""
    d = os.path.join(design_dir, RUN_DIR_NAME)
    return os.path.join(d, cfg_id) if cfg_id else d


def synthesize_argv(design_dir, cfg_id, step="full", program=False, component_exports=()):
    """The synthesize.py arguments `build` and `program` run. Absolute paths:
    the toolchain drivers run their tools with cwd set to the output dir."""
    argv = ["-c", cfg_id,
            "--top", os.path.join(design_dir, TOP_NAME),
            "-s", step,
            "-o", run_dir(design_dir, cfg_id)]
    for manifest in component_exports:
        argv.extend(("--component-export", os.path.abspath(manifest)))
    if program:
        argv.append("--program")
    return argv


def remove_run_dir(design_dir):
    """Remove <design>/run/ and nothing else. Returns the removed path, or
    None when there was nothing to remove. Refuses a symlink, a non-directory,
    anything not literally `run` directly inside a design directory."""
    design_dir = os.path.abspath(design_dir)
    target = os.path.join(design_dir, RUN_DIR_NAME)
    if not _has_top(design_dir):
        raise CliError("Refusing to clean {d}: no {top}, so it is not a design directory."
                       .format(d=design_dir, top=TOP_NAME))
    if os.path.islink(target):
        raise CliError("Refusing to remove {t}: it is a symbolic link.".format(t=target))
    if not os.path.lexists(target):
        return None
    if not os.path.isdir(target):
        raise CliError("Refusing to remove {t}: it is not a directory.".format(t=target))
    real = os.path.realpath(target)
    if os.path.basename(real) != RUN_DIR_NAME or os.path.dirname(real) != os.path.realpath(design_dir):
        raise CliError("Refusing to remove {t}: it is not the run/ directory of {d}."
                       .format(t=target, d=design_dir))
    shutil.rmtree(target)
    return target


# ---------------------------------------------------------------------------
# Board menu
# ---------------------------------------------------------------------------

def installed_toolchains():
    """{toolchain id: found?} the way synthesize.py resolves installs
    (tools/toolchain_detect.py: install_dir pin, vendor variable, PATH, the
    default install directories)."""
    found = {}
    for tid, tc in config.init.read_toolchains().items():
        found[tid] = toolchain_detect.detect(tid, pin=tc.get("install_dir")).found
    return found


def menu_lines(cfgs, installed, current=None):
    """Numbered menu of the configurations: id, board, toolchain; `*` when
    the toolchain is installed, `>` on the current choice."""
    ids = sorted(cfgs)
    w_id = max(len(i) for i in ids)
    w_board = max(len(str(cfgs[i].get("board", "?"))) for i in ids)
    lines = ["  * = toolchain found on this machine" + ("    > = current choice" if current else ""),
             "{:>6}  {} {:<{wi}}  {:<{wb}}  {}".format("#", " ", "configuration", "board", "toolchain",
                                                      wi=w_id, wb=w_board)]
    for n, cfg_id in enumerate(ids, start=1):
        cfg = cfgs[cfg_id]
        tc = cfg.get("toolchain", "?")
        lines.append("{cur}{n:>5}  {mark} {id:<{wi}}  {board:<{wb}}  {tc}".format(
            cur=">" if cfg_id == current else " ", n=n, mark="*" if installed.get(tc) else " ",
            id=cfg_id, board=cfg.get("board", "?"), tc=tc, wi=w_id, wb=w_board))
    return lines


def choose_interactively(ids, current=None):
    """Ask for a number (or an id) until it is valid. None when the user
    gives up (empty line or end of input)."""
    prompt = "Your choice (a number{keep}): ".format(
        keep=", Enter keeps " + current if current else ", Enter to quit")
    while True:
        try:
            raw = input(prompt).strip()
        except EOFError:
            print()
            return None
        if not raw:
            return None
        if raw in ids or config.init.target_of(raw) is not None:
            return raw
        if raw.isdigit() and 1 <= int(raw) <= len(ids):
            return ids[int(raw) - 1]
        print("Not one of the listed numbers (1..{n}); please choose again.".format(n=len(ids)))


def _select(cfgs, cfg_id, installed=None):
    cfg = cfgs.get(cfg_id) or target_configuration(cfg_id)
    if cfg is None:
        raise CliError(_unknown_configuration(cfg_id, "the command line", sorted(cfgs)))
    write_settings(cfg_id)
    tc = cfg.get("toolchain", "?")
    print("Board configuration: {id}  (board {b}, toolchain {tc}) -- saved to {p}".format(
        id=cfg_id, b=cfg.get("board", "?"), tc=tc, p=SETTINGS_PATH))
    pin = (config.init.read_toolchains().get(tc) or {}).get("install_dir")
    det = toolchain_detect.detect(tc, pin=pin)
    if det.found:
        print("Toolchain {tc}: {where} ({src})".format(
            tc=tc, where=det.install_dir or ", ".join(det.bin_dirs or []), src=det.source))
    else:
        print("note: toolchain {tc} was not found on this machine; "
              "./unifpga tools shows where it is looked for.".format(tc=tc))
    return 0


def cmd_board(args):
    cfgs = configurations()
    if args.id:
        return _select(cfgs, args.id)
    try:
        current = read_settings()
    except CliError as exc:
        print("note: {}".format(exc), file=sys.stderr)
        current = None
    installed = installed_toolchains()
    print("\n".join(menu_lines(cfgs, installed, current)))
    if args.list:
        print("\nCurrent choice: {}".format(current or "none yet (./unifpga board)"))
        return 0
    print()
    if current:
        print("The currently selected board configuration: {}".format(current))
    print("Please select a board configuration among the ones listed above.")
    choice = choose_interactively(sorted(cfgs), current)
    if choice is None:
        if current:
            print("Keeping {}".format(current))
            return 0
        print("No board selected; run ./unifpga board again.")
        return 1
    rc = _select(cfgs, choice, installed)
    if rc == 0 and sys.stdin.isatty():
        try:
            reply = input("Write the run directories of every design for this board now "
                          "(no tools are run)? [y/N] ").strip().lower()
        except EOFError:
            reply = ""
        if reply in ("y", "yes"):
            return cmd_prepare(argparse.Namespace(design=None, board=choice, all=True))
    return rc


# ---------------------------------------------------------------------------
# build / program / clean / tools / designs
# ---------------------------------------------------------------------------

def _shown(path):
    rel = os.path.relpath(path)
    return path if rel.startswith("..") else rel


LOG_NAME = "log.txt"
LOG_FORMAT = "%(levelname)s %(name)s: %(message)s"


def _run_synthesize(argv, out_dir):
    """synthesize.main() with its log also written to <out_dir>/log.txt; on
    failure the error lines of that log are repeated (`grep -i -A 5 error`)."""
    os.makedirs(out_dir, exist_ok=True)
    log_path = os.path.join(out_dir, LOG_NAME)
    logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)      # synthesize's own call is then a no-op
    handler = logging.FileHandler(log_path, mode="w", encoding="utf-8")
    handler.setFormatter(logging.Formatter(LOG_FORMAT))
    root = logging.getLogger()
    root.addHandler(handler)
    try:
        rc = synthesize.main(argv)
    finally:
        root.removeHandler(handler)
        handler.close()
    if rc:
        try:
            with open(log_path, encoding="utf-8", errors="replace") as f:
                errors = [l.rstrip("\n") for l in f if "error" in l.lower()]
        except OSError:
            errors = []
        if errors:
            print("\nErrors ({}):".format(_shown(log_path)), file=sys.stderr)
            for l in errors[-8:]:
                print("  " + l[:200], file=sys.stderr)
        print("unifpga: step failed with exit status {} (log: {})".format(rc, _shown(log_path)), file=sys.stderr)
    return rc


def cmd_build(args, program=False):
    design_dir = resolve_design(args.design)
    cfg_id = chosen_configuration(args.board)
    step = "full" if program else args.step
    out = run_dir(design_dir, cfg_id)
    print("{verb} {d} for {c} ...  output: {o}".format(
        verb="Building and programming" if program else "Building",
        d=os.path.basename(design_dir), c=cfg_id, o=_shown(out)))
    sys.stdout.flush()
    return _run_synthesize(synthesize_argv(design_dir, cfg_id, step, program,
                                           getattr(args, "component_export", ())), out)


def cmd_program(args):
    if not getattr(args, "no_build", False):
        return cmd_build(args, program=True)
    if args.component_export:
        raise CliError("--component-export requires a new build; omit --no-build")
    design_dir = resolve_design(args.design)
    cfg_id = chosen_configuration(args.board)
    out = run_dir(design_dir, cfg_id)
    if not os.path.isdir(out):
        raise CliError("{o} does not exist: run `unifpga build` first.".format(o=_shown(out)))
    print("Programming {c} from {o} ...".format(c=cfg_id, o=_shown(out)))
    sys.stdout.flush()
    return program.main(["-c", cfg_id, "-o", out])


def cmd_clean(args):
    if getattr(args, "all", False):                  # every design's run/
        removed = 0
        for name in list_designs():
            if remove_run_dir(os.path.join(DESIGNS_DIR, name)):
                removed += 1
        print("Removed the run/ directory of {} design(s)".format(removed))
        return 0
    design_dir = resolve_design(args.design)
    removed = remove_run_dir(design_dir)
    if removed:
        print("Removed {}".format(_shown(removed)))
    else:
        print("Nothing to clean: {} does not exist".format(_shown(run_dir(design_dir))))
    return 0


# ---------------------------------------------------------------------------
# sim (Icarus Verilog + gtkwave / surfer) and gui
# ---------------------------------------------------------------------------

TB_NAME = "tb.sv"
SIM_DIR_NAME = "sim"


def _iverilog_language_option(version_text):
    """-g2012, or -g2023 for Icarus 14+."""
    import re
    m = re.search(r"Icarus Verilog version (\d+)\.", version_text or "")
    if m and int(m.group(1)) >= 14:
        return "-g2023"
    return "-g2012"


def sim_sources(design_dir, component_exports=(), *, component_sources=None):
    """Simulation view of the same design fileset used by synthesis."""
    rtl = os.path.join(REPO, "rtl")
    files = [os.path.join(rtl, "sim", "timescale.sv")]          # `timescale 1 ns / 1 ps first
    generated = (source_set.component_export_sources(component_exports)
                 if component_sources is None else list(component_sources))
    sources, simulation, _ = source_set.design_inputs(design_dir)
    selected = [p for p in sources if p.endswith((".sv", ".v"))] + simulation
    if {os.path.realpath(path) for path in generated} & {os.path.realpath(path) for path in selected}:
        raise source_set.SourceSetError("component export duplicates a design source")
    files += generated + selected
    # peripherals/*.sv too (LCD testbenches instantiate the panel timing
    # modules); only tb's hierarchy is elaborated (-s tb), so
    # unreferenced models cost nothing
    # A generated file must not silently hide a repository peripheral merely
    # because it has the same basename. Let the HDL compiler diagnose modules.
    local = {os.path.basename(files[0])} | {os.path.basename(f) for f in selected}
    for sub, pattern in (("peripherals/designs_common", "*.sv"), ("peripherals", "*.sv"), ("peripherals", "*.v"),
                         ("io", "*.sv"), ("pll", "*.sv"), ("sim", "*.sv")):
        files += sorted(f for f in glob.glob(os.path.join(rtl, sub, pattern))
                        if os.path.basename(f) != "design_top_interface.sv" and os.path.basename(f) not in local)
    return files


def sim_command(design_dir, out_dir, lang="-g2012", *, component_exports=(),
                component_sources=None, tb_top="tb"):
    # SIMULATION enables the simulation-only modules (fifo_monitor and
    # others sit behind `ifdef SIMULATION)
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_$]*", tb_top):
        raise CliError("simulation top must be a SystemVerilog module name")
    files = sim_sources(design_dir, component_exports, component_sources=component_sources)
    includes = [design_dir, os.path.join(design_dir, "cpu"),
                os.path.join(REPO, "rtl", "peripherals"),
                os.path.join(REPO, "rtl", "peripherals", "designs_common")]
    seen_includes = {os.path.abspath(path) for path in includes}
    # Included headers may live beside a selected nested source, not only in
    # the design root or the historical cpu/ subdirectory.
    design_root = os.path.abspath(design_dir)
    for source in files:
        parent = os.path.dirname(source)
        if (os.path.commonpath((design_root, os.path.abspath(parent))) == design_root and
                os.path.abspath(parent) not in seen_includes):
            includes.append(parent)
            seen_includes.add(os.path.abspath(parent))
    return (["iverilog", lang, "-D", "SIMULATION", "-s", tb_top, "-o", os.path.join(out_dir, "a.out")]
            + [arg for directory in includes for arg in ("-I", directory)] + files)


def _waveform_viewer():
    """gtkwave, or surfer on Apple silicon; None when neither exists."""
    if platform.system() == "Darwin" and platform.machine() == "arm64" and shutil.which("surfer"):
        return ["surfer"]
    for name in ("gtkwave", "surfer"):
        if shutil.which(name):
            return [name]
    return None


def cmd_sim(args):
    design_dir = resolve_design(args.design)
    tb = os.path.join(design_dir, TB_NAME)
    if not os.path.isfile(tb):
        raise CliError("{d} has no {tb}. The testbench sits next to design_top.sv; add one "
                       "(module tb, instantiating design_top) and run again.".format(d=_shown(design_dir), tb=TB_NAME))
    if not shutil.which("iverilog"):
        raise CliError("iverilog is not on PATH. Install Icarus Verilog (apt/yum/brew install iverilog).")
    if not shutil.which("vvp"):
        raise CliError("vvp is not on PATH. Install Icarus Verilog (apt/yum/brew install iverilog).")
    out = args.output_dir or os.path.join(run_dir(design_dir), SIM_DIR_NAME)
    if args.output_dir:
        out = os.path.abspath(out)
        resolved = os.path.realpath(out)
        design_root = os.path.realpath(design_dir)
        if (os.path.commonpath((resolved, design_root)) in (resolved, design_root) or
                (os.path.lexists(out) and
                 (os.path.islink(out) or not os.path.isdir(out) or os.listdir(out)))):
            raise CliError("explicit simulation output directory must be empty and separate from the design")
    version = subprocess.run(["iverilog", "-V"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             universal_newlines=True).stdout
    try:
        language = _iverilog_language_option(version)
        # Reject invalid inputs before creating an explicit output directory.
        sim_command(design_dir, out, language,
                    component_exports=args.component_export, tb_top=args.tb_top)
    except source_set.SourceSetError as exc:
        raise CliError(str(exc)) from exc
    os.makedirs(out, exist_ok=True)
    try:
        staged = source_set.stage_component_exports(args.component_export, out)
        cmd = sim_command(design_dir, out, language,
                          component_sources=staged, tb_top=args.tb_top)
        source_set.stage_assets(design_dir, out)
    except source_set.SourceSetError as exc:
        raise CliError(str(exc)) from exc
    print("Simulating {} ...  output: {}".format(os.path.basename(design_dir), _shown(out)))
    log_path = os.path.join(out, LOG_NAME)
    with open(log_path, "w", encoding="utf-8") as log:
        log.write("# " + " ".join(cmd) + "\n")
        log.flush()
        rc = subprocess.run(cmd, cwd=out, stdout=log, stderr=subprocess.STDOUT).returncode
        if rc == 0:
            rc = subprocess.run(["vvp", os.path.join(out, "a.out")], cwd=out, stdout=log, stderr=subprocess.STDOUT).returncode
    with open(log_path, encoding="utf-8", errors="replace") as f:
        text = f.read()
    print(text, end="")
    if rc:
        print("unifpga: simulation failed with exit status {} (log: {})".format(rc, _shown(log_path)), file=sys.stderr)
        return rc
    if "ERROR" in text:
        print("unifpga: warning: errors detected in the simulation output", file=sys.stderr)
    vcd = os.path.join(out, "dump.vcd")
    if not os.path.isfile(vcd):
        print("unifpga: no dump.vcd written by the testbench; nothing to show")
        return 0
    if getattr(args, "no_wave", False):
        return 0
    viewer = _waveform_viewer()
    if viewer is None:
        print("unifpga: gtkwave / surfer not installed; the waveform is {}".format(_shown(vcd)))
        return 0
    script = os.path.join(design_dir, "gtkwave.tcl" if viewer[0] == "gtkwave" else "surfer.scr")
    extra = (["--script", script] if viewer[0] == "gtkwave" else ["--command-file", script]) if os.path.isfile(script) else []
    subprocess.Popen(viewer + extra + [vcd], cwd=out)
    return 0


def gui_command(toolchain_id, out_dir, bins=None):
    """Vendor GUI command for the last build in <out_dir>, or None with a
    reason. Quartus opens the .qpf, Vivado the
    latest checkpoint, Gowin the .gprj, Efinity the project XML."""
    def find(*patterns):
        for pat in patterns:
            hits = sorted(glob.glob(os.path.join(out_dir, pat)))
            if hits:
                return hits[0]
        return None
    if toolchain_id.startswith("quartus"):
        prj = find("*.qpf")
        return (["quartus", prj], None) if prj else (None, "no Quartus project in {} (run build first)".format(out_dir))
    if toolchain_id == "vivado":
        dcp = find("post_route.dcp", "post_place.dcp", "post_synth.dcp")
        return (["vivado", dcp], None) if dcp else (["vivado"], None)
    if toolchain_id in ("gowin_eda", "gowin_standard"):
        prj = find("*.gprj")
        return (["gw_ide", "-prj", prj], None) if prj else \
            (None, "no Gowin IDE project in {}: run build (or prepare) first; the board needs "
                   "toolchain_options.gowin.gprj_device".format(out_dir))
    if toolchain_id == "efinity":
        xml = find("unifpga_top.xml", "*.xml")
        return (["efinity", "--project", xml] if xml else ["efinity"], None)
    if toolchain_id.startswith("nextpnr_"):
        return (["nextpnr", "--gui"], None)             # marker: the place-and-route rerun with --gui
    return (None, "no GUI known for toolchain {}".format(toolchain_id))


def cmd_gui(args):
    design_dir = resolve_design(args.design)
    cfg_id = chosen_configuration(args.board)
    tc_id = target_configuration(cfg_id).get("toolchain", "")
    out = run_dir(design_dir, cfg_id)
    cmd, why = gui_command(tc_id, out)
    if cmd is None:
        raise CliError(why)
    if cmd == ["nextpnr", "--gui"]:
        exports = getattr(args, "component_export", ())
        if not exports and glob.glob(os.path.join(out, "component-exports-*")):
            raise CliError("this run contains component exports; pass --component-export "
                           "for each generated component when rerunning nextpnr")
        # the synthesis script runs again with nextpnr --gui; nextpnr opens its window in place-and-route
        print("Rerunning synthesis for {} with nextpnr --gui (the window opens at place-and-route) ...".format(cfg_id))
        sys.stdout.flush()
        os.environ["UNIFPGA_NEXTPNR_GUI"] = "1"
        try:
            return _run_synthesize(synthesize_argv(design_dir, cfg_id, "pnr",
                                                   component_exports=exports), out)
        finally:
            os.environ.pop("UNIFPGA_NEXTPNR_GUI", None)
    tc = config.init.resolve_toolchain_install(config.init.read_toolchains().get(tc_id) or {"id": tc_id})
    for d in reversed(tc.get("bin_dirs") or []):
        os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")
    if not shutil.which(cmd[0]):
        raise CliError("{} is not on PATH (./unifpga tools shows where the toolchain is looked for)".format(cmd[0]))
    print("Opening: " + " ".join(cmd))
    subprocess.Popen(cmd, cwd=out if os.path.isdir(out) else design_dir)
    return 0


def prepare_design(design_dir, cfg_id, component_exports=()):
    """Write <design>/run/<configuration>/ without running the tools: the
    generated top, constraints and the vendor project files (synthesize's
    dry run)."""
    out = run_dir(design_dir, cfg_id)
    os.environ["UNIFPGA_DRY_RUN"] = "1"
    try:
        return _run_synthesize(synthesize_argv(design_dir, cfg_id, "full",
                                               component_exports=component_exports), out)
    finally:
        os.environ.pop("UNIFPGA_DRY_RUN", None)


def cmd_prepare(args):
    cfg_id = chosen_configuration(args.board)
    exports = getattr(args, "component_export", ())
    if getattr(args, "all", False):
        if exports:
            raise CliError("--component-export selects one design; omit --all")
        failed = []
        names = list_designs()
        for name in names:
            if prepare_design(os.path.join(DESIGNS_DIR, name), cfg_id):
                failed.append(name)
        print("Prepared run/{c}/ in {n} design(s){f}".format(
            c=cfg_id, n=len(names) - len(failed),
            f="; failed: " + ", ".join(failed) if failed else ""))
        return 1 if failed else 0
    design_dir = resolve_design(args.design)
    print("Preparing {d} for {c} ...  output: {o}".format(
        d=os.path.basename(design_dir), c=cfg_id, o=_shown(run_dir(design_dir, cfg_id))))
    return prepare_design(design_dir, cfg_id, exports)


def cmd_tools(args):
    # The same report as `synthesize.py --list-toolchains`.
    print("\n".join(toolchain_detect.report(config.init.read_toolchains())))
    return 0


def cmd_designs(args):
    names = list_designs()
    if names:
        print("\n".join(names))
    else:
        print("no designs with {t} under {d}".format(t=TOP_NAME, d=DESIGNS_DIR), file=sys.stderr)
    return 0


def cmd_setup(args):
    """setup check [id...]: every setup is a sound rig — no rig errors, it
    expands to its configuration and that resolves. setup show <id>: print the
    configuration a setup expands to (what the build reads; not a file)."""
    from tools import setup as su
    setups = su.read_setups()
    if args.action == "show":
        if not args.ids:
            raise CliError("setup show <id>")
        for sid in args.ids:
            if sid not in setups:
                raise CliError("unknown setup '{}'".format(sid))
            sys.stdout.write(su.generated_text(setups[sid]))
        return 0
    ids = args.ids or sorted(setups)
    failed = 0
    with config.init.boards_frozen():
        for sid in ids:
            if sid not in setups:
                raise CliError("unknown setup '{}'".format(sid))
            problems = su.validate(setups[sid])
            try:
                su.generate(setups[sid])
                config.init.resolve_configuration(sid)
            except (su.SetupError, config.init.ConfigError) as exc:
                problems.append(("error", str(exc)))
            errors = [m for level, m in problems if level == "error"]
            failed += bool(errors)
            print("{:<48} {}".format(sid, "FAIL" if errors else "ok"))
            for level, msg in problems:
                print("    {}: {}".format(level, msg))
    return 1 if failed else 0


def cmd_check(args):
    """check [entity...]: every configuration file against its schema
    (config/schema/), every reference between entities resolved, the rules
    a reference cannot express (tools/check.py)."""
    from tools import check
    try:
        report = check.check()
    except check.CheckError as exc:
        raise CliError(str(exc))
    unknown = [e for e in args.entities if e not in report["entities"]]
    if unknown:
        raise CliError("unknown entity {} (one of {})".format(", ".join(unknown), ", ".join(report["entities"])))
    if args.json:
        json.dump(report, sys.stdout, indent=1, sort_keys=True)
        print()
    else:
        sys.stdout.write(check.render(report, set(args.entities) or None))
    return 1 if report["status"] == "failed" else 0


def cmd_interface(args):
    """interface [--write] [design.sv ...]: is rtl/peripherals/design_top_interface.sv
    what config/design_top.yml and the capabilities render to, and does every
    design (or those named) take its module header from the rendered include
    design_top_interface.svh; --write renders the file and the includes, and
    converts a design still carrying a hand-written header."""
    from tools import design_top
    stale = 0
    try:
        targets = [os.path.abspath(p) for p in args.designs] if args.designs else design_top.design_files()
        if not args.designs:
            if args.write:
                print("{:<10} {}".format("wrote" if design_top.write() else "current", _shown(design_top.INTERFACE)))
            elif not design_top.is_current():
                stale += 1
                print("{:<10} {}".format("stale", _shown(design_top.INTERFACE)))
        for path in targets:
            with open(path, encoding="utf-8") as f:
                text = f.read()
            if design_top.includes_header(text):
                if args.write:
                    changed = design_top.write_include(path)
                    print("{:<10} {}".format("rendered" if changed else "current", _shown(design_top.include_path(path))))
                continue
            if args.write:
                design_top.convert(path)
                print("{:<10} {} (its header is now {})".format("converted", _shown(path), design_top.INCLUDE_NAME))
            else:
                stale += 1
                print("{:<10} {} carries a hand-written module header".format("hand", _shown(path)))
    except (config.init.ConfigError, codegen.CodegenError, OSError) as exc:
        raise CliError(str(exc))
    if not args.write:
        print("{} to render ({} designs looked at){}".format(stale, len(targets), "; ./unifpga interface --write" if stale else ""))
    return 1 if stale else 0


def cmd_view(args):
    """Write the board editor's page for a setup (or, with --board, a board)
    with its data inlined, read-only."""
    from tools import setup as su, studio
    out = args.output or "{}.html".format(args.id)
    try:
        page = studio.standalone_page(board_id=args.id) if args.board else studio.standalone_page(setup_id=args.id)
    except (su.SetupError, studio.ApiError) as exc:
        raise CliError(str(exc))
    with open(out, "w", encoding="utf-8") as f:
        f.write(page)
    print("wrote {}".format(os.path.abspath(out)))
    return 0


def free_port(first=8765, last=8789, host="127.0.0.1"):
    """The editor's port: `first` unless it is taken (an editor already running?),
    then the next free one, said so on stderr; None when none is free."""
    import socket
    for port in range(first, last + 1):
        try:
            with socket.socket() as s:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)   # as the server binds
                s.bind((host, port))
        except OSError:
            continue
        if port != first:
            sys.stderr.write("unifpga: port {0} is in use (an editor already running? http://{2}:{0}/) — using {1}\n".format(first, port, host))
        return port
    return None


def cmd_serve(args):
    from tools import studio
    from tools import setup as su
    port = args.port if args.port is not None else free_port()
    if port is None:
        raise CliError("no free port between 8765 and 8789; name one with --port")
    try:
        studio.serve(port=port)
    except su.SetupError as exc:
        raise CliError(str(exc))
    return 0


def cmd_layout(args):
    """layout draft [board...] [--all]: draw the board's headers and parts into
    its file (the drawn section of config/boards/<producer>/<family>/<board>.yml)
    from its banks, its rigs and the facts already drawn from documents (tools/layout_draft.py);
    a hand-made drawn section is left alone."""
    from tools import layout_draft
    boards = sorted({c["board"] for c in config.init.read_configurations().values()})
    ids = boards if args.all else args.boards
    if not ids:
        raise CliError("name boards, or --all")
    for b in ids:
        if b not in boards:
            raise CliError("no configuration uses board '{}'".format(b))
        if not layout_draft.is_generated(b):
            print("{:<32} drawn by hand, left alone".format(b))
            continue
        path, changed = layout_draft.write(b)
        layout = layout_draft.draft(b)
        if args.setups:
            # the board's setups follow its layout (connector ids, variants)
            from tools import setup as su
            for cid, cfg in sorted(config.init.read_configurations().items()):
                if cfg["board"] == b:
                    diffs = su.check_roundtrip(cfg)
                    if diffs:
                        raise CliError("{} does not round-trip with the new layout:\n  {}".format(cid, "\n  ".join(diffs)))
                    su.write_setup(su.derive(cfg))
        print("{:<32} {} {} ({} headers, {} on-board parts{})".format(
            b, "wrote" if changed else "unchanged", _shown(path), len(layout["headers"]), len(layout["parts"]),
            ", verified" if layout["verified"] else ""))
    return 0


def cmd_inventory(args):
    """inventory import <file.yml>... [--dry-run]: a board inventory's devices into
    the board's banks (new banks only, each with its source), its product name,
    summary and documents into the board's file (tools/inventory.py); then
    `./unifpga layout draft` draws them."""
    from tools import inventory
    rc = 0
    for path in args.files:
        try:
            print(inventory.report_text(inventory.import_inventory(inventory.read(path), write=not args.dry_run)))
        except inventory.InventoryError as exc:
            print("{}: {}".format(path, exc))
            rc = 1
    return rc


def cmd_sources(args):
    """sources fetch [board...]: download the documents a board's file lists
    into the cache and record their SHA-256; sources text <board> <doc>
    [--pages a-b] [--grep re]: a document's text; sources verify [board...]:
    the facts (headers, parts and banks with a source) against the banks."""
    from tools import board_sources as bs
    boards = bs.boards_with_documents()
    if args.action == "text":
        if len(args.ids) != 2:
            raise CliError("sources text <board> <document id>")
        doc = bs.documents(boards.get(args.ids[0])).get(args.ids[1])
        if doc is None:
            raise CliError("no document '{}' for {}".format(args.ids[1], args.ids[0]))
        pages = tuple(int(x) for x in args.pages.split("-")) if args.pages else None
        if pages and len(pages) == 1:
            pages = (pages[0], pages[0])
        try:
            texts = bs.text(args.ids[0], doc, pages)
        except bs.SourcesError as e:
            raise CliError(str(e))
        for n, txt in texts:
            lines = txt.splitlines()
            if args.grep:
                lines = [l for l in lines if re.search(args.grep, l, re.I)]
                if not lines:
                    continue
            print("=== page {} ===".format(n))
            print("\n".join(lines))
        return 0
    ids = args.ids or sorted(boards)
    failed = 0
    for b in ids:
        if b not in boards:
            raise CliError("board '{}' lists no documents (its file's documents:)".format(b))
        if args.action == "fetch":
            for doc in boards[b].get("documents") or []:
                try:
                    got = bs.fetch(b, doc)
                    if got["sha256"] != doc.get("sha256") or got["bytes"] != doc.get("bytes"):
                        bs.record_fetch(b, doc["id"], got)
                    print("{:<28} {:<14} {} ({} bytes)".format(b, doc["id"], "fetched" if got["fresh"] else "cached", got["bytes"]))
                except bs.SourcesError as exc:
                    failed += 1
                    print("{:<28} {:<14} FAILED: {}".format(b, doc["id"], exc))
            continue
        v = bs.verify(boards[b])
        bad = v["documents"] + ["header {}: {}".format(k, p) for k, ps in v["headers"].items() for p in ps] + \
            ["part {}: {}".format(k, p) for k, ps in v["parts"].items() for p in ps]
        failed += bool(bad)
        print("{:<32} {} ({} headers, {} parts or banks checked)".format(b, "FAIL" if bad else "ok", len(v["headers"]), len(v["parts"])))
        for line in bad:
            print("    " + line)
        for line in v["info"]:
            print("    note: " + line)
    return 1 if failed else 0


COMMANDS = {
    "board": cmd_board,
    "build": cmd_build,
    "program": cmd_program,
    "sim": cmd_sim,
    "gui": cmd_gui,
    "prepare": cmd_prepare,
    "clean": cmd_clean,
    "tools": cmd_tools,
    "designs": cmd_designs,
    "setup": cmd_setup,
    "check": cmd_check,
    "interface": cmd_interface,
    "layout": cmd_layout,
    "sources": cmd_sources,
    "inventory": cmd_inventory,
    "view": cmd_view,
    "serve": cmd_serve,
}


def build_parser():
    p = argparse.ArgumentParser(
        prog="unifpga",
        description="Build and program FPGA designs with one remembered board choice.",
        epilog=QUICK_START + "\n"
               "UNIFPGA_BOARD=<id> overrides the remembered choice for one command.\n"
               "Full control (include dirs, temp output, ...): PYTHONPATH=. python3 synthesize.py -h",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="command", title="commands", metavar="<command>")

    b = sub.add_parser("board", help="choose the board configuration (menu, `board <id>`, or -l to list)")
    b.add_argument("id", nargs="?", help="rig id (config/setups/<id>.yml)")
    b.add_argument("-l", "--list", action="store_true", help="list the configurations without prompting")

    def design_arg(sp):
        sp.add_argument("design", nargs="?",
                        help="design directory or designs/<name> (default: the current directory)")

    def board_arg(sp):
        sp.add_argument("-b", "--board", metavar="ID", help="configuration id for this run only")

    bd = sub.add_parser("build", help="synthesize a design into <design>/run/<configuration>/")
    design_arg(bd)
    board_arg(bd)
    bd.add_argument("-s", "--step", choices=["elaborate", "pnr", "full"], default="full",
                    help="stop after this step (default: full)")
    bd.add_argument("--component-export", action="append", default=[], metavar="MANIFEST",
                    help="include digest-checked generated RTL from a component export")

    pr = sub.add_parser("program", help="synthesize (full) and program the connected board")
    design_arg(pr)
    board_arg(pr)
    pr.add_argument("--no-build", action="store_true",
                    help="load the bitstream of the last build in run/<configuration>/ without rebuilding")
    pr.add_argument("--component-export", action="append", default=[], metavar="MANIFEST",
                    help="include generated RTL when building before programming")

    sm = sub.add_parser("sim", help="simulate <design>/tb.sv with Icarus Verilog, open the waveform")
    design_arg(sm)
    sm.add_argument("-n", "--no-wave", action="store_true", help="do not open gtkwave / surfer")
    sm.add_argument("--component-export", action="append", default=[], metavar="MANIFEST",
                    help="include digest-checked generated RTL from a component export")
    sm.add_argument("--tb-top", default="tb", metavar="MODULE",
                    help="testbench module to elaborate (default: tb)")
    sm.add_argument("--output-dir", metavar="DIR",
                    help="write simulation outputs to an empty directory")

    gu = sub.add_parser("gui", help="open the vendor GUI on the last build")
    design_arg(gu)
    board_arg(gu)
    gu.add_argument("--component-export", action="append", default=[], metavar="MANIFEST",
                    help="include generated RTL when nextpnr reruns synthesis for its GUI")

    pp = sub.add_parser("prepare", help="write run/<configuration>/ (top, constraints, project) without running the tools")
    design_arg(pp)
    board_arg(pp)
    pp.add_argument("--component-export", action="append", default=[], metavar="MANIFEST",
                    help="include generated RTL in this design's prepared project")
    pp.add_argument("--all", action="store_true", help="every design under designs/")

    cl = sub.add_parser("clean", help="remove <design>/run/ (--all: every design)")
    design_arg(cl)
    cl.add_argument("--all", action="store_true", help="remove run/ of every design under designs/")

    sub.add_parser("tools", help="report where each toolchain was found (or why not)")
    sub.add_parser("designs", help="list the designs under designs/")

    st = sub.add_parser("setup", help="check the rigs (config/setups/), or show the configuration one expands to")
    st.add_argument("action", choices=["check", "show"])
    st.add_argument("ids", nargs="*", help="setup ids (check: default all)")

    ck = sub.add_parser("check", help="every configuration file against its schema (config/schema/) and every "
                                      "reference between entities resolved")
    ck.add_argument("entities", nargs="*", help="only these entities (default: all; see config/schema/entities.yml)")
    ck.add_argument("--json", action="store_true", help="print the report as JSON")

    it = sub.add_parser("interface", help="the design_top interface rendered from config/design_top.yml and the capabilities: "
                                          "rtl/peripherals/design_top_interface.sv and each design's design_top_interface.svh "
                                          "(--write: render them; a hand-written header is converted)")
    it.add_argument("designs", nargs="*", help="design_top.sv files (default: every design under designs/, and the interface file)")
    it.add_argument("--write", action="store_true", help="render")

    ly = sub.add_parser("layout", help="draw boards' headers and parts (the drawn section of their files) "
                                        "from their banks, their rigs and the facts already drawn from documents")
    ly.add_argument("action", choices=["draft"])
    ly.add_argument("boards", nargs="*")
    ly.add_argument("--all", action="store_true", help="every board a configuration uses")
    ly.add_argument("--setups", action="store_true", help="re-derive the board's setups from its configurations")

    so = sub.add_parser("sources", help="the documents a board's file lists: fetch them into the cache, "
                                        "show their text, verify the facts read from them against the banks")
    so.add_argument("action", choices=["fetch", "text", "verify"])
    so.add_argument("ids", nargs="*", help="boards (text: <board> <document id>)")
    so.add_argument("--pages", help="text: a page or a range, e.g. 30-34")
    so.add_argument("--grep", help="text: only lines matching this regular expression")

    iv = sub.add_parser("inventory", help="import board inventories (every on-board device with its pins and "
                                           "sources) into the boards' files")
    iv.add_argument("action", choices=["import"])
    iv.add_argument("files", nargs="+", help="inventory YAML files")
    iv.add_argument("--dry-run", action="store_true", help="report what would change, write nothing")

    vw = sub.add_parser("view", help="write a read-only page drawing a setup (or a board with --board)")
    vw.add_argument("id", help="setup id, or board id with --board")
    vw.add_argument("--board", action="store_true", help="the id is a board: draw its layout")
    vw.add_argument("-o", "--output", help="output file (default: <id>.html)")

    sv = sub.add_parser("serve", help="the board editor on a local web page (http://127.0.0.1:8765/, or the next free port)")
    sv.add_argument("--port", type=int, default=None, help="the port (default: 8765, else the next free one up to 8789)")
    return p


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 0
    try:
        return COMMANDS[args.command](args)
    except (CliError, config.init.ConfigError) as exc:
        print("unifpga: {}".format(exc), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print(file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
