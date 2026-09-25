"""
YAML smoke tests beyond the schema and relationship check (tools/check.py,
tests/test_check.py owns the shapes and the references): pin tokens, the
toolchain driver modules, the configurations resolving and codegen running
for every rig, the design requirements parser.

Run with:  python -m pytest tests/
Or stand-alone (pytest not required) via the script at the bottom.
"""

import importlib
import os
import re

import yaml

try:
    import pytest
except ImportError:                                    # pragma: no cover
    pytest = None

from config import init as config_init


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
# Vendor-agnostic pin shape: letters+digits ("E3", "K17"), or letters-only
# ("AN" on QMtech), or pure digits ("35" on iCE40 PCF), or differential
# pair "P,N" ("H5,J5" on Gowin).
_PIN_TOKEN = re.compile(r"^[A-Za-z]+\d*$|^\d+$|^[A-Za-z][A-Za-z0-9_]*$")


# ---------------------------------------------------------------------------
# Catalog round-trip and cross-reference tests
# ---------------------------------------------------------------------------

def _boards():        return config_init.read_boards_catalog()
def _toolchains():    return config_init.read_toolchains()
def _peripherals():   return config_init.read_peripherals()
def _capabilities():  return config_init.read_capabilities()
def _configurations(): return config_init.read_configurations()


# ---------------------------------------------------------------------------
# Capability catalog
# ---------------------------------------------------------------------------


def test_boards_have_required_fields():
    boards = _boards()
    required = {"Id", "BoardName", "BoardProducer", "PartProducer", "PartFamily"}
    for board_id, board in boards.items():
        missing = required - set(board.keys())
        assert not missing, "Board {b} missing fields: {m}".format(b=board_id, m=missing)


def test_board_features_registry_loads():
    """board_features.yml is well-formed; every entry has required fields."""
    features = config_init.read_board_features()
    assert features, "config/board_features.yml has no Features list"
    valid_categories = {"io", "memory", "connectivity", "sensor", "power",
                        "expansion", "display", "storage", "programming", "audio"}
    for fid, f in features.items():
        assert "Id" in f and f["Id"] == fid
        assert "Name" in f, "Feature {f}: missing Name".format(f=fid)
        assert "Category" in f, "Feature {f}: missing Category".format(f=fid)
        assert f["Category"] in valid_categories, \
            "Feature {f}: Category {c!r} not in {v}".format(f=fid, c=f["Category"], v=sorted(valid_categories))
        assert "Description" in f, "Feature {f}: missing Description".format(f=fid)


def test_mezzanines_registry_validates():
    """Every entry in config/mezzanines/* resolves cleanly: known producer,
    valid Type (mezzanine | som | piggyback | carrier), registered Features/Devices,
    SoM Chip resolves against the chip registry, CompatibleBoards exist."""
    result = config_init.validate_mezzanines()
    failures = {k: v for k, v in result.items() if v}
    assert not failures, "Mezzanine validation failures: {}".format(
        {k: items[:5] for k, items in failures.items()})


def test_board_producer_aka_uniqueness():
    """Every Name + AKA string maps to exactly one producer Id (no overlap
    that would silently mis-route a BoardProducer during migration)."""
    producers = config_init.read_board_producers()
    inverse = {}
    for pid, p in producers.items():
        candidates = [p.get("Name", pid), pid] + list(p.get("AKA") or [])
        for c in candidates:
            if c is None:
                continue
            existing = inverse.get(c)
            assert existing is None or existing == pid, \
                "Producer alias {c!r} maps to both {a!r} and {b!r}".format(c=c, a=existing, b=pid)
            inverse[c] = pid


def test_every_toolchain_id_has_a_module():
    toolchains = _toolchains()
    for tcid in toolchains:
        module_path = os.path.join(REPO_ROOT, "toolchains", tcid, tcid + ".py")
        assert os.path.exists(module_path), \
            "Toolchain {t}: missing module file {p}".format(t=tcid, p=module_path)
        mod = importlib.import_module("toolchains.{t}.{t}".format(t=tcid))
        assert hasattr(mod, "synthesize"), \
            "Toolchain {t}: module has no synthesize()".format(t=tcid)


