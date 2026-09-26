"""Setups, board layouts and modules (tools/setup.py) and their drawings
(tools/viewer.py)."""

import copy
import json
import os
import re
import shutil
import subprocess
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
def test_setup_expands_to_its_configuration(sid):
    """A rig is its setup: the configuration the build reads is the setup's
    expansion (no file), the same every time, and it resolves."""
    setup = su.read_setup(sid)
    cfg = config_init.read_configurations()[sid]
    assert su.same_configuration(su.generate(setup), cfg)            # nested keys in codegen's order too
    assert su.generated_text(setup) == su.generated_text(setup)
    assert [p for p in su.validate(setup) if p[0] == "error"] == []
    config_init.resolve_configuration(sid)


def test_every_configuration_of_a_laid_out_board_has_a_setup_and_round_trips():
    boards = set(su.drawn_boards())
    setups = su.read_setups()
    for cid, cfg in config_init.read_configurations().items():
        if cfg["board"] in boards:
            assert cid in setups
            assert su.check_roundtrip(cfg) == []


@pytest.mark.parametrize("board_id", sorted(su.drawn_boards()))
def test_layout_pins_are_distinct_board_pins_on_signal_positions(board_id):
    layout = su.read_drawn(board_id)
    connectors = su.read_connectors()
    seen = {}
    for conn in layout["headers"]:
        ctype = connectors[conn["type"]]
        power = {str(k) for k in (ctype.get("power") or {})}
        numbered = {str(k) for row in ctype.get("rows") or [] for k in row}
        for key, ref in conn["pins"].items():
            assert str(key) not in power, (conn["id"], key)
            if numbered:
                assert str(key) in numbered, (conn["id"], key)
            pins = [p for _b, p in codegen._bind_pins(layout, ref)]
            assert pins and None not in pins, (conn["id"], key, ref)
            bank = (layout.get("banks") or {}).get(conn.get("bank")) or {}
            for p in pins:
                if p in seen:
                    # two connectors on the same FPGA pins only where the board
                    # says it multiplexes them (`shares:`)
                    other = (layout.get("banks") or {}).get(seen[p][2]) or {}
                    assert seen[p][2] in (bank.get("shares") or []) or conn.get("bank") in (other.get("shares") or []), \
                        "{} is {} and {}".format(p, seen.get(p)[:2], (conn["id"], key))
                seen.setdefault(p, (conn["id"], key, conn.get("bank")))
    for o in layout["parts"]:
        for _vid, _label, attach in su.onboard_variants(o):
            assert attach["peripheral"] in config_init.read_peripherals()


def test_plugged_pmod_wires_its_row():
    base = su.read_setup("arty_a7_pmod_mic3")
    plugged = copy.deepcopy(base)
    for u in plugged["use"]:
        if u.get("module") == "digilent_pmod_mic3":
            u.pop("wires", None)
            u["plug"] = {"connector": "jd", "row": 2}
    got = [a for a in su.generate(plugged)["attach"] if a["peripheral"] == "pmod_mic3"][0]
    assert got["bind"] == {"cs": "pmod_jd[4]", "miso": "pmod_jd[6]", "sclk": "pmod_jd[7]"}


def test_plugging_follows_pin_roles_in_either_orientation():
    """A module plugs by its numbered header into any connector row as long as
    the header lands power on VCC, ground on GND and signals on signal pins, in
    whichever orientation does that; nothing names a module form."""
    connectors, mic = su.read_connectors(), su.read_modules()["digilent_pmod_mic3"]
    arty, dock = su.read_drawn("arty_a7"), su.read_drawn("tang_primer_20k_dock")
    assert {"connector": "ja", "row": 1} in su.plug_placements(connectors, arty, mic)
    placements = su.plug_placements(connectors, dock, mic)
    assert placements and all(p.get("reversed") for p in placements)   # the Dock's rows run the other way
    plug = placements[0]
    wires = su.plug_wires(connectors, dock, mic, plug)
    assert su._as_plug(connectors, dock, mic, wires) == plug
    with pytest.raises(su.SetupError, match=r"pin 6 \(power\) would sit on"):
        su.plug_wires(connectors, dock, mic, {"connector": plug["connector"], "row": plug["row"]})
    rig = copy.deepcopy(su.read_setup("tang_primer_20k_dock_hdmi_no_tm1638"))
    rig["use"].append({"module": "digilent_pmod_mic3", "wires": {}})
    assert su.autowire(rig, len(rig["use"]) - 1)["plug"].get("reversed") is True


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
    r = config_init.resolve_configuration("arty_a7_pmod_mic3")
    ports = {(p["capability"], p["signal"]): p for p in trace.trace(r)["ports"]}
    leds = ports[("leds", "led")]["providers"]
    board = [pr for pr in leds if pr["peripheral"] == "led_bank"][0]
    # top.sv: assign onboard_leds = cap_leds_led[3:0];
    assert [(b["design_bit"], b["ref"], b["pin"]) for b in board["bits"]] == \
        [(0, "onboard_leds[0]", "H5"), (1, "onboard_leds[1]", "J5"), (2, "onboard_leds[2]", "T9"), (3, "onboard_leds[3]", "T10")]
    tm = [pr for pr in leds if pr["peripheral"] == "tm1638_led_key"][0]
    assert tm["via"] == "tm1638_board_controller" and tm["attach_index"] == 12
    assert tm["pins"]["stb"] == [{"ref": "arduino_io[29]", "pin": "N17"}]
    top = codegen.emit_top_sv(r)
    assert "assign onboard_leds = cap_leds_led[3:0];" in top


