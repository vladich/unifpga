"""
QuickLogic QuickWorks toolchain driver — STUB.

QuickWorks was QuickLogic's proprietary IDE for their pre-EOS-S3
families: pASIC 1/2/3 (antifuse), Eclipse / Eclipse II, PolarPro /
PolarPro II / PolarPro 3 / 3E, ArcticLink / II / III. It bundled
schematic entry, third-party synthesis (Synplify), and QuickLogic's
own place-and-route. Latest publicly available release is from 2014;
QuickLogic effectively replaced it with the Aurora IDE and the open
qorc-sdk + ql-symbiflow flow for the EOS S3 line.

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
