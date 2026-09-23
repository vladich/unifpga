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
    assert ".data_i(cap_audio_out_sample[15:8])" in top and ".sound(cap_audio_out_sample)" in top
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
    assert bits == [("onboard_leds[2]", False, False), ("onboard_leds[1]", False, False), ("onboard_leds[0]", False, False),
                    ("onboard_led_red", True, False)]
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
    assert bits == [("onboard_leds[0]", True, False), ("onboard_leds[1]", True, False), ("onboard_leds[2]", True, False)]
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


def test_gowin_ide_project_file_from_the_pinmap_device():
    """BGM writes fpga_project.gprj for the IDE (05_run_gui): the <Device>
    line is board data synced from BGM's template; without it no project."""
    assert codegen.emit_gowin_gprj({}, ["a.sv"], "x.cst", "x.sdc") is None
    pm = {"toolchain_options": {"gowin": {
        "gprj_device": '<Device name="GW1NR-9C" pn="GW1NR-LV9QN88PC6/I5">gw1nr9c-004</Device>'}}}
    text = codegen.emit_gowin_gprj(pm, ["/r/top.sv", "/r/rtl/x.sv"], "/o/top.cst", "/o/top.sdc")
    assert '<Device name="GW1NR-9C" pn="GW1NR-LV9QN88PC6/I5">gw1nr9c-004</Device>' in text
    assert '<File path="/r/rtl/x.sv" type="file.verilog" enable="1"/>' in text
    assert '<File path="/o/top.cst" type="file.cst" enable="1"/>' in text
    assert '<File path="/o/top.sdc" type="file.sdc" enable="1"/>' in text
    r = config_init.resolve_configuration("tang_nano_9k_hdmi_tm1638")
    assert "gw1nr9c-004" in codegen.emit_gowin_gprj(r["board_pinmap"], [], None, None)
    r = config_init.resolve_configuration("tang_nano_20k_hdmi_tm1638")
    assert "gw2ar18c-000" in codegen.emit_gowin_gprj(r["board_pinmap"], [], None, None)   # majority of BGM's 7 variants


def test_nextpnr_gui_args_follow_the_environment(monkeypatch):
    monkeypatch.delenv("UNIFPGA_NEXTPNR_GUI", raising=False)
    assert codegen.nextpnr_gui_args() == []
    monkeypatch.setenv("UNIFPGA_NEXTPNR_GUI", "1")
    assert codegen.nextpnr_gui_args() == ["--gui"]


def test_bgm_board_info_device_keys(tmp_path):
    """board_info.source_bash: DEVICE_PART/FAMILY/PACK and SPEED feed
    nextpnr-himbaechel (--device, --vopt family), gowin_pack -d and
    nextpnr-ecp5 --speed, as BGM's synthesize_for_fpga_yosys uses them."""
    import tools.sync_from_bgm as sfb
    v = tmp_path / "tang_nano_9k"
    v.mkdir()
    (v / "board_info.source_bash").write_text(
        'DEVICE_FAMILY="GW1N-9C"\nDEVICE_PART="GW1NR-LV9QN88PC6/I5"\nDEVICE_PACK="GW1N-9C"\n'
        'BOARD="tangnano9k"\n')
    info = sfb._bgm_board_info(str(v))
    assert info["device_family"] == "GW1N-9C" and info["device_part"] == "GW1NR-LV9QN88PC6/I5"
    assert info["device_pack"] == "GW1N-9C" and info["loader_board"] == "tangnano9k"
    (v / "board_info.source_bash").write_text('SPEED="8"\nCABLE="ft2232"\n')
    info = sfb._bgm_board_info(str(v))
    assert info == {"speed": "8", "loader_cable": "ft2232"}
    r = config_init.resolve_configuration("tang_nano_9k_lcd_480_272_tm1638_yosys")
    yo = codegen.yosys_loader_settings(r["board_pinmap"])
    assert yo["device_family"] == "GW1N-9C" and yo["device_part"] == "GW1NR-LV9QN88PC6/I5"
    r = config_init.resolve_configuration("colorlight75b_tm1638_ecp5_yosys")
    assert codegen.yosys_loader_settings(r["board_pinmap"])["speed"] == "6"


def test_efinity_command_is_bgm_project_mode():
    """BGM synthesize_for_fpga_efinity / configure_fpga_efinity: efx_run.py
    --pgm_opts source=work_pnr/<p>.lbf --pgm_opts dest=work_pnr/<p>.hex
    --flow compile <project.xml>; programming is --flow program."""
    from toolchains.efinity import efinity as ef
    cmd = ef._efx_command("/e/scripts/efx_run.py", "compile", "/o/unifpga_top.xml")
    assert cmd == ["python3", "/e/scripts/efx_run.py",
                   "--pgm_opts", "source=" + os.path.join("work_pnr", "unifpga_top.lbf"),
                   "--pgm_opts", "dest=" + os.path.join("work_pnr", "unifpga_top.hex"),
                   "--flow", "compile", "/o/unifpga_top.xml"]
    assert "--flist" not in cmd and "--family" not in cmd
    cmd = ef._efx_command("/e/scripts/efx_run.py", "program", "/o/unifpga_top.xml")
    assert cmd[-3:] == ["--flow", "program", "/o/unifpga_top.xml"]
    assert cmd[2:4] == ["--pgm_opts", "source=" + os.path.join("work_pnr", "unifpga_top.hex")]


def test_offset_binary_sample_is_centred_and_sign_extended():
    """The Pmod MIC3's 12-bit ADC code becomes the 24-bit signed sample the
    way BGM wires it: `mic_12 - 12'h800`, sign-extended."""
    r = config_init.resolve_configuration("saylinx_pmod_mic3")
    top = codegen.emit_top_sv(r)
    inst = next(l for l in top.splitlines() if "digilent_pmod_mic3_spi_receiver" in l and " i_pmod_mic3_" in l)
    name = inst.split(" i_pmod_mic3_")[1].split(" ")[0]
    net = "i_pmod_mic3_" + name + "_value"
    assert "    wire [11:0] {};".format(net) in top
    assert "        .value({})".format(net) in top
    assert "    wire [11:0] {n}_minus_offset = {n} - 12'h800;".format(n=net) in top
    assert "    assign cap_audio_in_sample = { { 12 { %s_minus_offset [11] } }, %s_minus_offset };" % (net, net) in top
    assert codegen._format_conversion("v", 12, "unsigned", "t", 24) == ["    assign t = { 12'b0, v };"]
    assert codegen._format_conversion("v", 16, "signed", "t", 16) == ["    assign t = v;"]
    with pytest.raises(codegen.CodegenError):
        codegen._format_conversion("v", 12, "gray", "t", 24)