def test_board_data_and_evaluation():
    data = studio.board_data("arty_a7")
    jd = [c for c in data["connectors"] if c["id"] == "jd"][0]
    assert jd["rows"][1] == ["7", "8", "9", "10", "11", "12"] and jd["pins"]["7"] == {"ref": "pmod_jd[4]", "pin": "E2"}
    assert jd["power"]["12"] == "VCC"
    assert "arty_a7_pmod_mic3" in data["setups"] and "1_06_binary_counter" in data["designs"]
    ev = studio.evaluate(su.read_setup("arty_a7_pmod_mic3"))
    assert ev["trace"] and not [p for p in ev["problems"] if p["level"] == "error"]
    assert ev["configuration_text"] == su.generated_text(su.read_setup("arty_a7_pmod_mic3"))
    broken = copy.deepcopy(su.read_setup("arty_a7_pmod_mic3"))
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
    monkeypatch.setattr(su, "SETUP_DIR", str(tmp_path / "setups"))
    return tmp_path


def test_save_writes_the_setup(scratch):
    rig = copy.deepcopy(su.read_setup("tang_primer_20k_dock_hdmi_tm1638"))
    rig["id"] = "dock_test_rig"
    del rig["aliases"]                                  # the original keeps its old ids
    rig["use"] = [u for u in rig["use"] if u.get("module") != "inmp441_breakout"]
    paths = studio.save(rig)
    assert paths["setup"].endswith("dock_test_rig.yml") and list(paths) == ["setup"]
    assert (scratch / "setups" / "dock_test_rig.yml").read_text() == su.dump_setup(rig)
    assert "inmp441" not in studio.evaluate(rig)["configuration_text"]
    bad = copy.deepcopy(rig)
    bad["use"].append({"module": "tm1638_led_key", "wires": {"CLK": "j12.8", "STB": "j12.6", "DIO": "j12.10"}})
    with pytest.raises(studio.ApiError, match="used by both"):
        studio.save(bad)
    for wrong in ("../evil", "Upper", ""):
        with pytest.raises(studio.ApiError):
            studio.save(dict(rig, id=wrong))


def test_project_is_the_dry_run(tmp_path, monkeypatch):
    import shutil
    design = tmp_path / "designs" / "1_06_binary_counter"
    shutil.copytree(os.path.join(studio.DESIGNS_DIR, "1_06_binary_counter"), str(design))
    monkeypatch.setattr(studio, "DESIGNS_DIR", str(tmp_path / "designs"))
    out = studio.project("arty_a7", "1_06_binary_counter")
    assert out["ok"] and "top.sv" in out["files"] and any(f.endswith(".xdc") for f in out["files"])
    data = studio.project_zip("arty_a7", "1_06_binary_counter")
    import zipfile, io
    names = zipfile.ZipFile(io.BytesIO(data)).namelist()
    assert "design_top.sv" in names and "arty_a7/top.sv" in names
    with pytest.raises(studio.ApiError):
        studio.project("no_such_setup", "1_06_binary_counter")


