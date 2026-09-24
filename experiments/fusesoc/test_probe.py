"""Run with the independently locked experiment environment."""

import shutil
import tempfile
import unittest
from pathlib import Path

from probe import CORE, FILES, FIXTURES, PULSE, ProbeError, run_probe


class FuseSoCProbeTests(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory()
        self.addCleanup(self.scratch.cleanup)
        self.root = Path(self.scratch.name)

    def test_resolved_cores_and_exported_files(self):
        report = run_probe(self.root / "build")
        self.assertEqual(report["dependencies"], {PULSE: [], CORE: [PULSE]})
        self.assertEqual([(item["core"], item["source"], item["type"])
                          for item in report["files"]], list(FILES))
        self.assertEqual(report["top"], "top")
        self.assertEqual(report["width_default"], 4)
        self.assertEqual(report["flow_tool"], "icarus")
        self.assertFalse(report["asset_listed_in_makefile"])
        self.assertFalse(report["tool_executed"])
        self.assertEqual(len(report["core_sha256"]), 2)

    def test_missing_dependency_rejected(self):
        fixture = self.root / "fixtures"
        shutil.copytree(FIXTURES, fixture)
        (fixture / "pulse.core").unlink()
        with self.assertRaisesRegex(ProbeError, "rejected fixture"):
            run_probe(self.root / "build", fixture)

    def test_unknown_capi2_field_rejected(self):
        fixture = self.root / "fixtures"
        shutil.copytree(FIXTURES, fixture)
        with (fixture / "composed.core").open("a", encoding="utf-8") as stream:
            stream.write("unsupported_unifpga_extension: true\n")
        with self.assertRaisesRegex(ProbeError, "rejected fixture"):
            run_probe(self.root / "build", fixture)

    def test_work_root_must_be_empty(self):
        build = self.root / "build"
        build.mkdir()
        (build / "preexisting.txt").write_text("keep", encoding="utf-8")
        with self.assertRaisesRegex(ProbeError, "must be empty"):
            run_probe(build)
        self.assertEqual((build / "preexisting.txt").read_text(), "keep")


if __name__ == "__main__":
    unittest.main()
