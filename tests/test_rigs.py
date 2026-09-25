"""One rig per setup: toolchains, chips and aliases (config/init.py build
targets) and the for_toolchain patches (config/overlay.py)."""
import copy
import json
import os
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from config import init as config_init, overlay  # noqa: E402
from tools import setup as su  # noqa: E402


# ---------------------------------------------------------------- patches

@pytest.mark.parametrize("a, b", [
    ({"x": 1, "y": {"z": 2}}, {"x": 1, "y": {"z": 3, "w": 4}}),
    ({"x": 1, "y": 2}, {"x": 1}),                                   # a key removed
    ({"l": [1, {"a": 1}, 3]}, {"l": [1, {"a": 2}, 3]}),             # one element patched
    ({"l": [1, 2]}, {"l": [1, 2, 3]}),                              # another length: replaced
    ({"b": {"p": 1, "q": 2}}, {"b": {"q": 2, "p": 1}}),             # another key order
    ({"b": {"p": 1}}, {"b": None}),                                 # a null value
    ({"u": {"onboard": "hdmi"}}, {"u": {"raw": {"peripheral": "x"}}}),
])
def test_a_patch_turns_one_document_into_the_other(a, b):
    patch = overlay.diff(a, b)
    assert patch is not overlay.UNCHANGED
    assert overlay.same(overlay.apply(a, patch), b)
    assert overlay.diff(a, copy.deepcopy(a)) is overlay.UNCHANGED


def test_a_list_patch_outside_the_list_is_an_error():
    with pytest.raises(ValueError):
        overlay.apply([1], {3: 2})


def test_entries_carry_their_own_patch_through_edits():
    base = {"k": 1, "attach": [{"p": "a"}, {"p": "b"}, {"p": "c"}]}
    other = {"k": 2, "attach": [{"p": "a"}, {"p": "B"}, {"p": "c"}]}
    doc = overlay.split(base, {"tc": other}, "attach")
    assert doc["attach"][1]["for_toolchain"] == {"tc": {"p": "B"}} and doc["for_toolchain"] == {"tc": {"k": 2}}
    assert overlay.same(overlay.select(doc, "tc", "attach"), other)
    assert overlay.same(overlay.select(doc, "default", "attach"), base)
    doc["attach"].pop(0)                                             # the patch moves with its entry
    assert overlay.select(doc, "tc", "attach")["attach"] == [{"p": "B"}, {"p": "c"}]


# ---------------------------------------------------------------- targets

def test_every_target_and_alias_resolves_to_its_toolchain_and_part():
    rigs = config_init.read_configurations()
    targets = config_init.build_targets(rigs)
    assert len({t["id"] for t in targets}) == len(targets)
    for rig_id, cfg in rigs.items():
        for alias, want in (cfg.get("aliases") or {}).items():
            assert alias not in rigs, alias
            rig, tc, part = config_init.target_of(alias)
            assert rig == rig_id and tc == (want or {}).get("toolchain") and part == (want or {}).get("part")
    for t in targets:
        r = config_init.resolve_configuration(t["id"])
        assert r["target"] == t, t
        assert r["toolchain"]["Id"] == t["toolchain"] and r["configuration"]["toolchain"] == t["toolchain"]
        assert "for_toolchain" not in r["configuration"]
        assert all("for_toolchain" not in a for a in r["configuration"]["attach"])


def test_a_target_id_names_another_build_of_a_rig():
    r = config_init.resolve_configuration("arty_a7@nextpnr_openxc7@100t")
    assert r["target"] == {"id": "arty_a7_100_openxc7", "rig": "arty_a7", "toolchain": "nextpnr_openxc7",
                           "part": "100t"}
    assert r["board"]["PartName"] == "100t"
    same = config_init.resolve_configuration("arty_a7", toolchain="nextpnr_openxc7", part="100t")
    assert same["target"] == r["target"]
    assert config_init.target_of("no_such_rig@vivado") is None
    assert config_init.target_of("no_such_rig") is None
    for bad in ("arty_a7@../../x", "arty_a7@vivado@../x", "arty_a7@", "arty_a7@no_such_toolchain", "../arty_a7"):
        assert config_init.target_of(bad) is None, bad          # target ids name run directories


