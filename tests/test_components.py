"""
Lost / invented components: the i2s_audio_out, gpio_header, pin_tie, wm8731
and ADV7513 contracts through codegen, the `tie:` expansion, the broadcast
audio_out capability, BGM-faithful QSF settings and yosys synth options, and
the pure helpers of tools/sync_components.py.
"""

import logging
import os
import sys
from collections import OrderedDict

import pytest
import yaml

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
logging.disable(logging.CRITICAL)

from config import init as config_init     # noqa: E402
from tools import codegen                  # noqa: E402
from tools import sync_components as sc    # noqa: E402


def _perif(pid):
    with open(os.path.join(REPO, "config", "peripherals", pid + ".yml")) as f:
        return yaml.safe_load(f)["Peripheral"]


def _attach(pid, bind, params=None):
    return {"peripheral_id": pid, "peripheral": _perif(pid), "params": params or {}, "bind": bind}


# ---------------------------------------------------------------- codegen

def test_tie_values_and_expansion():
    assert config_init._tie_value("x", "a.b", 0) == "const.0"
    assert config_init._tie_value("x", "a.b", "1") == "const.1"
    assert config_init._tie_value("x", "a.b", "~rst") == "~context.rst"
    assert config_init._tie_value("x", "a.b", "rst_n") == "~context.rst"
    assert config_init._tie_value("x", "a.b", "rst") == "context.rst"
    with pytest.raises(config_init.ConfigError):
        config_init._tie_value("x", "a.b", "z")


def test_pin_tie_drives_and_constrains_the_pin():
    r = config_init.resolve_configuration("nexys_a7_100")
    r["peripherals"] = [a for a in r["peripherals"] if a["peripheral_id"] != "pin_tie"]
    r["peripherals"].append(_attach("pin_tie", {"pin": "onboard_mic.clk"}, {"value": "const.0"}))
    r["peripherals"].append(_attach("pin_tie", {"pin": "onboard_mic.lrsel"}, {"value": "~context.rst"}))
    assert codegen.validate_configuration(r) == []
    top = codegen.emit_top_sv(r, strict=True)
    assert "output onboard_mic_clk" in top
    assert "assign onboard_mic_clk = 1'b0;" in top
    assert "assign onboard_mic_lrsel = ~rst;" in top
    assert "PACKAGE_PIN J5 IOSTANDARD LVCMOS33 } [get_ports { onboard_mic_clk }]" in codegen.emit_xdc(r)


def test_i2s_audio_out_broadcasts_the_sound_bus():
    """BGM nexys_a7_100: PWM amplifier plus I2S DACs on JB and JC hear `sound`."""
    r = config_init.resolve_configuration("nexys_a7_100")
    r["peripherals"] = [a for a in r["peripherals"] if a["peripheral_id"] != "i2s_audio_out"]
    r["peripherals"].append(_attach("i2s_audio_out", {"mclk": "pmod_jb[0]", "bclk": "pmod_jb[1]",
                                                      "sdata": "pmod_jb[2]", "lrclk": "pmod_jb[3]"}))
    r["peripherals"].append(_attach("i2s_audio_out", {"mclk": "pmod_jc[0]", "bclk": "pmod_jc[1]",
                                                      "sdata": "pmod_jc[2]", "lrclk": "pmod_jc[3]"}))
    assert codegen.validate_configuration(r) == []
    plans = codegen.build_capability_plans(r)
    assert plans["audio_out"].aggregation == "broadcast" and len(plans["audio_out"].providers) == 3
    top = codegen.emit_top_sv(r, strict=True)
    assert top.count(".data_in(cap_audio_out_sample)") == 2
    assert ".data_i(cap_audio_out_sample)" in top and ".sound(cap_audio_out_sample)" in top
    assert ".bclk(pmod_jb[1])" in top and ".lrclk(pmod_jc[3])" in top


def test_i2s_optional_pins_and_pa_enable():
    r = config_init.resolve_configuration("tang_nano_20k_hdmi_tm1638")
    r["peripherals"] = [a for a in r["peripherals"] if a["peripheral_id"] != "i2s_audio_out"]
    r["peripherals"].append(_attach("i2s_audio_out", {"bclk": "onboard_headphone.bck", "lrclk": "onboard_headphone.ws",
                                                      "sdata": "onboard_headphone.din", "pa_en": "onboard_pa_enable"}))
    top = codegen.emit_top_sv(r, strict=True)
    assert ".mclk()," in top                                    # BGM leaves MCLK unconnected
    assert "assign onboard_pa_enable = 1'b1;" in top           # BGM: assign PA_EN = 1'b1
    assert "output onboard_pa_enable" in top


