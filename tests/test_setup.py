"""Setups, board layouts and modules (tools/setup.py) and their drawings
(tools/viewer.py)."""

import copy
import os
import sys
import threading
import urllib.request

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import init as config_init   # noqa: E402
from tools import codegen                # noqa: E402
from tools import setup as su            # noqa: E402
from tools import viewer                 # noqa: E402


def _setups():
    return sorted(su.read_setups())


@pytest.mark.parametrize("sid", _setups())
def test_setup_generates_its_configuration(sid):
    setup = su.read_setup(sid)
    assert su.generate(setup) == config_init.read_configurations()[sid]
    assert [p for p in su.validate(setup) if p[0] == "error"] == []


def test_every_configuration_of_a_laid_out_board_has_a_setup_and_round_trips():
    boards = set(su.read_layouts())
    setups = su.read_setups()
    for cid, cfg in config_init.read_configurations().items():
        if cfg["board"] in boards:
            assert cid in setups
            assert su.check_roundtrip(cfg) == []


@pytest.mark.parametrize("board_id", sorted(su.read_layouts()))
def test_layout_pins_are_distinct_board_pins_on_signal_positions(board_id):
    layout = su.read_layout(board_id)
    pinmap = config_init.read_board_pinmap(board_id)
    connectors = su.read_connectors()
    seen = {}
    for conn in layout["connectors"]:
        ctype = connectors[conn["type"]]
        power = {str(k) for k in (ctype.get("power") or {})}
        numbered = {str(k) for row in ctype.get("rows") or [] for k in row}
        for key, ref in conn["pins"].items():
            assert str(key) not in power, (conn["id"], key)
            if numbered:
                assert str(key) in numbered, (conn["id"], key)
            pins = [p for _b, p in codegen._bind_pins(pinmap, ref)]
            assert pins and None not in pins, (conn["id"], key, ref)
            for p in pins:
                assert p not in seen, "{} is {} and {}".format(p, seen.get(p), (conn["id"], key))
                seen[p] = (conn["id"], key)
    for o in layout["onboard"]:
        assert o["attach"]["peripheral"] in config_init.read_peripherals()


def test_plugged_pmod_equals_its_wires():
    base = su.read_setup("arty_a7_35_pmod_mic3")
    plugged = [u for u in base["use"] if u.get("module") == "digilent_pmod_mic3"][0]
    assert plugged["plug"] == {"connector": "jd", "row": 2}
    wired = copy.deepcopy(base)
    for u in wired["use"]:
        if u.get("module") == "digilent_pmod_mic3":
            u.pop("plug")
            u["wires"] = {"1": "jd.7", "3": "jd.9", "4": "jd.10"}
    assert su.generate(wired) == su.generate(base)


def _problems(setup):
    return [m for level, m in su.validate(setup) if level == "error"]


def test_validate_reports_rig_mistakes():
    base = su.read_setup("tang_primer_20k_dock_hdmi_tm1638")
    assert _problems(base) == []

    clash = copy.deepcopy(base)       # a second TM1638 on the first one's pins
    clash["use"].append({"module": "tm1638_led_key", "wires": {"CLK": "j12.8", "STB": "j12.6", "DIO": "j12.10"}})
    assert any("used by both" in m for m in _problems(clash))

    shared = copy.deepcopy(base)      # a connector handed to the design as gpio may overlap
    shared["use"].append({"gpio": "j12", "params": {"width": 8}})
    assert _problems(shared) == []

    missing = copy.deepcopy(base)
    for u in missing["use"]:
        if u.get("module") == "tm1638_led_key":
            del u["wires"]["DIO"]
    assert any("required signal 'dio'" in m for m in _problems(missing))

    bad_pin = copy.deepcopy(base)
    for u in bad_pin["use"]:
        if u.get("module") == "tm1638_led_key":
            u["wires"]["DIO"] = "j12.1"       # 3V3
    assert any("no signal pin '1'" in m for m in _problems(bad_pin))

    nowhere = copy.deepcopy(base)
    nowhere["use"].append({"gpio": "j99"})
    assert _problems(nowhere) == ["board 'tang_primer_20k_dock' has no connector 'j99'"]

    lcd = su.read_setup("tang_primer_20k_dock_lcd_800_480_tm1638")
    on_lcd_pins = copy.deepcopy(lcd)  # J5 shares its FPGA pins with the LCD connector
    on_lcd_pins["use"].append({"module": "inmp441_breakout",
                               "wires": {"SD": "j5.5", "WS": "j5.6", "SCK": "j5.7", "L/R": "j5.8"}})
    assert any("used by both lcd and inmp441_breakout" in m for m in _problems(on_lcd_pins))


def test_voltage_range_is_checked(monkeypatch):
    base = su.read_setup("tang_primer_20k_dock_hdmi_tm1638")
    modules = su.read_modules()
    modules["tm1638_led_key"]["voltage"] = 5.0
    monkeypatch.setattr(su, "read_modules", lambda: modules)
    assert any("module for 5.0 V on the 3.3 V connector j12" in m for m in _problems(base))


def test_drawings():
    page = viewer.render_page(setup_id="arty_a7_35_pmod_mic3")
    assert "<svg" in page and "Digilent PmodMIC3" in page and "plugged into jd row 2" in page
    assert "jd.7: pmod_jd[4] = E2" in page                 # hover text: pinmap entry and FPGA pin
    assert "Shield header  (design gpio)" in page
    board = viewer.render_page(board_id="tang_primer_20k_dock")
    assert "J14 (mic array)" in board and "j14.5: gpio_0[7] = P9" in board
    index = viewer.render_index()
    assert "/setup/tang_primer_20k_dock_hdmi_tm1638" in index and "/board/arty_a7" in index


def test_local_web_page():
    httpd = viewer.make_server(port=0)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    try:
        base = "http://127.0.0.1:{}".format(httpd.server_address[1])
        assert "/board/arty_a7" in urllib.request.urlopen(base + "/").read().decode()
        assert "<svg" in urllib.request.urlopen(base + "/setup/arty_a7_35").read().decode()
        with pytest.raises(urllib.error.HTTPError):
            urllib.request.urlopen(base + "/setup/no_such_setup")
    finally:
        httpd.shutdown()
        httpd.server_close()