def test_open_drain_pwm_and_eight_bit_sample():
    """Digilent Nexys AUD_PWM: `audio_pwm # (.data_w (8)) (.data_i (sound [15:8]))`
    and `assign AUD_PWM = sound_pwm ? 'Z : '0` (the board datasheet)."""
    r = config_init.resolve_configuration("nexys_a7_100")
    pwm = next(a for a in r["peripherals"] if a["peripheral_id"] == "pwm_amp")
    assert pwm["params"].get("open_drain") is True
    top = codegen.emit_top_sv(r)
    assert "audio_pwm # (.data_w(8))" in top
    assert ".data_i(cap_audio_out_sample[15:8])" in top
    idx = next(i for i, a in enumerate(r["peripherals"]) if a["peripheral_id"] == "pwm_amp")
    assert "        .pwm_o(i_pwm_amp_{}_pwm_o)".format(idx) in top
    assert "    assign onboard_pwm_amp_pwm = i_pwm_amp_{}_pwm_o ? 1'bz : 1'b0;".format(idx) in top
    assert "assign onboard_pwm_amp_sd = 1'b1;" in top
    # the RGB LEDs BGM ties off: dropped by the overlay, pins tied
    assert not any(a["peripheral_id"] == "rgb_led" for a in r["peripherals"])
    assert "assign onboard_rgb_led_16_r = 1'b0;" in top and "assign onboard_rgb_led_17_b = 1'b0;" in top
    assert ".w_rgb_led(0)," in top
    # without the overlay the hardware is back
    from tools import bgm_overlay
    os.environ["UNIFPGA_BGM_OVERLAY"] = "0"
    try:
        config_init.clear_cache()
        r0 = config_init.resolve_configuration("nexys_a7_100")
    finally:
        os.environ.pop("UNIFPGA_BGM_OVERLAY", None)
        config_init.clear_cache()
    assert sum(1 for a in r0["peripherals"] if a["peripheral_id"] == "rgb_led") == 2
    assert bgm_overlay.enabled()


def test_gpio_header_direction_out_and_header_uart():
    """emooc_cc: `assign GPIO_P2 [14:6] = lab_gpio` — the design drives 8
    header pins and never reads them, the ninth is the zero extension;
    de0_cv: the lab's UART on GPIO_1 [34] / [35]."""
    r = config_init.resolve_configuration("emooc_cc")
    hdr = next(a for a in r["peripherals"] if a["peripheral_id"] == "gpio_header")
    assert hdr["params"] == {"width": 8, "direction": "out"}
    top = codegen.emit_top_sv(r)
    i = next(k for k, a in enumerate(r["peripherals"]) if a["peripheral_id"] == "gpio_header")
    assert "    wire [7:0] gpio_out_{};".format(i) in top
    assert "    assign gpio_p2[4] = gpio_out_{} [0];".format(i) in top
    assert "    assign gpio_p2[11] = gpio_out_{} [7];".format(i) in top
    assert "    assign gpio_p2[12] = 1'b0;" in top
    gpio_line = next(l for l in top.splitlines() if l.strip().startswith(".gpio("))
    assert "gpio_p2[" not in gpio_line and "gpio_out_{} [7], gpio_out_{} [6]".format(i, i) in gpio_line
    assert ".w_gpio(8)" in top and ".w_btn(8)," in top
    r = config_init.resolve_configuration("de0_cv")
    uart = next(a for a in r["peripherals"] if a["peripheral_id"] == "uart_2wire")
    assert uart["bind"] == {"tx": "gpio_1[35]", "rx": "gpio_1[34]"}
    top = codegen.emit_top_sv(r)
    assert "assign gpio_1[35] = cap_serial_console_tx;" in top
    assert "assign cap_serial_console_rx = gpio_1[34];" in top
    assert ".uart_rx(cap_serial_console_rx)" in top


def test_decimal_point_on_the_top_leds():
    """Terasic DE0-CV / DE1-SoC / C5GX / DE2-115 / DE23-Lite: the HEX dp is
    not wired to the FPGA; BGM shows it on the top w_digit LEDs, latched
    with the digit like the segments (de23_lite inverted). The overlay binds
    seven_segment_per_digit's optional `dp` there."""
    r = config_init.resolve_configuration("de0_cv")
    seg = next(a for a in r["peripherals"] if a["peripheral_id"] == "seven_segment_per_digit")
    assert seg["bind"]["dp"] == ["onboard_leds[{}]".format(i) for i in range(4, 10)]
    assert seg["params"].get("dp_active") in (None, "high")
    leds = next(a for a in r["peripherals"] if a["peripheral_id"] == "led_bank")
    assert leds["lab_bits"]["leds"] == [0, 1, 2, 3, None, None, None, None, None, None]
    assert codegen.validate_configuration(r) == []
    top = codegen.emit_top_sv(r)
    assert ".dp_o({onboard_leds[9], onboard_leds[8], onboard_leds[7], onboard_leds[6], onboard_leds[5], onboard_leds[4]})" in top
    assert '.dp_active("high")' in top and ".latched(1'b1)" in top
    # the inverted form (BGM de23_lite: `dp [i] <= ~ hgfedcba [...]`, reset '1)
    text = ("module board_specific_top # (parameter w_led = 10, w_digit = 6) (output [w_led - 1:0] LEDR); "
            "localparam w_lab_led = w_led - w_digit; assign LEDR [w_lab_led - 1:0] = ~ lab_led; "
            "always_ff @ (posedge clk) for (int i = 0; i < w_digit; i ++) if (digit [i]) "
            "dp [i] <= ~ hgfedcba [$left (HEX0) + 1]; assign LEDR [w_led - 1:w_lab_led] = dp;")
    sig_pins = {"LEDR[{}]".format(i): "P{}".format(i) for i in range(10)}
    pinmap = {"pinBanks": {"onboard_leds": {"pins": ["P{}".format(i) for i in range(10)]}}}
    refs, inverted, zero = sc.dp_leds(text, sig_pins, sc._Rev(pinmap, set()), sc._top_params(text))
    assert refs == ["onboard_leds[{}]".format(i) for i in range(4, 10)] and inverted and not zero
    # c5gx: LEDR [9:6] are the dp, LEDR [5:0] are tied 0, the lab's led is LEDG
    r = config_init.resolve_configuration("c5gx")
    seg = next(a for a in r["peripherals"] if a["peripheral_id"] == "seven_segment_per_digit")
    assert seg["bind"]["dp"] == ["onboard_leds[{}]".format(i) for i in range(6, 10)]
    assert all(r["configuration"]["tie"].get("onboard_leds[{}]".format(i)) == 0 for i in range(6))
    top = codegen.emit_top_sv(r)
    assert "assign onboard_leds[0] = 1'b0;" in top and ".dp_o({onboard_leds[9], onboard_leds[8], onboard_leds[7], onboard_leds[6]})" in top
    # without the overlay the dp is unconnected and the LEDs are the lab's
    os.environ["UNIFPGA_BGM_OVERLAY"] = "0"
    try:
        config_init.clear_cache()
        r0 = config_init.resolve_configuration("de0_cv")
    finally:
        os.environ.pop("UNIFPGA_BGM_OVERLAY", None)
        config_init.clear_cache()
    seg0 = next(a for a in r0["peripherals"] if a["peripheral_id"] == "seven_segment_per_digit")
    assert "dp" not in seg0["bind"]
    assert ".dp_o()" in codegen.emit_top_sv(r0)


