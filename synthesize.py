#!/usr/bin/env python3
"""
UniFPGA top-level entry point.

Resolve a rig's configuration (its setup, config/setups/<id>.yml), import the matching
toolchain module (toolchains/<toolchain_id>/<toolchain_id>.py), and dispatch
synthesis to it.

Usage:
    synthesize.py --top top.sv                          # use settings.yml
    synthesize.py -c nexys4_ddr_default --top top.sv    # explicit configuration
    synthesize.py -c <id> --top top.sv -o build/        # custom output dir
"""

import argparse
import importlib
import logging
import os
import shutil
import sys
import tempfile

import config.init


log = logging.getLogger(__name__)
dir_path = os.path.dirname(os.path.realpath(__file__))


def prepare_toolchain(toolchain):
    """Report where the toolchain was found and put its tool directories first
    in PATH (the drivers resolve their binaries with shutil.which())."""
    if toolchain.get("detect_source"):
        log.info("Toolchain %s: %s (%s)", toolchain["id"], toolchain.get("install_dir") or
                 ", ".join(toolchain.get("bin_dirs") or []), toolchain["detect_source"])
    else:
        for note in toolchain.get("detect_notes") or []:
            log.warning("Toolchain %s: %s", toolchain["id"], note)
    for d in reversed(toolchain.get("bin_dirs") or []):
        os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")


def toolchain_module(toolchain):
    return importlib.import_module("toolchains.{id}.{id}".format(id=toolchain["id"]))


def driver_exit_code(result, operation):
    """A driver must explicitly report an integer exit status."""
    if type(result) is not int:
        log.error("Toolchain %s returned %r instead of an integer exit code", operation, result)
        return 2
    return result


def _build_parser():
    p = argparse.ArgumentParser(description="UniFPGA Compile")
    p.add_argument("-c", "--configuration",
                   help="Rig id (a setup, config/setups/<id>.yml). "
                        "If omitted, settings.yml is consulted; if that's also absent, "
                        "the user is prompted interactively.")
    p.add_argument("-t", "--toolchain",
                   help="build the configuration with this of its toolchains instead of its default "
                        "(the setup's toolchains:)")
    p.add_argument("--part",
                   help="build for this of the board's chips instead of the configuration's default part:")
    p.add_argument("-s", "--step", choices=["elaborate", "pnr", "full"], default="full",
                   help="Compilation step (default: full)")
    p.add_argument("--top", required=True,
                   help="Path to the top module")
    p.add_argument("-i", "--include", action="append", default=[],
                   help="Include directory (repeatable)")
    p.add_argument("--component-export", action="append", default=[], metavar="MANIFEST",
                   help="include digest-checked generated RTL from a component export")
    p.add_argument("-o", "--output",
                   help="Output folder for board-specific and toolchain-specific artifacts. "
                        "If omitted, a temp dir is created and deleted on exit unless "
                        "--keep-temp-dir is set.")
    p.add_argument("--no-profile", action="store_true",
                   help="ignore the rig's design section and design_bits (config/setups/<id>.yml): generate the "
                        "generic composition, buses concatenated in attach order, power-up reset")
    p.add_argument("--keep-temp-dir", action="store_true",
                   help="Keep the auto-created temp output dir instead of deleting it")
    p.add_argument("--program", action="store_true",
                   help="After successful synthesis, download the bitstream "
                        "to the connected board (calls toolchain.program()).")
    p.add_argument("--list-toolchains", action="store_true",
                   help="Report where every toolchain in config/toolchains.yml was found "
                        "(InstallDir pin, vendor environment variable, PATH, or the default "
                        "install directories the vendors use) and exit.")
    return p