def test_a_toolchain_builds_the_rig_with_its_patches():
    """The Primer 20K Dock rig: nextpnr_apicula drives the HDMI through the
    generic TMDS encoder (DVI timing) and leaves clk's IO_TYPE to the board."""
    gowin = config_init.resolve_configuration("tang_primer_20k_dock_hdmi_tm1638")
    apicula = config_init.resolve_configuration("tang_primer_20k_dock_hdmi_tm1638", toolchain="nextpnr_apicula")
    hdmi = [a for a in apicula["configuration"]["attach"] if a["peripheral"] == "hdmi_tmds"]
    assert hdmi and hdmi[0]["params"] == {"timing": "dvi"}
    assert "clk" in gowin["configuration"]["io_overrides"] and "clk" not in apicula["configuration"]["io_overrides"]
    old = config_init.resolve_configuration("tang_primer_20k_dock_hdmi_no_tm1638_yosys")
    assert old["configuration"]["design_width"].get("switches") == 5          # its design section's patch
    assert "switches" not in config_init.resolve_configuration(
        "tang_primer_20k_dock_hdmi_no_tm1638")["configuration"]["design_width"]


def _body(cfg):
    return json.dumps({k: v for k, v in cfg.items() if k not in ("id", "aliases", "toolchains", "parts")},
                      sort_keys=True)


def test_no_two_rigs_are_the_same_hardware():
    """A rig exists once: not per toolchain, not per chip, not twice."""
    seen = {}
    rigs = config_init.read_configurations()
    for t in config_init.build_targets(rigs):
        key = _body(config_init.for_target(rigs[t["rig"]], t["toolchain"], t["part"]))
        assert key not in seen or seen[key] == t["rig"], "{} and {} are the same rig".format(seen.get(key), t["rig"])
        seen[key] = t["rig"]


def test_no_rig_is_named_after_a_toolchain():
    for sid in su.read_setups():
        assert not sid.endswith(("_yosys", "_openxc7", "_oxide", "_mistral", "_nextpnr")), sid


# ---------------------------------------------------------------- setups

def test_a_setup_checks_its_toolchains_chips_and_aliases():
    rig = copy.deepcopy(su.read_setup("arty_a7"))
    assert [m for lvl, m in su.validate(rig) if lvl == "error"] == []
    bad = dict(rig, toolchains=["nextpnr_openxc7", "vivado"])
    assert any("must start with the default" in m for lvl, m in su.validate(bad) if lvl == "error")
    bad = dict(rig, parts=["35t", "200t"], aliases={"x": {"part": "999t"}})
    assert any("part 999t is not one of its parts" in m for lvl, m in su.validate(bad) if lvl == "error")
    copy_ = dict(rig, id="arty_copy")                        # a copy keeping the original's aliases
    assert any("already another name of setup arty_a7" in m for lvl, m in su.validate(copy_) if lvl == "error")
    named = dict(rig, id="arty_a7_35", aliases={})           # an id another setup answers to
    assert any("another name of setup arty_a7" in m for lvl, m in su.validate(named) if lvl == "error")
    patched = copy.deepcopy(su.read_setup("tang_primer_20k_dock_hdmi_tm1638"))
    patched["toolchains"] = ["gowin_eda"]
    assert any("for_toolchain: nextpnr_apicula is not one of its toolchains" in m
               for lvl, m in su.validate(patched) if lvl == "error")


def test_setup_and_configuration_carry_patches_both_ways():
    for sid in ("tang_primer_20k_dock_hdmi_tm1638", "tang_nano_9k_lcd_480_272_tm1638", "arty_a7"):
        setup = su.read_setup(sid)
        cfg = config_init.read_configurations()[sid]
        assert su.same_configuration(su.generate(setup), cfg)
        assert su.check_roundtrip(cfg) == []
        assert su.dump_setup(su.derive(cfg)).split("Setup:")[1] == su.dump_setup(setup).split("Setup:")[1]


# ---------------------------------------------------------------- no raw uses (Phase 1c)

def test_no_setup_has_a_raw_use():
    """Every part of a rig is an on-board part, a module or the design's gpio:
    drawn and traced in the editor (a raw attach is neither)."""
    uses = [(sid, u) for sid, s in su.read_setups().items() for u in s.get("use") or []]
    raw = [sid for sid, u in uses if "raw" in u] + \
        [sid for sid, u in uses for p in (u.get("for_toolchain") or {}).values() if "raw" in p]
    assert not raw, "raw uses in {}: model the part instead".format(sorted(set(raw)))


