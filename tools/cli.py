#!/usr/bin/env python3
"""
`./unifpga` -- the short command line, modelled on BGM's lab scripts.

BGM's flow: choose a board once (scripts/06_choose_another_fpga_board), then
run 03_synthesize_for_fpga / 04_configure_fpga / 01_clean inside a lab
directory with no parameters; the output lands in the lab's run/ directory.
The equivalent here:

    ./unifpga board                # numbered menu; remembered in settings.yml
    cd designs/1_06_binary_counter
    ../../unifpga build            # -> run/<configuration id>/
    ../../unifpga program          # synthesize (full) and load the bitstream
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
import os
import shutil
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
    if installed is None:
        pin = (config.init.read_toolchains().get(tc) or {}).get("InstallDir")
        installed = {tc: toolchain_detect.detect(tc, pin=pin).found}
    if not installed.get(tc):
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
    return _select(cfgs, choice, installed)


# ---------------------------------------------------------------------------
# build / program / clean / tools / designs
# ---------------------------------------------------------------------------

def _shown(path):
    rel = os.path.relpath(path)
    return path if rel.startswith("..") else rel


def cmd_build(args, program=False):
    design_dir = resolve_design(args.design)
    cfg_id = chosen_configuration(args.board)
    step = "full" if program else args.step
    print("{verb} {d} for {c} ...  output: {o}".format(
        verb="Building and programming" if program else "Building",
        d=os.path.basename(design_dir), c=cfg_id, o=_shown(run_dir(design_dir, cfg_id))))
    sys.stdout.flush()
    return synthesize.main(synthesize_argv(design_dir, cfg_id, step, program))


def cmd_program(args):
    return cmd_build(args, program=True)


def cmd_clean(args):
    design_dir = resolve_design(args.design)
    removed = remove_run_dir(design_dir)
    if removed:
        print("Removed {}".format(_shown(removed)))
    else:
        print("Nothing to clean: {} does not exist".format(_shown(run_dir(design_dir))))
    return 0


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


COMMANDS = {
    "board": cmd_board,
    "build": cmd_build,
    "program": cmd_program,
    "clean": cmd_clean,
    "tools": cmd_tools,
    "designs": cmd_designs,
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

    cl = sub.add_parser("clean", help="remove <design>/run/")
    design_arg(cl)

    sub.add_parser("tools", help="report where each toolchain was found (or why not)")
    sub.add_parser("designs", help="list the designs under designs/")
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
