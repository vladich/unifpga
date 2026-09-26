"""
The virtual device interface is rendered from config/design_top.yml (the
sections, in order) and the capabilities' design: blocks (tools/design_top.py):
rtl/peripherals/design_top_interface.sv with its prose, and each design's
module header as the include design_top_interface.svh beside it, rendered by
every build from the design's // requires: (the optional capabilities it
names). Codegen's contract follows the same order and reads a design's
declared ports through the include.
"""

import os
import shutil
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from config import init as config_init                        # noqa: E402
from tools import codegen, design_top, source_set             # noqa: E402


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
    for expected in ("    parameter int clk_mhz       = 50,    // Advertised frequency of `clk` in MHz",
                     "    parameter int screen_width  = 0,",
                     "    parameter int w_sd_pixel    = 1,     // Bits per small-display pixel",
                     "    parameter int w_x = (screen_width > 1) ? $clog2(screen_width) : 1,",
                     "    parameter int w_mem_data = mem_bytes * 8",
                     "    input        [w_sw     - 1 : 0]   sw,",
                     "    inout        [w_gpio   - 1 : 0]   gpio,",
                     "    assign uart_tx   = 1'b1;", "    assign txt_char  = 8'h20;", "    assign led       = '0;",
                     "// ---- Storage (optional): an SD card read block by block"):
        assert expected in text, expected


def test_a_capability_left_out_of_the_device_is_an_error(monkeypatch):
    sections = config_init.read_design_top()["sections"]
    short = {"sections": [dict(s, capabilities=[c for c in s["capabilities"] if c != "leds"]) for s in sections]}
    monkeypatch.setattr(config_init, "read_design_top", lambda: short)
    with pytest.raises(codegen.CodegenError, match="leaves out the capabilities leds"):
        codegen.design_top_order()


# ---------------------------------------------------------------------------
# the designs' includes
# ---------------------------------------------------------------------------

def test_every_design_takes_its_header_from_the_include():
    files = design_top.design_files()
    assert len(files) > 100
    for path in files:
        text = open(path).read()
        assert design_top.includes_header(text), path
        assert "module design_top\n" + design_top.DIRECTIVE in text, path


def test_the_include_holds_the_core_and_the_required_optional_capabilities():
    plain = design_top.render_include(open(os.path.join(REPO, "designs", "1_06_binary_counter", "design_top.sv")).read())
    _p, ports = codegen.design_declarations("module design_top\n" + plain.split("\n", 3)[3])
    assert ports[:2] == ["clk", "rst"] and ports[-1] == "gpio" and "mem_req" not in ports
    memory = design_top.render_include(open(os.path.join(REPO, "designs", "memory_test", "design_top.sv")).read())
    params, ports = codegen.design_declarations("module design_top\n" + memory.split("\n", 3)[3])
    assert ports[-8:] == ["mem_req", "mem_we", "mem_addr", "mem_wdata", "mem_be", "mem_ready", "mem_ack", "mem_rdata"]
    assert "w_mem_addr" in params and "mem_bytes" in params and "w_mem_data" in params
    assert "optional capabilities: memory" in memory


def test_codegen_reads_the_declared_ports_through_the_include():
    r = config_init.resolve_configuration("de10_lite")
    counter = codegen.emit_top_sv(r, design=os.path.join(REPO, "designs", "1_06_binary_counter", "design_top.sv"))
    memory = codegen.emit_top_sv(r, design=os.path.join(REPO, "designs", "memory_test", "design_top.sv"))
    assert "the design does not take mem_req" in counter and ".mem_req(" not in counter
    assert ".mem_req(cap_memory_req)" in memory


def test_a_build_renders_the_include_and_never_lists_it_as_a_source(tmp_path):
    src = os.path.join(REPO, "designs", "1_06_binary_counter")
    work = tmp_path / "counter"
    shutil.copytree(src, work, ignore=shutil.ignore_patterns("run", design_top.INCLUDE_NAME))
    assert not (work / design_top.INCLUDE_NAME).exists()
    sources, simulation, _assets = source_set.design_inputs(str(work))
    include = work / design_top.INCLUDE_NAME
    assert include.exists() and "parameter int clk_mhz" in include.read_text()
    assert str(work / "design_top.sv") in sources and str(include) not in sources


def test_a_hand_written_header_is_converted_to_the_include(tmp_path):
    design = tmp_path / "design_top.sv"
    design.write_text("// requires:\n//   storage\nmodule design_top\n# (\n    parameter int clk_mhz = 50\n)\n(\n"
                      "    input clk,\n    input rst\n);\n    assign st_req = 1'b0;\nendmodule\n")
    assert design_top.convert(str(design))
    text = design.read_text()
    assert text.startswith("// requires:\n//   storage\nmodule design_top\n" + design_top.DIRECTIVE + "\n    assign st_req")
    include = (tmp_path / design_top.INCLUDE_NAME).read_text()
    assert "st_req" in include and "optional capabilities: storage" in include
    assert design_top.convert(str(design)) is False                      # a second time: nothing to convert
    (tmp_path / "other.sv").write_text("module other; endmodule\n")
    with pytest.raises(codegen.CodegenError, match="no `module design_top` header"):
        design_top.convert(str(tmp_path / "other.sv"))
