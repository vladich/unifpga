"""
Stub toolchain module. Synthesis isn't actually wired up yet — this just
logs what would have been built and fails explicitly. Replace `synthesize()` with a
real driver (subprocess to the vendor tool, or yosys/nextpnr invocation)
when implementing the toolchain.
"""

import logging

log = logging.getLogger(__name__)


def synthesize(*, dir, configuration, board, board_pinmap, toolchain,
               peripherals, top, include, output, step="full", **_):
    """Entry point invoked by synthesize.py. Kwargs-only to keep the signature
    extensible without breaking call sites."""
    log.error(
        "[stub %s] cannot synthesize configuration=%s, board=%s, top=%s, "
        "step=%s, output=%s, peripherals=%d",
        toolchain["id"], configuration["id"], board["Id"], top, step, output,
        len(peripherals),
    )
    return 2


def program(**kwargs):
    """Placeholder for board programming/loading."""
    log.error("[stub program] not implemented")
    return 2
