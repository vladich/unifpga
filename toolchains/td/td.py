"""
Anlogic Tang Dynasty (TD) toolchain driver — STUB.

TD is Anlogic Microelectronics' proprietary IDE for their FPGA families:
Eagle EG4 (4S/4D/4M), Elf 2 (EF2), Phoenix EF3 (Anlogic's own names: SALEAGLE, SALELF,
SALPHOENIX, SALDRAGON, SALSWIFT). Despite
the name, it has no relation to Sipeed's "Tang" boards (those use Gowin
parts). Anlogic publishes TD only in Chinese-localised builds; an
English UI exists but documentation is sparse outside CN sources.

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