def test_standalone_page_is_self_contained():
    page = studio.standalone_page(setup_id="arty_a7_pmod_mic3")
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
        js = urllib.request.urlopen(base + "/studio.js")
        assert "use strict" in js.read().decode()
        # the page is never taken from a browser cache: after a restart it is this server's code
        assert js.headers["Cache-Control"] == "no-store"
        assert urllib.request.urlopen(base + "/").headers["Cache-Control"] == "no-store"
        assert "arty_a7" in urllib.request.urlopen(base + "/api/boards").read().decode()
        progress = json.loads(urllib.request.urlopen(base + "/api/progress").read().decode())
        assert set(progress) == {"ready", "stage", "done", "total"}     # the page's progress bar polls it
        setup = su.read_setup("arty_a7")
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
    rig = copy.deepcopy(su.read_setup("arty_a7"))
    rig["id"] = "arty_no_display"
    del rig["aliases"]
    rig["use"] = [u for u in rig["use"] if u.get("module") != "digilent_pmod_vga"]
    fit = studio.evaluate(rig)["designs"]
    screen = [d for d, unmet in fit.items() if any("screen" in m for m in unmet)]
    assert screen and fit["1_06_binary_counter"] == []
    with_vga = studio.evaluate(dict(su.read_setup("arty_a7"), id="arty_with_vga", aliases={}))["designs"]
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
            per_pin = all(isinstance(x, list) for x in ports)
            if per_pin:          # one list per pin of a bus: as many as the bus is wide
                width = next(s for s in p["signals"] if s["name"] == sig).get("width")
                assert not isinstance(width, int) or len(ports) == width, (pid, sig)
            for port in (x for group in ports for x in group) if per_pin else ports:
                cap, _, name = port.partition(".")
                assert cap in provided and any(s["name"] == name for s in caps[cap]["signals"]), (pid, port)


def test_hdmi_channels_carry_their_colour():
    """TMDS channel k carries one colour (d[0] blue with the syncs, d[1] green,
    d[2] red: rtl/peripherals/hdmi_tmds_out.sv), not every screen port."""
    rig = su.read_setup("tang_nano_9k_hdmi_tm1638")
    ev = studio.evaluate(rig)
    hdmi = [e for e in ev["trace"]["edges"] if e["via"] and e["signal"] in ("d_p", "d_n")]
    by_pin = {}
    for e in hdmi:
        by_pin.setdefault((e["signal"], e["ref"]), set()).add(e["design_port"])
    a = next(x for x in ev["trace"]["attaches"] if x["peripheral"] == "hdmi_tmds")
    refs = [p["ref"] for p in a["pins"]["d_p"]]
    assert by_pin[("d_p", refs[0])] == {"blue", "x", "y"}
    assert by_pin[("d_p", refs[1])] == {"green"} and by_pin[("d_p", refs[2])] == {"red"}


def test_autowire_plugs_or_wires_free_pins():
    rig = copy.deepcopy(su.read_setup("arty_a7"))
    rig["id"] = "aw_rig"
    rig.pop("aliases", None)
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
    dock.pop("aliases", None)
    dock["use"].append({"module": "digilent_pmod_vga", "wires": {}})
    dock["use"][-1]["wires"] = su.autowire(dock, len(dock["use"]) - 1)["wires"]      # spans J6 and J5
    assert len(dock["use"][-1]["wires"]) == 14 and [p for p in su.validate(dock) if p[0] == "error"] == []

    lcd = copy.deepcopy(su.read_setup("tang_primer_20k_dock_lcd_800_480_tm1638"))
    lcd["use"].append({"module": "digilent_pmod_vga", "wires": {}})
    with pytest.raises(su.SetupError, match="needs 14 free pins; the board has 5 left"):
        su.autowire(lcd, len(lcd["use"]) - 1)             # J5 / J6 are the LCD's


def test_autowire_prefers_the_pmods_to_the_sdram_socket_and_says_what_the_pins_share():
    """Tang Mega 138K: both Pmods are handed to the design as gpio and J14 (the
    SDRAM1 socket) is the roomiest header. The microphone goes to Pmod pins the
    design also has as gpio, not through J14, whose pins the board multiplexes
    with the SDRAM module, the camera and the Pmods; the note says which."""
    rig = copy.deepcopy(su.read_setup("tang_mega_138k_lcd_480_272_tm1638"))
    i = next(k for k, u in enumerate(rig["use"]) if u.get("module") == "inmp441_breakout")
    rig["use"][i] = {"module": "inmp441_breakout", "wires": {}}
    got = su.autowire(rig, i)
    assert {w.split(".")[0] for w in got["wires"].values()} <= {"pmod_0", "pmod_1"}, got
    note = su.wiring_note(rig, i, got)
    assert "gpio" in note and "J14" not in note, note
    rig["use"][i].update(got)
    assert [p for p in su.validate(rig) if p[0] == "error"] == []
    through = {"wires": {"SCK": "j14.1", "WS": "j14.3", "SD": "j14.5", "L/R": "j14.7"}}
    assert "through J14 (SDRAM1), which the board multiplexes with onboard_sdram_1" in su.wiring_note(rig, i, through)
    # with the Pmods not handed to the design, their free pins come first and nothing is shared
    free = copy.deepcopy(rig)
    free["use"] = [u for u in free["use"] if not u.get("gpio")]
    j = next(k for k, u in enumerate(free["use"]) if u.get("module") == "inmp441_breakout")
    free["use"][j] = {"module": "inmp441_breakout", "wires": {}}
    got = su.autowire(free, j)
    assert {w.split(".")[0] for w in got["wires"].values()} == {"pmod_0"} and su.wiring_note(free, j, got) == ""