# ---------------------------------------------------------------------------
# Per-board YAML structure (new pinBanks schema)
# ---------------------------------------------------------------------------

def _walk_pinmap_files():
    """Yield (path, board_id) for every per-board pinmap under the
    hierarchical config/boards/<producer>/<family>/<board_id>.yml layout."""
    boards_dir = os.path.join(REPO_ROOT, "config", "boards")
    import glob
    for path in glob.glob(os.path.join(boards_dir, "*", "*", "*.yml")):
        if "/_raw/" in path:
            continue
        board_id = os.path.basename(path)[:-4]
        yield path, board_id


def test_per_board_yamls_have_pinbanks():
    """Every per-board pinmap YAML must parse and expose Board.pinBanks."""
    files = list(_walk_pinmap_files())
    assert files, "No per-board pinmap files found under config/boards/<producer>/<family>/"

    for path, board_id in files:
        with open(path) as f:
            data = yaml.safe_load(f)
        assert data and "Board" in data, "{p} has no 'Board' root".format(p=path)
        board = data["Board"]
        assert "id" in board, "{p}: Board.id missing".format(p=path)
        assert "pinBanks" in board, "{p}: Board.pinBanks missing".format(p=path)


def test_pin_bank_pins_well_formed():
    """Walk every pinBank's pin values and reject anything that's clearly malformed
    (embedded spaces, empty strings, etc.). Catches missing-comma YAML typos."""
    for path, _bid in _walk_pinmap_files():
        with open(path) as f:
            data = yaml.safe_load(f)
        board_id = data["Board"]["id"]
        pin_banks = data["Board"].get("pinBanks", {}) or {}
        for bank_name, bank in pin_banks.items():
            pins = (bank or {}).get("pins") if isinstance(bank, dict) else None
            if pins is None:
                continue
            for pin_value in _walk_pin_values(pins):
                _assert_pin_token(board_id, bank_name, pin_value)


def _walk_pin_values(pins):
    """Yield every individual pin string (or int) from a pinBank.pins value,
    flattening lists and sub-key maps."""
    if pins is None:
        return
    if isinstance(pins, (str, int)):
        yield pins
    elif isinstance(pins, list):
        for el in pins:
            yield from _walk_pin_values(el)
    elif isinstance(pins, dict):
        for v in pins.values():
            yield from _walk_pin_values(v)


def _assert_pin_token(board_id, bank, pin_value):
    s = str(pin_value)
    assert s, "{b}.{n}: empty pin".format(b=board_id, n=bank)
    assert " " not in s, (
        "{b}.{n}: pin {p!r} contains a space — likely a missing comma in the YAML list"
        .format(b=board_id, n=bank, p=s)
    )
    # Allow simple pin tokens (E3, AN, 35) and Gowin diff pairs (H5,J5).
    parts = s.split(",")
    for p in parts:
        assert _PIN_TOKEN.match(p), (
            "{b}.{n}: pin {p!r} does not look like a valid pin name"
            .format(b=board_id, n=bank, p=p)
        )


# ---------------------------------------------------------------------------
# Peripheral catalog
# ---------------------------------------------------------------------------


def test_peripheral_drivers_reference_existing_files():
    for pid, p in _peripherals().items():
        drv = p.get("driver")
        if drv is None:
            continue
        assert "module" in drv, "Peripheral {p}: driver has no module".format(p=pid)
        assert "file" in drv,   "Peripheral {p}: driver has no file".format(p=pid)
        assert "port_map" in drv, "Peripheral {p}: driver has no port_map".format(p=pid)
        # The SV file must exist on disk so codegen can compile it.
        path = os.path.join(REPO_ROOT, drv["file"])
        assert os.path.exists(path), (
            "Peripheral {p}: driver file {f} does not exist".format(p=pid, f=drv["file"])
        )
        # Sanity check: the file declares the named module.
        with open(path) as f:
            text = f.read()
        assert ("module " + drv["module"]) in text, (
            "Peripheral {p}: file {f} does not declare module '{m}'"
            .format(p=pid, f=drv["file"], m=drv["module"])
        )


