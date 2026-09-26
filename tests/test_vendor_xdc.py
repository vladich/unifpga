"""
tools/verify_pinmap_against_vendor.py: a board's banks against the Digilent
XDC its documents list — the port-name mapping, the comparison, and which
boards have such a document.
"""

import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from tools import verify_pinmap_against_vendor as vx    # noqa: E402

XDC = """\
## Clock signal
set_property -dict { PACKAGE_PIN W5   IOSTANDARD LVCMOS33 } [get_ports clk]
## Switches
set_property -dict { PACKAGE_PIN V17   IOSTANDARD LVCMOS33 } [get_ports {sw[0]}]
#set_property -dict { PACKAGE_PIN V16   IOSTANDARD LVCMOS33 } [get_ports {sw[1]}]
set_property -dict { PACKAGE_PIN U16   IOSTANDARD LVCMOS33 } [get_ports {led[0]}]
set_property -dict { PACKAGE_PIN U18   IOSTANDARD LVCMOS33 } [get_ports btnC]
set_property -dict { PACKAGE_PIN J1   IOSTANDARD LVCMOS33 } [get_ports {JA[1]}]
set_property -dict { PACKAGE_PIN A18   IOSTANDARD LVCMOS33 } [get_ports RsTx]
"""

BOARD = {"id": "b", "banks": {
    "clk": {"pins": "W5"},
    "onboard_switches": {"pins": ["V17", "V99"]},
    "onboard_leds": {"pins": ["U16"]},
    "onboard_buttons": {"pins": ["U18"]},
    "pmod_ja": {"pins": ["J1", "L2", "J2", "G2"]},
    "onboard_uart": {"pins": {"tx": "A18", "rx": "B18"}},
    "virtual_clk": {"virtual": True, "origin": "osc"},
}}


def test_the_xdc_parser_reads_live_and_commented_lines():
    tuples = vx.parse_digilent_xdc_text(XDC)
    assert ("clk", None, "W5", "LVCMOS33") in tuples
    assert ("sw", 1, "V16", "LVCMOS33") in tuples            # a commented line counts as documentation
    assert ("JA", 1, "J1", "LVCMOS33") in tuples


def test_banks_flatten_to_tokens_and_compare_with_the_vendor():
    ours = vx.pins_of(BOARD)
    assert ours["clk"] == "W5" and ours["onboard_switches[1]"] == "V99" and ours["onboard_uart.tx"] == "A18"
    assert "virtual_clk" not in ours
    matches, mismatches, our_only, vendor_only, unmapped = vx.compare(ours, vx.parse_digilent_xdc_text(XDC))
    assert set(matches) >= {"clk", "onboard_switches[0]", "onboard_leds[0]", "onboard_buttons[0]", "pmod_ja[0]"}
    assert mismatches == [("onboard_switches[1]", "V99", "V16")]
    assert "onboard_uart.rx" in our_only and unmapped == [("RsTx", None, "A18")]


def test_boards_list_their_xdc_among_their_documents():
    with_xdc = vx.boards_with_xdc()
    assert {"basys3", "arty_a7", "nexys_a7", "cmod_a7", "zedboard"} <= set(with_xdc)
    doc = vx.xdc_document(with_xdc["basys3"])
    assert doc["kind"] == "constraints" and doc["url"].endswith("/Basys-3-Master.xdc") and doc.get("sha256")
    assert not os.path.exists(os.path.join(REPO, "config", "vendor_constraints.yml"))