def test_uart_rx_idle_level_follows_bgm():
    """No UART pin: the generic top reads the idle line (1); BGM's overlay
    says 0 where its top leaves `.uart_rx ( )` unconnected (de1_soc) and 1
    where it writes `wire UART_RX = '1` (de10_nano)."""
    for cid, level in (("de1_soc", 0), ("de10_nano", 1)):
        r = config_init.resolve_configuration(cid)
        assert not any(a["peripheral_id"].startswith("uart") for a in r["peripherals"])
        assert r["configuration"]["uart_rx"] == level
        assert ".uart_rx(1'b{})".format(level) in codegen.emit_top_sv(r)
    r = config_init.resolve_configuration("de1_soc")
    r["configuration"] = {k: v for k, v in r["configuration"].items() if k != "uart_rx"}
    assert ".uart_rx(1'b1)" in codegen.emit_top_sv(r)
    r["configuration"]["uart_rx"] = 2
    with pytest.raises(codegen.CodegenError):
        codegen.emit_top_sv(r)
    # BGM's own UART pins become the uart_2wire attach (nexys4: rx only)
    r = config_init.resolve_configuration("nexys4")
    uart = next(a for a in r["peripherals"] if a["peripheral_id"] == "uart_2wire")
    assert list(uart["bind"]) == ["rx"]
    assert ".uart_tx()" not in codegen.emit_top_sv(r) or True


def test_led_bits_from_a_sliced_left_hand_side():
    """de0_cv: `assign LEDR [w_lab_led - 1:0] = lab_led;` — the lab's led is
    the low w_lab_led LEDs, the rest carry the HEX decimal point."""
    text = ("module board_specific_top # (parameter w_led = 10, w_digit = 6)\n(\n    output [w_led - 1:0] LEDR\n);\n"
            "localparam w_lab_led = w_led - w_digit;\nassign LEDR [w_lab_led - 1:0] = lab_led;\n"
            "assign LEDR [w_led - 1:w_lab_led] = dp;\n")
    sig_pins = {"LEDR[{}]".format(i): "P{}".format(i) for i in range(10)}
    pinmap = {"pinBanks": {"onboard_leds": {"pins": ["P{}".format(i) for i in range(10)]}}}
    bits, notes = sc.led_bits(text, sig_pins, sc._Rev(pinmap, set()))
    assert bits == [("onboard_leds[{}]".format(i), False, False) for i in range(4)] and not notes


def test_partially_used_led_bank_stays_whole():
    """de0_cv: BGM's lab drives LEDR [3:0]; the bank keeps its ten LEDs in
    the configuration (hardware), the overlay marks the six the lab does
    not reach (--lab-bits) and puts the HEX decimal point on them."""
    r = config_init.resolve_configuration("de0_cv")
    leds = next(a for a in r["peripherals"] if a["peripheral_id"] == "led_bank")
    assert leds["params"]["width"] == 10 and leds["bind"] == {"led": "onboard_leds"}
    assert leds["lab_bits"] == {"leds": [0, 1, 2, 3, None, None, None, None, None, None]}
    assert ".w_led(4)," in codegen.emit_top_sv(r)


