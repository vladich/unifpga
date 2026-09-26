"""
tools/check.py: every configuration file against its schema
(config/schema/<entity>.schema.json), every reference between entities
resolved, the rules a reference table cannot express — driven by
config/schema/entities.yml. The repository passes; a synthetic catalogue in
documents mode shows each kind of finding is precise and bounded.
"""

import copy
import os
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from tools import check, cli                      # noqa: E402

REPO_RULES = {"rig.rig_expands", "peripheral.peripheral_driver_files", "board.board_provenance",
              "toolchain.toolchain_driver", "design.design_fileset", "design_top.design_top_interface"}


def _catalogue():
    """A minimal catalogue that satisfies every schema and relation (paths
    relative to config/, as tools/catalog_snapshot.py captures them)."""
    return {
        "schema/entities.yml": {"Entities": check.read_entities()},
        "design_top.yml": {"DesignTop": {"sections": [{"title": "Everything", "capabilities": ["leds", "reset"]}]}},
        "toolchains.yml": {"Toolchains": [{"id": "tool", "name": "Tool", "operations": [],
                                           "known_versions": ["1.0"], "version": None, "install_dir": None}]},
        "programmers.yml": {"Programmers": [{"id": "prog", "name": "Prog", "known_versions": [], "version": None,
                                             "bundled": "tool", "binary": "prog", "bridge": "jtag",
                                             "families": ["family"]}]},
        "producers.yml": {"Producers": [{"id": "maker", "name": "Maker", "aka": ["Maker Inc"], "url": None,
                                         "country": None, "founded": None, "categories": ["hobby"],
                                         "description": "", "defunct_since": None, "notes": None}]},
        "chips/maker/family.yml": {"Family": {"id": "family", "producer": "maker", "name": "Family", "description": "",
                                              "default_toolchains": ["tool[*]"], "chips": [{"id": "CHIP-1"}]}},
        "boards/maker/family/board.yml": {"Board": {
            "id": "board", "name": "Board", "producer": "maker", "chip": "CHIP-1", "programmer": "prog",
            "features": ["led_feature"], "devices": ["led_device"],
            "banks": {"leds": {"pins": ["A1", "A2"], "device": {"name": "two LEDs", "kind": "leds"}},
                      "j1": {"pins": ["B1", "B2"], "device": {"name": "J1", "kind": "header"}}},
            "layout": {"verified": False, "generated": True},
            "headers": [{"id": "j1", "type": "pmod_2x6", "label": "J1", "bank": "j1", "pins": {"1": "j1[0]", "2": "j1[1]"}}],
            "parts": [{"id": "leds", "label": "LEDs", "attach": {"peripheral": "led", "bind": {"led": "leds"}, "params": {"width": 2}}}]}},
        "connectors.yml": {"Connectors": {"pmod_2x6": {"name": "Pmod", "source": "spec", "voltage": 3.3,
                                                       "rows": [[1, 2]], "power": {2: "GND"}}}},
        "capabilities/leds.yml": {"Capability": {
            "id": "leds", "description": "LEDs", "aggregation": "concat", "primary": "width",
            "parameters": {"width": {"type": "int", "required": True}},
            "signals": [{"name": "led", "type": "bus", "width": "$width", "direction": "user_to_hw"}],
            "design": {"parameters": {"w_led": {"value": "width", "absent": 1}},
                       "ports": {"led": {"signal": "led", "width": "w_led"}}}}},
        "capabilities/reset.yml": {"Capability": {
            "id": "reset", "description": "Reset", "aggregation": "or",
            "signals": [{"name": "rst", "type": "scalar", "direction": "hw_to_user"}],
            "design": {"ports": {"rst": {"signal": "rst", "width": 1, "net": {"source": "reset_net"}}}}}},
        "peripherals/led.yml": {"Peripheral": {
            "id": "led", "description": "LED bank", "driver": None,
            "parameters": {"width": {"type": "int", "required": True}},
            "signals": [{"name": "led", "type": "bus", "width": "$width", "direction": "output"}],
            "provides": [{"capability": "leds", "params": {"width": "$width"}}]}},
        "peripherals/blink.yml": {"Peripheral": {
            "id": "blink", "description": "A driven LED", "parameters": {"hz": {"type": "int", "default": 2}},
            "signals": [{"name": "led", "type": "scalar", "direction": "output"}],
            "provides": [{"capability": "leds", "params": {"width": 1}}],
            "clocks": [{"name": "slow", "mhz": 1}],
            "driver": {"module": "blink", "file": "rtl/peripherals/blink.sv",
                       "parameters": {"HZ": "$hz", "CLK_MHZ": "context.clk_mhz"},
                       "port_map": {"clk": "clock.slow", "rst": "context.rst",
                                    "value": "capability.leds.led[0]", "led": "pin.led"}}}},
        "modules/addon.yml": {"Module": {"id": "addon", "name": "Addon", "peripheral": "led", "voltage": 3.3,
                                         "pins": {"1": "led[0]", "2": "ground"}, "params": {"width": 1},
                                         "source": "spec", "verified": False}},
        "setups/rig.yml": {"Setup": {"id": "rig", "board": "board", "toolchain": "tool",
                                     "use": [{"onboard": "leds"},
                                             {"module": "addon", "plug": {"connector": "j1"}}],
                                     "design": {"reset": {"sources": [{"power_up": True}]}}}},
        "features.yml": {"Features": [{"id": "led_feature", "name": "LEDs", "category": "io", "description": "",
                                        "capabilities": ["leds"]}]},
        "kinds.yml": {"Kinds": [{"kind": "leds", "description": "LEDs", "features": ["led_feature"]},
                                {"kind": "header", "description": "A header", "features": []}]},
        "devices.yml": {"Devices": [{"id": "led_device", "name": "An LED", "manufacturer": "Generic", "part": "LED",
                                     "feature": "led_feature", "interface": "gpio", "peripherals": ["led"]}]},
        "mezzanines/maker/family.yml": {"Mezzanines": [{
            "id": "som", "name": "A SoM", "producer": "maker", "type": "som", "connector": "b2b_custom_maker",
            "chip": "CHIP-1", "features": ["led_feature"], "devices": ["led_device"], "status": "active",
            "compatible_boards": ["board"], "default_carrier": "board"}]},
    }