def test_gpio_header_concatenation_order():
    """BGM de0_cv `.gpio ( { GPIO_0, GPIO_1 } )`: the first attach is the LSB half."""
    r = config_init.resolve_configuration("de0_cv")
    r["peripherals"] = [a for a in r["peripherals"] if a["peripheral_id"] not in ("gpio_header", "pmod_12pin")]
    r["peripherals"].append(_attach("gpio_header", {"io": "gpio_1"}, {"width": 36}))
    r["peripherals"].append(_attach("gpio_header", {"io": "gpio_0"}, {"width": 36}))
    top = codegen.emit_top_sv(r, strict=True)
    assert ".w_gpio(72)" in top
    gpio = top[top.index(".gpio({"):]
    assert gpio.startswith(".gpio({gpio_0[35], gpio_0[34]")
    assert gpio.split("})")[0].endswith("gpio_1[1], gpio_1[0]")


def test_wm8731_and_adv7513_generate():
    r = config_init.resolve_configuration("de2_115")
    r["peripherals"] = [a for a in r["peripherals"] if a["peripheral_id"] not in ("i2s_audio_out", "wm8731_i2s_out")]
    r["peripherals"].append(_attach("wm8731_i2s_out", {
        "xck": "onboard_audio_codec.xck", "bclk": "onboard_audio_codec.bclk", "dac_lrck": "onboard_audio_codec.dac_lrck",
        "dac_dat": "onboard_audio_codec.dac_dat", "i2c_sclk": "onboard_i2c.sclk", "i2c_sdat": "onboard_i2c.sdat"}))
    assert codegen.validate_configuration(r) == []
    top = codegen.emit_top_sv(r, strict=True)
    assert "wm8731_i2s_out # (.clk_mhz(clk_mhz), .in_res(16))" in top
    assert "inout  onboard_i2c_sdat" in top
    paths = codegen.pll_source_paths(REPO, os.devnull, r["peripherals"])
    assert os.path.join(REPO, "rtl", "peripherals", "I2C_AUDIO_Config.v") in paths
    assert os.path.join(REPO, "rtl", "peripherals", "i2s_audio_out.sv") in paths

    r = config_init.resolve_configuration("de10_nano")
    r["peripherals"] = [a for a in r["peripherals"] if a["peripheral_id"] != "hdmi_adv7513"]
    r["peripherals"].append(_attach("hdmi_adv7513", {
        "clk": "onboard_hdmi_tx.clk", "de": "onboard_hdmi_tx.de", "hs": "onboard_hdmi_tx.hs", "vs": "onboard_hdmi_tx.vs",
        "d": "onboard_hdmi_tx.d", "i2c_scl": "onboard_hdmi_tx.i2c_scl", "i2c_sda": "onboard_hdmi_tx.i2c_sda",
        "int": "onboard_hdmi_tx.int"}))
    assert codegen.validate_configuration(r) == []
    top = codegen.emit_top_sv(r, strict=True)
    assert 'CONFIG_TABLE("de10_nano")' in top and ".tx_d(onboard_hdmi_tx_d)" in top
    assert ".screen_width(640)" in top and ".w_red(4)" in top


def test_qsf_global_assignments_and_no_invented_default():
    r = config_init.resolve_configuration("omdazz")
    pm = r["board_pinmap"]
    pm.setdefault("toolchain_options", {})["quartus"] = {
        "global_assignments": ['CYCLONEII_RESERVE_NCEO_AFTER_CONFIGURATION "USE AS REGULAR IO"']}
    qsf = codegen.emit_qsf(r, "EP4CE6E22C8")
    assert 'set_global_assignment -name CYCLONEII_RESERVE_NCEO_AFTER_CONFIGURATION "USE AS REGULAR IO"' in qsf
    pm.pop("defaults", None)
    for b in pm["pinBanks"].values():
        b.pop("iostandard", None)
        b.pop("overrides", None)
    qsf = codegen.emit_qsf(r, "EP4CE6E22C8")
    assert "set_location_assignment" in qsf and "IO_STANDARD" not in qsf


