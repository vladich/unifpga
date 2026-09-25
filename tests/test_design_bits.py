"""
design_bits — bit-mapped capability aggregation (a TM1638 can share the design's
led / key buses with the board's own LEDs and keys instead of extending
them) and the reset that reads the board's own keys.
"""

import logging
import os
import re
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from config import init as config_init     # noqa: E402
from tools import codegen                  # noqa: E402

logging.disable(logging.CRITICAL)

CFG = "tang_nano_9k_hdmi_tm1638"           # button_array (2), led_bank (6), tm1638_led_key


def _resolved_with_design_bits(**per_peripheral):
    """The configuration with only the given design_bits (the synced ones cleared)."""
    r = config_init.resolve_configuration(CFG)
    for a in r["peripherals"]:
        a["design_bits"] = dict(per_peripheral.get(a["peripheral_id"], {}))
    return r


def _pidx(r, pid):
    return next(i for i, a in enumerate(r["peripherals"]) if a["peripheral_id"] == pid)


def _lines(text, pattern):
    return [l.strip() for l in text.splitlines() if re.search(pattern, l.strip())]


def test_tm1638_duplicate_mode():
    """TM1638 duplicating the board's own LEDs and keys:
    key = tm_key, tm_led = led, LED = w_led'(~ led) — the board
    LEDs show the low bits of the same bus, the board keys are reset only."""
    r = _resolved_with_design_bits(
        button_array={"buttons": []},
        led_bank={"leds": list(range(6))},
        tm1638_led_key={"leds": list(range(8)), "buttons": list(range(8)),
                        "switches": list(range(8)), "seven_segment": list(range(8))})
    plans = codegen.build_capability_plans(r)
    assert plans["buttons"].params["width"] == 8
    assert plans["leds"].params["width"] == 8
    text = codegen.emit_top_sv(r)
    assert ".w_btn(8)," in text and ".w_led(8)," in text
    assert "assign onboard_leds = ~ cap_leds_led[5:0];" in text            # contiguous run keeps the slice
    assert ".ledr(cap_leds_led[7:0])," in text
    # the design's btn bits come from the TM1638's wire alone; the board keys' wire feeds nothing
    tm, btn = _pidx(r, "tm1638_led_key"), _pidx(r, "button_array")
    merge = _lines(text, r"^assign cap_buttons_btn\[\d+\] = ")
    assert merge == ["assign cap_buttons_btn[{i}] = cap_buttons_btn__p{tm}[{i}];".format(i=i, tm=tm) for i in range(8)]
    assert "cap_buttons_btn__p{}".format(btn) not in "\n".join(merge)
    assert ".keys(cap_switches_sw__p{})".format(tm) in text
    # the reset still comes from the board's keys, not the TM1638's
    assert _lines(text, r"assign rst = ") == ["assign rst = rst_on_power_up | ((~ onboard_buttons[0]) | (~ onboard_buttons[1]));"]


def test_non_contiguous_bits_are_wired_one_by_one():
    r = _resolved_with_design_bits(
        button_array={"buttons": [5, 2]},
        led_bank={"leds": [7, 6, 5, 4, 3, 2]},
        tm1638_led_key={"leds": [0, 1, 2, 3, 4, 5, 6, 7], "buttons": [0, 1, 3, 4, 6, 7, 8, 9],
                        "switches": list(range(8)), "seven_segment": list(range(8))})
    text = codegen.emit_top_sv(r)
    tm, btn = _pidx(r, "tm1638_led_key"), _pidx(r, "button_array")
    assert "assign cap_buttons_btn__p{}[0] = ~ onboard_buttons[0];".format(btn) in text
    assert "assign cap_buttons_btn[5] = cap_buttons_btn__p{}[0];".format(btn) in text
    assert "assign cap_buttons_btn[2] = cap_buttons_btn__p{}[1];".format(btn) in text
    assert "assign cap_buttons_btn[3] = cap_buttons_btn__p{}[2];".format(tm) in text
    assert "assign onboard_leds[0] = ~ cap_leds_led[7];" in text
    assert "assign onboard_leds[5] = ~ cap_leds_led[2];" in text
    assert ".ledr(cap_leds_led[7:0])," in text
    assert ".w_btn(10)," in text
    assert ".keys(cap_switches_sw__p{})".format(tm) in text                # a driver writes its own wire


def test_shared_input_bits_are_ored():
    """arty: `key [w_key - 1:0] |= KEY; key [w_tm_key - 1:0] |= tm_key`."""
    r = _resolved_with_design_bits(button_array={"buttons": [0, 1]}, tm1638_led_key={"buttons": list(range(8))})
    text = codegen.emit_top_sv(r)
    tm, btn = _pidx(r, "tm1638_led_key"), _pidx(r, "button_array")
    assert "assign cap_buttons_btn[0] = cap_buttons_btn__p{b}[0] | cap_buttons_btn__p{t}[0];".format(b=btn, t=tm) in text
    assert "assign cap_buttons_btn[2] = cap_buttons_btn__p{t}[2];".format(t=tm) in text
    assert ".w_btn(8)," in text


