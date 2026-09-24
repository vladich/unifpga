"""Optional capabilities (small_display, text_display, actuators): design_top
gets their parameters and ports only when the design declares them (or, with
no design at hand, when the rig provides them), so every other design and
configuration is untouched."""

import logging
import os
import re
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
logging.disable(logging.CRITICAL)

from config import init as config_init        # noqa: E402
from tools import codegen                     # noqa: E402
from tools import design_requirements as dr   # noqa: E402
from tools import setup as su                 # noqa: E402

RIG = "arty_a7_35_oled_lcd_servos"
DEMO = os.path.join(REPO, "designs", "displays_and_actuators", "design_top.sv")
PLAIN = os.path.join(REPO, "designs", "1_09_hex_counter", "design_top.sv")


def _instance(top):
    return top[top.index("design_top # ("):]


def test_contract_follows_the_interface_and_the_capabilities():
    params, derived, ports = codegen.design_contract()
    declared = codegen.design_declarations(codegen.DESIGN_INTERFACE)
    assert [p.name for p in params + derived] == [n for n in declared[0] if n in {p.name for p in params + derived}]
    assert [p.name for p in ports] == declared[1]
    optional = {p.capability for p in ports if p.optional}
    assert optional == {"small_display", "text_display", "actuators"}


def test_design_declarations_reads_parameters_and_ports():
    text = """
    // module design_top (input nope);
    module design_top # (parameter int clk_mhz = 50, parameter int w_sw = 0, parameter int w_x = $clog2(640))
    ( input clk, input [w_sw - 1:0] sw, /* input fake, */ output logic [7:0] txt_char );
    endmodule
    """
    assert codegen.design_declarations(text) == (["clk_mhz", "w_sw", "w_x"], ["clk", "sw", "txt_char"])
    assert codegen.design_declarations("module other (input a); endmodule") is None


def test_the_design_decides_the_optional_ports():
    r = config_init.resolve_configuration(RIG)
    demo, plain, bare = (_instance(codegen.emit_top_sv(r, design=d)) for d in (DEMO, PLAIN, None))
    for port in ("sd_x", "sd_pixel", "txt_char", "act_on", "act_level"):
        assert ".{}(".format(port) in demo and ".{}(".format(port) in bare and ".{}(".format(port) not in plain
    assert ".sd_width(128)" in demo and ".txt_columns(16)" in demo and ".w_act(8)" in demo
    assert ".w_act(" not in plain


def test_a_declared_optional_port_without_a_provider_is_tied_off():
    r = config_init.resolve_configuration("arty_a7_35_pmod_mic3")
    inst = _instance(codegen.emit_top_sv(r, design=DEMO))
    assert ".sd_x('0)" in inst and ".txt_row('0)" in inst and ".sd_pixel()" in inst and ".act_level()" in inst
    assert ".sd_width(0)" in inst and ".w_act(0)" in inst and ".w_sd_pixel(1)" in inst
    assert ".sd_x(" not in _instance(codegen.emit_top_sv(r))


def test_widths_follow_the_rig():
    r = config_init.resolve_configuration(RIG)
    top = codegen.emit_top_sv(r, design=DEMO)
    assert "wire [6:0] cap_small_display_x;" in top and "wire [4:0] cap_small_display_y;" in top
    assert "wire [3:0] cap_text_display_column;" in top and "wire [0:0] cap_text_display_row;" in top
    assert "wire [7:0] cap_actuators_enable;" in top and "wire [63:0] cap_actuators_level;" in top
    widths = codegen.design_top_widths(codegen.design_top_parameters(r, codegen.build_capability_plans(r), DEMO))
    assert (widths["w_sd_x"], widths["w_sd_y"], widths["w_txt_col"], widths["w_txt_row"], widths["w_act_level"]) == (7, 5, 4, 1, 64)


def test_actuator_levels_slice_eight_bits_per_actuator():
    """A servo Pmod after a relay Pmod drives actuators 4..7: levels [63:32]."""
    cfg = su.generate(su.read_setups()[RIG])
    servo = next(a for a in cfg["attach"] if a["peripheral"] == "servo_pwm")
    cfg["attach"].remove(servo)
    cfg["attach"].append(servo)
    top = codegen.emit_top_sv(config_init.resolve_configuration(RIG, configuration=cfg))
    assert ".level(cap_actuators_level[63:32])" in top and ".enable(cap_actuators_enable[7:4])" in top
    assert "= cap_actuators_enable[3:0];" in top


def test_sized_requirements_of_the_new_capabilities():
    r = config_init.resolve_configuration(RIG)
    assert dr.check(r, dr.parse(DEMO)) == []
    errs = dr.check(r, {"text_display": {"min_width": 20, "min_height": 4}})
    assert len(errs) == 2 and "text_display is 16x2, design_top requires 20x4" in errs[0], errs
    errs = dr.check(r, {"actuators": {"min_width": 9}})
    assert errs and "w_act=8" in errs[0]
    other = config_init.resolve_configuration("arty_a7_35_pmod_mic3")
    assert any("does not provide capability 'small_display'" in e for e in dr.check(other, dr.parse(DEMO)))


def test_a_two_row_pmod_plugs_into_the_whole_connector():
    layout, modules, connectors = su.read_layout("arty_a7"), su.read_modules(), su.read_connectors()
    oled = modules["digilent_pmod_oled"]
    wires = su.plug_wires(connectors, layout, oled, {"connector": "ja", "row": "all"})
    assert wires == {"1": "ja.1", "2": "ja.2", "4": "ja.4", "7": "ja.7", "8": "ja.8", "9": "ja.9", "10": "ja.10"}
    assert {"connector": "ja", "row": "all"} in su.plug_placements(connectors, layout, oled, ["ja"])
    assert su.plug_fits(connectors, layout, oled, {"connector": "ja", "row": 1})


def test_module_parameters_round_trip():
    setup = su.read_setups()[RIG]
    cfg = su.generate(setup)
    servo = next(a for a in cfg["attach"] if a["peripheral"] == "servo_pwm")
    assert servo["params"] == {"count": 4}
    back = su.derive(cfg)
    assert next(u for u in back["use"] if u.get("module") == "digilent_pmod_con3").get("params") is None
    cfg2 = su.generate(dict(setup, use=[dict(u, params={"servo": False}) if u.get("module") == "digilent_pmod_con3" else u
                                        for u in setup["use"]]))
    assert next(a for a in cfg2["attach"] if a["peripheral"] == "servo_pwm")["params"] == {"count": 4, "servo": False}
    assert next(u for u in su.derive(cfg2)["use"] if u.get("module") == "digilent_pmod_con3")["params"] == {"servo": False}


def test_capability_data_replaces_the_code_tables():
    caps = config_init.read_capabilities()
    assert [c for c, d in caps.items() if d.get("required")] == ["clock"]
    assert su.gpio_passthrough() == ("gpio_header", "io")
    src = open(os.path.join(REPO, "tools", "codegen.py")).read()
    assert not re.search(r"_PRIMARY_PARAM|CAPABILITY_WIDTH_PARAMETER\s*=|DESIGN_PORTS\s*=", src)