def test_yosys_synth_options():
    assert codegen.yosys_synth_options({"toolchain_options": {"yosys": {"synth_options": ["dsp", "-noabc9"]}}}) \
        == ["-dsp", "-noabc9"]
    assert codegen.yosys_synth_options({}) == []


# ---------------------------------------------------------------- sync helpers

_PINMAP = {"pinBanks": {
    "gpio_0": {"pins": ["A%d" % i for i in range(4)]},
    "pmod_ja": {"pins": ["B%d" % i for i in range(8)]},
    "onboard_leds": {"pins": ["L0", "L1", "L2"], "active": "low"},
    "onboard_led_red": {"pins": "R0"},
    "onboard_mic": {"pins": {"clk": "M0", "lrsel": "M1", "data": "M2"}},
}}
_SIG = {"GPIO_0[%d]" % i: "A%d" % i for i in range(4)}
_SIG.update({"JA[%d]" % i: "B%d" % i for i in range(8)})
_SIG.update({"LED[%d]" % i: "L%d" % i for i in range(3)})
_SIG.update({"LED_R_N": "R0", "M_CLK": "M0", "M_LRSEL": "M1", "M_DATA": "M2", "PA_EN": "A3"})


def _rev():
    return sc._Rev(_PINMAP, set())


def _hdr(*ports):
    """BGM-style port list, one declaration per line (bgm_oracle.top_ports)."""
    return "module board_specific_top\n(\n" + ",\n".join("    " + p for p in ports) + "\n);\n"


_HDR_GPIO = _hdr("input CLK", "inout [3:0] GPIO_0", "inout [7:0] JA")
_HDR_I2S = _hdr("input CLK", "inout [3:0] GPIO_0", "output PA_EN")
_HDR_TIE = _hdr("input CLK", "output M_CLK", "output M_LRSEL", "input M_DATA", "output PA_EN")
_HDR_LED = _hdr("input CLK", "output [2:0] LED")
_HDR_LED2 = _hdr("input CLK", "output [2:0] LED", "output LED_R_N")


def test_gpio_bits_from_concat_and_macro():
    text = _HDR_GPIO + "lab_top i_lab (.gpio ( `USER_GPIO )); endmodule"
    raw = "`define USER_GPIO \\\n { GPIO_0, \\\n JA }\n" + text
    bits, notes = sc.gpio_bits(text, raw, _SIG, _rev())
    assert notes == []
    assert [b[0] for b in bits][:8] == ["pmod_ja[%d]" % i for i in range(8)]     # LSB first
    assert sc.gpio_attaches(bits, _PINMAP) == [("pmod_12pin", {}, {"io": "pmod_ja"}),
                                               ("gpio_header", {"width": 4}, {"io": "gpio_0"})]


def test_gpio_partial_and_reversed_runs():
    bits = [("gpio_0[3]", "k"), ("gpio_0[2]", "k"), ("gpio_0[1]", "k"), ("gpio_0[0]", "k")]
    assert sc.gpio_attaches(bits, _PINMAP) == [("gpio_header", {"width": 4, "mirror": True}, {"io": "gpio_0"})]
    bits = [("gpio_0[1]", "k"), ("gpio_0[3]", "k")]
    assert sc.gpio_attaches(bits, _PINMAP) == [("gpio_header", {"width": 2}, {"io": ["gpio_0[1]", "gpio_0[3]"]})]


def test_i2s_and_pa_en():
    text = (_HDR_I2S + "i2s_audio_out # (.clk_mhz (clk_mhz)) i_audio (.clk (clk), .reset (rst), .data_in (sound), "
            ".mclk ( ), .bclk (GPIO_0 [0]), .lrclk (GPIO_0 [1]), .sdata (GPIO_0 [2])); assign PA_EN = 1'b1; endmodule")
    out = sc.i2s_attaches(text, _SIG, _rev())
    assert out == [({"bclk": "gpio_0[0]", "lrclk": "gpio_0[1]", "sdata": "gpio_0[2]", "pa_en": "gpio_0[3]"}, [])]


def test_tie_entries_skip_bound_pins():
    text = (_HDR_TIE + "assign M_CLK = 1'b0; assign M_LRSEL = 1'b0; assign PA_EN = 1'b1; endmodule")
    ties = sc.tie_entries(text, _SIG, _rev(), exclude_pins={"A3"})
    assert ties == OrderedDict([("onboard_mic.clk", "0"), ("onboard_mic.lrsel", "0")])