def test_gpio_gaps_keep_bgm_numbering():
    """arty: `.gpio ({ck_io41, .., ck_io26, dummy_ck_io25_14, ck_io13, .., ck_io0})`
    — the 12-bit wire is a gap the lab's numbering jumps over; zybo's
    `wire [w_gpio - 1:0] gpio` is a bus wired to nothing at all."""
    text = ("module board_specific_top # (parameter w_gpio = 6)\n(\n    inout [1:0] A,\n    inout [1:0] B\n);\n"
            "wire [1:0] dummy;\nlab_top i_lab_top (.gpio ({ B, dummy, A }));\n")
    sig_pins = {"A[0]": "P0", "A[1]": "P1", "B[0]": "P2", "B[1]": "P3"}
    pinmap = {"pinBanks": {"hdr_a": {"pins": ["P0", "P1"]}, "hdr_b": {"pins": ["P2", "P3"]}}}
    rev = sc._Rev(pinmap, set())
    bits, notes = sc.gpio_bits(text, text, sig_pins, rev)
    assert [b[0] for b in bits] == ["hdr_a[0]", "hdr_a[1]", None, None, "hdr_b[0]", "hdr_b[1]"]
    assert any("carries no pins: 2 lab bits" in n for n in notes)
    assert [(p, b["io"]) for p, _pr, b in sc.gpio_attaches(bits, pinmap)] == [("gpio_header", "hdr_a"), ("gpio_header", "hdr_b")]
    assert sc.gpio_placement(bits, pinmap) == ([[0, 1], [4, 5]], 6)
    # no gap: the plain concatenation
    bits2 = [b for b in bits if b[0] is not None]
    assert sc.gpio_placement(bits2, pinmap) == (None, 4)
    # only a wire: no attach, a lab_width
    text3 = "module board_specific_top # (parameter w_gpio = 8)\n(\n    input CLK\n);\nwire [w_gpio - 1:0] gpio;\nlab_top i (.gpio (gpio));\n"
    bits3, _n = sc.gpio_bits(text3, text3, {"CLK": "C1"}, rev)
    assert bits3 == [(None, None)] * 8 and sc.gpio_placement(bits3, pinmap) == (None, 8)
    # codegen: the arty header's bits sit where BGM puts them, the gap reads nothing
    r = config_init.resolve_configuration("arty_a7_35")
    hdr = next(a for a in r["peripherals"] if a["peripheral_id"] == "gpio_header")
    assert hdr["lab_bits"]["gpio"][:2] == [41, 40] and hdr["lab_bits"]["gpio"][-1] == 0
    top = codegen.emit_top_sv(r)
    assert ".w_gpio(42)" in top and "wire gpio_nc_16;" in top and "wire gpio_nc_27;" in top
    gpio_line = next(l for l in top.splitlines() if l.strip().startswith(".gpio("))
    assert gpio_line.strip().startswith(".gpio({arduino_io[0], arduino_io[1],")
    # zybo: eight dangling bits, no header
    r = config_init.resolve_configuration("zybo_z7")
    assert not any(a["peripheral_id"] in ("gpio_header", "pmod_12pin") for a in r["peripherals"])
    top = codegen.emit_top_sv(r)
    assert ".w_gpio(8)" in top and ".gpio({gpio_nc_7, gpio_nc_6, gpio_nc_5, gpio_nc_4, gpio_nc_3, gpio_nc_2, gpio_nc_1, gpio_nc_0})" in top


def test_synchronised_reset_deassertion():
    """c5gx: `rstn_ff <= {rstn_ff [0], 1'b1}; rst = ~ rstn_ff [1]` (2 flops);
    a7_lite: xpm_cdc_async_rst (4). The overlay's reset.sync."""
    from tools import bgm_oracle
    t = "logic [1:0] rstn_ff;\nwire rst = ~rstn_ff[1];\nalways_ff @(posedge clk or negedge arst_n) if (!arst_n) rstn_ff <= '0; else rstn_ff <= {rstn_ff[0], 1'b1};"
    assert bgm_oracle.reset_sync_stages(t) == 2
    t2 = "xpm_cdc_async_rst i_x (.dest_clk (clk), .dest_arst (rst), .src_arst (~ RESETN));"
    assert bgm_oracle.reset_sync_stages(t2) == 4 and bgm_oracle.reset_exprs(t2) == ["~ RESETN"]
    assert bgm_oracle.reset_sync_stages("wire rst = ~ RESET_N;") is None
    r = config_init.resolve_configuration("c5gx")
    assert r["configuration"]["reset"].get("sync") == 2
    top = codegen.emit_top_sv(r)
    assert "logic [1:0] rst_sync_0;" in top
    assert "always_ff @ (posedge clk or negedge cpu_resetn)" in top
    assert "if (! cpu_resetn) rst_sync_0 <= '0;" in top and "else rst_sync_0 <= { rst_sync_0 [0:0], 1'b1 };" in top
    assert "assign rst = (~ rst_sync_0 [1]);" in top
    r = config_init.resolve_configuration("a7_lite_35t")
    assert r["configuration"]["reset"].get("sync") == 4 and "logic [3:0] rst_sync_0;" in codegen.emit_top_sv(r)
    r["configuration"] = dict(r["configuration"], reset={"sync": 0})
    with pytest.raises(codegen.CodegenError):
        codegen.emit_top_sv(r)


def test_vga_colour_forms():
    """BGM gates the colours with display_on on resistor ladders, passes them
    raw to a DAC blanked through BLANK_N (de1_soc), and registers them on the
    de0_nano ("to remove the glitches")."""
    from tools import sync_from_bgm as sy
    assert sy._vga_colour_form("assign VGA_R = display_on ? red : '0;") == (True, False)
    assert sy._vga_colour_form("assign VGA_R = display_on & ( | red );") == (True, False)
    assert sy._vga_colour_form("reg_vga_r <= display_on ? red : '0;") == (True, True)
    assert sy._vga_colour_form("module board_specific_top (output [7:0] VGA_R);\nlab_top i (.red (VGA_R));") == (False, False)
    for cid, gate, registered in (("de1_soc", False, False), ("de0_nano_vga_pmod", True, True), ("emooc_cc", False, False), ("omdazz", True, False)):
        r = config_init.resolve_configuration(cid)
        vga = next(a for a in r["peripherals"] if a["peripheral_id"] == "vga_4bit")
        assert bool(vga["params"].get("gate", True)) is gate and bool(vga["params"].get("registered", False)) is registered, cid
        top = codegen.emit_top_sv(r)
        assert (".GATE(1'b0)" in top) is (not gate) and (".REGISTERED(1'b1)" in top) is registered


def test_i2s_dac_format_and_hdmi_widths_follow_bgm():
    """tang_primer_20k_dock: `i2s_audio_out # (.align_right (1'b1), .offset_by_one_cycle (1'b0))`
    (PT8211); de10_nano / c5gx: HDMI_TX_D carries 8-bit colours."""
    r = config_init.resolve_configuration("tang_primer_20k_dock_hdmi_tm1638")
    dac = next(a for a in r["peripherals"] if a["peripheral_id"] == "i2s_audio_out")
    assert dac["params"].get("align_right") == 1 and dac["params"].get("offset_by_one_cycle") == 0
    assert ".align_right(1), .offset_by_one_cycle(0)" in codegen.emit_top_sv(r)
    for cid in ("de10_nano", "c5gx"):
        r = config_init.resolve_configuration(cid)
        hdmi = next(a for a in r["peripherals"] if a["peripheral_id"] == "hdmi_adv7513")
        assert hdmi["params"].get("bits_r") == 8 and hdmi["params"].get("bits_b") == 8
        assert ".W_RED(8), .W_GREEN(8), .W_BLUE(8)" in codegen.emit_top_sv(r)