def _run(documents):
    return check.check(documents=documents, root="config")


def _codes(report):
    return {row["code"] for row in report["findings"]}


def _details(report, code):
    return [row["detail"] for row in report["findings"] if row["code"] == code]


# ---------------------------------------------------------------------------
# the repository
# ---------------------------------------------------------------------------

def test_the_repository_passes():
    report = check.check()
    assert report["findings"] == [], check.render(report)
    assert report["status"] == "ok" and report["unexamined"] == [] and report["rules_skipped"] == []
    for name, s in report["entities"].items():
        assert s["records"] > 0, name
    assert report["entities"]["rig"]["files"] == report["entities"]["rig"]["records"]


def test_the_registry_names_real_entities_and_rules():
    entities = check.read_entities()
    for name, spec in entities.items():
        assert spec.get("schema") or spec.get("no_schema_because"), name
        for rel in spec.get("relations") or []:
            if "rule" in rel:
                assert rel["rule"] in check.RULES, (name, rel["rule"])
            else:
                assert rel["to"] in entities, (name, rel)
    assert set(check.RULES) == {r["rule"] for e in entities.values() for r in e.get("relations") or [] if "rule" in r}


def test_the_command_prints_the_summary_and_exits_zero(capsys):
    assert cli.main(["check", "rig", "capability"]) == 0
    out = capsys.readouterr().out
    assert out.splitlines()[0].startswith("entity") and "rig.schema.json" in out
    assert "0 findings" in out and "without a schema yet" in out
    assert cli.main(["check", "nonsense"]) == 1
    assert "unknown entity nonsense" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# documents mode: each finding is precise
# ---------------------------------------------------------------------------

