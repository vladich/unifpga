"""
Achronix CAD Environment (ACE) toolchain driver — STUB.

ACE is Achronix's proprietary place-and-route tool for the Speedster
22i HD (Intel 22 nm) and Speedster 7t (TSMC 7 nm) families. It bundles
Synopsys Synplify Pro for synthesis. The Speedster 7t targets ML /
networking workloads with hard MLP (machine learning processor) blocks.

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
