"""Board inventories (tools/inventory.py): new devices become pinmap banks
tagged `device:`, existing banks are only compared, headers keep their FPGA
pins in pin-number order without power pins, shared pins are recorded."""

import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from tools import inventory as iv   # noqa: E402

PINMAP = {"pinBanks": {"clk": {"pins": "V22", "frequency_mhz": 50},
                       "onboard_leds": {"pins": ["A1", "A2", "A3"]},
                       "pmod_0": {"pins": ["B1", "B2", "B3", "B4", "B7", "B8", "B9", "B10"]}}}


def _inv(*devices):
    return {"board": "x", "devices": list(devices)}


def test_existing_banks_are_compared_never_changed():
    p = iv.plan(_inv({"name": "clock", "kind": "clock", "bank": "clk", "pins": {"clk": "V22"}},
                     {"name": "LEDs", "kind": "leds", "bank": "onboard_leds", "pins": ["A3", "A2", "A1"]},
                     {"name": "PMOD 0", "kind": "pmod", "bank": "pmod_0",
                      "pins": {"1": "B1", "2": "B2", "3": "B3", "4": "B4", "5": "GND", "6": "VCC",
                               "7": "B7", "8": "B8", "9": "B9", "10": "B10", "11": "GND", "12": "VCC"}}), PINMAP)
    assert p["matched"] == ["clk", "pmod_0"] and p["same_pins_other_order"] == ["onboard_leds"] and not p["add"]
    p = iv.plan(_inv({"name": "LEDs", "kind": "leds", "bank": "onboard_leds", "pins": ["A1", "A2", "C9"]}), PINMAP)
    assert [b for b, _ in p["differs"]] == ["onboard_leds"]


def test_new_devices_become_onboard_banks_and_shared_pins_are_named():
    p = iv.plan(_inv({"name": "LED Pmod", "kind": "leds", "bank": "led_pmod", "pins": ["B1", "B2"]},
                     {"name": "Camera", "kind": "camera_dvp", "bank": "onboard_camera", "pins": {"d": ["C1", "B7"]}},
                     {"name": "odd", "kind": "other", "pins": {"x": "maybe B3?"}}), PINMAP)
    assert [b for b, _ in p["add"]] == ["onboard_led_pmod", "onboard_camera"]
    assert p["skipped"] == [("odd", "no well-formed pins")]
    banks = dict(PINMAP["pinBanks"], onboard_led_pmod={"pins": ["B1", "B2"]})
    assert iv._shares("onboard_led_pmod", ["B1", "B2"], banks) == ["pmod_0"]
    lines = iv._bank_text("onboard_camera", p["add"][1][1], ["pmod_0"])
    assert lines[1] == '      device: {"name": "Camera", "kind": "camera_dvp"}' and '"pmod_0"' in lines[2]


def test_header_pins_drop_power_and_repeated_rails():
    d = {"kind": "header", "pins": {"1": "Vapp", "2": "Vapp", "3": "IO_A0", "4": "GND", "5": "IO_A1", "6": "3V3",
                                    "7": "V16", "8": "V1", "9": "VCCIO"}}
    assert iv._as_bank_pins(d) == ["IO_A0", "IO_A1", "V16", "V1"]   # V16 / V1 are balls, not rails