# ---------------------------------------------------------------------------
# Configuration resolution
# ---------------------------------------------------------------------------

def test_every_configuration_resolves():
    """Every rig (config/setups/*.yml, expanded) must resolve cleanly: board exists,
    toolchain exists and is compatible, every attached peripheral exists."""
    configurations = _configurations()
    assert configurations, "No rigs under config/setups/"
    for cfg_id in sorted(configurations):
        # resolve_configuration raises ConfigError on any inconsistency.
        config_init.resolve_configuration(cfg_id)


def test_every_configuration_has_known_toolchain():
    toolchains = _toolchains()
    for cfg_id, cfg in _configurations().items():
        tc = cfg.get("toolchain")
        assert tc in toolchains, (
            "Configuration {c}: toolchain {t!r} not in toolchains.yml"
            .format(c=cfg_id, t=tc)
        )


def test_every_configuration_has_known_peripherals():
    peripherals = _peripherals()
    for cfg_id, cfg in _configurations().items():
        for entry in cfg.get("attach", []) or []:
            pid = entry.get("peripheral")
            assert pid in peripherals, (
                "Configuration {c}: unknown peripheral {p!r}"
                .format(c=cfg_id, p=pid)
            )


# ---------------------------------------------------------------------------
# Codegen smoke test
# ---------------------------------------------------------------------------

def test_codegen_runs_for_every_configuration():
    """Strict codegen (what synthesize.py uses) succeeds for every
    configuration and produces a top module instantiating design_top."""
    from tools import codegen
    for cfg_id in sorted(_configurations()):
        try:
            text = codegen.generate_for(cfg_id, strict=True)
        except codegen.CodegenError as exc:
            raise AssertionError("strict codegen refused {c}: {e}".format(c=cfg_id, e=exc))
        assert "module top" in text, "codegen for {c}: no 'module top'".format(c=cfg_id)
        assert "endmodule" in text, "codegen for {c}: no 'endmodule'".format(c=cfg_id)
        assert "design_top" in text, "codegen for {c}: no design_top instantiation".format(c=cfg_id)


# ---------------------------------------------------------------------------
# Capability-requirements parser
# ---------------------------------------------------------------------------

def test_design_requirements_parser():
    from tools import design_requirements
    import tempfile
    sample = """\
// Some preamble
// requires:
//   switches >= 4
//   leds     >= 4
//   buttons  >= 2
//   screen   >= 640x480
//   audio_in
//   serial_console

module design_top (
    input clk
);
endmodule
"""
    with tempfile.NamedTemporaryFile("w", suffix=".sv", delete=False) as f:
        f.write(sample)
        p = f.name
    try:
        reqs = design_requirements.parse(p)
    finally:
        os.unlink(p)
    assert "switches" in reqs and reqs["switches"] == {"min_width": 4}, reqs
    assert "leds"     in reqs and reqs["leds"]     == {"min_width": 4}, reqs
    assert "buttons"  in reqs and reqs["buttons"]  == {"min_width": 2}, reqs
    assert "screen"   in reqs and reqs["screen"]   == {"min_width": 640, "min_height": 480}, reqs
    assert "audio_in" in reqs and reqs["audio_in"] == {}, reqs
    assert "serial_console" in reqs and reqs["serial_console"] == {}, reqs


def test_design_requirements_check_passes_when_satisfied():
    from tools import design_requirements
    resolved = config_init.resolve_configuration("nexys4_ddr_default")
    reqs = {
        "switches": {"min_width": 8},
        "leds":     {"min_width": 8},
        "buttons":  {"min_width": 3},
        "audio_in": {},
    }
    errs = design_requirements.check(resolved, reqs)
    assert errs == [], errs