def test_a_valid_catalogue_passes_and_lists_the_rules_that_need_the_tree():
    report = _run(_catalogue())
    assert report["findings"] == [], report["findings"]
    assert report["status"] == "ok" and report["unexamined"] == []
    assert set(report["rules_skipped"]) == REPO_RULES
    assert report["counts"]["rig"] == 1 and report["counts"]["peripheral"] == 2 and report["counts"]["chip"] == 1


def test_schema_violations_name_the_place():
    documents = _catalogue()
    documents["setups/rig.yml"]["Setup"]["use"][0]["extra_bits"] = {"leds": [0]}
    documents["capabilities/leds.yml"]["Capability"]["aggregation"] = "sum"
    del documents["modules/addon.yml"]["Module"]["source"]
    report = _run(documents)
    assert _codes(report) == {"schema"}
    details = _details(report, "schema")
    assert any(d.startswith("Setup/use/0") and "extra_bits" in d for d in details)
    assert any(d.startswith("Capability/aggregation") for d in details)
    assert any(d.startswith("Module") and "source" in d for d in details)


def test_unknown_and_invalid_references_are_precise():
    documents = _catalogue()
    documents["setups/rig.yml"]["Setup"]["board"] = "absent"
    documents["setups/rig.yml"]["Setup"]["use"][1]["module"] = "missing"
    documents["peripherals/led.yml"]["Peripheral"]["provides"][0]["capability"] = "unknown"
    documents["chips/maker/family.yml"]["Family"]["default_toolchains"] = ["tool[bad"]
    documents["boards/maker/family/board.yml"]["Board"]["chip"] = "absent_chip"
    documents["mezzanines/maker/family.yml"]["Mezzanines"][0]["producer"] = "nobody"
    documents["programmers.yml"]["Programmers"][0]["families"] = ["other"]
    report = _run(documents)
    unknown = _details(report, "unknown_reference")
    assert len(unknown) == 6, unknown
    for text in ("rig 'rig' board refers to absent board 'absent'",
                 "use.*.module/use/1/module refers to absent module 'missing'",
                 "provides.*.capability/provides/0/capability refers to absent capability 'unknown'",
                 "board 'board' chip refers to absent chip 'absent_chip'",
                 "mezzanine 'som' producer refers to absent producer 'nobody'",
                 "programmer 'prog' families.*/families/0 refers to absent family 'other'"):
        assert any(text in d for d in unknown), text
    assert _details(report, "invalid_reference") == ["family 'family' default_toolchains.*: 'tool[bad' is not a toolchain reference"]


def test_identities_and_files():
    documents = _catalogue()
    documents["setups/other.yml"] = copy.deepcopy(documents["setups/rig.yml"])        # the same id twice
    documents["modules/elsewhere.yml"] = copy.deepcopy(documents["modules/addon.yml"])  # id addon in elsewhere.yml
    documents["modules/elsewhere.yml"]["Module"]["id"] = "addon2"
    documents["peripherals/bad.yml"] = {"Peripheral": None}
    documents["mezzanines/maker/empty.yml"] = {"Mezzanines": None}
    documents["future.yml"] = {"Future": {"id": "new"}}
    del documents["toolchains.yml"]
    report = _run(documents)
    assert {"duplicate_identity", "filename_identity", "invalid_root", "unsupported_path", "missing_entity"} <= _codes(report)
    assert "rig 'rig' is also in config/setups/other.yml" in _details(report, "duplicate_identity")
    assert "module 'addon2' is in a file named 'elsewhere'" in _details(report, "filename_identity")
    assert report["unexamined"] == ["config/future.yml"]
    assert report["counts"]["peripheral"] == 2 and report["counts"]["module"] == 2
    assert any("toolchain" in d for d in _details(report, "missing_entity"))


