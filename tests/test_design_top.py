"""
rtl/peripherals/design_top_interface.sv is rendered from config/design_top.yml
(the sections, in order) and the capabilities' design: blocks (tools/design_top.py);
codegen's contract follows the same order.
"""

import os
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from config import init as config_init            # noqa: E402
from tools import codegen, design_top             # noqa: E402


def test_the_interface_file_is_what_the_data_renders_to():
    assert design_top.is_current(), "./unifpga interface --write"


def test_the_rendered_header_declares_the_contract_in_its_order():
    params, derived, ports = codegen.design_contract()
    names_p, names_o = codegen.design_declarations(design_top.render())
    assert names_p == [p.name for p in params] + [d.name for d in derived]
    assert names_o == [p.name for p in ports]
    order = codegen.design_top_order()
    assert order[:2] == ["clock", "reset"] and order[-1] == "storage"
    assert [p.capability for p in ports] == [c for c in order for p in ports if p.capability == c]


def test_every_capability_is_on_the_device_once():
    sections = config_init.read_design_top()["sections"]
    listed = [c for s in sections for c in s["capabilities"]]
    assert len(listed) == len(set(listed))
    assert set(listed) == {cid for cid, cap in config_init.read_capabilities().items() if cap.get("design")}


def test_the_render_carries_the_capabilities_prose_and_defaults():
    text = design_top.render()
    for expected in ('    parameter int clk_mhz       = 50,    // Advertised frequency of `clk` in MHz', '    parameter int w_x = (screen_width > 1) ? $clog2(screen_width) : 1,', '    parameter int w_mem_data = mem_bytes * 8', '    input        [w_sw     - 1 : 0]   sw,', '    inout        [w_gpio   - 1 : 0]   gpio,', "    assign uart_tx   = 1'b1;", "    assign txt_char  = 8'h20;", "    assign led       = '0;"):
        assert expected in text, expected
    assert "// ---- Storage (optional): an SD card read block by block" in text


def test_a_capability_left_out_of_the_device_is_an_error(monkeypatch):
    sections = config_init.read_design_top()["sections"]
    short = {"sections": [dict(s, capabilities=[c for c in s["capabilities"] if c != "leds"]) for s in sections]}
    monkeypatch.setattr(config_init, "read_design_top", lambda: short)
    with pytest.raises(codegen.CodegenError, match="leaves out the capabilities leds"):
        codegen.design_top_order()
