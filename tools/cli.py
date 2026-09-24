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
except ImportError:                         # the one dependency (requirements.txt)
    sys.exit("unifpga: PyYAML is not installed for {py}.\n"
             "Install it ({py} -m pip install pyyaml) or run the launcher with a Python that has it."
             .format(py=sys.executable))

REPO = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
if REPO not in sys.path:                    # `python3 tools/cli.py` without the launcher
    sys.path.insert(0, REPO)

import config.init                          # noqa: E402
import program                              # noqa: E402  (called, never shelled out)
import synthesize                           # noqa: E402  (called, never shelled out)
from tools import toolchain_detect          # noqa: E402

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
    """{configuration id: Configuration dict} from config/configurations/."""
    try:
        return config.init.read_configurations()
    except config.init.ConfigError as exc:
        raise CliError(str(exc))


def _unknown_configuration(cfg_id, origin, ids):
    close = difflib.get_close_matches(cfg_id, ids, n=5, cutoff=0.5)
    return ("Unknown configuration '{c}' (from {o}). ./unifpga board -l lists the ids{hint}"
            .format(c=cfg_id, o=origin,
                    hint="; did you mean: " + ", ".join(close) + "?" if close else "."))


def chosen_configuration(override=None):
    """The configuration id to build for: -b/--board, else $UNIFPGA_BOARD,
    else settings.yml. Validated against config/configurations/."""
    if override:
        cfg_id, origin = override, "-b/--board"
    elif os.environ.get(ENV_BOARD):
        cfg_id, origin = os.environ[ENV_BOARD], "$" + ENV_BOARD
    else:
        cfg_id, origin = read_settings(), SETTINGS_PATH
    if not cfg_id:
        raise CliError("No board chosen yet. Run ./unifpga board "
                       "(./unifpga board -l lists the configurations).")
    ids = sorted(configurations())
    if cfg_id not in ids:
        raise CliError(_unknown_configuration(cfg_id, origin, ids))
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


