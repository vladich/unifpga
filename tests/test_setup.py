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
    assert "no signal pin '99'" in ev["problems"][0]["message"]
    assert ev["trace"] and ev["excluded"][0]["reason"] == ev["problems"][0]["message"]     # the rest still traces


@pytest.fixture
def scratch(tmp_path, monkeypatch):
    """Setups and configurations written to a scratch directory (starting
    with a copy of the repository's setups)."""
    import shutil
    shutil.copytree(su.SETUP_DIR, str(tmp_path / "setups"))
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


def test_designs_that_do_not_fit_the_rig_are_flagged_and_refused(scratch, tmp_path, monkeypatch):
    rig = copy.deepcopy(su.read_setup("arty_a7_35"))
    rig["id"] = "arty_no_display"
    rig["use"] = [u for u in rig["use"] if u.get("module") != "digilent_pmod_vga"]
    fit = studio.evaluate(rig)["designs"]
    screen = [d for d, unmet in fit.items() if any("screen" in m for m in unmet)]
    assert screen and fit["1_06_binary_counter"] == []
    with_vga = studio.evaluate(dict(su.read_setup("arty_a7_35"), id="arty_with_vga"))["designs"]
    assert all(not any("screen" in m for m in with_vga[d]) for d in screen)
    studio.save(rig)
    config_init.clear_cache()
    monkeypatch.setattr(config_init, "read_configurations", lambda real=config_init.read_configurations: dict(
        real(), arty_no_display=su.generate(rig)))
    with pytest.raises(studio.ApiError, match="does not fit"):
        studio.project("arty_no_display", screen[0])


def _links(peripheral):
    return {s: sorted((l["port"], l["driver_port"]) for l in ls)
            for s, ls in trace.pin_links(config_init.read_peripherals()[peripheral]).items()}


def test_module_pins_link_to_the_design_ports_they_serve():
    vga = _links("vga_4bit")
    assert vga["r"] == [("screen.red", "vga_r")] and vga["hs"] == [("screen.x", "hsync")]
    assert _links("pmod_mic3") == {"cs": [("audio_in.sample", "cs")], "sclk": [("audio_in.sample", "sck")],
                                   "miso": [("audio_in.sample", "sdo")]}
    tm = {p for p, _d in _links("tm1638_led_key")["dio"]}
    assert tm == {"switches.sw", "buttons.btn", "leds.led", "seven_segment.abcdefgh", "seven_segment.digit"}
    lcd = _links("lcd_800_480")
    assert lcd["r"] == [("screen.red", None)] and ("screen.x", "LCD_HSYNC") in lcd["hs"]
    assert _links("uart_2wire") == {"tx": [("serial_console.tx", None)], "rx": [("serial_console.rx", None)]}


def test_serves_names_real_pins_and_provided_ports():
    caps = config_init.read_capabilities()
    for pid, p in config_init.read_peripherals().items():
        signals = {s["name"] for s in p.get("signals") or []}
        provided = {e["capability"] for e in p.get("provides") or []}
        for sig, ports in (p.get("serves") or {}).items():
            assert sig in signals, (pid, sig)
            for port in ports:
                cap, _, name = port.partition(".")
                assert cap in provided and any(s["name"] == name for s in caps[cap]["signals"]), (pid, port)