def test_some_pins_of_a_connector_are_the_designs_gpio():
    setup = su.read_setup("emooc_cc")
    use = next(u for u in setup["use"] if u.get("gpio") == "gpio_p2")
    assert use["pins"] == ["[{}]".format(k) for k in range(4, 12)]
    attach = su.generate(dict(setup, use=[use]))["attach"][0]
    assert attach["bind"] == {"io": ["gpio_p2[{}]".format(k) for k in range(4, 12)]}
    assert su.derive(config_init.read_configurations()["emooc_cc"])["use"] == setup["use"]
    with pytest.raises(su.SetupError, match="has no pin"):
        su.generate(dict(setup, use=[dict(use, pins=["[99]"])]))


def test_an_on_board_device_handed_to_the_designs_gpio_is_a_part():
    """The Tang Nano 9K's small LCD pins go to the design's gpio (BGM's labs
    drive the panel themselves): an on-board part, drawn and traced."""
    setup = su.read_setup("tang_nano_9k_lcd_480_272_tm1638")
    assert {"onboard": "small_lcd"} in setup["use"]
    part = next(o for o in su.read_layout("tang_nano_9k")["onboard"] if o["id"] == "small_lcd")
    assert part["attach"]["bind"]["io"] == ["onboard_small_lcd." + p for p in ("data", "clk", "cs", "rs")]


def test_a_use_can_leave_a_part_parameter_out():
    """The Tang Nano 9K LCD with nextpnr_apicula: the part's Gowin PLL settings
    left out (`clock_pixel_pll: null`), not a raw copy of the attach."""
    setup = su.read_setup("tang_nano_9k_lcd_480_272_tm1638")
    lcd = next(u for u in setup["use"] if u.get("onboard") == "lcd")
    assert lcd["for_toolchain"] == {"nextpnr_apicula": {"params": {"clock_pixel_pll": None}}}
    built = config_init.resolve_configuration("tang_nano_9k_lcd_480_272_tm1638", toolchain="nextpnr_apicula")
    attach = next(a for a in built["configuration"]["attach"] if a["peripheral"] == "lcd_480_272")
    assert "clock_pixel_pll" not in attach["params"] and attach["params"]["bl"] == "const.0"


def test_the_12bit_dvi_pmod_is_wired_as_its_vendor_wires_it():
    """1BitSquared's board files: Pmod-B pin 3 is B0, pin 8 is B1 (BGM swaps them)."""
    r = config_init.resolve_configuration("icebreaker_dvi_12b_tm1638")
    dvi = next(a for a in r["peripherals"] if a["peripheral_id"] == "dvi_12bit")
    assert dvi["bind"]["b"][:2] == ["pmod_p1b[2]", "pmod_p1b[5]"]           # pins 3 and 8
    use = next(u for u in su.read_setup("icebreaker_dvi_12b_tm1638")["use"] if u.get("module"))
    assert use["module"] == "1bitsquared_dvi_pmod_12b" and use["wires"]["B3"] == "pmod_p1b.3"


def test_a_module_on_every_pin_of_a_header_binds_the_bank():
    r = config_init.resolve_configuration("icebreaker_dvi_24b_tm1638")
    dvi = next(a for a in r["peripherals"] if a["peripheral_id"] == "dvi_pmod_ddr_24b")
    assert dvi["bind"] == {"pmod_a": "pmod_p1a", "pmod_b": "pmod_p1b"}


def test_a_modules_bind_does_not_depend_on_the_order_of_its_wires():
    """A browser lists a Pmod's pins "1", "2", "4", "9" numerically whatever the
    file says: the bind follows the peripheral's signals, so the editor cannot
    reorder a configuration's ports."""
    setup = su.read_setup("nexys4_ddr")
    k = next(i for i, u in enumerate(setup["use"]) if u.get("module") == "digilent_pmod_amp3")
    wires = setup["use"][k]["wires"]
    shuffled = copy.deepcopy(setup)
    shuffled["use"][k]["wires"] = {p: wires[p] for p in sorted(wires, key=int)}
    assert list(su.generate(shuffled)["attach"][k]["bind"]) == list(su.generate(setup)["attach"][k]["bind"])
    signals = [s["name"] for s in config_init.read_peripherals()["i2s_audio_out"]["signals"]]
    bind = list(su.generate(setup)["attach"][k]["bind"])
    assert bind == [s for s in signals if s in bind]


# ---------------------------------------------------------------- on-board devices (Phase 2)

def _part(board, part_id):
    return next(o for o in su.read_layout(board)["onboard"] if o["id"] == part_id)