def test_partial_mapping_leaves_provider_bits_unused():
    """de10_lite: the design gets SW [8:0]; SW [9] is the reset only."""
    r = config_init.resolve_configuration("de10_lite")
    for a in r["peripherals"]:
        if a["peripheral_id"] == "sw_bank":
            a["design_bits"] = {"switches": list(range(9)) + [None]}
    text = codegen.emit_top_sv(r)
    sw = _pidx(r, "sw_bank")
    assert ".w_sw(9)," in text
    assert "assign cap_switches_sw__p{}[9] = onboard_switches[9];".format(sw) in text
    assert "assign cap_switches_sw[8] = cap_switches_sw__p{}[8];".format(sw) in text
    assert not _lines(text, r"^assign cap_switches_sw\[9\]")
    assert _lines(text, r"assign rst = ") == ["assign rst = (onboard_switches[9]);"]   # the pin, not a lab bit


def test_design_bits_errors():
    with pytest.raises(codegen.CodegenError, match="every provider of leds needs it"):
        codegen.build_capability_plans(_resolved_with_design_bits(led_bank={"leds": list(range(6))}))
    with pytest.raises(codegen.CodegenError, match="names 3 bits for a 2-bit"):
        codegen.build_capability_plans(_resolved_with_design_bits(
            button_array={"buttons": [8, 9, 10]}, tm1638_led_key={"buttons": list(range(8))}))


def test_reset_from_keys_reads_the_board_buttons():
    """`any_key` is `| (~ KEY)`: the board's keys, whatever the TM1638 adds."""
    r = config_init.resolve_configuration(CFG)
    text = codegen.emit_top_sv(r)
    assert _lines(text, r"assign rst = ") == ["assign rst = rst_on_power_up | ((~ onboard_buttons[0]) | (~ onboard_buttons[1]));"]
    plans = codegen.build_capability_plans(r)
    assert codegen._board_provider_terms(r, plans, "buttons", "btn") == ["(~ onboard_buttons[0])", "(~ onboard_buttons[1])"]


def test_header_bits_a_part_uses_dangle_on_the_design_side():
    """The header is the design's gpio, but a pin the microphone (or a tie)
    uses is the microphone's: its gpio bit dangles, the other bits keep the
    header's numbering (BGM hands the design the whole header; unifpga does not
    let the design drive a pad another part drives)."""
    r = config_init.resolve_configuration("de10_lite")
    text = codegen.emit_top_sv(r)
    gpio_line = next(l for l in text.splitlines() if l.strip().startswith(".gpio("))
    taken = {p for a in r["peripherals"] if a["peripheral_id"] not in ("gpio_header",)
             for ref in (a.get("bind") or {}).values() for p in codegen._bind_bit_ports(r, ref)}
    header_bits = [b for b in re.findall(r"gpio\[\d+\]", gpio_line)]
    assert header_bits and not set(header_bits) & taken
    assert "gpio_nc_" in gpio_line and "is taken by another part" in text
    assert ".uart_rx(1'b0)" in text                                          # unconnected = ground


def test_keys_double_as_switches():
    """omdazz: `.key ( ~ KEY_SW )`, `.sw ( ~ KEY_SW )` — the button array also
    provides the design's sw (as_switches), a gated provides entry reading the
    same pins."""
    r = config_init.resolve_configuration("omdazz")
    btn = next(a for a in r["peripherals"] if a["peripheral_id"] == "button_array")
    assert btn["params"].get("as_switches") is True and btn["design_bits"] == {"switches": [0, 1, 2, 3]}
    text = codegen.emit_top_sv(r)
    assert ".w_sw(4)," in text and ".w_btn(4)," in text
    plans = codegen.build_capability_plans(r)
    assert [p[1]["id"] for p in plans["switches"].providers] == ["button_array"]
    i = _pidx(r, "button_array")
    assert "assign cap_switches_sw__p{}[0] = ~ onboard_buttons[0];".format(i) in text
    assert "assign cap_switches_sw[3] = cap_switches_sw__p{}[3];".format(i) in text
    # without the parameter the entry is inactive
    btn["params"] = dict(btn["params"], as_switches=False)
    btn["design_bits"] = {}
    assert not codegen.build_capability_plans(r)["switches"].providers


def test_design_width_widens_the_bus_and_grounds_the_top_bit():
    """emooc_cc: `wire [w_key - 2:0] key` but `.w_key (w_key)` — the design
    gets 8 keys, the top one wired to nothing (reads 0)."""
    r = _resolved_with_design_bits(button_array={"buttons": [0, None]}, led_bank={"leds": list(range(6))},
                                tm1638_led_key={"leds": list(range(8)), "buttons": [1, 2, 3, 4, 5, 6, None, None],
                                                "switches": list(range(8)), "seven_segment": list(range(8))})
    r["configuration"] = dict(r["configuration"], design_width={"buttons": 8, "leds": 8})
    plans = codegen.build_capability_plans(r)
    assert plans["buttons"].params["width"] == 8
    text = codegen.emit_top_sv(r)
    assert ".w_btn(8)," in text
    assert "assign cap_buttons_btn[7] = 1'b0;" in text
    # a plain concatenation (no design_bits) widened by design_width reads 0 on the extra bits
    r = config_init.resolve_configuration("omdazz")
    for a in r["peripherals"]:
        a["design_bits"] = {}
    r["configuration"] = dict(r["configuration"], design_width={"buttons": 6})
    text = codegen.emit_top_sv(r)
    assert ".w_btn(6)," in text and "assign cap_buttons_btn [5:4] = '0;   // design_width: no provider on these bits" in text


