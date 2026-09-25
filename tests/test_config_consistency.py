"""
YAML smoke tests. These catch the class of bugs found while cleaning up the
repo: mismatched PartFamily strings between boards and the chip registry,
fake chip families, missing-comma typos that silently merge two pin names
into one, toolchain ids that don't resolve to a Python module,
configurations that reference unknown peripherals, etc.

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
def _chips():         return config_init.read_chips()
def _programmers():   return config_init.read_programmers()
def _peripherals():   return config_init.read_peripherals()
def _capabilities():  return config_init.read_capabilities()
def _configurations(): return config_init.read_configurations()


# ---------------------------------------------------------------------------
# Capability catalog
# ---------------------------------------------------------------------------

_VALID_DIRECTIONS = {"hw_to_user", "user_to_hw", "inout"}
_VALID_AGGREGATION = {"concat", "exclusive", "or", "mux", "broadcast"}
_VALID_SIGNAL_TYPES = {"scalar", "bus"}


def test_capabilities_have_required_fields():
    caps = _capabilities()
    assert caps, "No capabilities under config/capabilities/"
    for cap_id, cap in caps.items():
        assert "id" in cap, "Capability {c}: missing id".format(c=cap_id)
        assert "signals" in cap, "Capability {c}: missing signals".format(c=cap_id)
        assert "aggregation" in cap, "Capability {c}: missing aggregation".format(c=cap_id)
        assert cap["aggregation"] in _VALID_AGGREGATION, (
            "Capability {c}: aggregation {a!r} is not one of {v}"
            .format(c=cap_id, a=cap["aggregation"], v=sorted(_VALID_AGGREGATION))
        )


def test_capability_signals_well_formed():
    for cap_id, cap in _capabilities().items():
        for sig in cap["signals"]:
            assert "name" in sig, "Capability {c}: signal without name".format(c=cap_id)
            assert "type" in sig, ("Capability {c}: signal {s} has no type"
                                    .format(c=cap_id, s=sig.get("name")))
            assert sig["type"] in _VALID_SIGNAL_TYPES, (
                "Capability {c}.{s}: type {t!r} is not one of {v}"
                .format(c=cap_id, s=sig["name"], t=sig["type"], v=sorted(_VALID_SIGNAL_TYPES))
            )
            assert "direction" in sig, ("Capability {c}: signal {s} has no direction"
                                         .format(c=cap_id, s=sig["name"]))
            assert sig["direction"] in _VALID_DIRECTIONS, (
                "Capability {c}.{s}: direction {d!r} is not one of {v}"
                .format(c=cap_id, s=sig["name"], d=sig["direction"], v=sorted(_VALID_DIRECTIONS))
            )


def test_boards_have_required_fields():
    boards = _boards()
    required = {"Id", "BoardName", "BoardProducer", "PartProducer", "PartFamily"}
    for board_id, board in boards.items():
        missing = required - set(board.keys())
        assert not missing, "Board {b} missing fields: {m}".format(b=board_id, m=missing)


def test_every_board_chip_resolves():
    """Every board's Chip / Chips entries must exist in the chip registry."""
    boards = _boards()
    chips = _chips()
    for board_id, board in boards.items():
        chip_refs = []
        if board.get("Chip"):
            chip_refs.append(board["Chip"])
        elif board.get("Chips"):
            for entry in board["Chips"]:
                if isinstance(entry, dict):
                    chip_refs.append(entry.get("Id"))
                else:
                    chip_refs.append(entry)
        for cid in chip_refs:
            assert cid in chips, "Board {b}: Chip {c!r} not in chip registry".format(b=board_id, c=cid)


def test_every_chip_toolchain_is_declared():
    """Every Toolchains entry in the chip registry must reference a known toolchain id."""
    chips = _chips()
    toolchains = _toolchains()
    for chip_id, chip in chips.items():
        for ref in chip.get("Toolchains", []):
            tc_id, _ = config_init.parse_versioned_ref(ref)
            assert tc_id in toolchains, (
                "Chip {c}: Toolchains references unknown id {t!r}".format(c=chip_id, t=tc_id)
            )


