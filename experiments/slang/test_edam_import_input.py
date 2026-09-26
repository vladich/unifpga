"""EDAM source closure and the Slang request derived from it."""

import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest import mock

import yaml

from tools import edam_import_input, sv_import_candidate


ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "experiments" / "fusesoc" / "fixtures"
PULSE = "unifpga:probe:pulse:1.0.0"
COMPOSED = "unifpga:probe:composed:1.0.0"


class EdamImportInputTests(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory()
        self.addCleanup(self.scratch.cleanup)
        self.work = Path(self.scratch.name) / "build"
        self.work.mkdir()
        files = (
            (PULSE, "rtl/pulse.sv", "systemVerilogSource"),
            (COMPOSED, "rtl/top.sv", "systemVerilogSource"),
            (COMPOSED, "data/table.hex", "user"),
        )
        self.entries = []
        for core, source, kind in files:
            rel = "src/{}/{}".format(core.replace(":", "_"), source)
            target = self.work / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(FIXTURES / source, target)
            self.entries.append({"name": rel, "core": core, "file_type": kind})
        self.document = {
            "version": "0.2.1", "name": "composed", "toplevel": "top",
            "dependencies": {PULSE: [], COMPOSED: [PULSE]},
            "cores": {core: {"core_file": os.path.relpath(FIXTURES / filename, self.work),
                             "dependencies": deps, "license": None}
                      for core, filename, deps in ((PULSE, "pulse.core", []),
                                                   (COMPOSED, "composed.core", [PULSE]))},
            "parameters": {"WIDTH": {"datatype": "int", "paramtype": "vlogparam",
                                     "default": 4}},
            "filters": [], "flow_options": {"tool": "icarus"},
            "hooks": {}, "files": self.entries, "vpi": [],
        }
        self.edam = self.work / "composed.eda.yml"
        self.write_edam()

    def write_edam(self):
        self.edam.write_text(yaml.safe_dump(self.document, sort_keys=False), encoding="utf-8")

    def import_input(self):
        return edam_import_input.import_edam(self.work, self.edam, FIXTURES, "separate")

    def test_resolved_files_assets_and_top_override_reach_frontend(self):
        bundle = self.import_input()
        self.assertEqual([item["kind"] for item in bundle["files"]],
                         ["source", "source", "asset"])
        self.assertEqual(bundle["asset_placement"], "unverified")
        self.assertEqual(bundle["dependencies"][COMPOSED], [PULSE])
        self.assertEqual(bundle["slang_request"]["top_parameter_overrides"], ["WIDTH=4"])
        request = Path(self.scratch.name) / "request.json"
        request.write_text(json.dumps(bundle["slang_request"]), encoding="utf-8")
        candidate = sv_import_candidate.run(self.work, request)
        self.assertEqual(candidate["state"], "needs_semantic_review")
        self.assertEqual(candidate["top"]["ports"][1]["evaluated_bit_width"], 4)
        self.assertEqual(len(candidate["source_files"]), 2)
        combined = edam_import_input.verified_candidate(
            self.work, self.edam, FIXTURES, "separate", self.scratch.name)
        self.assertEqual(combined["source_closure"],
                         "checked_before_and_after_elaboration")
        self.assertEqual(combined["rtl_candidate"]["state"], "needs_semantic_review")
        self.assertEqual(combined["edam_input"]["files"][2]["kind"], "asset")
        self.assertEqual(len(combined["candidate_sha256"]), 64)

    def test_asset_change_during_elaboration_rejects_combined_candidate(self):
        actual_run = edam_import_input.probe.run

        def mutate_after_parse(*args):
            result = actual_run(*args)
            (self.work / self.entries[2]["name"]).write_text("changed\n", encoding="utf-8")
            return result

        with mock.patch.object(edam_import_input.probe, "run", side_effect=mutate_after_parse):
            with self.assertRaisesRegex(edam_import_input.EdamImportError,
                                        "changed during elaboration"):
                edam_import_input.verified_candidate(
                    self.work, self.edam, FIXTURES, "separate", self.scratch.name)

    def test_declared_but_unused_include_is_preserved(self):
        rel = "src/unifpga_probe_composed_1.0.0/rtl/optional.svh"
        path = self.work / rel
        path.write_text("`define OPTIONAL 1\n", encoding="utf-8")
        self.document["files"].append({"name": rel, "core": COMPOSED,
                                       "file_type": "systemVerilogSource",
                                       "is_include_file": True})
        self.write_edam()
        combined = edam_import_input.verified_candidate(
            self.work, self.edam, FIXTURES, "separate", self.scratch.name)
        self.assertEqual(combined["unused_declared_includes"], [rel])
        self.assertEqual(combined["edam_input"]["files"][-1]["kind"], "include")

    def test_frontend_read_of_undeclared_include_is_rejected(self):
        source = self.work / self.entries[1]["name"]
        source.write_text(source.read_text() + '\n`include "hidden.svh"\n', encoding="utf-8")
        (source.parent / "hidden.svh").write_text("`define HIDDEN 1\n", encoding="utf-8")
        with self.assertRaisesRegex(edam_import_input.EdamImportError,
                                    "differ from the EDAM HDL/include closure"):
            edam_import_input.verified_candidate(
                self.work, self.edam, FIXTURES, "separate", self.scratch.name)

    def test_rejects_traversal_and_symlinked_export(self):
        original = self.document["files"][0]["name"]
        self.document["files"][0]["name"] = "../pulse.sv"
        self.write_edam()
        with self.assertRaisesRegex(edam_import_input.EdamImportError, "invalid source path"):
            self.import_input()
        self.document["files"][0]["name"] = original
        path = self.work / original
        path.unlink()
        path.symlink_to(FIXTURES / "rtl/pulse.sv")
        self.write_edam()
        with self.assertRaisesRegex(edam_import_input.EdamImportError, "symlink"):
            self.import_input()

    def test_rejects_lost_semantics_and_dependency_cycle(self):
        self.document["hooks"] = {"pre_build": ["command"]}
        self.write_edam()
        with self.assertRaisesRegex(edam_import_input.EdamImportError, "hooks"):
            self.import_input()
        self.document["hooks"] = {}
        self.document["flow_options"]["defines"] = ["FEATURE"]
        self.write_edam()
        with self.assertRaisesRegex(edam_import_input.EdamImportError, "flow options"):
            self.import_input()
        del self.document["flow_options"]["defines"]
        self.document["files"][2]["file_type"] = "tclSource"
        self.write_edam()
        with self.assertRaisesRegex(edam_import_input.EdamImportError, "file type"):
            self.import_input()
        self.document["files"][2]["file_type"] = "user"
        self.document["dependencies"][PULSE] = [COMPOSED]
        self.document["cores"][PULSE]["dependencies"] = [COMPOSED]
        self.write_edam()
        with self.assertRaisesRegex(edam_import_input.EdamImportError, "cycle"):
            self.import_input()

    def test_rejects_unsafe_core_and_duplicate_yaml_key(self):
        self.document["cores"][PULSE]["core_file"] = "../../missing.core"
        self.write_edam()
        with self.assertRaisesRegex(edam_import_input.EdamImportError, "outside"):
            self.import_input()
        self.write_edam()
        self.edam.write_text(self.edam.read_text() + "version: 0.2.1\n", encoding="utf-8")
        with self.assertRaisesRegex(edam_import_input.EdamImportError, "duplicate key"):
            self.import_input()


if __name__ == "__main__":
    unittest.main()
