#!/usr/bin/env python3
"""
Load an existing build's bitstream onto the connected board, without
rebuilding (`synthesize.py --program` builds first).

Usage:
    program.py -o build/                  # configuration from settings.yml
    program.py -c nexys4_ddr_default -o build/
"""

import argparse
import logging
import os
import sys

import config.init
import synthesize


log = logging.getLogger(__name__)


def _build_parser():
    p = argparse.ArgumentParser(description="UniFPGA Program")
    p.add_argument("-c", "--configuration",
                   help="Configuration id (from config/configurations/<id>.yml). "
                        "If omitted, settings.yml is consulted.")
    p.add_argument("-o", "--output", required=True,
                   help="The build's output folder (synthesize.py -o), holding the bitstream")
    return p


def main(argv=None):
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    args = _build_parser().parse_args(argv)
    if not os.path.isdir(args.output):
        log.error("%s does not exist: build first (synthesize.py -s full -o %s)", args.output, args.output)
        return 1
    try:
        resolved = config.init.read_or_init(args.configuration)
    except config.init.ConfigError as exc:
        log.error("%s", exc)
        return 1
    if resolved is None:
        log.error("Could not resolve configuration: %s", args.configuration)
        return 1
    toolchain = resolved["toolchain"]
    try:
        config.init.require_toolchain_operation(toolchain, "program")
        config.init.require_toolchain_version(toolchain)
    except config.init.ConfigError as exc:
        log.error("%s", exc)
        return 2
    synthesize.prepare_toolchain(toolchain)
    rc = synthesize.toolchain_module(toolchain).program(
        board=resolved["board"],
        board_pinmap=resolved["board_pinmap"],
        toolchain=toolchain,
        output=args.output,
    )
    return rc or 0


if __name__ == "__main__":
    sys.exit(main())
