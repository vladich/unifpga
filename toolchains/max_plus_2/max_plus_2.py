"""
Altera MAX+PLUS II toolchain driver — STUB.

MAX+PLUS II (last 10.2 BL2) was Altera's pre-Quartus flow, covering
the FLEX 6K/8K/10K/10KE/10KA, APEX 20K/20KE/KC, ACEX 1K, MAX 7000
/ 7000S / 7000B (CPLDs), MAX 3000A, EP300 / EP1800 / Classic, Mercury
(EP1M), and the original Cyclone (EP1C) in its final years before
Quartus II 9.0sp2 took over. The tool runs only on Windows XP; recent
work has gotten it to launch under wine.

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
        toolchain["id"], configuration["id"], board["Id"], top, step, output,
        len(peripherals),
    )
    return 2


def program(**kwargs):
    log.error("[stub program] not implemented")
    return 2
