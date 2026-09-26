"""
Lattice ispLEVER toolchain driver — STUB.

ispLEVER (last version 8.1) was Lattice's primary FPGA flow before
Diamond replaced it in 2010. It covers ispLSI 1000/2000/3000/5000/8000,
ispGDX/GDXV/GDX2, LatticeECP/ECP-DSP, LatticeXP/XP2, LatticeECP2/ECP2M,
LatticeSC/SCM, and ORCA 2C/3T/3L/4 (from the AT&T/Lucent acquisition).
Some of these families later gained Diamond support; LatticeSC/SCM and
the very old ispLSI parts are ispLEVER-only.

Note: this is the FPGA ispLEVER, distinct from `isplever_classic` which
is the still-distributed CPLD-only build.

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