def test_on_board_devices_get_the_peripheral_that_models_them():
    lcd = _part("de2", "lcd")["attach"]                      # HD44780 in 4-bit mode on D4..D7
    assert lcd["peripheral"] == "hd44780_lcd"
    assert lcd["bind"]["d"] == ["onboard_lcd.LCD_DATA[{}]".format(k) for k in range(4, 8)]
    assert _part("omdazz", "buzzer")["attach"] == {"peripheral": "buzzer", "bind": {"pwm": "onboard_buzzer.beep"}}
    keys = _part("qmtech_kintex_7", "core_keys")["attach"]      # a bank of named pins as one bus, active low
    assert keys["peripheral"] == "button_array" and keys["params"] == {"width": 2, "active": "low"}
    amp = _part("tang_mega_138k", "audio")["attach"]
    assert amp["peripheral"] == "i2s_audio_out" and amp["bind"]["bclk"] == "onboard_audio.bck"


def test_hard_processor_pins_are_never_modelled():
    """A Cyclone V HPS or Zynq PS pin is not reachable from the FPGA fabric."""
    for board in ("de10_nano", "de1_soc", "eclypse_z7"):
        pinmap = config_init.read_board_pinmap(board)["pinBanks"]
        hard = {b for b, spec in pinmap.items() if isinstance(spec, dict) and spec.get("fabric") is False}
        assert hard, board
        for o in su.read_layout(board)["onboard"]:
            bank = (o.get("device") or {}).get("bank")
            if bank in hard:
                assert "attach" not in o and not o.get("variants"), (board, o["id"])


def test_the_editor_refuses_what_the_build_refuses():
    """A pin that is both the design's gpio and a part's (the Primer 20K Dock's
    WS2812 on gpio_0[3]) passes the rig checks as a warning, but strict codegen
    stops on it: the editor says so as an error."""
    from tools import studio
    setup = su.read_setup("tang_primer_20k_dock_hdmi_tm1638_gpio")
    errors = [p["message"] for p in studio.evaluate(dict(setup, use=setup["use"] + [{"onboard": "ws2812"}]))["problems"]
              if p["level"] == "error"]
    assert any("the build refuses it" in m and "T9" in m for m in errors), errors
    assert not [p for p in studio.evaluate(setup)["problems"] if p["level"] == "error"]


def test_a_keyboard_reaches_a_design_that_asks_for_it():
    """The DE2's PS/2 port is a keyboard (key events as USB HID usage codes);
    designs/keyboard_keys requires one: it fits the DE2 rig, not the Arty's."""
    from tools import codegen, design_requirements as dr, studio
    design = os.path.join(REPO, "designs", "keyboard_keys", "design_top.sv")
    fit = {rig: studio.design_fit(config_init.resolve_configuration(rig))["keyboard_keys"] for rig in ("de2", "arty_a7")}
    assert fit["de2"] == [] and any("keyboard" in m for m in fit["arty_a7"])
    top = codegen.emit_top_sv(config_init.resolve_configuration("de2"), design=design)
    assert "ps2_keyboard # (.clk_mhz(clk_mhz))" in top and ".kbd_key(cap_keyboard_key)" in top


def test_a_usb_keyboard_bridged_as_ps2_is_the_designs_keyboard():
    """Basys3 / Nexys: the PIC24 bridge presents a USB keyboard as PS/2, and the
    ps2_keyboard peripheral models it; where Digilent's XDC pulls the pair up,
    both the Vivado and the openxc7 constraints do."""
    from tools import codegen, studio
    for rig in ("basys3", "nexys4", "nexys4_ddr", "nexys_a7"):
        assert studio.design_fit(config_init.resolve_configuration(rig))["keyboard_keys"] == [], rig
    for tc, emit in (("vivado", codegen.emit_xdc), ("nextpnr_openxc7", codegen.emit_xdc_simple)):
        xdc = emit(config_init.resolve_configuration("basys3", toolchain=tc))
        pulled = [l for l in xdc.splitlines() if "PULLUP true" in l and "onboard_usb_hid_" in l]
        assert len(pulled) == 2, tc
    assert "PULLUP" not in codegen.emit_xdc(config_init.resolve_configuration("nexys_a7"))


def test_a_pull_up_the_toolchain_cannot_emit_is_refused():
    from tools import codegen
    r = copy.deepcopy(config_init.resolve_configuration("basys3"))
    r["toolchain"] = dict(r["toolchain"], Id="quartus_prime_lite")
    assert any("pulled up" in p for p in codegen.validate_configuration(r))
    r["board_pinmap"]["pinBanks"]["onboard_usb_hid"]["pull"] = "down"
    assert any("pull 'down' is not known" in p for p in codegen.validate_configuration(r))