def test_evaluation_traces_around_a_broken_part():
    rig = copy.deepcopy(su.read_setup("arty_a7"))
    rig["use"].append({"module": "tm1638_led_key", "wires": {"CLK": "ja.99"}})
    ev = studio.evaluate(rig)
    assert ev["trace"] and ev["excluded"] == [{"use": len(rig["use"]) - 1, "label": "tm1638_led_key",
                                               "reason": "connector 'ja' has no signal pin '99'"}]
    assert {a["attach_index"] for a in ev["trace"]["attaches"] if a["attach_index"] is not None} <= set(range(len(rig["use"]) - 1))
    placed = copy.deepcopy(su.read_setup("arty_a7"))         # its design_bits fix the bit layout
    placed["use"].append({"module": "tm1638_led_key", "wires": {"STB": "ja.1", "CLK": "ja.2", "DIO": "ja.3"}})
    ev = studio.evaluate(placed)
    assert ev["trace"] and ev["excluded"][0]["use"] == len(placed["use"]) - 1 and "design_bits" in ev["excluded"][0]["reason"]


def test_design_ports_follow_the_design_top_interface():
    import re
    with open(os.path.join(os.path.dirname(su.CONFIG_DIR), "rtl", "peripherals", "design_top_interface.sv")) as f:
        text = f.read()
    body = text[text.index(")\n(") + 3:text.index(");")]
    declared = re.findall(r"^\s*(?:input|output|inout)\b[^\n]*?(\w+)\s*,?\s*(?://[^\n]*)?$", body, re.M)
    assert [p for p, *_ in codegen.design_ports()] == declared
    r = config_init.resolve_configuration("arty_a7_pmod_mic3")
    ports = {p["design_port"]: p for p in trace.trace(r)["ports"]}
    assert (ports["x"]["width"], ports["y"]["width"], ports["red"]["width"]) == (10, 9, 4)
    assert (ports["mic_sample"]["width"], ports["sound"]["width"], ports["sound"]["providers"]) == (24, 0, [])
    assert ports["gpio"]["width"] == 42 and ports["led"]["width"] == 8


def test_pins_the_fpga_drives_itself_are_sources_not_gaps():
    """A pin the build drives from the clock tree, a level or the reset (a
    peripheral's `pin.X: clock.pixel / const.0 / $bl`) is no design bit: the
    trace names its source so the editor draws it driven, not dangling."""
    ev = studio.evaluate(su.read_setup("tang_mega_138k_lcd_480_272_tm1638"))
    lcd = next(a for a in ev["trace"]["attaches"] if a["peripheral"] == "lcd_480_272")
    assert lcd["links"]["ck"] == [] and lcd["sources"]["ck"]["kind"] == "clock"
    assert lcd["sources"]["ck"]["text"] == "the pixel clock, 8 MHz from the PLL"
    assert set(lcd["sources"]) == {"ck"}                    # r, g, b, hs, vs, de carry design bits
    ev = studio.evaluate(su.read_setup("de10_lite"))
    ties = [a["sources"]["pin"] for a in ev["trace"]["attaches"] if a["peripheral"] == "pin_tie"]
    assert {t["text"] for t in ties} == {"tied to 0", "tied to 1", "the design's reset, inverted"}
    assert {t["kind"] for t in ties} == {"tied", "reset"}


def test_edges_connect_design_bits_to_pins():
    r = config_init.resolve_configuration("arty_a7_pmod_mic3")
    edges = trace.trace(r)["edges"]
    red = sorted((e["bit"], e["ref"], e["via"]) for e in edges if e["design_port"] == "red")
    assert red == [(0, "pmod_jb[4]", "vga"), (1, "pmod_jb[5]", "vga"), (2, "pmod_jb[6]", "vga"), (3, "pmod_jb[7]", "vga")]
    assert [(e["bit"], e["signal"]) for e in edges if e["design_port"] == "x"] == [(None, "hs")]
    leds = sorted((e["bit"], e["ref"], e["via"]) for e in edges if e["design_port"] == "led" and e["via"] is None)
    assert leds == [(0, "onboard_leds[0]", None), (1, "onboard_leds[1]", None), (2, "onboard_leds[2]", None),
                    (3, "onboard_leds[3]", None)]
    # the TM1638's pins carry the bits its design_bits give it, and no sw bit here
    tm = {e["design_port"]: e["bits"] for e in edges if e["ref"] == "arduino_io[27]" and e["via"]}
    assert tm == {"btn": list(range(8)), "led": list(range(8)), "abcdefgh": list(range(8)), "digit": list(range(8))}
    mic = {e["ref"] for e in edges if e["design_port"] == "mic_sample"}
    assert mic == {"pmod_jd[4]", "pmod_jd[6]", "pmod_jd[7]"}


