"""
A rig is one file, config/setups/<id>.yml: its wiring, and how the design sees
it — the `design:` section (reset, clock, uart_rx, width, tie) and each use's
`design_bits`, `bind` and `params`. The configuration the build reads is the
setup's expansion, computed on load (config/init.py, tools/setup.py); nothing
under config/ holds a configuration or a profile.
"""

import copy
import os
import sys

import pytest
import yaml

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from config import init as config_init     # noqa: E402
from tools import codegen                  # noqa: E402
from tools import setup as su              # noqa: E402

RIG = "tang_nano_9k_hdmi_tm1638"


def _lines(text, needle):
    return [l.strip() for l in text.splitlines() if needle in l]


def test_no_configuration_or_profile_files():
    """The rig is its setup: no config/configurations, no config/profiles, no
    config/profile.py, no config/layouts; the boards carry no design conventions."""
    for gone in ("configurations", "profiles", "profile.py", "layouts"):
        assert not os.path.exists(os.path.join(REPO, "config", gone)), gone
    for board in config_init.read_boards().values():
        text = open(board["_path"]).read()
        assert "design_bits" not in text and "lab_bits" not in text, board["_path"]


def test_setups_hold_the_design_conventions():
    """Every setup's design section uses the known keys; design_bits sit on
    the use they belong to and name buses its peripheral provides; no `lab`
    vocabulary anywhere in the setups."""
    peripherals = config_init.read_peripherals()
    modules = su.read_modules()
    known = {k for k, _c in su.DESIGN_KEYS}
    with_section = with_bits = 0
    for sid, setup in sorted(su.read_setups().items()):
        text = open(os.path.join(su.SETUP_DIR, sid + ".yml")).read()
        assert "lab_" not in text and "profile" not in text, sid
        design = setup.get("design") or {}
        assert set(design) <= known, (sid, set(design) - known)
        with_section += bool(design)
        cfg = su.generate(setup)
        for use, attach in zip(setup["use"], cfg["attach"]):
            bits = use.get("design_bits") or (use.get("raw") or {}).get("design_bits")
            if not bits:
                continue
            with_bits += 1
            provided = {p["capability"] for p in peripherals[attach["peripheral"]].get("provides") or []}
            assert set(bits) <= provided, (sid, attach["peripheral"], set(bits) - provided)
            assert attach.get("design_bits") == bits
    assert with_section > 50 and with_bits > 100


def test_design_section_reaches_the_build_and_can_be_switched_off(monkeypatch):
    """With the design section: the TM1638 is the design's key bus and KEY0 /
    KEY1 reset (Tang Nano 9K). UNIFPGA_PROFILE=0: buses concatenated in attach
    order, a power-up reset, no design_bits."""
    monkeypatch.delenv("UNIFPGA_PROFILE", raising=False)
    config_init.clear_cache()
    r = config_init.resolve_configuration(RIG)
    text = codegen.emit_top_sv(r)
    assert ".w_btn(8)," in text
    assert _lines(text, "assign rst =") == ["assign rst = rst_on_power_up | ((~ onboard_buttons[0]) | (~ onboard_buttons[1]));"]
    tm = next(a for a in r["peripherals"] if a["peripheral_id"] == "tm1638_led_key")
    assert tm["design_bits"]["buttons"] == list(range(8))
    assert r["configuration"]["reset"] and "design_clock" not in r["configuration"]

    monkeypatch.setenv("UNIFPGA_PROFILE", "0")
    config_init.clear_cache()
    r = config_init.resolve_configuration(RIG)
    text = codegen.emit_top_sv(r)
    assert ".w_btn(10)," in text
    assert _lines(text, "assign rst =") == ["assign rst = rst_on_power_up;"]
    assert not any(a.get("design_bits") for a in r["peripherals"])
    assert "reset" not in r["configuration"]
    config_init.clear_cache()


def test_the_design_section_expands_and_round_trips():
    setup = copy.deepcopy(su.read_setup("de10_lite"))
    cfg = su.generate(setup)
    assert cfg["reset"] == setup["design"]["reset"] and cfg["uart_rx"] == 0
    assert cfg["tie"]["arduino_reset_n"] == "~rst" and cfg["tie"]["gpio[1]"] == 0     # design ties over extra's
    sw = next(a for a in cfg["attach"] if a["peripheral"] == "sw_bank")
    assert sw["design_bits"] == {"switches": [0, 1, 2, 3, 4, 5, 6, 7, 8, None]}
    back = su.derive(cfg)
    assert back["design"] == {"reset": setup["design"]["reset"], "uart_rx": 0}   # tie cannot be told apart from the hardware's
    assert next(u for u in back["use"] if u.get("onboard") == "switches")["design_bits"] == sw["design_bits"]
    assert su.check_roundtrip(cfg) == []
    # the design clock and a width
    setup = copy.deepcopy(su.read_setup("a7_lite_35t"))
    cfg = su.generate(setup)
    assert cfg["design_clock"] == {"name": "design", "mhz": 50}
    assert su.generate(su.read_setup("tang_primer_20k_dock_hdmi_no_tm1638"))["for_toolchain"]["nextpnr_apicula"]["design_width"] == {"switches": 5}


def test_a_use_bind_override_and_bad_design_keys():
    setup = copy.deepcopy(su.read_setup("de10_lite"))
    leds = next(u for u in setup["use"] if u.get("onboard") == "leds")
    leds["bind"] = {"led": ["onboard_leds[9]", "onboard_leds[8]"]}
    leds["params"] = {"width": 2}
    cfg = su.generate(setup)
    assert next(a for a in cfg["attach"] if a["peripheral"] == "led_bank")["bind"] == {"led": ["onboard_leds[9]", "onboard_leds[8]"]}
    setup["design"]["lab_clock"] = "pixel"
    with pytest.raises(su.SetupError, match="design: has no key lab_clock"):
        su.generate(setup)
    del setup["design"]["lab_clock"]
    setup["use"][0]["lab_bits"] = {"x": [0]}
    with pytest.raises(su.SetupError, match="no key lab_bits"):
        su.generate(setup)
    setup["use"][0].pop("lab_bits")
    setup["extra"]["reset"] = {}
    with pytest.raises(su.SetupError, match="extra.reset belongs to the design section"):
        su.generate(setup)


def test_the_setup_writer_keeps_the_design_conventions(tmp_path, monkeypatch):
    setup = copy.deepcopy(su.read_setup("de10_lite"))
    monkeypatch.setattr(su, "SETUP_DIR", str(tmp_path))
    su.write_setup(setup)
    text = open(str(tmp_path / "de10_lite.yml")).read()
    assert "      design_bits:\n        switches: [0, 1, 2, 3, 4, 5, 6, 7, 8, null]\n" in text
    assert "  design:\n" in text and "uart_rx: 0" in text
    again = yaml.safe_load(text)["Setup"]
    assert su.same_configuration(su.generate(again), su.generate(setup))