def test_autowire_plugs_or_wires_free_pins():
    rig = copy.deepcopy(su.read_setup("arty_a7_35"))
    rig["id"] = "aw_rig"
    rig["use"].append({"module": "digilent_pmod_mic3", "wires": {}})
    assert su.autowire(rig, len(rig["use"]) - 1) == {"plug": {"connector": "ja", "row": 1}}
    rig["use"][-1] = dict(module="digilent_pmod_mic3", plug={"connector": "ja", "row": 1})
    rig["use"].append({"module": "tm1638_led_key", "wires": {}})
    w = su.autowire(rig, len(rig["use"]) - 1)["wires"]
    # JA's top row is under the plugged MIC3, its unconnected pin 2 too
    assert set(w) == {"STB", "CLK", "DIO"} and all(x.split(".")[1] not in ("1", "2", "3", "4")
                                                   for x in w.values() if x.startswith("ja."))
    under = copy.deepcopy(rig)
    under["use"][-1]["wires"] = {"STB": "ja.2", "CLK": "ja.7", "DIO": "ja.8"}
    assert any("used by both digilent_pmod_mic3 and tm1638_led_key" in m for lvl, m in su.validate(under))
    rig["use"][-1]["wires"] = w
    assert [p for p in su.validate(rig) if p[0] == "error"] == []

    dock = copy.deepcopy(su.read_setup("tang_primer_20k_dock_hdmi_no_tm1638"))
    dock["id"] = "aw_dock"
    dock["use"].append({"module": "digilent_pmod_vga", "wires": {}})
    dock["use"][-1]["wires"] = su.autowire(dock, len(dock["use"]) - 1)["wires"]      # spans J6 and J5
    assert len(dock["use"][-1]["wires"]) == 14 and [p for p in su.validate(dock) if p[0] == "error"] == []

    lcd = copy.deepcopy(su.read_setup("tang_primer_20k_dock_lcd_800_480_tm1638"))
    lcd["use"].append({"module": "digilent_pmod_vga", "wires": {}})
    with pytest.raises(su.SetupError, match="needs 14 free pins; the board has 5 left"):
        su.autowire(lcd, len(lcd["use"]) - 1)             # J5 / J6 are the LCD's


def test_evaluation_traces_around_a_broken_part():
    rig = copy.deepcopy(su.read_setup("arty_a7_35"))
    rig["use"].append({"module": "tm1638_led_key", "wires": {"CLK": "ja.99"}})
    ev = studio.evaluate(rig)
    assert ev["trace"] and ev["excluded"] == [{"use": len(rig["use"]) - 1, "label": "tm1638_led_key",
                                               "reason": "connector 'ja' has no signal pin '99'"}]
    assert {a["attach_index"] for a in ev["trace"]["attaches"] if a["attach_index"] is not None} <= set(range(len(rig["use"]) - 1))
    profiled = copy.deepcopy(su.read_setup("arty_a7_35"))       # its profile fixes the bit layout
    profiled["use"].append({"module": "tm1638_led_key", "wires": {"STB": "ja.1", "CLK": "ja.2", "DIO": "ja.3"}})
    ev = studio.evaluate(profiled)
    assert ev["trace"] and ev["excluded"][0]["use"] == len(profiled["use"]) - 1 and "lab_bits" in ev["excluded"][0]["reason"]


def test_design_ports_follow_the_design_top_interface():
    import re
    with open(os.path.join(os.path.dirname(su.CONFIG_DIR), "rtl", "peripherals", "design_top_interface.sv")) as f:
        text = f.read()
    body = text[text.index(")\n(") + 3:text.index(");")]
    declared = re.findall(r"^\s*(?:input|output|inout)\b[^\n]*?(\w+)\s*,?\s*(?://[^\n]*)?$", body, re.M)
    assert [p for p, *_ in codegen.DESIGN_PORTS] == declared
    r = config_init.resolve_configuration("arty_a7_35_pmod_mic3")
    ports = {p["design_port"]: p for p in trace.trace(r)["ports"]}
    assert (ports["x"]["width"], ports["y"]["width"], ports["red"]["width"]) == (10, 9, 4)
    assert (ports["mic_sample"]["width"], ports["sound"]["width"], ports["sound"]["providers"]) == (24, 0, [])
    assert ports["gpio"]["width"] == 42 and ports["led"]["width"] == 8


def test_edges_connect_design_bits_to_pins():
    r = config_init.resolve_configuration("arty_a7_35_pmod_mic3")
    edges = trace.trace(r)["edges"]
    red = sorted((e["bit"], e["ref"], e["via"]) for e in edges if e["design_port"] == "red")
    assert red == [(0, "pmod_jb[4]", "vga"), (1, "pmod_jb[5]", "vga"), (2, "pmod_jb[6]", "vga"), (3, "pmod_jb[7]", "vga")]
    assert [(e["bit"], e["signal"]) for e in edges if e["design_port"] == "x"] == [(None, "hs")]
    leds = sorted((e["bit"], e["ref"], e["via"]) for e in edges if e["design_port"] == "led" and e["via"] is None)
    assert leds == [(0, "onboard_leds[0]", None), (1, "onboard_leds[1]", None), (2, "onboard_leds[2]", None),
                    (3, "onboard_leds[3]", None)]
    mic = {e["ref"] for e in edges if e["design_port"] == "mic_sample"}
    assert mic == {"pmod_jd[4]", "pmod_jd[6]", "pmod_jd[7]"}
