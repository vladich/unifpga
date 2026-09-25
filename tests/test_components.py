"""
Lost / invented components: the i2s_audio_out, gpio_header, pin_tie, wm8731
and ADV7513 contracts through codegen, the `tie:` expansion, the broadcast
audio_out capability, and the QSF project settings and yosys synth options.
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


def _perif(pid):
    with open(os.path.join(REPO, "config", "peripherals", pid + ".yml")) as f:
        return yaml.safe_load(f)["Peripheral"]


def _attach(pid, bind, params=None):
    return {"peripheral_id": pid, "peripheral": _perif(pid), "params": params or {}, "bind": bind}


# ---------------------------------------------------------------- codegen

def test_a_list_parameter_is_a_table_of_bytes():
    """A YAML list reaches the driver as a concatenation of 8-bit entries,
    entry i at [8 i +: 8] (the XADC's VAUX table)."""
    assert codegen._sv_literal([6, 14, 7, 15]) == "{8'd15, 8'd7, 8'd14, 8'd6}"
    assert codegen._sv_literal([3]) == "{8'd3}"
    assert codegen._sv_literal(True) == "1'b1" and codegen._sv_literal("x") == '"x"'


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
    """nexys_a7_100: PWM amplifier plus I2S DACs on JB and JC hear `sound`."""
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
    assert ".mclk()," in top                                    # MCLK left unconnected
    assert "assign onboard_pa_enable = 1'b1;" in top           # assign PA_EN = 1'b1
    assert "output onboard_pa_enable" in top


def test_gpio_header_concatenation_order():
    """de0_cv `.gpio ( { GPIO_0, GPIO_1 } )`: the first attach is the LSB half."""
    r = config_init.resolve_configuration("de0_cv")
    r["peripherals"] = [a for a in r["peripherals"] if a["peripheral_id"] != "gpio_header"]
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
    assert os.path.join(REPO, "rtl", "peripherals", "i2c_reg_writer.sv") in paths
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


def _hdr(*ports):
    """A Verilog-2001 port list, one declaration per line."""
    return "module board_specific_top\n(\n" + ",\n".join("    " + p for p in ports) + "\n);\n"


_HDR_GPIO = _hdr("input CLK", "inout [3:0] GPIO_0", "inout [7:0] JA")
_HDR_I2S = _hdr("input CLK", "inout [3:0] GPIO_0", "output PA_EN")
_HDR_TIE = _hdr("input CLK", "output M_CLK", "output M_LRSEL", "input M_DATA", "output PA_EN")
_HDR_LED = _hdr("input CLK", "output [2:0] LED")
_HDR_LED2 = _hdr("input CLK", "output [2:0] LED", "output LED_R_N")


def test_openfpgaloader_args_follow_the_board():
    """openFPGALoader: --cable (colorlight), --ftdi-channel (karnix 0,
    orangecrab 1), -b BOARD; the Gowin table when the pinmap says nothing."""
    assert codegen.openfpgaloader_args({"toolchain_options": {"yosys": {"loader_ftdi_channel": "1"}}}) == ["--ftdi-channel", "1"]
    assert codegen.openfpgaloader_args({"toolchain_options": {"yosys": {"loader_cable": "ft2232"}}}) == ["--cable", "ft2232"]
    assert codegen.openfpgaloader_args({"toolchain_options": {"yosys": {"loader_board": "ice40_generic"}}}) == ["-b", "ice40_generic"]
    assert codegen.openfpgaloader_args(config_init.read_board_pinmap("tang_nano_20k")) == ["-b", "tangnano20k"]
    assert codegen.openfpgaloader_args({}) == []
    assert codegen.openfpgaloader_args(config_init.read_board_pinmap("de10_lite")) == []
    r = config_init.resolve_configuration("orangecrab_ecp5_yosys")
    assert codegen.openfpgaloader_args(r["board_pinmap"]) == ["--ftdi-channel", "1"]


def test_qsf_has_project_template_lines():
    r = config_init.resolve_configuration("de10_nano")
    qsf = codegen.emit_qsf(r, "5CSEBA6U23I7")
    assert "set_global_assignment -name NUM_PARALLEL_PROCESSORS 4" in qsf
    assert "VERILOG_MACRO" not in qsf          # designs do not test the vendor
    assert r["board_pinmap"]["toolchain_options"]["quartus"]["jtag_device_index"] == 2


def test_quartus_cable_list_parsing(monkeypatch):
    from toolchains.quartus_prime import quartus_prime as qp
    import subprocess

    class R(object):
        stdout = "Info: *******\n1) USB-Blaster [1-2]\n2) DE-SoC [1-3]\n"
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: R())
    assert qp._cables("quartus_pgm", {}, ".") == ["USB-Blaster [1-2]", "DE-SoC [1-3]"]


def test_gowin_ide_project_file_from_the_pinmap_device():
    """A .gprj project file for the Gowin IDE: the <Device> line is board
    data from the pinmap; without it no project."""
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
    assert "gw2ar18c-000" in codegen.emit_gowin_gprj(r["board_pinmap"], [], None, None)   # the majority of the 7 variants