def test_every_board_programmer_is_declared():
    """Every board's `Programmer:` field references a known programmer id."""
    boards = _boards()
    programmers = _programmers()
    for board_id, board in boards.items():
        p = board.get("Programmer")
        if p is not None:
            assert p in programmers, \
                "Board {b}: Programmer {p!r} not in programmers.yml".format(b=board_id, p=p)
        for ref in board.get("ExtraProgrammers") or []:
            pid, _ = config_init.parse_versioned_ref(ref)
            assert pid in programmers, \
                "Board {b}: ExtraProgrammers entry {p!r} not in programmers.yml".format(b=board_id, p=pid)


def test_every_board_producer_is_declared():
    """Every board's `BoardProducer:` field references a known producer Id."""
    boards = _boards()
    producers = config_init.read_board_producers()
    name_idx = config_init.read_board_producers_name_index()
    for board_id, board in boards.items():
        bp = board.get("BoardProducer")
        if bp is None:
            continue
        # Must be a registered Id (after Phase 2 slug migration). The name
        # index also accepts legacy Name / AKA references in case a future
        # board is added with the display name by mistake — but the strict
        # check is membership in `producers` keyed by Id.
        assert bp in producers, (
            "Board {b}: BoardProducer {bp!r} is not a registered Id in "
            "board_producers.yml (resolves via AKA: {via!r})".format(
                b=board_id, bp=bp, via=name_idx.get(bp)
            )
        )


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


def test_all_board_feature_tokens_are_registered():
    """Soft check: every Features: token on every board is a registered Id.
    Phase 3 ships an empty Features: state, so this test is currently
    enforcing nothing — but as features get populated in subsequent phases,
    it catches typos and unregistered tokens."""
    unknown = config_init.validate_board_features()
    assert not unknown, \
        "Unregistered Features tokens: {}".format(unknown[:10])


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

def test_peripherals_have_required_fields():
    for pid, p in _peripherals().items():
        assert "id" in p, "Peripheral {p}: missing id".format(p=pid)
        assert "signals" in p, "Peripheral {p}: missing signals".format(p=pid)
        for sig in p["signals"]:
            assert "name" in sig, "Peripheral {p}: signal without a name".format(p=pid)
            assert "type" in sig, ("Peripheral {p}: signal {s} has no type"
                                   .format(p=pid, s=sig.get("name")))
        # New schema: provides + driver are required (provides may be []).
        assert "provides" in p, "Peripheral {p}: missing 'provides' (use [] if none)".format(p=pid)
        assert "driver" in p,   "Peripheral {p}: missing 'driver' (use null for passthrough)".format(p=pid)


def test_every_peripheral_provides_known_capabilities():
    caps = _capabilities()
    for pid, p in _peripherals().items():
        for entry in p.get("provides") or []:
            cap_id = entry.get("capability")
            assert cap_id in caps, (
                "Peripheral {p}: provides unknown capability {c!r}".format(p=pid, c=cap_id)
            )


_VALID_CONTEXT_REFS = {"clk", "rst", "rst_n", "clk_mhz"}


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


