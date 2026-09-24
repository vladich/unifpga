"""Small controlled corpus for the pinned Slang experiment."""

import json
import pathlib
import subprocess
import sys
import tempfile
import unittest

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
        self.assertEqual(json.dumps(result, sort_keys=True),
                         json.dumps(probe.run(FIXTURES, FIXTURES / "good.json"), sort_keys=True))

    def test_malformed_source_is_diagnostic_not_success(self):
        result = probe.run(FIXTURES, FIXTURES / "bad.json")
        self.assertFalse(result["accepted"])
        self.assertTrue(result["parse_diagnostics"])
        self.assertEqual(result["parse_diagnostics"][0]["location"]["path"], "bad.sv")

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


if __name__ == "__main__":
    unittest.main()
