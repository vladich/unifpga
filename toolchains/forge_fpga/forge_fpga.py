"""
Renesas ForgeFPGA Workshop toolchain driver — STUB.

ForgeFPGA Workshop is a plugin to Renesas' "Go Configure Software Hub"
(originally Dialog Semiconductor's tool for the SLG / GreenPAK CPLD
line). It targets the small-but-cheap ForgeFPGA family (SLG4791x /
SLG4792x, ~1k LUTs), which is positioned as a glue-logic alternative
to CPLDs. Renesas hints at a larger ForgeFPGA Evo coming.

Stub module. Synthesis isn't wired up yet — this just logs what would
have been built and fails explicitly. Replace `synthesize()` with a real driver
when implementing.
"""

import logging

log = logging.getLogger(__name__)


def synthesize(*, dir, configuration, board, board_pinmap, toolchain,
               peripherals, top, include, output, step="full", **_):
    log.error(
        "[stub %s] cannot synthesize configuration=%s, board=%s, top=%s, "
        "step=%s, output=%s, peripherals=%d",
        toolchain["Id"], configuration["id"], board["Id"], top, step, output,
        len(peripherals),
    )
    return 2


def program(**kwargs):
    log.error("[stub program] not implemented")
    return 2
