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
from tools import studio                 # noqa: E402
from tools import trace                  # noqa: E402


def _setups():
    return sorted(su.read_setups())


@pytest.mark.parametrize("sid", _setups())
def test_setup_generates_its_configuration(sid):
    setup = su.read_setup(sid)
    cfg = config_init.read_configurations()[sid]
    assert su.same_configuration(su.generate(setup), cfg)            # nested keys in codegen's order too
    with open(su.configuration_path(sid), encoding="utf-8") as f:
        assert f.read() == su.generated_text(setup)                   # the committed file is the generated one
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


def test_plugged_pmod_wires_its_row():
    base = su.read_setup("arty_a7_35_pmod_mic3")
    plugged = copy.deepcopy(base)
    for u in plugged["use"]:
        if u.get("module") == "digilent_pmod_mic3":
            u.pop("wires")
            u["plug"] = {"connector": "jd", "row": 2}
    got = [a for a in su.generate(plugged)["attach"] if a["peripheral"] == "pmod_mic3"][0]
    assert got["bind"] == {"cs": "pmod_jd[4]", "miso": "pmod_jd[6]", "sclk": "pmod_jd[7]"}


def test_ordered_compares_key_order():
    assert su.ordered({"a": 1, "b": [{"x": 1, "y": 2}]}) != su.ordered({"b": [{"x": 1, "y": 2}], "a": 1})
    assert su.ordered({"a": 1}) == su.ordered({"a": 1})


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


def test_trace_follows_the_generated_top():
    r = config_init.resolve_configuration("arty_a7_35_pmod_mic3")
    ports = {(p["capability"], p["signal"]): p for p in trace.trace(r)["ports"]}
    leds = ports[("leds", "led")]["providers"]
    board = [pr for pr in leds if pr["peripheral"] == "led_bank"][0]
    # top.sv: assign onboard_leds = cap_leds_led[3:0];
    assert [(b["design_bit"], b["ref"], b["pin"]) for b in board["bits"]] == \
        [(0, "onboard_leds[0]", "H5"), (1, "onboard_leds[1]", "J5"), (2, "onboard_leds[2]", "T9"), (3, "onboard_leds[3]", "T10")]
    tm = [pr for pr in leds if pr["peripheral"] == "tm1638_led_key"][0]
    assert tm["via"] == "tm1638_board_controller" and tm["attach_index"] == 11
    assert tm["pins"]["stb"] == [{"ref": "arduino_io[29]", "pin": "N17"}]
    top = codegen.emit_top_sv(r)
    assert "assign onboard_leds = cap_leds_led[3:0];" in top


def test_board_data_and_evaluation():
    data = studio.board_data("arty_a7")
    jd = [c for c in data["connectors"] if c["id"] == "jd"][0]
    assert jd["rows"][1] == ["7", "8", "9", "10", "11", "12"] and jd["pins"]["7"] == {"ref": "pmod_jd[4]", "pin": "E2"}
    assert jd["power"]["12"] == "VCC"
    assert "arty_a7_35_pmod_mic3" in data["setups"] and "1_06_binary_counter" in data["designs"]
    ev = studio.evaluate(su.read_setup("arty_a7_35_pmod_mic3"))
    assert ev["trace"] and not [p for p in ev["problems"] if p["level"] == "error"]
    assert ev["configuration_text"] == su.generated_text(su.read_setup("arty_a7_35_pmod_mic3"))
    broken = copy.deepcopy(su.read_setup("arty_a7_35_pmod_mic3"))
    broken["use"].append({"module": "tm1638_led_key", "wires": {"CLK": "jd.99"}})
    ev = studio.evaluate(broken)
    assert ev["trace"] is None and "no signal pin '99'" in ev["problems"][0]["message"]