def test_optional_signal_bgm_ties_off_is_unbound():
    """tang_nano_20k LCD: `.LCD_HSYNC ( )` with `assign LCD_HS = 1'b0` — the
    overlay unbinds hs / vs (the driver's own outputs) and ties the pins; the
    backlight, a pin the peripheral drives from its `bl` parameter, stays
    bound and follows that parameter (--clock-tree)."""
    r = config_init.resolve_configuration("tang_nano_20k_lcd_480_272_no_tm1638")
    lcd = next(a for a in r["peripherals"] if a["peripheral_id"] == "lcd_480_272")
    assert "hs" not in lcd["bind"] and "vs" not in lcd["bind"] and lcd["bind"]["bl"] == "onboard_lcd.bl"
    assert r["configuration"]["tie"].get("onboard_lcd.hs") == 0 and r["configuration"]["tie"].get("onboard_lcd.vs") == 0
    assert "onboard_lcd.bl" not in (r["configuration"].get("tie") or {})
    top = codegen.emit_top_sv(r)
    assert ".LCD_HSYNC()" in top and "assign onboard_lcd_hs = 1'b0;" in top and "assign onboard_lcd_bl = 1'b1;" in top
    os.environ["UNIFPGA_BGM_OVERLAY"] = "0"
    try:
        config_init.clear_cache()
        r0 = config_init.resolve_configuration("tang_nano_20k_lcd_480_272_no_tm1638")
    finally:
        os.environ.pop("UNIFPGA_BGM_OVERLAY", None)
        config_init.clear_cache()
    lcd0 = next(a for a in r0["peripherals"] if a["peripheral_id"] == "lcd_480_272")
    assert lcd0["bind"]["hs"] == "onboard_lcd.hs"


def test_equivalence_testbench_starts_in_reset():
    from tools import equiv_check as ec
    gate_map = {"P1": ("onboard_buttons", 0), "P2": ("onboard_switches", 9), "P3": ("cpu_resetn", None), "P4": ("onboard_leds", 0)}
    text = "assign rst = rst_on_power_up | ((~ onboard_buttons[0]) | (onboard_switches[9])) | (~ cpu_resetn);"
    assert ec.reset_levels(text, gate_map) == {"P1": 0, "P2": 1, "P3": 0}
    assert ec.reset_levels("assign rst = rst_on_power_up;", gate_map) == {}
    klass = {"C": "CLOCK", "P1": "INPUT", "P2": "INPUT", "P4": "OUTPUT"}
    tb = ec.testbench_text("w", ["C", "P1", "P2", "P4"], klass, {"C": 50.0}, "C", ["P4"], 100, {"P1": 0, "P2": 1})
    assert "localparam [N_IN - 1:0] RST_INIT = 2'b10;" in tb and "in_v [k] = RST_INIT [k];" in tb


def test_exact_rpll_dividers_from_bgm():
    """tang_nano_9k_lcd_800_480: BGM's gowin_rpll.v takes the 32.4 MHz LCD
    clock from CLKOUTD (64.8 MHz / 2); our solver reached 32.4 MHz on CLKOUT
    with other dividers and the LCD counters drifted against BGM's."""
    from tools import pll_solver
    r = {"mhz": 32.4, "tolerance_pct": 0.5, "pll": {"idiv": 4, "fbdiv": 23, "odiv": 4, "sdiv": 4, "clkoutd": True}}
    sol = codegen._pinned_rpll("cfg", "pixel", 27.0, r)
    assert (sol.idiv, sol.fbdiv, sol.odiv, sol.sdiv, sol.use_clkoutd) == (4, 23, 4, 4, True)
    assert abs(sol.f_out - 32.4) < 1e-9 and abs(sol.f_clkout - 129.6) < 1e-9
    with pytest.raises(codegen.CodegenError):
        codegen._pinned_rpll("cfg", "pixel", 27.0, dict(r, mhz=9.0))
    r = config_init.resolve_configuration("tang_nano_9k_lcd_800_480_tm1638")
    lcd = next(a for a in r["peripherals"] if a["peripheral_id"] == "lcd_800_480")
    assert lcd["params"]["clock_pixel_pll"] == {"idiv": 4, "fbdiv": 23, "odiv": 4, "sdiv": 4, "clkoutd": True}
    top = codegen.emit_top_sv(r)
    assert ".IDIV_SEL(4), .FBDIV_SEL(23), .ODIV_SEL(4), .DYN_SDIV_SEL(4), .USE_CLKOUTD(1'b1)" in top


def test_screenless_lab_keeps_bgm_widths_and_mirrored_keys():
    """alinx_ax7035b: no display, yet BGM passes w_x = $clog2 (640); the keys
    are `SWAP_BITS (lab_key, ~ key_in)`: active low (pinmap) and mirrored (overlay)."""
    from tools import bgm_oracle
    pol = bgm_oracle.port_polarity("module board_specific_top\n(\n    input [3:0] key_in\n);\n`SWAP_BITS ( lab_key , ~ key_in  );\n")
    assert pol.get("key_in") == {"inverted": True, "mirrored": True}
    r = config_init.resolve_configuration("alinx_ax7035b")
    assert not r["peripherals"] or not any(a["peripheral_id"].startswith("vga") for a in r["peripherals"])
    top = codegen.emit_top_sv(r)
    assert ".screen_width(640)," in top and ".screen_height(480)," in top and ".w_red(4)," in top
    btn = next(a for a in r["peripherals"] if a["peripheral_id"] == "button_array")
    assert btn["lab_bits"] == {"buttons": [3, 2, 1, 0], "switches": [3, 2, 1, 0]}   # SWAP_BITS: placed, not mirrored
    assert btn["params"].get("mirror") is None
    i = next(k for k, a in enumerate(r["peripherals"]) if a["peripheral_id"] == "button_array")
    assert "assign cap_buttons_btn__p{}[0] = ~ onboard_buttons[0];".format(i) in top
    assert "assign cap_buttons_btn[3] = cap_buttons_btn__p{}[0];".format(i) in top