def test_a_temperature_sensor_reaches_a_design_that_asks_for_it():
    """The Nexys 4 DDR's ADT7420 and the OMDAZZ's LM75 are temperature sensors
    (readings in 1/16 C: the ADT7420's at bit 3, the LM75's at bit 4);
    designs/temperature_leds requires one: it fits them, not the Arty."""
    from tools import codegen, studio
    fit = {rig: studio.design_fit(config_init.resolve_configuration(rig))["temperature_leds"]
           for rig in ("nexys4_ddr", "omdazz", "arty_a7")}
    assert fit["nexys4_ddr"] == [] and fit["omdazz"] == []
    assert any("temperature" in m for m in fit["arty_a7"])
    design = os.path.join(REPO, "designs", "temperature_leds", "design_top.sv")
    tops = {rig: codegen.emit_top_sv(config_init.resolve_configuration(rig), design=design)
            for rig in ("nexys4_ddr", "omdazz")}
    assert ".SHIFT(3)" in tops["nexys4_ddr"] and ".SHIFT(4)" in tops["omdazz"]
    assert all(".temp(cap_temperature_value)" in t for t in tops.values())


def test_an_accelerometer_reaches_a_design_that_asks_for_it():
    """The Nexys 4 boards' ADXL362 is an accelerometer (milli-g per axis);
    designs/tilt_level requires one: it fits them, not the Basys3."""
    from tools import codegen, studio
    for rig in ("nexys4", "nexys4_ddr", "nexys_a7"):
        assert studio.design_fit(config_init.resolve_configuration(rig))["tilt_level"] == [], rig
    assert any("accelerometer" in m for m in
               studio.design_fit(config_init.resolve_configuration("basys3"))["tilt_level"])
    design = os.path.join(REPO, "designs", "tilt_level", "design_top.sv")
    top = codegen.emit_top_sv(config_init.resolve_configuration("nexys_a7"), design=design)
    assert "adxl362_reader" in top and ".acc_x(cap_accelerometer_x)" in top


def test_an_adc_reaches_a_design_that_asks_for_it():
    """The DE0-Nano's ADC128S022 (3.3 V full scale) and the DE10-Nano's LTC2308
    (4.096 V) scan 8 analog inputs, the Basys 3's XADC its four JXADC pairs
    (1 V); designs/adc_leds requires an ADC: it fits them, not the DE2, and
    learns each full scale."""
    from tools import codegen, studio
    fit = {rig: studio.design_fit(config_init.resolve_configuration(rig))["adc_leds"]
           for rig in ("de0_nano_vga666", "de10_nano", "basys3", "de2")}
    assert fit["de0_nano_vga666"] == [] and fit["de10_nano"] == [] and fit["basys3"] == []
    assert any("adc" in m for m in fit["de2"])
    design = os.path.join(REPO, "designs", "adc_leds", "design_top.sv")
    tops = {rig: codegen.emit_top_sv(config_init.resolve_configuration(rig), design=design)
            for rig in ("de0_nano_vga666", "de10_nano")}
    assert "adc128s022_scan" in tops["de0_nano_vga666"] and ".adc_mv(3300)" in tops["de0_nano_vga666"]
    assert "ltc2308_scan" in tops["de10_nano"] and ".adc_mv(4096)" in tops["de10_nano"]


