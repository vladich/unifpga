"""Import-candidate behavior on the pinned SystemVerilog frontend corpus."""

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from tools import sv_import_candidate as candidate


ROOT = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).parent / "fixtures"


class ImportCandidateTests(unittest.TestCase):
    def test_top_interface_has_provenance_but_no_inferred_roles(self):
        report = candidate.run(FIXTURES, FIXTURES / "good.json")
        self.assertEqual(report["state"], "needs_semantic_review")
        self.assertEqual(report["source_files"][0]["path"], "includes/config.svh")
        self.assertEqual(report["top"]["name"], "top")
        self.assertEqual(report["source_scope"], "frontend_read_files_only")
        self.assertTrue(report["candidate_sha256"])
        self.assertEqual({port["semantic_role"] for port in report["top"]["ports"]}, {None})
        self.assertIn("virtual_device_binding", report["review_topics"])
        self.assertEqual(report, candidate.run(FIXTURES, FIXTURES / "good.json"))

    def test_plain_top_and_parameter_override_are_reviewable(self):
        report = candidate.run(FIXTURES, FIXTURES / "parameter.json")
        self.assertEqual(report["state"], "needs_semantic_review")
        self.assertEqual([port["evaluated_bit_width"] for port in report["top"]["ports"]],
                         [4, 4])
        self.assertEqual(report["top"]["parameters"][0]["evaluated_value"], "4")
        with tempfile.TemporaryDirectory() as scratch:
            request = json.loads((FIXTURES / "parameter.json").read_text())
            request["top_parameter_overrides"] = ["WIDTH=8"]
            path = Path(scratch) / "request.json"
            path.write_text(json.dumps(request), encoding="utf-8")
            wider = candidate.run(FIXTURES, path)
        self.assertEqual([port["evaluated_bit_width"] for port in wider["top"]["ports"]],
                         [8, 8])
        self.assertNotEqual(wider["candidate_sha256"], report["candidate_sha256"])

    def test_partial_projection_and_interface_port_are_explicit_blockers(self):
        partial = candidate.run(FIXTURES, FIXTURES / "array.json")
        self.assertEqual(partial["state"], "structurally_blocked")
        self.assertIn("partial_elaboration_projection",
                      [item["code"] for item in partial["blockers"]])
        graph = candidate.run(FIXTURES, FIXTURES / "graph.json")
        self.assertEqual(graph["state"], "needs_semantic_review")
        self.assertIn("graph_bus", graph["hierarchy"]["definitions"])
        self.assertEqual(graph["hierarchy"]["instance_count"], 6)
        with tempfile.TemporaryDirectory() as scratch:
            request = json.loads((FIXTURES / "graph.json").read_text())
            request["top"] = "graph_consumer"
            path = Path(scratch) / "request.json"
            path.write_text(json.dumps(request), encoding="utf-8")
            interface_top = candidate.run(FIXTURES, path)
        self.assertEqual(interface_top["state"], "structurally_blocked")
        self.assertEqual(interface_top["top"]["ports"][0]["shape"], "interface")

    def test_rejected_frontend_and_output_limit_fail_closed(self):
        with self.assertRaisesRegex(candidate.CandidateError, "did not accept"):
            candidate.run(FIXTURES, FIXTURES / "bad.json")
        with mock.patch.object(candidate, "MAX_REPORT_BYTES", 100):
            with self.assertRaisesRegex(candidate.CandidateError, "exceeds"):
                candidate.run(FIXTURES, FIXTURES / "parameter.json")

    def test_cli_emits_machine_readable_candidate_and_failure(self):
        command = [sys.executable, "-m", "tools.sv_import_candidate",
                   "--root", str(FIXTURES), "--request"]
        good = subprocess.run(command + [str(FIXTURES / "parameter.json")],
                              cwd=ROOT, text=True, capture_output=True, check=False)
        self.assertEqual(good.returncode, 0, good.stderr)
        self.assertEqual(json.loads(good.stdout)["state"], "needs_semantic_review")
        bad = subprocess.run(command + [str(FIXTURES / "bad.json")],
                             cwd=ROOT, text=True, capture_output=True, check=False)
        self.assertEqual(bad.returncode, 2)
        self.assertIn("error", json.loads(bad.stdout))


if __name__ == "__main__":
    unittest.main()