@pytest.fixture
def scratch(tmp_path, monkeypatch):
    """Setups and configurations written to a scratch directory."""
    (tmp_path / "setups").mkdir()
    (tmp_path / "configurations").mkdir()
    monkeypatch.setattr(su, "SETUP_DIR", str(tmp_path / "setups"))
    monkeypatch.setattr(su, "configuration_path", lambda cid: str(tmp_path / "configurations" / (cid + ".yml")))
    return tmp_path


def test_save_writes_setup_and_configuration(scratch):
    rig = copy.deepcopy(su.read_setup("tang_primer_20k_dock_hdmi_tm1638"))
    rig["id"] = "dock_test_rig"
    rig["use"] = [u for u in rig["use"] if u.get("module") != "inmp441_breakout"]
    paths = studio.save(rig)
    assert paths["setup"].endswith("dock_test_rig.yml")
    text = (scratch / "configurations" / "dock_test_rig.yml").read_text()
    assert text == su.generated_text(rig) and "inmp441" not in text
    bad = copy.deepcopy(rig)
    bad["use"].append({"module": "tm1638_led_key", "wires": {"CLK": "j12.8", "STB": "j12.6", "DIO": "j12.10"}})
    with pytest.raises(studio.ApiError, match="used by both"):
        studio.save(bad)
    for wrong in ("../evil", "Upper", ""):
        with pytest.raises(studio.ApiError):
            studio.save(dict(rig, id=wrong))
    with pytest.raises(studio.ApiError, match="exists and has no setup"):
        studio.save(dict(rig, id="de10_lite"))


def test_project_is_the_dry_run(tmp_path, monkeypatch):
    import shutil
    design = tmp_path / "designs" / "1_06_binary_counter"
    shutil.copytree(os.path.join(studio.DESIGNS_DIR, "1_06_binary_counter"), str(design))
    monkeypatch.setattr(studio, "DESIGNS_DIR", str(tmp_path / "designs"))
    out = studio.project("arty_a7_35", "1_06_binary_counter")
    assert out["ok"] and "top.sv" in out["files"] and any(f.endswith(".xdc") for f in out["files"])
    data = studio.project_zip("arty_a7_35", "1_06_binary_counter")
    import zipfile, io
    names = zipfile.ZipFile(io.BytesIO(data)).namelist()
    assert "design_top.sv" in names and "arty_a7_35/top.sv" in names
    with pytest.raises(studio.ApiError):
        studio.project("no_such_setup", "1_06_binary_counter")


def test_standalone_page_is_self_contained():
    page = studio.standalone_page(setup_id="arty_a7_35_pmod_mic3")
    assert "window.STUDIO_STATIC" in page and 'src="studio.js"' not in page and "<style>" in page
    assert '"pmod_jd[4]"' in page


def _server():
    httpd = studio.make_server(port=0)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, "http://127.0.0.1:{}".format(httpd.server_address[1])


def _post(url, body, headers):
    import json
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers=dict({"Content-Type": "application/json"}, **headers))
    return urllib.request.urlopen(req)


def test_editor_server_and_its_write_guard():
    httpd, base = _server()
    try:
        assert "studio.js" in urllib.request.urlopen(base + "/").read().decode()
        assert "use strict" in urllib.request.urlopen(base + "/studio.js").read().decode()
        assert "arty_a7" in urllib.request.urlopen(base + "/api/boards").read().decode()
        setup = su.read_setup("arty_a7_35")
        ok = _post(base + "/api/evaluate", {"setup": setup}, {"X-Unifpga-Studio": "1"})
        assert b'"trace"' in ok.read()
        for headers in ({}, {"X-Unifpga-Studio": "1", "Origin": "https://evil.example"},
                        {"X-Unifpga-Studio": "1", "Host": "evil.example"}):
            with pytest.raises(urllib.error.HTTPError) as err:
                _post(base + "/api/evaluate", {"setup": setup}, headers)
            assert err.value.code == 403
        with pytest.raises(urllib.error.HTTPError):
            urllib.request.urlopen(base + "/api/setup/no_such_setup")
    finally:
        httpd.shutdown()
        httpd.server_close()