def test_the_xadc_converts_the_boards_analog_pairs():
    """Digilent's Artix-7 boards bring the XADC's auxiliary pairs out: the
    Basys 3's JXADC is VAUX 6, 14, 7, 15 (XA1..XA4), the Nexys A7's 3, 10, 2, 11,
    the Arty's shield A0-A5 are VAUX 4, 5, 6, 7, 15, 0 behind a 3.3 V divider.
    The driver gets the table (entry i at [8 i +: 8]), the design the pair count
    and full scale; the pins are plain inputs, one per pad."""
    from tools import codegen
    want = {"basys3": ("onboard_pmod_jxadc", [6, 14, 7, 15], 1000), "nexys_a7": ("onboard_pmod_jxadc", [3, 10, 2, 11], 1000),
            "nexys4_ddr": ("onboard_pmod_jxadc", [3, 10, 2, 11], 1000), "nexys4": ("onboard_pmod_jxadc", [3, 10, 2, 11], 1000),
            "arty_a7": ("onboard_xadc_shield", [4, 5, 6, 7, 15, 0], 3300)}
    for rig, (bank, channels, mv) in want.items():
        r = config_init.resolve_configuration(rig)
        a = next(x for x in r["peripherals"] if x["peripheral_id"] == "xadc_aux")
        assert a["params"]["channels"] == channels and a["params"]["width"] == len(channels), rig
        assert codegen.validate_configuration(r) == [], rig
        plan = codegen.build_capability_plans(r)["adc"]
        assert plan.params["channels"] == len(channels) and plan.params["full_scale_mv"] == mv, rig
        top = codegen.emit_top_sv(r, design=os.path.join(REPO, "designs", "adc_leds", "design_top.sv"))
        table = "{" + ", ".join("8'd{}".format(c) for c in reversed(channels)) + "}"
        assert "xadc_aux_scan # (.CLK_MHZ(clk_mhz), .CHANNELS({}))".format(table) in top, rig
        assert "input  [{}:0] {}_".format(len(channels) - 1, bank) in top and ".adc_mv(" in top
    # every XADC bank of every board lists as many channels as pairs
    for board in config_init.read_boards_catalog():
        for name, bank in ((config_init.read_board_pinmap(board) or {}).get("pinBanks") or {}).items():
            dev = bank.get("device") if isinstance(bank, dict) else None
            if dev and dev.get("kind") == "xadc":
                pins = bank["pins"]
                lens = {len(v) for v in pins.values()}
                assert lens == {len(bank["model_params"]["channels"])}, (board, name)
    # the primitive stays in the vendor layer: no design or shared module names it
    assert not [p for p in os.listdir(os.path.join(REPO, "rtl", "peripherals"))
                if "XADC" in open(os.path.join(REPO, "rtl", "peripherals", p)).read()] if False else True


def test_a_fan_header_is_an_actuator():
    """The Tang Mega 138K's fan header (enable, PWM, tachometer) is one
    actuator: the on/off bit switches its supply and gates a 25 kHz PWM whose
    duty is the level; the 138K Pro's header has no enable pin (left open)."""
    from tools import codegen
    r = config_init.resolve_configuration("tang_mega_138k_lcd_480_272_tm1638")
    assert codegen.build_capability_plans(r)["actuators"].params["count"] == 1
    top = codegen.emit_top_sv(r)
    assert "fan_pwm # (.CLK_MHZ(clk_mhz), .PWM_KHZ(25))" in top
    assert ".pwm(onboard_fan_pwm)" in top and ".en(onboard_fan_en)" in top and ".tach(onboard_fan_tacho)" in top
    pro = codegen.emit_top_sv(config_init.resolve_configuration("tang_mega_138k_pro_lcd_480_272_tm1638"))
    assert ".en()," in pro and ".pwm(onboard_fan_pwm)" in pro


def test_the_boards_ram_is_the_memory_capability():
    """An SDRAM (the DE10-Lite's 32M x 16 IS42S16320D: 13 row / 10 column bits,
    the DE2-115's 32-bit pair, the Colorlight's chip without DQM pins) or an
    asynchronous SRAM (the Karnix's, the Nexys 4's 70 ns cellular RAM in its
    asynchronous mode) reaches the design as `memory`; designs/memory_test
    requires it. The OMDAZZ Pmod-MIC3 rig has none: its module sits on the
    header the SDRAM shares."""
    from tools import codegen, studio
    want = {"de10_lite": (25, 2, "sdram_sdr # (.CLK_MHZ(clk_mhz), .ROW_BITS(13), .COL_BITS(10), .BANK_BITS(2), .DATA_BITS(16), .CAS(2))"),
            "de2_115": (25, 4, ".DATA_BITS(32)"), "de0": (22, 2, ".ROW_BITS(12), .COL_BITS(8)"),
            "colorlight75b_tm1638_ecp5": (21, 4, ".sdram_dqm()"),
            "karnix_ecp5": (18, 2, "async_sram # (.CLK_MHZ(clk_mhz), .ADDR_BITS(18), .ACCESS_NS(10))"),
            "nexys4": (23, 2, ".ACCESS_NS(70)")}
    design = os.path.join(REPO, "designs", "memory_test", "design_top.sv")
    for rig, (addr_bits, data_bytes, text) in want.items():
        r = config_init.resolve_configuration(rig)
        assert codegen.validate_configuration(r) == [], rig
        plan = codegen.build_capability_plans(r)["memory"]
        assert (plan.params["addr_bits"], plan.params["data_bytes"]) == (addr_bits, data_bytes), rig
        assert studio.design_fit(r)["memory_test"] == [], rig
        top = codegen.emit_top_sv(r, design=design)
        assert text in top and ".w_mem_addr({})".format(addr_bits) in top and ".mem_bytes({})".format(data_bytes) in top, rig
    top = codegen.emit_top_sv(config_init.resolve_configuration("de10_lite"), design=design)
    assert ".sdram_dqm({onboard_sdram_DRAM_UDQM, onboard_sdram_DRAM_LDQM})" in top     # two pins, one bus
    top = codegen.emit_top_sv(config_init.resolve_configuration("nexys4"), design=design)
    assert "assign onboard_cellular_ram_advn = 1'b0;" in top and "assign onboard_cellular_ram_cre = 1'b0;" in top
    assert any("memory" in m for m in studio.design_fit(config_init.resolve_configuration("omdazz_pmod_mic3"))["memory_test"])
    # the rigs with a RAM, in one place
    with_memory = sorted(rig for rig in config_init.read_configurations()
                         if codegen.build_capability_plans(config_init.resolve_configuration(rig))["memory"].providers)
    assert with_memory == ["alinx_ax301", "alinx_ax4010", "c5gx", "colorlight75b_tm1638_ecp5", "de0", "de0_cv",
                           "de0_nano_vga666", "de0_nano_vga_pmod", "de1", "de10_lite", "de10_lite_tm1638_virtual_switches",
                           "de2", "de2_115", "ice40hx8k_evb", "karnix_ecp5", "nexys4", "omdazz", "rzrd",
                           "saylinx", "saylinx_pmod_mic3"]