def test_design_requirements_check_fails_when_under_provisioned():
    from tools import design_requirements
    resolved = config_init.resolve_configuration("nexys4_ddr_default")
    # Nexys 4 DDR has 16 switches; 32 should fail.
    reqs = {"switches": {"min_width": 32}}
    errs = design_requirements.check(resolved, reqs)
    assert len(errs) == 1 and "switches" in errs[0], errs


def test_design_requirements_check_fails_when_capability_missing():
    from tools import design_requirements
    # icebreaker_bare has no audio_out (no DAC or amplifier there).
    resolved = config_init.resolve_configuration("icebreaker_bare")
    reqs = {"audio_out": {}}
    errs = design_requirements.check(resolved, reqs)
    assert len(errs) == 1 and "audio_out" in errs[0], errs


# ---------------------------------------------------------------------------
# Stand-alone runner so we can validate without pytest installed
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    funcs = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failures = 0
    for fn in funcs:
        try:
            fn()
            print("ok    {}".format(fn.__name__))
        except Exception as exc:
            failures += 1
            print("FAIL  {}: {}".format(fn.__name__, exc))
    sys.exit(1 if failures else 0)


def test_design_requirements_conditional(tmp_path):
    """`<req> if <condition>`: enforced only when the condition holds for the
    parameters the generated top passes (codegen.design_top_parameters)."""
    from tools import design_requirements
    p = tmp_path / "design_top.sv"
    p.write_text("// requires:\n//   switches >= 3 if !(w_btn >= 3)\n//   leds >= 2\n\nmodule design_top;\nendmodule\n")
    reqs = design_requirements.parse(str(p))
    assert reqs["switches"] == {"when": [{"min_width": 3, "condition": "!(w_btn >= 3)"}]}, reqs
    assert reqs["leds"] == {"min_width": 2}
    resolved = config_init.resolve_configuration("nexys4_ddr_default")
    params = design_requirements.design_parameters(resolved)
    for w_btn, w_sw, unmet in ((5, 0, 0), (2, 3, 0), (2, 2, 1)):
        errs = design_requirements.check(resolved, reqs, parameters=dict(params, w_btn=w_btn, w_sw=w_sw))
        assert len(errs) == unmet, (w_btn, w_sw, errs)
    assert "when !(w_btn >= 3)" in design_requirements.check(
        resolved, reqs, parameters=dict(params, w_btn=0, w_sw=0))[0]
    with pytest.raises(ValueError):
        design_requirements.condition_holds("w_nonexistent > 1", params)


def test_design_requirements_widths_are_design_top_parameters():
    """A width requirement compares with the width design_top is given: on a rig
    whose on-board LEDs and a TM1638's LEDs drive the same bus that is 8, not 12."""
    from tools import codegen, design_requirements
    resolved = config_init.resolve_configuration("arty_a7_35_pmod_mic3")
    params = design_requirements.design_parameters(resolved)
    caps = config_init.read_capabilities()
    widths = {c: codegen.capability_width_parameter(c) for c in caps}
    assert {w for w in widths.values() if w} == {"w_sw", "w_btn", "w_led", "w_digit", "w_rgb_led", "w_gpio", "w_act", "w_mem_addr", "st_blocks"}
    # an optional capability's parameter reaches design_top only with a provider (or the design declaring it)
    assert {w for c, w in widths.items() if w and not caps[c].get("optional")} <= set(params) and "w_act" not in params
    assert design_requirements.check(resolved, {"leds": {"min_width": params["w_led"]}}) == []
    errs = design_requirements.check(resolved, {"leds": {"min_width": params["w_led"] + 1}})
    assert len(errs) == 1 and "w_led={}".format(params["w_led"]) in errs[0], errs


def test_every_design_requirement_parses_and_names_design_parameters():
    from tools import design_requirements
    params = design_requirements.design_parameters(config_init.resolve_configuration("nexys4_ddr_default"))
    import glob
    for p in sorted(glob.glob(os.path.join(REPO_ROOT, "designs", "*", "design_top.sv"))):
        for cap, req in design_requirements.parse(p).items():
            conditions = req if cap == design_requirements.WHERE else [w["condition"] for w in req.get("when") or []]
            for cond in conditions:
                design_requirements.condition_holds(cond, params)   # raises on a bad name