def test_design_port_widths_are_the_interface_declarations():
    """Each DESIGN_PORTS width is the declaration in design_top_interface.sv:
    a parameter name for `[w_x - 1 : 0]`, a number for `[7 : 0]`, 1 without a range."""
    import re
    with open(os.path.join(os.path.dirname(su.CONFIG_DIR), "rtl", "peripherals", "design_top_interface.sv")) as f:
        text = f.read()
    body = text[text.index(")\n(") + 3:text.index(");")]
    declared = {}
    for m in re.finditer(r"^\s*(?:input|output|inout)\b(?:\s+logic)?\s*(\[[^\]]*\])?\s*(\w+)", body, re.M):
        rng, port = m.group(1), m.group(2)
        if not rng:
            declared[port] = 1
        else:
            hi = rng[1:-1].split(":")[0].strip()
            p = re.match(r"^(\w+)\s*-\s*1$", hi)
            declared[port] = p.group(1) if p else int(hi) + 1
    assert {p: w for p, _c, _s, w in codegen.design_ports()} == declared
    r = config_init.resolve_configuration("arty_a7_pmod_mic3")
    t = trace.trace(r)
    red = [p for p in t["ports"] if p["design_port"] == "red"][0]
    assert red["width_parameter"] == "w_red" and t["parameters"]["w_red"] == red["width"] == 4


def test_driver_bit_relations_follow_the_pin_fit():
    assert trace._fit([0, 1, 2, 3], 4, 2, "msb") == (2, "bit")
    assert trace._fit([0, 1, 2, 3], 1, 0, "msb") == (None, "or")
    assert trace._fit(list(range(8)), 4, 0, "msb") == (4, "msb")
    assert trace._fit(list(range(8)), 3, 1, None) == (None, "shared")
    # one pin per colour (omdazz, rgb12 onto 3 pins): the driver ORs the channel onto it
    om = trace.trace(config_init.resolve_configuration("omdazz"))
    assert [(e["bits"], e["relation"]) for e in om["edges"] if e["design_port"] == "red"] == [([0, 1, 2, 3], "or")]
    # a 10-bit DAC (de2, rgb30): bit for bit
    de2 = trace.trace(config_init.resolve_configuration("de2"))
    assert de2["parameters"]["w_red"] == 10
    assert sorted(e["bit"] for e in de2["edges"] if e["design_port"] == "red" and e["relation"] == "bit") == list(range(10))
    # 8 design bits onto a 4-pin PmodVGA: the top four reach the pins
    rig = copy.deepcopy(su.read_setup("arty_a7_pmod_mic3"))
    vga = [u for u in rig["use"] if u.get("module") == "digilent_pmod_vga"][0]
    vga["params"].update(bits_r=8)
    ev = studio.evaluate(rig)
    got = sorted((e["signal"], e["bit"], e["relation"]) for e in ev["trace"]["edges"] if e["design_port"] == "red")
    assert got == [("r", 4, "msb"), ("r", 5, "msb"), ("r", 6, "msb"), ("r", 7, "msb")], got
    assert ev["trace"]["parameters"]["w_red"] == 8


def test_verilog_view_marks_what_defines_the_target():
    rig = su.read_setup("arty_a7_pmod_mic3")
    tm = next(k for k, u in enumerate(rig["use"]) if u.get("module") == "tm1638_led_key")
    ev = studio.evaluate(rig)
    dio = next(e for e in ev["trace"]["edges"] if e["use"] == tm and e["signal"] == "dio")
    v = studio.verilog_view(rig, {"use": tm, "refs": [dio["ref"]], "design_port": "led", "module": dio["via"]})
    top, lines = v["files"][0], v["files"][0]["text"].split("\n")
    marked = [lines[n - 1] for n in top["highlight"]]
    assert top["path"] is None and any(dio["ref"] in l and ".sio_data(" in l for l in marked), marked
    assert any(l.strip().startswith(".led(") for l in marked)          # the design_top port
    assert v["files"][1]["path"].startswith("rtl/") and "module " + dio["via"] in v["files"][1]["text"]
    # the whole section of a part, the board's port list, a design_top parameter
    sec = studio.verilog_view(rig, {"use": tm})["files"][0]
    assert len(sec["highlight"]) > 5 and dio["via"] in sec["text"].split("\n")[sec["highlight"][1] - 1]
    b = studio.verilog_view(rig, {"board": True})["files"][0]
    assert b["text"].split("\n")[b["highlight"][0] - 1].startswith("module top")
    p = studio.verilog_view(rig, {"design_port": "red", "parameter": "w_red", "module": "design_top", "design": "2_9_pong"})
    assert p["files"][1]["path"] == "designs/2_9_pong/design_top.sv"
    assert p["files"][1]["text"].split("\n")[p["files"][1]["highlight"][0] - 1].startswith("module design_top")
    # the design's header is its rendered include: the parameter and the port are declared there
    header = p["files"][2]
    assert header["path"] == "designs/2_9_pong/design_top_interface.svh"
    assert [header["text"].split("\n")[n - 1].strip()[:5] for n in header["highlight"]] == ["param", "outpu"]
    assert "w_red" in header["text"].split("\n")[header["highlight"][0] - 1]


