"""
NanoXplore NanoXmap + Impulse toolchain driver — STUB.

NanoXmap is NanoXplore's proprietary synthesis/PnR tool for their
rad-hard European-fab FPGA families: NG-MEDIUM, NG-LARGE, NG-ULTRA
(the latter is a rad-hard space-grade part used by ESA, CNES, and
defence integrators). Impulse is the companion programming and JTAG
tool. There is no open flow.

Stub module. Synthesis isn't wired up yet — this just logs what would
have been built and fails explicitly. Replace `synthesize()` with a real driver
when implementing.
"""

import logging

log = logging.getLogger(__name__)


def synthesize(*, dir, configuration, board, toolchain,
               peripherals, top, include, output, step="full", **_):
    log.error(
        "[stub %s] cannot synthesize configuration=%s, board=%s, top=%s, "
        "step=%s, output=%s, peripherals=%d",
        toolchain["id"], configuration["id"], board["id"], top, step, output,
        len(peripherals),
    )
    return 2


def program(**kwargs):
    log.error("[stub program] not implemented")
    return 2