def test_led_bits_mixed_polarity_and_order():
    """iCEBreaker-style: LED_R_N = ~ led [3], LED[2:0] = led [2:0] reversed."""
    text = (_HDR_LED2 + "wire [3:0] led; assign LED_R_N = ~ led [3]; assign LED [0] = led [2]; assign LED [1] = led [1]; "
            "assign LED [2] = led [0]; endmodule")
    bits, notes = sc.led_bits(text, _SIG, _rev())
    assert bits == [("onboard_leds[2]", False), ("onboard_leds[1]", False), ("onboard_leds[0]", False),
                    ("onboard_led_red", True)]
    att = sc.led_attaches(bits, _PINMAP)
    assert att == [(OrderedDict([("width", 3), ("active", "high"), ("mirror", True)]), {"led": "onboard_leds"}),
                   (OrderedDict([("width", 1), ("active", "low")]), {"led": "onboard_led_red"})]
    # the same wiring expressed by the configuration is recognised as equal
    cur = [{"params": {"width": 3, "active": "high", "mirror": True}, "bind": {"led": "onboard_leds"}},
           {"params": {"width": 1, "active": "low"}, "bind": {"led": "onboard_led_red"}}]
    assert sc._led_signature(cur, _PINMAP) == sc._led_signature([{"params": dict(p), "bind": b} for p, b in att], _PINMAP)


def test_led_whole_bus_assign_and_direct_connection():
    text = _HDR_LED + "assign LED = ~ led; endmodule"
    bits, _ = sc.led_bits(text, _SIG, _rev())
    assert bits == [("onboard_leds[0]", True), ("onboard_leds[1]", True), ("onboard_leds[2]", True)]
    assert sc.led_attaches(bits, _PINMAP) == [(OrderedDict([("width", 3)]), {"led": "onboard_leds"})]
    text = _HDR_LED + "lab_top i (.led ( LED )); endmodule"
    bits, _ = sc.led_bits(text, _SIG, _rev())
    assert [b[0] for b in bits] == ["onboard_leds[0]", "onboard_leds[1]", "onboard_leds[2]"]


def test_bgm_clock_port():
    assert sc.bgm_clock_port("wire clk = OSC_50_B3B;") == "OSC_50_B3B"
    assert sc.bgm_clock_port("assign clk = pixel_clk_pll;") == "pixel_clk_pll"
    assert sc.bgm_clock_port("wire clk_100 = CLK;") is None


def test_openfpgaloader_args_follow_bgm_board_info():
    """BGM configure_fpga_yosys: --cable (colorlight), --ftdi-channel (karnix 0,
    orangecrab 1), -b BOARD; the Gowin table when the pinmap says nothing."""
    assert codegen.openfpgaloader_args({"toolchain_options": {"yosys": {"loader_ftdi_channel": "1"}}}) == ["--ftdi-channel", "1"]
    assert codegen.openfpgaloader_args({"toolchain_options": {"yosys": {"loader_cable": "ft2232"}}}) == ["--cable", "ft2232"]
    assert codegen.openfpgaloader_args({"toolchain_options": {"yosys": {"loader_board": "ice40_generic"}}}) == ["-b", "ice40_generic"]
    assert codegen.openfpgaloader_args({}, "tang_nano_20k") == ["-b", "tangnano20k"]
    assert codegen.openfpgaloader_args({}, "de10_lite") == []
    r = config_init.resolve_configuration("orangecrab_ecp5_yosys")
    assert codegen.openfpgaloader_args(r["board_pinmap"], "orangecrab_ecp5") == ["--ftdi-channel", "1"]


def test_qsf_has_bgm_project_template_lines():
    r = config_init.resolve_configuration("de10_nano")
    qsf = codegen.emit_qsf(r, "5CSEBA6U23I7")
    assert "set_global_assignment -name NUM_PARALLEL_PROCESSORS 4" in qsf
    assert 'set_global_assignment -name VERILOG_MACRO "INTEL_VERSION"' in qsf
    assert r["board_pinmap"]["toolchain_options"]["quartus"]["jtag_device_index"] == 2


def test_quartus_cable_list_parsing(monkeypatch):
    from toolchains.quartus_prime import quartus_prime as qp
    import subprocess

    class R(object):
        stdout = "Info: *******\n1) USB-Blaster [1-2]\n2) DE-SoC [1-3]\n"
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: R())
    assert qp._cables("quartus_pgm", {}, ".") == ["USB-Blaster [1-2]", "DE-SoC [1-3]"]