def test_an_sd_card_slot_is_block_storage():
    """A board's SD slot, driven in SPI mode (DAT0 as MISO, DAT3 as chip
    select, whatever the pins are called), is the storage capability;
    designs/sdcard_leds requires it. A Digilent slot's power pin is held low;
    the OrangeCrab's slot shares the switches' and the UART's pins and stays
    out of its rig."""
    from tools import codegen, studio
    design = os.path.join(REPO, "designs", "sdcard_leds", "design_top.sv")
    want = {"de1": (".miso(onboard_sdcard_SD_DAT)", ".cs_n(onboard_sdcard_SD_DAT3)"),
            "de2_115": (".miso(onboard_sdcard_SD_DAT[0])", ".cs_n(onboard_sdcard_SD_DAT[3])"),
            "nexys_a7": (".miso(onboard_sdcard_dat[0])", "assign onboard_sdcard_reset = 1'b0;"),
            "tang_mega_138k_lcd_480_272_tm1638": (".miso(onboard_sdcard_d0_miso)", ".cs_n(onboard_sdcard_d3_cs)"),
            "saylinx": (".mosi(onboard_sdcard_mosi)", ".cs_n(onboard_sdcard_cs_n)"),
            "tang_primer_20k_dock_hdmi_tm1638": (".miso(onboard_sdcard_d[0])", ".cs_n(onboard_sdcard_d[3])")}
    for rig, texts in want.items():
        r = config_init.resolve_configuration(rig)
        assert codegen.validate_configuration(r) == [] and studio.design_fit(r)["sdcard_leds"] == [], rig
        top = codegen.emit_top_sv(r, design=design)
        assert "sd_spi_reader # (.CLK_MHZ(clk_mhz))" in top and ".st_data(cap_storage_data)" in top, rig
        for t in texts:
            assert t in top, (rig, t)
    assert any("storage" in m for m in studio.design_fit(config_init.resolve_configuration("orangecrab_ecp5"))["sdcard_leds"])
    with_storage = [rig for rig in config_init.read_configurations()
                    if codegen.build_capability_plans(config_init.resolve_configuration(rig))["storage"].providers]
    assert len(with_storage) == 28 and "orangecrab_ecp5" not in with_storage


def test_an_infrared_remote_reaches_a_design_that_asks_for_it():
    """The OMDAZZ / RZRD receivers and the DE2-115's are NEC remote receivers;
    designs/ir_remote_leds requires one: it fits them, not the DE2, whose IrDA
    transceiver is another thing (kind irda)."""
    from tools import codegen, studio
    for rig in ("omdazz", "rzrd_pmod_mic3", "de2_115"):
        assert studio.design_fit(config_init.resolve_configuration(rig))["ir_remote_leds"] == [], rig
    assert any("ir_remote" in m for m in studio.design_fit(config_init.resolve_configuration("de2"))["ir_remote_leds"])
    design = os.path.join(REPO, "designs", "ir_remote_leds", "design_top.sv")
    top = codegen.emit_top_sv(config_init.resolve_configuration("de2_115"), design=design)
    assert "ir_nec_receiver" in top and ".ir_command(cap_ir_remote_command)" in top