def synthesize_argv(design_dir, cfg_id, step="full", program=False):
    """The synthesize.py arguments `build` and `program` run. Absolute paths:
    the toolchain drivers run their tools with cwd set to the output dir."""
    argv = ["-c", cfg_id,
            "--top", os.path.join(design_dir, TOP_NAME),
            "-s", step,
            "-o", run_dir(design_dir, cfg_id)]
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
    (tools/toolchain_detect.py: InstallDir pin, vendor variable, PATH, the
    default install directories)."""
    found = {}
    for tid, tc in config.init.read_toolchains().items():
        found[tid] = toolchain_detect.detect(tid, pin=tc.get("InstallDir")).found
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
        if raw in ids:
            return raw
        if raw.isdigit() and 1 <= int(raw) <= len(ids):
            return ids[int(raw) - 1]
        print("Not one of the listed numbers (1..{n}); please choose again.".format(n=len(ids)))


def _select(cfgs, cfg_id, installed=None):
    if cfg_id not in cfgs:
        raise CliError(_unknown_configuration(cfg_id, "the command line", sorted(cfgs)))
    write_settings(cfg_id)
    cfg = cfgs[cfg_id]
    tc = cfg.get("toolchain", "?")
    print("Board configuration: {id}  (board {b}, toolchain {tc}) -- saved to {p}".format(
        id=cfg_id, b=cfg.get("board", "?"), tc=tc, p=SETTINGS_PATH))
    pin = (config.init.read_toolchains().get(tc) or {}).get("InstallDir")
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
    return _run_synthesize(synthesize_argv(design_dir, cfg_id, step, program), out)


def cmd_program(args):
    if not getattr(args, "no_build", False):
        return cmd_build(args, program=True)
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


def sim_sources(design_dir):
    """The files a simulation compiles: the design
    directory's *.sv / *.v (tb.sv included), the design-common helpers, and
    the peripheral models the design directory does not shadow."""
    rtl = os.path.join(REPO, "rtl")
    files = [os.path.join(rtl, "sim", "timescale.sv")]          # `timescale 1 ns / 1 ps first
    files += sorted(glob.glob(os.path.join(design_dir, "*.sv")) + glob.glob(os.path.join(design_dir, "*.v")))
    files += sorted(glob.glob(os.path.join(design_dir, "cpu", "*.sv")) + glob.glob(os.path.join(design_dir, "cpu", "*.v")))
    # peripherals/*.sv too (LCD testbenches instantiate the panel timing
    # modules); only tb's hierarchy is elaborated (-s tb), so
    # unreferenced models cost nothing
    local = {os.path.basename(f) for f in files}
    for sub, pattern in (("peripherals/designs_common", "*.sv"), ("peripherals", "*.sv"), ("peripherals", "*.v"),
                         ("io", "*.sv"), ("pll", "*.sv"), ("sim", "*.sv")):
        files += sorted(f for f in glob.glob(os.path.join(rtl, sub, pattern))
                        if os.path.basename(f) != "design_top_interface.sv" and os.path.basename(f) not in local)
    return files


def sim_command(design_dir, out_dir, lang="-g2012"):
    # SIMULATION enables the simulation-only modules (fifo_monitor and
    # others sit behind `ifdef SIMULATION)
    return (["iverilog", lang, "-D", "SIMULATION", "-s", "tb", "-o", os.path.join(out_dir, "a.out"),
             "-I", design_dir, "-I", os.path.join(design_dir, "cpu"),
             "-I", os.path.join(REPO, "rtl", "peripherals"),
             "-I", os.path.join(REPO, "rtl", "peripherals", "designs_common")]
            + sim_sources(design_dir))


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
    out = os.path.join(run_dir(design_dir), SIM_DIR_NAME)
    os.makedirs(out, exist_ok=True)
    version = subprocess.run(["iverilog", "-V"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             universal_newlines=True).stdout
    cmd = sim_command(design_dir, out, _iverilog_language_option(version))
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
            (None, "no Gowin IDE project in {}: run build (or prepare) first; the pinmap needs "
                   "toolchain_options.gowin.gprj_device (sync --gowin-options)".format(out_dir))
    if toolchain_id == "efinity":
        xml = find("unifpga_top.xml", "*.xml")
        return (["efinity", "--project", xml] if xml else ["efinity"], None)
    if toolchain_id.startswith("nextpnr_"):
        return (["nextpnr", "--gui"], None)             # marker: the place-and-route rerun with --gui
    return (None, "no GUI known for toolchain {}".format(toolchain_id))


def cmd_gui(args):
    design_dir = resolve_design(args.design)
    cfg_id = chosen_configuration(args.board)
    cfgs = configurations()
    tc_id = cfgs[cfg_id].get("toolchain", "")
    out = run_dir(design_dir, cfg_id)
    cmd, why = gui_command(tc_id, out)
    if cmd is None:
        raise CliError(why)
    if cmd == ["nextpnr", "--gui"]:
        # the synthesis script runs again with nextpnr --gui; nextpnr opens its window in place-and-route
        print("Rerunning synthesis for {} with nextpnr --gui (the window opens at place-and-route) ...".format(cfg_id))
        sys.stdout.flush()
        os.environ["UNIFPGA_NEXTPNR_GUI"] = "1"
        try:
            return _run_synthesize(synthesize_argv(design_dir, cfg_id, "pnr"), out)
        finally:
            os.environ.pop("UNIFPGA_NEXTPNR_GUI", None)
    tc = config.init.resolve_toolchain_install(config.init.read_toolchains().get(tc_id) or {"Id": tc_id})
    for d in reversed(tc.get("BinDirs") or []):
        os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")
    if not shutil.which(cmd[0]):
        raise CliError("{} is not on PATH (./unifpga tools shows where the toolchain is looked for)".format(cmd[0]))
    print("Opening: " + " ".join(cmd))
    subprocess.Popen(cmd, cwd=out if os.path.isdir(out) else design_dir)
    return 0


def prepare_design(design_dir, cfg_id):
    """Write <design>/run/<configuration>/ without running the tools: the
    generated top, constraints and the vendor project files (synthesize's
    dry run)."""
    out = run_dir(design_dir, cfg_id)
    os.environ["UNIFPGA_DRY_RUN"] = "1"
    try:
        return _run_synthesize(synthesize_argv(design_dir, cfg_id, "full"), out)
    finally:
        os.environ.pop("UNIFPGA_DRY_RUN", None)


def cmd_prepare(args):
    cfg_id = chosen_configuration(args.board)
    if getattr(args, "all", False):
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
    return prepare_design(design_dir, cfg_id)


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
    """setup check: every setup generates its configuration (data and text) and
    has no rig errors. setup generate [id...]: write config/configurations/<id>.yml
    from the setup. setup derive <id>...: write config/setups/<id>.yml from the
    configuration (its board needs a layout)."""
    from tools import setup as su
    if args.action == "generate":
        setups = su.read_setups()
        for sid in args.ids or sorted(setups):
            if sid not in setups:
                raise CliError("unknown setup '{}'".format(sid))
            path = su.configuration_path(sid)
            text = su.generated_text(setups[sid])
            old = open(path, encoding="utf-8").read() if os.path.exists(path) else None
            if old != text:
                with open(path, "w", encoding="utf-8") as f:
                    f.write(text)
                print("wrote {}".format(_shown(path)))
        return 0
    if args.action == "derive":
        configurations = config.init.read_configurations()
        for cid in args.ids:
            if cid not in configurations:
                raise CliError("unknown configuration '{}'".format(cid))
            cfg = configurations[cid]
            diffs = su.check_roundtrip(cfg)
            if diffs:
                raise CliError("{} does not round-trip:\n  {}".format(cid, "\n  ".join(diffs)))
            print("wrote {}".format(_shown(su.write_setup(su.derive(cfg)))))
        return 0
    setups = su.read_setups()
    ids = args.ids or sorted(setups)
    configurations = config.init.read_configurations()
    failed = 0
    for sid in ids:
        if sid not in setups:
            raise CliError("unknown setup '{}'".format(sid))
        problems = su.validate(setups[sid])
        cfg = configurations.get(sid)
        if cfg is None:
            problems.append(("error", "no configuration {} to compare with".format(sid)))
        elif not su.same_configuration(su.generate(setups[sid]), cfg):
            problems.append(("error", "does not generate config/configurations/{}.yml".format(sid)))
        elif open(su.configuration_path(sid), encoding="utf-8").read() != su.generated_text(setups[sid]):
            problems.append(("error", "config/configurations/{}.yml differs from its setup's text "
                                      "(./unifpga setup generate {})".format(sid, sid)))
        errors = [m for level, m in problems if level == "error"]
        failed += bool(errors)
        print("{:<48} {}".format(sid, "FAIL" if errors else "ok"))
        for level, msg in problems:
            print("    {}: {}".format(level, msg))
    return 1 if failed else 0


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


def cmd_serve(args):
    from tools import studio
    studio.serve(port=args.port)
    return 0


def cmd_layout(args):
    """layout draft [board...] [--all]: write config/layouts/<board>.yml from the
    board's pinmap, configurations and registry (tools/layout_draft.py); hand-made
    layouts are left alone."""
    from tools import layout_draft
    boards = sorted({c["board"] for c in config.init.read_configurations().values()})
    ids = boards if args.all else args.boards
    if not ids:
        raise CliError("name boards, or --all")
    for b in ids:
        if b not in boards:
            raise CliError("no configuration uses board '{}'".format(b))
        if not layout_draft.is_generated(b):
            print("{:<32} hand-made layout, left alone".format(b))
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
            b, "wrote" if changed else "unchanged", _shown(path), len(layout["connectors"]), len(layout["onboard"]),
            ", verified" if layout["verified"] else ""))
    return 0


def cmd_sources(args):
    """sources fetch [board...]: download the registry's documents into the cache
    and record their SHA-256; sources text <board> <doc> [--pages a-b] [--grep re]:
    a document's text; sources verify [board...]: facts against the pinmaps."""
    from tools import board_sources as bs
    registry = bs.read_all()
    if args.action == "text":
        if len(args.ids) != 2:
            raise CliError("sources text <board> <document id>")
        entry = registry.get(args.ids[0]) or {}
        doc = next((d for d in entry.get("documents") or [] if d["id"] == args.ids[1]), None)
        if doc is None:
            raise CliError("no document '{}' for {}".format(args.ids[1], args.ids[0]))
        pages = tuple(int(x) for x in args.pages.split("-")) if args.pages else None
        if pages and len(pages) == 1:
            pages = (pages[0], pages[0])
        for n, txt in bs.text(args.ids[0], doc, pages):
            lines = txt.splitlines()
            if args.grep:
                lines = [l for l in lines if re.search(args.grep, l, re.I)]
                if not lines:
                    continue
            print("=== page {} ===".format(n))
            print("\n".join(lines))
        return 0
    ids = args.ids or sorted(registry)
    failed = 0
    for b in ids:
        if b not in registry:
            raise CliError("no registry entry {} ($UNIFPGA_SOURCES_DIR)".format(bs.registry_path(b)))
        if args.action == "fetch":
            for doc in registry[b].get("documents") or []:
                try:
                    got = bs.fetch(b, doc)
                    if got["sha256"] != doc.get("sha256") or got["bytes"] != doc.get("bytes"):
                        bs.record_fetch(b, doc["id"], got)
                    print("{:<28} {:<14} {} ({} bytes)".format(b, doc["id"], "fetched" if got["fresh"] else "cached", got["bytes"]))
                except bs.SourcesError as exc:
                    failed += 1
                    print("{:<28} {:<14} FAILED: {}".format(b, doc["id"], exc))
            continue
        v = bs.verify(b, registry[b])
        bad = v["documents"] + ["header {}: {}".format(k, p) for k, ps in v["headers"].items() for p in ps] + \
            ["on-board {}: {}".format(k, p) for k, ps in v["onboard"].items() for p in ps]
        failed += bool(bad)
        print("{:<32} {} ({} headers, {} on-board parts checked)".format(b, "FAIL" if bad else "ok", len(v["headers"]), len(v["onboard"])))
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
    "layout": cmd_layout,
    "sources": cmd_sources,
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
    b.add_argument("id", nargs="?", help="configuration id (config/configurations/<id>.yml)")
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

    pr = sub.add_parser("program", help="synthesize (full) and program the connected board")
    design_arg(pr)
    board_arg(pr)
    pr.add_argument("--no-build", action="store_true",
                    help="load the bitstream of the last build in run/<configuration>/ without rebuilding")

    sm = sub.add_parser("sim", help="simulate <design>/tb.sv with Icarus Verilog, open the waveform")
    design_arg(sm)
    sm.add_argument("-n", "--no-wave", action="store_true", help="do not open gtkwave / surfer")

    gu = sub.add_parser("gui", help="open the vendor GUI on the last build")
    design_arg(gu)
    board_arg(gu)

    pp = sub.add_parser("prepare", help="write run/<configuration>/ (top, constraints, project) without running the tools")
    design_arg(pp)
    board_arg(pp)
    pp.add_argument("--all", action="store_true", help="every design under designs/")

    cl = sub.add_parser("clean", help="remove <design>/run/ (--all: every design)")
    design_arg(cl)
    cl.add_argument("--all", action="store_true", help="remove run/ of every design under designs/")

    sub.add_parser("tools", help="report where each toolchain was found (or why not)")
    sub.add_parser("designs", help="list the designs under designs/")

    st = sub.add_parser("setup", help="check setups (config/setups/), generate their configurations, "
                                      "or derive a setup from a configuration")
    st.add_argument("action", choices=["check", "generate", "derive"])
    st.add_argument("ids", nargs="*", help="setup / configuration ids (check: default all)")

    ly = sub.add_parser("layout", help="generate board layouts (config/layouts/) from pinmaps, configurations "
                                        "and the board-sources registry")
    ly.add_argument("action", choices=["draft"])
    ly.add_argument("boards", nargs="*")
    ly.add_argument("--all", action="store_true", help="every board a configuration uses")
    ly.add_argument("--setups", action="store_true", help="re-derive the board's setups from its configurations")

    so = sub.add_parser("sources", help="the board-sources registry ($UNIFPGA_SOURCES_DIR, default "
                                        "../unifpga-board-sources): fetch documents, "
                                        "show their text, verify the facts against the pinmaps")
    so.add_argument("action", choices=["fetch", "text", "verify"])
    so.add_argument("ids", nargs="*", help="boards (text: <board> <document id>)")
    so.add_argument("--pages", help="text: a page or a range, e.g. 30-34")
    so.add_argument("--grep", help="text: only lines matching this regular expression")

    vw = sub.add_parser("view", help="write a read-only page drawing a setup (or a board with --board)")
    vw.add_argument("id", help="setup id, or board id with --board")
    vw.add_argument("--board", action="store_true", help="the id is a board: draw its layout")
    vw.add_argument("-o", "--output", help="output file (default: <id>.html)")

    sv = sub.add_parser("serve", help="the board editor on a local web page (http://127.0.0.1:8765/)")
    sv.add_argument("--port", type=int, default=8765)
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
