"""Small controlled corpus for the pinned Slang experiment."""

import json
import pathlib
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import probe


FIXTURES = pathlib.Path(__file__).parent / "fixtures"


class ProbeTests(unittest.TestCase):
    def test_packages_interface_include_macro_and_top(self):
        result = probe.run(FIXTURES, FIXTURES / "good.json")
        self.assertTrue(result["accepted"], result)
        self.assertEqual(result["elaborated_tops"], ["top"])
        self.assertEqual(result["parse_diagnostics"], [])
        self.assertEqual(result["semantic_diagnostics"], [])
        self.assertEqual([item["path"] for item in result["sources"]],
                         ["numbers_pkg.sv", "stream_if.sv", "top.sv"])
        self.assertIn("includes/config.svh", [item["path"] for item in result["read_files"]])
        self.assertEqual(result["elaboration"]["schema"], probe.ELABORATION_SCHEMA)
        self.assertEqual(result["elaboration"]["semantic_completeness"], "unproven")
        self.assertEqual(json.dumps(result, sort_keys=True),
                         json.dumps(probe.run(FIXTURES, FIXTURES / "good.json"), sort_keys=True))

    def test_malformed_source_is_diagnostic_not_success(self):
        result = probe.run(FIXTURES, FIXTURES / "bad.json")
        self.assertFalse(result["accepted"])
        self.assertTrue(result["parse_diagnostics"])
        self.assertIsNone(result["elaboration"])
        self.assertEqual(result["parse_diagnostics"][0]["location"]["path"], "bad.sv")

    def test_elaborated_graph_covers_generate_types_interfaces_and_connections(self):
        result = probe.run(FIXTURES, FIXTURES / "graph.json")
        self.assertTrue(result["accepted"], result)
        graph = result["elaboration"]
        self.assertEqual(graph["projection_status"], "complete")
        nodes = {item["path"]: item for item in graph["instances"]}
        self.assertEqual(len(nodes), 6)
        self.assertEqual(nodes["graph_top.bank[0].leaf"]["parent_instance"], "graph_top")
        self.assertEqual(nodes["graph_top.bank[1].leaf"]["parent_instance"], "graph_top")
        leaf = nodes["graph_top.bank[0].leaf"]
        self.assertEqual(leaf["ports"][0]["evaluated_bit_width"], 8)
        self.assertEqual(leaf["parameters"][0]["kind"], "type")
        self.assertEqual(leaf["parameters"][0]["type"], "logic[7:0]")
        self.assertIsNone(leaf["parameters"][0]["evaluated_value"])
        self.assertTrue(leaf["parameters"][1]["is_overridden"])
        self.assertEqual(leaf["connections"][0]["expression_kind"], "BinaryOp")
        self.assertIsNone(leaf["connections"][0]["direct_symbol_reference"])
        self.assertEqual(leaf["connections"][0]["source_range"]["path"], "graph.sv")
        legacy = nodes["graph_top.legacy"]
        self.assertEqual(legacy["ports"][1]["direction"], "output")
        self.assertEqual(legacy["connections"][0]["direct_symbol_reference"], "graph_top.clk")
        self.assertEqual(legacy["connections"][1]["direct_symbol_reference"], "graph_top.q")
        consumer = nodes["graph_top.consumer"]
        self.assertEqual(consumer["ports"][0]["interface_definition"], "graph_bus")
        self.assertEqual(consumer["ports"][0]["modport"], "source")
        self.assertEqual(consumer["connections"][0]["interface_instance"], "graph_top.link")
        self.assertEqual(consumer["connections"][0]["modport"], "source")
        self.assertEqual(json.dumps(graph, sort_keys=True),
                         json.dumps(probe.run(FIXTURES, FIXTURES / "graph.json")["elaboration"],
                                    sort_keys=True))

    def test_elaborated_graph_limit_fails_closed(self):
        with mock.patch.object(probe, "MAX_GRAPH_INSTANCES", 1):
            with self.assertRaisesRegex(probe.ProbeError, "instance limit"):
                probe.run(FIXTURES, FIXTURES / "graph.json")

    def test_instance_arrays_are_projected_and_primitives_are_explicit_gaps(self):
        result = probe.run(FIXTURES, FIXTURES / "array.json")
        self.assertTrue(result["accepted"], result)
        graph = result["elaboration"]
        self.assertEqual(graph["projection_status"], "partial")
        nodes = {item["path"]: item for item in graph["instances"]}
        self.assertEqual(set(nodes), {"array_top", "array_top.lane[0]", "array_top.lane[1]"})
        self.assertEqual(nodes["array_top.lane[0]"]["name"], "lane[0]")
        self.assertEqual(nodes["array_top.lane[1]"]["name"], "lane[1]")
        self.assertEqual(graph["findings"][0]["code"], "unprojected_instance_kind")
        self.assertEqual(graph["findings"][0]["symbol_kind"], "PrimitiveInstance")

    def test_v2_top_override_changes_elaborated_width(self):
        result = probe.run(FIXTURES, FIXTURES / "parameter.json")
        self.assertTrue(result["accepted"], result)
        self.assertEqual(result["schema"], probe.SCHEMA_V2)
        top = result["elaboration"]["instances"][0]
        self.assertEqual(top["ports"][0]["evaluated_bit_width"], 4)
        self.assertEqual(top["parameters"][0]["evaluated_value"], "4")
        self.assertTrue(top["parameters"][0]["is_overridden"])
        self.assertEqual(top["parameters"][0]["override_origin"], "request")

    def test_v2_rejects_duplicate_and_malformed_overrides(self):
        with tempfile.TemporaryDirectory() as name:
            path = pathlib.Path(name) / "request.json"
            request = json.loads((FIXTURES / "parameter.json").read_text())
            request["top_parameter_overrides"] = ["WIDTH=4", "WIDTH=8"]
            path.write_text(json.dumps(request))
            with self.assertRaisesRegex(probe.ProbeError, "duplicate top parameter"):
                probe.load_request(FIXTURES, path)
            request["top_parameter_overrides"] = ["WIDTH="]
            path.write_text(json.dumps(request))
            with self.assertRaisesRegex(probe.ProbeError, "invalid top parameter"):
                probe.load_request(FIXTURES, path)
            request["schema"] = probe.SCHEMA
            path.write_text(json.dumps(request))
            with self.assertRaisesRegex(probe.ProbeError, "request fields"):
                probe.load_request(FIXTURES, path)

    def test_v2_rejects_unknown_override_ignored_by_slang(self):
        with tempfile.TemporaryDirectory() as name:
            path = pathlib.Path(name) / "request.json"
            request = json.loads((FIXTURES / "parameter.json").read_text())
            request["top_parameter_overrides"] = ["MISSING=4"]
            path.write_text(json.dumps(request))
            with self.assertRaisesRegex(probe.ProbeError, "not a parameter"):
                probe.run(FIXTURES, path)
            request["top_parameter_overrides"] = ["INTERNAL=4"]
            path.write_text(json.dumps(request))
            with self.assertRaisesRegex(probe.ProbeError, "not a parameter"):
                probe.run(FIXTURES, path)

    def test_single_compilation_unit_is_explicit(self):
        with tempfile.TemporaryDirectory() as name:
            path = pathlib.Path(name) / "request.json"
            request = json.loads((FIXTURES / "good.json").read_text())
            request["compilation_unit"] = "single"
            path.write_text(json.dumps(request))
            result = probe.run(FIXTURES, path)
            self.assertTrue(result["accepted"], result)
            self.assertEqual(result["compilation_unit"], "single")

    def test_request_rejects_traversal_and_duplicate_sources(self):
        with tempfile.TemporaryDirectory() as name:
            path = pathlib.Path(name) / "request.json"
            request = json.loads((FIXTURES / "good.json").read_text())
            request["sources"] = ["../bad.sv"]
            path.write_text(json.dumps(request))
            with self.assertRaisesRegex(probe.ProbeError, "invalid source path"):
                probe.load_request(FIXTURES, path)
            request["sources"] = ["top.sv", "top.sv"]
            path.write_text(json.dumps(request))
            with self.assertRaisesRegex(probe.ProbeError, "duplicate source"):
                probe.load_request(FIXTURES, path)

    def test_request_rejects_duplicate_fields_and_macros(self):
        with tempfile.TemporaryDirectory() as name:
            path = pathlib.Path(name) / "request.json"
            request = (FIXTURES / "good.json").read_text()
            path.write_text(request.replace('"top": "top"', '"top": "top", "top": "other"'))
            with self.assertRaisesRegex(probe.ProbeError, "duplicate request field"):
                probe.load_request(FIXTURES, path)
            parsed = json.loads(request)
            parsed["defines"] = ["FEATURE=1", "FEATURE=0"]
            path.write_text(json.dumps(parsed))
            with self.assertRaisesRegex(probe.ProbeError, "duplicate macro"):
                probe.load_request(FIXTURES, path)

    def test_requested_top_must_exist(self):
        with tempfile.TemporaryDirectory() as name:
            path = pathlib.Path(name) / "request.json"
            request = json.loads((FIXTURES / "good.json").read_text())
            request["top"] = "missing_top"
            path.write_text(json.dumps(request))
            result = probe.run(FIXTURES, path)
            self.assertFalse(result["accepted"])
            self.assertTrue(result["semantic_diagnostics"])

    def test_cli_failure_is_nonzero_and_machine_readable(self):
        completed = subprocess.run(
            [sys.executable, str(pathlib.Path(__file__).parent / "probe.py"),
             "--root", str(FIXTURES), "--request", str(FIXTURES / "bad.json")],
            text=True, capture_output=True, check=False)
        self.assertEqual(completed.returncode, 3)
        self.assertFalse(json.loads(completed.stdout)["accepted"])

    def test_cli_emits_bounded_elaboration_facts(self):
        completed = subprocess.run(
            [sys.executable, str(pathlib.Path(__file__).parent / "probe.py"),
             "--root", str(FIXTURES), "--request", str(FIXTURES / "graph.json")],
            text=True, capture_output=True, check=False)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = json.loads(completed.stdout)
        self.assertTrue(result["accepted"])
        self.assertEqual(len(result["elaboration"]["instances"]), 6)
        self.assertLessEqual(len(completed.stdout.encode("utf-8")), probe.MAX_GRAPH_BYTES)


if __name__ == "__main__":
    unittest.main()