def _validate_ref(pid, where, ref, pin_names, caps, provided, peripheral=None):
    """Validate a single port_map / pin_assigns reference. Allows leading '~'
    (combinational invert) and indexed expressions like pin.d_p[0]."""
    assert isinstance(ref, str), (
        "Peripheral {p}: {w} is not a string ({r!r})"
        .format(p=pid, w=where, r=ref)
    )
    expr = ref.lstrip("~ ").strip()
    # Strip a single trailing index like [0..N].
    expr_root = expr.split("[", 1)[0]
    if expr_root.startswith("pin."):
        pin = expr_root[len("pin."):]
        assert pin in pin_names, (
            "Peripheral {p}: {w} references pin '{pin}' not in signals"
            .format(p=pid, w=where, pin=pin)
        )
    elif expr_root.startswith("capability."):
        rest = expr_root[len("capability."):]
        cap_id = rest.split(".", 1)[0]
        assert cap_id in caps, (
            "Peripheral {p}: {w} references unknown capability '{c}'"
            .format(p=pid, w=where, c=cap_id)
        )
        assert cap_id in provided, (
            "Peripheral {p}: {w} uses capability '{c}' but the peripheral does "
            "not declare it under provides:".format(p=pid, w=where, c=cap_id)
        )
    elif expr_root.startswith("context."):
        ctx = expr_root[len("context."):]
        assert ctx in _VALID_CONTEXT_REFS, (
            "Peripheral {p}: {w} references unknown context '{c}'"
            .format(p=pid, w=where, c=ctx)
        )
    elif expr_root.startswith("const."):
        pass
    elif expr_root.startswith("clock."):
        # PLL clock the peripheral declares under `clocks:` (P3.1)
        name = expr_root[len("clock."):]
        declared = {c["name"] for c in ((peripheral or {}).get("clocks") or [])}
        assert name in declared, (
            "Peripheral {p}: {w} references clock '{c}' not declared under clocks:"
            .format(p=pid, w=where, c=name)
        )
    elif expr_root.startswith("$"):
        # peripheral-instance parameter (`pin.bl: $bl`)
        assert expr_root[1:] in ((peripheral or {}).get("parameters") or {}), (
            "Peripheral {p}: {w} references undeclared parameter {r}".format(p=pid, w=where, r=ref)
        )
    elif expr_root == "":
        # A blank RHS in port_map means "leave port unconnected; codegen handles
        # via pin_assigns or with a wire". Allow it.
        pass
    else:
        raise AssertionError(
            "Peripheral {p}: {w} = {r!r} does not start with pin./capability./context./const./clock."
            .format(p=pid, w=where, r=ref)
        )


def test_peripheral_port_map_references_well_formed():
    caps = _capabilities()
    for pid, p in _peripherals().items():
        drv = p.get("driver")
        if drv is None:
            continue
        pin_names = {sig["name"] for sig in p.get("signals", [])}
        provided = {entry["capability"] for entry in p.get("provides") or []}
        for port, ref in (drv.get("port_map") or {}).items():
            if ref is None:
                continue   # port intentionally unconnected
            # A list renders as a concatenation (first element = MSB); every
            # element must be a valid reference on its own.
            for one in (ref if isinstance(ref, list) else [ref]):
                _validate_ref(pid, "port_map[{}]".format(port), one, pin_names, caps, provided, p)


def test_peripheral_pin_assigns_well_formed():
    """pin_assigns are direct combinational connections that bypass the driver
    instance — RHS must use the same vocabulary as port_map."""
    caps = _capabilities()
    for pid, p in _peripherals().items():
        pa = p.get("pin_assigns") or {}
        if not pa:
            continue
        pin_names = {sig["name"] for sig in p.get("signals", [])}
        provided = {entry["capability"] for entry in p.get("provides") or []}
        for lhs, rhs in pa.items():
            # LHS must be a valid pin / capability sink.
            _validate_ref(pid, "pin_assigns[{}].lhs".format(lhs), lhs, pin_names, caps, provided, p)
            _validate_ref(pid, "pin_assigns[{}].rhs".format(lhs), rhs, pin_names, caps, provided, p)


# ---------------------------------------------------------------------------
# Configuration resolution
# ---------------------------------------------------------------------------

def test_every_configuration_resolves():
    """Every config/configurations/*.yml must resolve cleanly: board exists,
    toolchain exists and is compatible, every attached peripheral exists."""
    configurations = _configurations()
    assert configurations, "No configurations under config/configurations/"
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
    assert {w for w in widths.values() if w} == {"w_sw", "w_btn", "w_led", "w_digit", "w_rgb_led", "w_gpio", "w_act"}
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
