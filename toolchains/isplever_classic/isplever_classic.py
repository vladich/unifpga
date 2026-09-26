"""
Lattice ispLEVER Classic toolchain driver — STUB.

ispLEVER Classic is the still-distributed CPLD-only spinoff of
ispLEVER. It covers the ispMACH 4000 / 5000VG / 5000B CPLD families
plus the older PAL/GAL-style ispLSI parts that were too far along
the EOL curve to migrate into Diamond. Lattice keeps releasing minor
updates (last public ~2.1) for production users who still ship parts
on these families.

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
