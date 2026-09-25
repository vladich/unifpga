"""Designs and shared RTL never test the vendor, the toolchain or the
simulator, and never instantiate a vendor primitive: the vendor layer is the
generated top (global_clock_buffer, per board) and rtl/pll, rtl/io, rtl/sim."""

import os
import re
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from config import init as config_init  # noqa: E402
from tools import codegen  # noqa: E402

TOOL_MACROS = ("ALTERA_RESERVED_QIS", "XILINX_VIVADO", "INTEL_VERSION", "ICE40_VERSION",
               "SERIES7_VERSION", "GENERIC_VERSION", "__ICARUS__", "YOSYS", "MODEL_TECH",
               "VCS", "INCA", "VERILATOR", "SYNOPSYS_CADENCE_MENTOR")
VENDOR_LAYER = [os.path.join("rtl", d) for d in ("pll", "io", "sim")]


def _sources():
    for top in ("designs", "rtl"):
        for d, _, names in os.walk(os.path.join(REPO, top)):
            rel = os.path.relpath(d, REPO)
            if any(rel == v or rel.startswith(v + os.sep) for v in VENDOR_LAYER) or \
                    os.sep + "run" in os.sep + rel:
                continue
            for n in names:
                if n.endswith((".sv", ".v", ".svh", ".vh")):
                    yield os.path.join(rel, n)


def _primitives():
    """The vendor primitives the lint stands in for (rtl/sim/vendor_stubs.sv)."""
    text = open(os.path.join(REPO, "rtl", "sim", "vendor_stubs.sv"), encoding="utf-8").read()
    names = set(re.findall(r"^module\s+\\?(\w+)", text, re.M)) - {"gw5_pll_model"}
    return names | {"global", "altclkctrl", "BUFGCE", "IBUFG"}


def test_no_source_tests_a_tool():
    pattern = re.compile(r"`(ifdef|ifndef|elsif)\s+({})\b".format("|".join(TOOL_MACROS)))
    found = ["{}:{}".format(p, k + 1) for p in _sources()
             for k, line in enumerate(open(os.path.join(REPO, p), encoding="utf-8", errors="replace"))
             if pattern.search(line)]
    assert not found, found


def test_no_source_instantiates_a_vendor_primitive():
    names = "|".join(sorted(_primitives(), key=len, reverse=True))
    pattern = re.compile(r"^\s*\\?({})\s*(#\s*\(|\w+\s*\()".format(names))
    found = []
    for p in _sources():
        text = re.sub(r"/\*.*?\*/", "", open(os.path.join(REPO, p), encoding="utf-8", errors="replace").read(), flags=re.S)
        for k, line in enumerate(text.splitlines()):
            if pattern.match(re.sub(r"//.*", "", line)):
                found.append("{}:{}: {}".format(p, k + 1, line.strip()))
    assert not found, found


def test_the_generated_top_defines_the_boards_clock_buffer():
    kinds = {rig: codegen.clock_buffer_kind(config_init.resolve_configuration(rig))
             for rig in ("de2", "arty_a7", "tang_nano_9k_hdmi_tm1638", "icebreaker_no_dvi_tm1638")}
    assert kinds == {"de2": "intel", "arty_a7": "xilinx", "tang_nano_9k_hdmi_tm1638": "generic",
                     "icebreaker_no_dvi_tm1638": "generic"}
    top = codegen.emit_top_sv(config_init.resolve_configuration("de2"))
    assert "module global_clock_buffer (input in, output out);" in top
    assert "\\global  i_buffer" in top                  # escaped: `global` is an SV keyword
    assert "BUFG i_buffer" in codegen.emit_top_sv(config_init.resolve_configuration("arty_a7"))


def test_a_quartus_project_defines_no_vendor_macro():
    r = config_init.resolve_configuration("de10_lite")
    assert "VERILOG_MACRO" not in codegen.emit_qsf(r, "10M50DAF484C7G")