def test_module_source_is_limited_to_module_names():
    assert "module vga" in studio.module_source("vga")["text"]
    # the line is the module's own, also after leading blank lines
    for name in ("vga", "digilent_pmod_mic3_spi_receiver", "tm1638_board_controller"):
        src = studio.module_source(name)
        assert src["text"].split("\n")[src["line"] - 1].lstrip().startswith("module " + name), name
    for bad in ("../etc/passwd", "vga;rm", ""):
        with pytest.raises(studio.ApiError):
            studio.module_source(bad)
    with pytest.raises(studio.ApiError):
        studio.module_source("no_such_module_here")
    with pytest.raises(studio.ApiError):
        studio.module_source("design_top", "../x")


def test_parts_that_do_not_reach_the_design_say_why():
    for sid in su.read_setups():
        assert studio.evaluate(su.read_setup(sid))["parts"] == [], sid
    base = su.read_setup("arty_a7_pmod_mic3")
    # a second microphone: audio_in is exclusive, the first provider keeps it
    rig = copy.deepcopy(base)
    rig["use"].append({"module": "inmp441_breakout", "wires": {}})
    rig["use"][-1].update(su.autowire(rig, len(rig["use"]) - 1))
    ev = studio.evaluate(rig)
    (x,) = ev["parts"]
    assert x["use"] == len(rig["use"]) - 1 and not x["connected"] and x["reasons"][0][0] == "exclusive"
    assert "digilent_pmod_mic3 already provides it" in x["reasons"][0][1]
    assert any(p["level"] == "warning" and "audio_in" in p["message"] for p in ev["problems"])
    # a second TM1638 on a rig whose other parts have design_bits and it none
    rig = copy.deepcopy(base)
    rig["use"].append({"module": "tm1638_led_key", "wires": {}})
    rig["use"][-1].update(su.autowire(rig, len(rig["use"]) - 1))
    (x,) = studio.evaluate(rig)["parts"]
    assert x["reasons"][0][0] == "untraced" and "design_bits" in x["reasons"][0][1]
    assert x["label"] == "tm1638_led_key #2"
    # an unwired module
    rig = copy.deepcopy(base)
    rig["use"].append({"module": "i2s_dac_breakout", "wires": {}})
    kinds = [r[0] for p in studio.evaluate(rig)["parts"] for r in p["reasons"]]
    assert kinds and set(kinds) <= {"unwired", "untraced"}, kinds


def test_a_part_on_the_designs_gpio_header_takes_its_pins():
    """The TM1638 sits on three ChipKit pins of a header the rig hands to
    design_top's gpio: those pins are the TM1638's, their gpio bits dangle, so
    nothing is shared and nothing warns."""
    rig = su.read_setup("arty_a7_pmod_mic3")
    ev = studio.evaluate(rig)
    assert not [p for p in ev["problems"] if "shared by" in p["message"]]
    tm = next(k for k, u in enumerate(rig["use"]) if u.get("module") == "tm1638_led_key")
    gp = next(k for k, u in enumerate(rig["use"]) if u.get("gpio") == "ck")
    tm_refs = {e["ref"] for e in ev["trace"]["edges"] if e["use"] == tm}
    assert tm_refs and not tm_refs & {e["ref"] for e in ev["trace"]["edges"] if e["use"] == gp}


def _apply(rig, fix):
    """What the page does with a problem's button."""
    rig = copy.deepcopy(rig)
    if fix["op"] == "remove":
        del rig["use"][fix["use"]]
    else:
        use = rig["use"][fix["use"]]
        use.pop("plug", None)
        use.pop("wires", None)
        use.update(su.autowire(rig, fix["use"]))
    return rig