def test_rules_catch_what_a_reference_cannot():
    documents = _catalogue()
    blink = documents["peripherals/blink.yml"]["Peripheral"]
    blink["driver"]["port_map"]["led"] = "pin.lamp"
    blink["driver"]["port_map"]["clk"] = "clock.fast"
    blink["driver"]["parameters"]["HZ"] = "$rate"
    blink["driver"]["port_map"]["value"] = "capability.leds.glow"
    documents["modules/addon.yml"]["Module"]["pins"]["1"] = "lamp[0]"
    documents["modules/addon.yml"]["Module"]["params"] = {"count": 1}
    documents["capabilities/leds.yml"]["Capability"]["design"]["ports"]["led"]["signal"] = "lamp"
    documents["setups/rig.yml"]["Setup"]["design"]["reset"]["sources"].append({"magic": True})
    documents["setups/rig.yml"]["Setup"]["part"] = "chip-9"
    documents["boards/maker/family/board.yml"]["Board"]["headers"][0]["type"] = "mystery"
    documents["boards/maker/family/board.yml"]["Board"]["features"] = []
    documents["boards/maker/family/board.yml"]["Board"]["banks"]["j1"]["device"]["kind"] = "mystery"
    documents["producers.yml"]["Producers"].append(dict(documents["producers.yml"]["Producers"][0], id="other", aka=["Maker"]))
    report = _run(documents)
    assert _codes(report) == {"peripheral_refs", "module_pins", "capability_refs", "rig_reset_sources",
                              "rig_chip_variant", "board_drawn", "board_kinds", "producer_names_unique"}
    kinds = _details(report, "board_kinds")
    assert any("kind 'mystery'" in d for d in kinds) and any("led_feature" in d and "none is listed" in d for d in kinds)
    assert "'Maker' also names producer" in _details(report, "producer_names_unique")[0]
    refs = _details(report, "peripheral_refs")
    assert len(refs) == 4 and all("blink" in d for d in refs)
    assert any("pin.lamp" in d for d in refs) and any("clock.fast" in d for d in refs)
    assert any("$rate" in d for d in refs) and any("no signal glow" in d for d in refs)
    assert len(_details(report, "module_pins")) == 2
    assert _details(report, "capability_refs") == ["capability 'leds' port led: signal lamp is not one of its signals"]
    assert "rig 'rig': reset source 'magic' is not one of bank, pin, pll_lock, power_up" in _details(report, "rig_reset_sources")[0]
    assert "part 'chip-9' is not one of the board's chips (CHIP-1)" in _details(report, "rig_chip_variant")[0]
    assert "'mystery'" in _details(report, "board_drawn")[0]


def test_a_chip_variant_matches_by_id_or_name():
    documents = _catalogue()
    documents["setups/rig.yml"]["Setup"]["part"] = "chip-1"
    assert _run(documents)["findings"] == []
    board = documents["boards/maker/family/board.yml"]["Board"]
    del board["chip"]
    board["chips"] = [{"id": "CHIP-1", "name": "small"}]
    documents["setups/rig.yml"]["Setup"]["part"] = "small"
    documents["setups/rig.yml"]["Setup"]["parts"] = ["small"]
    assert _run(documents)["findings"] == []


def test_a_yaml_file_that_does_not_parse_is_a_finding(tmp_path, monkeypatch):
    report = check.check()
    assert report["status"] == "ok"
    monkeypatch.setattr(check, "repository_documents",
                        lambda entities, repo: ({}, [("config/setups/broken.yml", "YAML parse error")]))
    report = check.check()
    assert any(row["code"] == "yaml" and row["path"] == "config/setups/broken.yml" for row in report["findings"])


def test_the_report_is_bounded(monkeypatch):
    documents = _catalogue()
    documents["setups/rig.yml"]["Setup"]["use"] = [{"module": "missing"}] * 20
    monkeypatch.setattr(check, "MAX_FINDINGS", 2)
    report = _run(documents)
    assert len(report["findings"]) == 3
    assert any(row["code"] == "finding_limit" for row in report["findings"])


def test_a_missing_library_is_one_clear_error(monkeypatch):
    import builtins
    real_import = builtins.__import__

    def no_jsonschema(name, *args, **kwargs):
        if name == "jsonschema":
            raise ImportError("gone")
        return real_import(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", no_jsonschema)
    with pytest.raises(check.CheckError, match="jsonschema is not installed"):
        check.check()