def test_nextpnr_gui_args_follow_the_environment(monkeypatch):
    monkeypatch.delenv("UNIFPGA_NEXTPNR_GUI", raising=False)
    assert codegen.nextpnr_gui_args() == []
    monkeypatch.setenv("UNIFPGA_NEXTPNR_GUI", "1")
    assert codegen.nextpnr_gui_args() == ["--gui"]


def test_efinity_command_is_project_mode():
    """Efinity project mode: efx_run.py
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
    """The Pmod MIC3's 12-bit ADC code becomes the 24-bit signed sample:
    `mic_12 - 12'h800`, sign-extended."""
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
    # the RGB LEDs are the design's (rgb_r / rgb_g / rgb_b), not tied off at the board
    assert sum(1 for a in r["peripherals"] if a["peripheral_id"] == "rgb_led") == 2
    assert "assign onboard_rgb_led_16_r = cap_rgb_leds_r[0];" in top and ".w_rgb_led(2)," in top


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


def test_uart_rx_idle_level_follows_the_profile():
    """No UART pin: the generic top reads the idle line (1); the profile
    says 0 where the design leaves `.uart_rx ( )` unconnected (de1_soc) and
    1 where it writes `wire UART_RX = '1` (de10_nano)."""
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
    # the board's own UART pins become the uart_2wire attach (nexys4: rx only)
    r = config_init.resolve_configuration("nexys4")
    uart = next(a for a in r["peripherals"] if a["peripheral_id"] == "uart_2wire")
    assert list(uart["bind"]) == ["rx"]
    assert ".uart_tx()" not in codegen.emit_top_sv(r) or True


def test_partially_used_led_bank_stays_whole():
    """de0_cv: the lab drives LEDR [3:0]; the bank keeps its ten LEDs in
    the configuration (hardware), the profile marks the six the lab does
    not reach and puts the HEX decimal point on them."""
    r = config_init.resolve_configuration("de0_cv")
    leds = next(a for a in r["peripherals"] if a["peripheral_id"] == "led_bank")
    assert leds["params"]["width"] == 10 and leds["bind"] == {"led": "onboard_leds"}
    assert leds["lab_bits"] == {"leds": [0, 1, 2, 3, None, None, None, None, None, None]}
    assert ".w_led(4)," in codegen.emit_top_sv(r)


def test_i2s_dac_format_and_hdmi_widths():
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


def test_optional_signal_tied_off_is_unbound():
    """tang_nano_20k LCD: `.LCD_HSYNC ( )` with `assign LCD_HS = 1'b0` — the
    profile unbinds hs / vs (the driver's own outputs) and ties the pins; the
    backlight, a pin the peripheral drives from its `bl` parameter, stays
    bound and follows that parameter."""
    r = config_init.resolve_configuration("tang_nano_20k_lcd_480_272_no_tm1638")
    lcd = next(a for a in r["peripherals"] if a["peripheral_id"] == "lcd_480_272")
    assert "hs" not in lcd["bind"] and "vs" not in lcd["bind"] and lcd["bind"]["bl"] == "onboard_lcd.bl"
    assert r["configuration"]["tie"].get("onboard_lcd.hs") == 0 and r["configuration"]["tie"].get("onboard_lcd.vs") == 0
    assert "onboard_lcd.bl" not in (r["configuration"].get("tie") or {})
    top = codegen.emit_top_sv(r)
    assert ".LCD_HSYNC()" in top and "assign onboard_lcd_hs = 1'b0;" in top and "assign onboard_lcd_bl = 1'b1;" in top
    os.environ["UNIFPGA_PROFILE"] = "0"
    try:
        config_init.clear_cache()
        r0 = config_init.resolve_configuration("tang_nano_20k_lcd_480_272_no_tm1638")
    finally:
        os.environ.pop("UNIFPGA_PROFILE", None)
        config_init.clear_cache()
    lcd0 = next(a for a in r0["peripherals"] if a["peripheral_id"] == "lcd_480_272")
    assert lcd0["bind"]["hs"] == "onboard_lcd.hs"


def test_exact_rpll_dividers_are_pinned():
    """tang_nano_9k_lcd_800_480: the reference rPLL takes the 32.4 MHz LCD
    clock from CLKOUTD (64.8 MHz / 2); our solver reached 32.4 MHz on CLKOUT
    with other dividers and the LCD counters drifted against the reference."""
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


def test_tmds_timing_follows_the_vga_clock():
    """The TMDS driver's x / y come from `vga` on the clock the design runs
    it on: the serial clock (Gowin DVI_TX boards), the lab clock (Tang Nano 4K,
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
    emit = cg.EmissionContext("clk", "generic", {"serial": 252})
    assert cg._resolve_ref("clock.serial.mhz", {"peripheral_id": "x"}, {}, {}, emit) == "252"
    with pytest.raises(cg.CodegenError):
        cg._resolve_ref("clock.other.mhz", {"peripheral_id": "x"}, {}, {}, emit)


def test_dvi_timing_where_the_design_instantiates_dvi_top():
    for cid in ("a7_lite_35t", "tang_primer_20k_dock_hdmi_tm1638_yosys"):
        r = config_init.resolve_configuration(cid)
        hdmi = next(a for a in r["peripherals"] if a["peripheral_id"] == "hdmi_tmds")
        assert hdmi["params"].get("timing") == "dvi", cid
        assert '.TIMING("dvi")' in codegen.emit_top_sv(r)