def test_conflicts_offer_buttons_that_resolve_them():
    base = su.read_setup("arty_a7_pmod_mic3")
    mic3 = next(k for k, u in enumerate(base["use"]) if u.get("module") == "digilent_pmod_mic3")
    # two microphones: keep either
    rig = copy.deepcopy(base)
    rig["use"].append({"module": "inmp441_breakout", "wires": {}})
    rig["use"][-1].update(su.autowire(rig, len(rig["use"]) - 1))
    (p,) = [p for p in studio.evaluate(rig)["problems"] if "audio_in" in p["message"]]
    new = len(rig["use"]) - 1
    assert p["uses"] == [mic3, new]
    assert [(f["op"], f["use"]) for f in p["resolve"]] == [("remove", mic3), ("remove", new)]
    assert p["resolve"][0]["label"] == "Use inmp441_breakout for audio_in (remove digilent_pmod_mic3)"
    for f in p["resolve"]:
        ev = studio.evaluate(_apply(rig, f))
        assert not [q for q in ev["problems"] if "audio_in" in q["message"]] and ev["parts"] == []
    # a module wired onto pins another module uses: re-wire it or remove one
    rig = copy.deepcopy(base)
    rig["use"].append({"module": "inmp441_breakout", "wires": {}})
    rig["use"][-1]["wires"] = {"SD": "jd.9", "WS": "jd.10", "SCK": "jd.7", "L/R": "jd.8"}
    (p,) = [p for p in studio.evaluate(rig)["problems"] if "used by both" in p["message"]]
    assert p["uses"] == [mic3, len(rig["use"]) - 1]
    ops = [(f["op"], f["use"]) for f in p["resolve"]]
    assert ("autowire", len(rig["use"]) - 1) in ops and ("remove", mic3) in ops
    fixed = _apply(rig, next(f for f in p["resolve"] if f["op"] == "autowire" and f["use"] == len(rig["use"]) - 1))
    assert not [q for q in studio.evaluate(fixed)["problems"] if "used by both" in q["message"]]


# ---------------------------------------------------------------- what the page draws by

STUDIO_JS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools", "studio", "studio.js")


def test_a_pin_on_two_headers_is_drawn_on_the_header_of_its_own_bank():
    """The Tang Mega 138K's J14 (labelled SDRAM1) carries the Pmods' nets, so
    its drawn pins reference pmod_0[…] / pmod_1[…]: a wire to a Pmod pin must be
    drawn at the Pmod, never at J14. The server's ref_index says so, the page
    takes it from board_data and derives no index of its own."""
    board = su.read_drawn("tang_mega_138k")
    j14 = next(c for c in board["headers"] if c["id"] == "j14")
    assert j14["pins"]["2"] == "pmod_0[0]" and "SDRAM" in j14["label"]      # the trap this guards against
    ri = su.ref_index(board)
    assert ri["pmod_0[0]"] == "pmod_0.11" and ri["pmod_1[0]"] == "pmod_1.11" and ri["header_j14[0]"] == "j14.1"
    assert studio.board_data("tang_mega_138k")["ref_index"] == ri
    with open(STUDIO_JS) as f:
        js = f.read()
    assert "S.board.ref_index" in js
    assert "for (const c of S.board.connectors) for (const [k, p] of Object.entries(c.pins)) idx[p.ref]" not in js


def test_every_drawn_board_indexes_each_reference_on_a_header_of_its_bank():
    """Over every drawn board: every indexed connector.key exists and holds that
    reference, and a reference some header of its own bank lists is indexed on
    such a header, whatever other headers carry the same net."""
    for board_id, board in sorted(su.drawn_boards().items()):
        ri = su.ref_index(board)
        headers_of = {}
        for c in board["headers"]:
            if c.get("bank"):
                headers_of.setdefault(c["bank"], set()).add(c["id"])
        keys = {"{}.{}".format(c["id"], k): ref for c in board["headers"] for k, ref in (c.get("pins") or {}).items()}
        for ref, where in ri.items():
            assert keys[where] == ref, (board_id, ref, where)
            bank = re.split(r"[.\[]", str(ref))[0]
            if bank in headers_of:
                assert where.split(".")[0] in headers_of[bank], (board_id, ref, where)