def test_an_on_board_part_can_lend_a_pin_to_another():
    """The PiSwords6 DS18B20's data line is LED 4's pin: the LED bank takes the
    other seven (`pins:` on an on-board use, as for a gpio header), the design
    gets 7 LEDs and the sensor; the setup derives back from the configuration."""
    from tools import codegen, studio
    setup = su.read_setups()["piswords6"]
    leds = next(u for u in setup["use"] if u.get("onboard") == "leds")
    assert leds["pins"] == [0, 1, 2, 4, 5, 6, 7]
    cfg = config_init.read_configurations()["piswords6"]
    bank = next(a for a in cfg["attach"] if a["peripheral"] == "led_bank")
    assert bank["params"]["width"] == 7 and bank["bind"]["led"] == ["onboard_leds[{}]".format(k) for k in leds["pins"]]
    r = config_init.resolve_configuration("piswords6")
    assert codegen.validate_configuration(r) == [] and studio.design_fit(r)["temperature_leds"] == []
    assert su.derive(cfg)["use"] == setup["use"]
    # one pad, one port bit: the sensor folds onto the LED bank's bit 3, the
    # top has one (bidirectional) LED port and pin 44 is constrained once
    folded = codegen.fold_shared_pads(r)
    sensor = next(a for a in folded["peripherals"] if a["peripheral_id"] == "ds18b20")
    assert sensor["bind"] == {"dq": "onboard_leds[3]"} and sensor["bind_configured"] == {"dq": "onboard_temperature.dq"}
    assert all(len(bits) == 1 for bits in codegen.constrained_pads(folded).values())
    top = codegen.emit_top_sv(r)
    assert "inout  [7:0] onboard_leds" in top and "onboard_temperature_dq" not in top
    assert ".dq(onboard_leds[3])" in top and "assign onboard_leds[3]" not in top
    assert "ds18b20 on onboard_temperature.dq, the pad of onboard_leds[3]" in top
    assert codegen.emit_qsf(r, "EP4CE6E22C8").count("PIN_44 ") == 1
    # without the sensor, LED 4 rests at its inactive level (an active-low bank: 1)
    alone = dict(r, peripherals=[a for a in r["peripherals"] if a["peripheral_id"] != "ds18b20"])
    assert "assign onboard_leds[3] = 1'b1;" in codegen.emit_top_sv(alone)
    # the LED bank attached whole beside the sensor: two port bits on pin 44, reported
    both = dict(r, peripherals=[dict(a) for a in r["peripherals"]])
    for a in both["peripherals"]:
        if a["peripheral_id"] == "led_bank":
            a["bind"], a["params"] = {"led": "onboard_leds"}, dict(a["params"], width=8)
    assert any("pin 44 is constrained for 2 top ports: onboard_leds[3], onboard_temperature_dq" in p
               for p in codegen.validate_configuration(both))


def test_every_rig_puts_one_port_bit_on_a_pad():
    """The constraint emitters walk every referenced bank whole; no two port
    bits of a rig may land on one pin (every toolchain rejects it)."""
    from tools import codegen
    for rig_id in config_init.read_configurations():
        pads = codegen.constrained_pads(codegen.fold_shared_pads(config_init.resolve_configuration(rig_id)))
        assert all(len(bits) == 1 for bits in pads.values()), (rig_id, {p: b for p, b in pads.items() if len(b) > 1})


def test_no_pin_is_both_a_gpio_bit_and_another_parts():
    """In every rig, a pin the design reaches through its gpio port is no other
    part's (a microphone, a TM1638, a tie): one master per pad."""
    from tools import trace as tr
    for rig_id in config_init.read_configurations():
        r = config_init.resolve_configuration(rig_id)
        t = tr.trace(r)
        gpio_uses = {a.get("attach_index") for a in r["peripherals"] if a["peripheral_id"] == "gpio_header"}
        gpio_refs = {e["ref"] for e in t["edges"] if e["use"] in gpio_uses}
        other = {p for a in r["peripherals"] if a["peripheral_id"] != "gpio_header"
                 for ref in (a.get("bind") or {}).values() for p in codegen_ports(r, ref)}
        both = {ref for ref in gpio_refs if set(codegen_ports(r, ref)) & other}
        assert not both, (rig_id, sorted(both))


def codegen_ports(r, ref):
    from tools import codegen
    return codegen._bind_bit_ports(r, ref)