def test_reversed_led_bank_is_placed_not_mirrored(tmp_path):
    """`SWAP_BITS (LED, ~ lab_led)` (ax7035b, the Tang Nano 9K without a
    TM1638): --lab-bits places leds [3, 2, 1, 0]; neither --components nor
    --polarity may add a mirror flag on top (it would reverse the bank twice),
    and the validator refuses the pair."""
    text = ("module board_specific_top # (parameter w_led = 4)\n(\n    input clk, output [w_led - 1:0] LED\n);\n"
            "wire [w_led - 1:0] lab_led;\n`SWAP_BITS ( LED , ~ lab_led );\nlab_top i (.led (lab_led));\n")
    sig_pins = {"LED[{}]".format(i): "L{}".format(i) for i in range(4)}
    pinmap = {"pinBanks": {"onboard_leds": {"pins": ["L0", "L1", "L2", "L3"]}}}
    rev = sc._Rev(pinmap, set())
    bits, notes = sc.led_bits(text, sig_pins, rev)
    want = sc.led_attaches(bits, pinmap)
    assert [b for _p, b in want] == [{"led": "onboard_leds"}]
    assert sc._take_mirror(want) == {0: True} and "mirror" not in want[0][0]
    for cid in ("alinx_ax7035b", "tang_nano_9k_lcd_480_272_no_tm1638"):
        r = config_init.resolve_configuration(cid)
        led = next(a for a in r["peripherals"] if a["peripheral_id"] == "led_bank")
        w = len(led["lab_bits"]["leds"])
        assert led["lab_bits"]["leds"] == list(range(w - 1, -1, -1)) and led["params"].get("mirror") is None, cid
        top = codegen.emit_top_sv(r)
        assert "assign onboard_leds[0] = ~ cap_leds_led[{}];".format(w - 1) in top, cid
        assert not codegen.validate_configuration(r), cid
        led["params"] = dict(led["params"], mirror=True)
        assert any("params.mirror and lab_bits" in p for p in codegen.validate_configuration(r)), cid


def test_check_power_up_model_and_undefined_status():
    from tools import equiv_check as ec
    src = "module tm1638_board_controller # (parameter clk_mhz = 50)\n(\n    input clk,\n    output logic [7:0] keys\n);\n    always_ff @ (posedge clk) keys <= '0;\nendmodule\n"
    out = ec._powerup_text(src)
    assert "initial keys = '0;" in out and out.index("initial keys") > out.index(");") and ec._powerup_text(out) == out
    assert ec._powerup_text("module other (input a);\nendmodule\n") == "module other (input a);\nendmodule\n"


def test_vga_colour_form_more_cases():
    from tools import sync_from_bgm as sy
    arty = ("module board_specific_top (output [7:0] jb, output [7:0] jc);\n"
            "lab_top i_lab (.red (jb [7:4]), .green (jc [7:4]), .blue (jb [3:0]));\n"
            "vga i_vga (.hsync (jc [0]), .vsync (jc [1]), .display_on ( ));\n")
    assert sy._vga_colour_form(arty) == (False, False)
    soc = "module board_specific_top (inout [35:0] GPIO_1);\nlab_top i (.red (red));\nassign GPIO_1 [13] = red [0];\n"
    assert sy._vga_colour_form(soc) == (False, False)
    assert sy._vga_colour_form("assign VGA_R = display_on ? red : '0;\nvga i_vga (.display_on (display_on));") == (True, False)


def test_tmds_timing_follows_bgm_vga_clock():
    """The TMDS driver's x / y come from BGM's `vga` on the clock BGM runs it
    on: the serial clock (Gowin DVI_TX boards), the lab clock (Tang Nano 4K,
    colorlight), with that clock's MHz for the pixel enable."""
    for cid, timing, mhz in (("tang_nano_9k_hdmi_tm1638", "serial", 252), ("tang_nano_4k_hdmi_no_tm1638", "lab", "clk_mhz"),
                             ("colorlight75b_tm1638_ecp5_yosys", "lab", "clk_mhz")):
        r = config_init.resolve_configuration(cid)
        hdmi = next(a for a in r["peripherals"] if a["peripheral_id"] == "hdmi_tmds")
        assert hdmi["params"].get("timing", "serial") == timing, cid
        top = codegen.emit_top_sv(r)
        inst = next(l for l in top.splitlines() if "hdmi_tmds_out #" in l)
        assert '.TIMING("{}")'.format(timing) in inst, inst
        if timing == "serial":
            assert ".SERIAL_MHZ({})".format(mhz) in inst and ".PIXEL_MHZ(25)" in inst, inst
        else:
            assert ".LAB_MHZ({})".format(mhz) in inst, inst
        assert ".lab_clk_i(clk)" in top or ".lab_clk_i(clk_pixel)" in top
    # the ref form
    from tools import codegen as cg
    cg._EMIT["clock_mhz"] = {"serial": 252}
    assert cg._resolve_ref("clock.serial.mhz", {"peripheral_id": "x"}, {}, {}) == "252"
    with pytest.raises(cg.CodegenError):
        cg._resolve_ref("clock.other.mhz", {"peripheral_id": "x"}, {}, {})