def test_every_rig_draws_its_pins_where_its_wires_and_parts_say():
    """For every rig, every traced pin has one place on the drawing (what the
    page's pinAt finds): a reference the rig wires is placed where the wire
    says, one place per reference, and every design-bit edge reaches a header
    pin of the reference's own bank or a pin of an on-board part the rig uses
    (as the rig binds it) — never another header that happens to carry the same
    net, never nowhere."""
    for sid in _setups():
        setup = su.read_setup(sid)
        bd = studio.board_data(setup["board"])
        conns = {c["id"]: c for c in bd["connectors"]}
        # the page's index: the board's, then wherever this rig's wires land a ref (a
        # module wired to J6 on a Tang Nano 20K: its trace lines end at J6, not at the
        # gpio row of the same net); one place per ref
        ri, wired = dict(bd["ref_index"]), {}
        for use in setup.get("use") or []:
            for pin, where in (use.get("wires") or {}).items():
                cid, key = where.split(".")
                ref = conns[cid]["pins"][key]["ref"]
                assert wired.get(ref, where) == where, (sid, use.get("module"), pin, ref, wired.get(ref), where)
                wired[ref] = where
        ri.update(wired)
        traced = trace.trace(config_init.resolve_configuration(sid))
        # the dots the page draws on a used part: its attach's pins as the rig binds
        # them (a use's `bind:` override included), else the board's variant's
        attaches = {a["attach_index"]: a for a in traced["attaches"]}
        parts = {o["id"]: o for o in bd["onboard"]}
        part_refs = set()
        for i, u in enumerate(setup.get("use") or []):
            if not u.get("onboard"):
                continue
            a, o = attaches.get(i), parts[u["onboard"]]
            pins = a["pins"] if a and a.get("pins") else next((v["pins"] for v in o["variants"] if v["id"] == u.get("variant")),
                                                               (o["variants"] or [{"pins": o["pins"]}])[0]["pins"])
            part_refs |= {p["ref"] for ps in pins.values() for p in ps}
        headers_of = {}
        for c in bd["connectors"]:
            if c.get("bank"):
                headers_of.setdefault(c["bank"], set()).add(c["id"])
        for ed in traced["edges"]:
            ref = ed.get("ref")
            if not ref:
                continue
            if ref in wired:
                continue                                        # where the wire says, whatever the header's bank
            if ref in ri:
                bank = re.split(r"[.\[]", str(ref))[0]
                if bank in headers_of:
                    assert ri[ref].split(".")[0] in headers_of[bank], (sid, ref, ri[ref])
            else:
                assert ref in part_refs, (sid, ed.get("design_port"), ref)


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_page_script_parses():
    subprocess.run(["node", "--check", STUDIO_JS], check=True)


def test_design_table_covers_every_design_and_configuration():
    t = studio.design_table()
    assert len(t["designs"]) == len(studio.list_designs())
    assert [c["id"] for c in t["configurations"]] == sorted(config_init.read_configurations())
    # every configuration now has a rig drawing (a generated layout where nobody drew one)
    assert all(c["layout"] and c["setup"] for c in t["configurations"])
    k = [c["id"] for c in t["configurations"]].index("arty_a7_pmod_mic3")
    aps = next(d for d in t["designs"] if d["id"] == "5_5_aps")
    assert aps["requires"] == ["seven_segment >= 1", "screen >= 320x240", "where clk_mhz % 50 == 0"]
    assert k in aps["fits"]                          # its 100 MHz clock divides into 10 and 25
    fifo = next(d for d in t["designs"] if d["id"] == "4_2_12_multi_push_multi_pop_fifo")
    assert k not in fifo["fits"] and "w_led=8" in fifo["unmet"][str(k)][0]
    for d in t["designs"]:          # every configuration is either a fit or has its reasons
        assert set(d["fits"]).isdisjoint(int(x) for x in d["unmet"]) and \
            len(d["fits"]) + len(d["unmet"]) == len(t["configurations"])
    assert studio.design_table() is t                     # cached until a file changes
    p = studio.table_progress()                           # what the page's progress bar reads
    assert p["ready"] and p["done"] == p["total"] == len(t["designs"]) and p["stage"] is None


def test_the_pinned_design_saves_only_over_what_the_page_loaded(tmp_path, monkeypatch):
    d = tmp_path / "designs" / "mine"
    d.mkdir(parents=True)
    f = d / "design_top.sv"
    f.write_text("module design_top (input clk);\nendmodule\n")
    monkeypatch.setattr(studio, "DESIGNS_DIR", str(tmp_path / "designs"))
    loaded = f.read_text()
    r = studio.save_design("mine", loaded + "// more\n", loaded)
    assert f.read_text() == loaded + "// more\n" and r["text"].endswith("// more\n")
    with pytest.raises(studio.ApiError, match="changed on disk"):
        studio.save_design("mine", "module design_top (); endmodule\n", loaded)
    assert f.read_text() == loaded + "// more\n"
    with pytest.raises(studio.ApiError, match="unknown design"):
        studio.save_design("../../etc", "x", "x")
    assert not list(d.glob("*.saving"))


def test_a_board_offers_only_the_toolchains_its_chip_supports():
    assert studio.board_toolchains("de10_lite") == ["quartus_prime_lite"]
    assert set(studio.board_toolchains("arty_a7")) == {"vivado", "nextpnr_openxc7"}
    rig = copy.deepcopy(su.read_setup("arty_a7"))
    rig["toolchain"] = "gowin_eda"
    assert any("does not build for arty_a7" in m for lvl, m in su.validate(rig) if lvl == "error")
    for s in su.read_setups().values():
        assert s["toolchain"] in studio.board_toolchains(s["board"]), s["id"]