def test_design_requirements_colour_depth_is_the_design_widths():
    from tools import design_requirements
    resolved = config_init.resolve_configuration("arty_a7_35_pmod_mic3")     # 4-4-4
    assert design_requirements.check(resolved, {"screen": {"min_width": 640, "min_height": 480, "min_color_depth": 444}}) == []
    errs = design_requirements.check(resolved, {"screen": {"min_width": 640, "min_height": 480, "min_color_depth": 888}})
    assert len(errs) == 1 and "4/4/4 (rgb12)" in errs[0] and "8/8/8" in errs[0], errs


def test_catalogue_aliases_name_no_real_entry_twice():
    """`Aliases:` keep the ids of duplicates merged into an entry (catalogue
    corrections): an alias is never also a board or mezzanine id, and no id is
    the alias of two entries."""
    import glob
    ids, seen = set(), {}
    entries = []
    for f in glob.glob(os.path.join(REPO_ROOT, "config", "boards", "*", "*.yml")) + \
            glob.glob(os.path.join(REPO_ROOT, "config", "mezzanines", "*", "*.yml")):
        with open(f) as fh:
            data = yaml.safe_load(fh) or {}
        for e in (data.get("Boards") or []) + (data.get("Mezzanines") or []):
            ids.add(e["Id"])
            entries.append(e)
    for e in entries:
        for a in e.get("Aliases") or []:
            assert a not in ids, "{} is an alias of {} and an entry of its own".format(a, e["Id"])
            assert a not in seen, "{} is an alias of {} and of {}".format(a, e["Id"], seen[a])
            seen[a] = e["Id"]


def test_unwired_pin_notes_name_module_pins_and_a_level():
    """A module's `unwired:` (what a pin must be when the rig leaves it
    unwired: the PCM5102A's SCK held low) names its own signal pins, a level
    and why; such a pin's signal is optional in the peripheral."""
    from tools import setup as su
    peripherals = config_init.read_peripherals()
    seen = 0
    for mid, m in su.read_modules().items():
        for pin, spec in (m.get("unwired") or {}).items():
            seen += 1
            assert pin in m["pins"] and m["pins"][pin] not in ("power", "ground"), (mid, pin)
            assert spec.get("tie") in ("ground", "power") and spec.get("why"), (mid, pin)
            sig = next(s for s in peripherals[m["peripheral"]]["signals"] if s["name"] == m["pins"][pin])
            assert sig.get("optional"), (mid, pin, "a pin left unwired must be an optional signal")
    assert seen


def test_design_requirements_where(tmp_path):
    """`where <condition>`: a condition on design_top's parameters alone must
    hold (5_5_aps divides its clock into exact 10 and 25 MHz ones)."""
    from tools import design_requirements
    p = tmp_path / "design_top.sv"
    p.write_text("// requires:\n//   leds >= 1\n//   where clk_mhz % 50 == 0\n\nmodule design_top;\nendmodule\n")
    reqs = design_requirements.parse(str(p))
    assert reqs[design_requirements.WHERE] == ["clk_mhz % 50 == 0"] and reqs["leds"] == {"min_width": 1}
    resolved = config_init.resolve_configuration("nexys4_ddr_default")
    params = design_requirements.design_parameters(resolved)
    assert design_requirements.check(resolved, reqs, parameters=dict(params, clk_mhz=100)) == []
    errs = design_requirements.check(resolved, reqs, parameters=dict(params, clk_mhz=27))
    assert errs == ["Configuration '{}': the design needs clk_mhz % 50 == 0 (clk_mhz = 27)"
                    .format(resolved["configuration"]["id"])], errs
    p.write_text("// requires:\n//   where clk_mhz ^ 2\n\nmodule design_top;\nendmodule\n")
    with pytest.raises(ValueError):
        design_requirements.parse(str(p))