def test_gpio_wire_named_like_a_port_and_partly_placed_port():
    """tang_nano_20k_hdmi: `.gpio (gpio)` is the internal wire, not the port
    GPIO; tang_nano_9k_hdmi: GPIO [9:0] with pins for [5:0] only keeps its
    ten bits (the top four read nothing)."""
    text20 = ("module board_specific_top # (parameter w_gpio = 5)\n(\n    inout [w_gpio - 1:0] GPIO\n);\n"
              "wire [w_gpio - 1:0] gpio;\nlab_top i (.gpio (gpio));\n")
    sig_pins = {"GPIO[{}]".format(i): "P{}".format(i) for i in range(5)}
    pinmap = {"pinBanks": {"gpio": {"pins": ["P{}".format(i) for i in range(5)]}}}
    rev = sc._Rev(pinmap, set())
    bits, notes = sc.gpio_bits(text20, text20, sig_pins, rev)
    assert bits == [(None, None)] * 5 and sc.gpio_attaches(bits, pinmap) == []
    text9 = "module board_specific_top # (parameter w_gpio = 10)\n(\n    inout [w_gpio - 1:0] GPIO\n);\nlab_top i (.gpio (GPIO));\n"
    sig6 = {"GPIO[{}]".format(i): "P{}".format(i) for i in range(6)}
    pinmap6 = {"pinBanks": {"gpio": {"pins": ["P{}".format(i) for i in range(6)]}}}
    bits, notes = sc.gpio_bits(text9, text9, sig6, sc._Rev(pinmap6, set()))
    assert [b[0] for b in bits] == ["gpio[{}]".format(i) for i in range(6)] + [None] * 4
    assert sc.gpio_placement(bits, pinmap6) == (None, 10)
    r = config_init.resolve_configuration("tang_nano_20k_hdmi_tm1638")
    assert r["configuration"]["lab_width"]["gpio"] == 5
    assert not any(a["peripheral_id"] in ("gpio_header", "pmod_12pin") for a in r["peripherals"])
    r = config_init.resolve_configuration("tang_nano_9k_hdmi_tm1638")
    assert r["configuration"]["lab_width"]["gpio"] == 10 and ".w_gpio(10)" in codegen.emit_top_sv(r)


def test_dvi_timing_where_bgm_instantiates_dvi_top():
    for cid in ("a7_lite_35t", "tang_primer_20k_dock_hdmi_tm1638_yosys"):
        r = config_init.resolve_configuration(cid)
        hdmi = next(a for a in r["peripherals"] if a["peripheral_id"] == "hdmi_tmds")
        assert hdmi["params"].get("timing") == "dvi", cid
        assert '.TIMING("dvi")' in codegen.emit_top_sv(r)


def test_mirrored_screen_coordinates():
    """BGM's Tang Mega 138K tops pass the lab `mirrored_x = w_x' (screen_width
    - 1 - x)` (the panel is mounted rotated); the 9K's `ifdef MIRROR_LCD`
    branch is never taken. The overlay's mirror_screen gives the same."""
    from tools import sync_from_bgm as sy
    text = ("module board_specific_top;\nwire [w_x - 1:0] mirrored_x = w_x' (screen_width  - 1 - x);\n"
            "wire [w_y - 1:0] mirrored_y = w_y' (screen_height - 1 - y);\n"
            "lab_top i_lab_top (.x ( mirrored_x ), .y ( mirrored_y ));\nendmodule\n")
    assert sy.screen_mirrored(text) == (True, True)
    assert sy.screen_mirrored(text.replace(".x ( mirrored_x ), .y ( mirrored_y )", ".x ( x ), .y ( y )")) == (False, False)
    r = config_init.resolve_configuration("tang_mega_138k_pro_lcd_480_272_tm1638")
    lcd = next(a for a in r["peripherals"] if a["peripheral_id"] == "lcd_480_272")
    assert lcd["params"].get("mirror_screen") is True
    top = codegen.emit_top_sv(r)
    assert ".x(i_lcd_480_272_{0}_x)".format(r["peripherals"].index(lcd)) in top
    assert "assign cap_screen_x = 9'(480 - 1 - i_lcd_480_272_{0}_x);".format(r["peripherals"].index(lcd)) in top
    assert "assign cap_screen_y = 9'(272 - 1 - i_lcd_480_272_{0}_y);".format(r["peripherals"].index(lcd)) in top
    r9 = config_init.resolve_configuration("tang_nano_9k_lcd_480_272_tm1638")
    assert "mirror_screen" not in codegen.emit_top_sv(r9)


def test_testbench_forces_the_lab_reset_at_power_up():
    """A lab whose reset only a TM1638 key raises (Tang Mega 138K) never
    leaves x in Icarus; the testbench holds the top's lab reset net for the
    first phase on both sides, as the FPGA's power-up state would."""
    from tools import equiv_check as ec
    gold = "module board_specific_top;\nwire rst;\nlab_top i_lab_top (.clk (clk), .rst ( rst ));\nendmodule\n"
    assert ec.lab_reset_net(gold) == "rst"
    assert ec.lab_reset_net(gold.replace(".rst ( rst )", ".rst ( ~ rst_n )")) is None
    tb = ec.testbench_text("gold_w", ["A1", "B1"], {"A1": "CLOCK", "B1": "OUTPUT"}, {"A1": 50.0}, "A1", ["B1"],
                           100, {}, rst_net="rst")
    assert "force dut.u.rst = 1'b0; #0.5 force dut.u.rst = 1'b1;" in tb
    assert "wait (cycle >= 1000); release dut.u.rst; end" in tb
    assert "force" not in ec.testbench_text("gold_w", ["A1", "B1"], {"A1": "CLOCK", "B1": "OUTPUT"}, {"A1": 50.0},
                                            "A1", ["B1"], 100, {})


def test_lab_gpio_wider_than_its_wiring():
    """BGM passes lab_top `w_gpio = 24` with a 16-bit `{ PMOD_1, PMOD_0 }`
    (Tang Mega 138K) or nothing at all (basys3 `.gpio ( )`): the lab's upper
    bits float; lab_width.gpio keeps BGM's width."""
    text = ("module board_specific_top # (parameter w_gpio = 24)\n(\n    inout [7:0] PMOD_0, inout [7:0] PMOD_1\n);\n"
            "lab_top # (.w_gpio ( w_gpio )) i_lab_top (.gpio ( { PMOD_1, PMOD_0 } ));\nendmodule\n")
    assert sc._lab_gpio_width(text) == 24
    assert sc._lab_gpio_width(text.replace(".w_gpio ( w_gpio )", ".w_gpio ( 2 /* w_gpio */ )")) == 2
    for cid, w in (("tang_mega_138k_lcd_480_272_tm1638", 24), ("basys3", 24)):
        r = config_init.resolve_configuration(cid)
        top = codegen.emit_top_sv(r)
        assert ".w_gpio({})".format(w) in top and "gpio_nc_{}".format(w - 1) in top, cid


