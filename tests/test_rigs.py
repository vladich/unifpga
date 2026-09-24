"""One rig per setup: toolchains, chips and aliases (config/init.py build
targets) and the for_toolchain patches (config/overlay.py)."""
import copy
import json
import os
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from config import init as config_init, overlay, profile  # noqa: E402
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
    assert old["configuration"]["lab_width"].get("switches") == 5          # its profile's patch
    assert "switches" not in config_init.resolve_configuration(
        "tang_primer_20k_dock_hdmi_no_tm1638")["configuration"]["lab_width"]


def _body(cfg):
    return json.dumps({k: v for k, v in cfg.items() if k not in ("id", "aliases", "toolchains", "parts")},
                      sort_keys=True)


def test_no_two_rigs_are_the_same_hardware():
    """A rig exists once: not per toolchain, not per chip, not twice."""
    seen = {}
    rigs = config_init.read_configurations()
    for t in config_init.build_targets(rigs):
        key = (_body(config_init.for_target(rigs[t["rig"]], t["toolchain"], t["part"])),
               json.dumps(profile.for_toolchain(profile.load(t["rig"]) or {}, t["toolchain"]), sort_keys=True))
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