def main(argv=None):
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    if "--list-toolchains" in (sys.argv[1:] if argv is None else argv):
        from tools import toolchain_detect
        print("\n".join(toolchain_detect.report(config.init.read_toolchains())))
        return 0
    args = _build_parser().parse_args(argv)
    if getattr(args, "no_profile", False):
        os.environ["UNIFPGA_PROFILE"] = "0"

    try:
        resolved = config.init.read_or_init(args.configuration, toolchain=args.toolchain, part=args.part)
    except config.init.ConfigError as exc:
        log.error("%s", exc)
        return 1
    if resolved is None:
        log.error("Could not resolve configuration: %s", args.configuration)
        return 1

    cfg        = resolved["configuration"]
    board      = resolved["board"]
    toolchain  = resolved["toolchain"]
    peripherals = resolved["peripherals"]

    target = resolved.get("target") or {"id": cfg["id"], "rig": cfg["id"]}
    log.info("Configuration: %s  (%sboard: %s, toolchain: %s, %d peripherals)",
             target["id"], "" if target["id"] == target["rig"] else "rig " + target["rig"] + ", ",
             board["id"], toolchain["id"], len(peripherals))
    try:
        config.init.require_toolchain_operation(toolchain, "synthesize")
        if args.program:
            config.init.require_toolchain_operation(toolchain, "program")
        config.init.require_toolchain_version(toolchain)
        if not os.environ.get("UNIFPGA_DRY_RUN"):
            config.init.require_hardware_readiness(board)
        else:
            log.warning("Dry run only: generated project is not admitted for hardware")
    except config.init.ConfigError as exc:
        log.error("%s", exc)
        return 2
    prepare_toolchain(toolchain)

    if args.output is None:
        output_folder = tempfile.mkdtemp(prefix="unifpga_{}_".format(cfg["id"]))
        delete_output = not args.keep_temp_dir
    else:
        output_folder = args.output
        delete_output = False
        os.makedirs(output_folder, exist_ok=True)

    # Validate the design_top's declared capability requirements against the
    # chosen configuration. Fail fast with a clear error if anything's missing.
    from tools import design_requirements
    requirements = design_requirements.parse(args.top)
    if requirements:
        errors = design_requirements.check(resolved, requirements)
        if errors:
            log.error("design_top capability requirements not met by configuration '%s':",
                      cfg["id"])
            for e in errors:
                log.error("  - %s", e)
            return 2
        log.info("All %d design_top capability requirements satisfied", len(requirements))

    try:
        # Validate one design fileset and put ROM/data assets in the working
        # directory before any frontend or dry-run project generation starts.
        from tools import source_set
        try:
            component_sources = source_set.stage_component_exports(args.component_export, output_folder)
            source_set.stage_assets(os.path.dirname(os.path.abspath(args.top)), output_folder)
        except source_set.SourceSetError as exc:
            log.error("Invalid design fileset or component export: %s", exc)
            return 2
        # Generate the top-level Verilog wrapper from the configuration.
        # Strict mode: a configuration whose binds do not resolve, whose pins
        # collide, or whose clock frequency is unknown is refused (exit 3)
        # rather than turned into a top that silently loses signals.
        from tools import codegen
        top_path = os.path.join(output_folder, "top.sv")
        try:
            top_text = codegen.emit_top_sv(resolved, design=args.top)
        except codegen.CodegenError as exc:
            log.error("Configuration '%s' cannot be built (audit code GEN-ERROR):\n%s",
                      cfg["id"], exc)
            return 3
        with open(top_path, "w") as f:
            f.write(top_text)
        log.info("Wrote generated top to %s", top_path)

        driver = toolchain_module(toolchain)
        rc = driver.synthesize(
            dir=dir_path,
            configuration=cfg,
            board=board,
            toolchain=toolchain,
            peripherals=peripherals,
            top=args.top,
            generated_top=top_path,
            include=args.include,
            component_sources=component_sources,
            output=output_folder,
            step=args.step,
        )
        rc = driver_exit_code(rc, "synthesize")
        if rc:
            return rc

        if args.program:
            log.info("Synthesis succeeded; programming the board.")
            rc = driver.program(
                board=board,
                toolchain=toolchain,
                output=output_folder,
            )
            rc = driver_exit_code(rc, "program")
            if rc:
                return rc
    finally:
        if delete_output:
            shutil.rmtree(output_folder, ignore_errors=True)

    return 0


if __name__ == "__main__":
    sys.exit(main())