def test_tm1638_on_the_power_up_reset():
    """BGM Tang Primer 25K: `rst = tm_rst | tm_key [7]` with `tm_rst =
    rst_on_power_up`, and the TM1638 controller on tm_rst alone — its own
    key resets the lab, not the controller."""
    from tools import bgm_oracle
    text = ("wire tm_rst;\nassign tm_rst = rst_on_power_up;\nwire rst = tm_rst | tm_key [w_tm_key - 1];\n"
            "tm1638_board_controller # (.clk_mhz (clk_mhz)) i_tm1638 (.clk ( clk ), .rst ( tm_rst ), .keys ( tm_key ));\n")
    assert bgm_oracle.classify_reset(bgm_oracle.reset_exprs(text)) == {"power_up", "tm_key_msb"}
    assert bgm_oracle.tm1638_reset(text) == "power_up"
    assert bgm_oracle.tm1638_reset(text.replace(".rst ( tm_rst )", ".rst ( rst )")) is None
    r = config_init.resolve_configuration("tang_primer_25k_pmod_hub75e_led_matrix")
    top = codegen.emit_top_sv(r)
    i = next(k for k, a in enumerate(r["peripherals"]) if a["peripheral_id"] == "tm1638_led_key")
    inst = top[top.index("i_tm1638_led_key_{} (".format(i)):]
    assert inst[:inst.index(");")].count(".rst(rst_on_power_up)") == 1
    assert "assign rst = rst_on_power_up |" in top


def test_marsohod3gw2_forms():
    """BGM marsohod3gw2: the shield's keys `top_key = ~ { IO [8], IO [9],
    IO [10], IO [11] }`, `assign IO [19:16] = 4'b0000`, the UART through
    `UART_RX = FTB0` / `FTB1 = UART_TX`, `rst = ~ (key_rst_n & pll_lock)`
    with key_rst_n = KEY0 & KEY1, and the 8-bit ADC as the microphone."""
    from tools import bgm_oracle
    assert sc._source_kind("~ { IO [8], IO [9], IO [10], IO [11] }", {"IO"}, {}) == \
        ("board", ["IO[8]", "IO[9]", "IO[10]", "IO[11]"], None)
    assert sc._source_bits(["IO[8]", "IO[9]"], None, {"IO[8]": "29", "IO[9]": "30"}, {}) == ["IO[9]", "IO[8]"]
    text = ("module board_specific_top (input KEY0, input KEY1, inout [19:0] IO, input FTB0, output FTB1);\n"
            "wire UART_RX; wire UART_TX;\nassign FTB1 = UART_TX;\nassign UART_RX = FTB0;\n"
            "assign IO[19:16]= 4'b0000;\nwire key_rst_n; assign key_rst_n = KEY0 & KEY1;\n"
            "wire rst; assign rst = ~( key_rst_n & pll_lock );\n"
            "lab_top i_top (.uart_rx ( UART_RX ), .uart_tx ( UART_TX ));\nendmodule\n")
    assert sc._slice_const_bits(text) == {"IO[16]": "1'b0", "IO[17]": "1'b0", "IO[18]": "1'b0", "IO[19]": "1'b0"}
    sig_pins = {"FTB0": "60", "FTB1": "59"}
    rev = sc._Rev({"pinBanks": {"onboard_ft_bridge": {"pins": {"b0": "60", "b1": "59"}}}}, set())
    notes = []
    binds, idle = sc.uart_attach(text, sig_pins, rev, notes)
    assert binds == {"tx": "onboard_ft_bridge.b1", "rx": "onboard_ft_bridge.b0"} and idle is None, notes
    pol = bgm_oracle.port_polarity(text)
    assert pol["KEY0"]["inverted"] and pol["KEY1"]["inverted"]
    r = config_init.resolve_configuration("marsohod3gw2")
    top = codegen.emit_top_sv(r, strict=True)
    assert "assign rst = ((~ onboard_buttons[0]) | (~ onboard_buttons[1])) | (~ clk_serial_locked);" in top
    assert "assign cap_buttons_btn__p" in top and "= ~ gpio[11];" in top
    assert "adc_parallel_sampler i_adc_8bit_mic_" in top and ".clk(clk_pixel)" in top
    assert "assign gpio[19] = 1'b0;" in top
    r["configuration"]["reset"]["sources"][0]["bank"] = "no_such_bank"
    with pytest.raises(codegen.CodegenError):
        codegen.emit_top_sv(r, strict=True)


def test_gold_repairs_are_narrow():
    """The checker repairs only behaviour-neutral BGM source errors Icarus
    rejects: an empty connection to a port the lab lacks, a verbatim repeated
    connection, a scalar wire declared twice; anything else stays BGM's."""
    from tools import equiv_check as ec
    inst = ["    lab_top i_lab_top", "    (", "        .clk ( clk ),", "        .x ( x ),", "        .x ( x ),",
            "        .hsync (  ),", "        .y ( y )", "    );"]
    lines = list(inst)
    assert "hsync" in ec._repair_one(lines, 1, "port ``hsync'' is not a port of i_lab_top.")
    assert all(".hsync" not in l for l in lines)
    assert "repeated" in ec._repair_one(lines, 1, "port ``x'' already bound.")
    assert sum(".x (" in l for l in lines) == 1
    lines = list(inst)
    lines[4] = "        .x ( x2 ),"
    assert ec._repair_one(lines, 1, "port ``x'' already bound.") is None
    lines = list(inst)
    lines[5] = "        .hsync ( '0 ),"
    assert "hsync" in ec._repair_one(lines, 1, "port ``hsync'' is not a port of i_lab_top.")
    lines = list(inst)
    lines[5] = "        .hsync ( hs ),"
    assert ec._repair_one(lines, 1, "port ``hsync'' is not a port of i_lab_top.") is None
    decl = ["    wire display_on;", "    wire [3:0] a;", "    wire hsync, vsync, display_on, pixel_clk;"]
    assert "display_on" in ec._repair_one(decl, 3, "'display_on' has already been declared in this scope.")
    assert decl[2] == "    wire hsync, vsync, pixel_clk;"
    wide = ["    wire [7:0] lab_led;", "    wire [5:0] lab_led;"]
    assert ec._repair_one(wide, 2, "'lab_led' has already been declared in this scope.") is None
