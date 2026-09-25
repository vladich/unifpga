"""Independently simulate generated LiteX RTL alone and with unifpga RTL."""

import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
EXPERIMENT = Path(__file__).resolve().parent
LITEX_ROOT = os.environ.get("LITEX_ROOT")
IVERILOG = os.environ.get("IVERILOG") or shutil.which("iverilog")
VVP = os.environ.get("VVP") or shutil.which("vvp")


@unittest.skipUnless(LITEX_ROOT and IVERILOG and VVP,
                     "set LITEX_ROOT and install Icarus Verilog (or set IVERILOG/VVP)")
class GeneratedRTLTests(unittest.TestCase):
    def test_standalone_and_mixed_source_simulation(self):
        with tempfile.TemporaryDirectory() as scratch:
            scratch = Path(scratch)
            export = scratch / "export"
            run = subprocess.run(
                [sys.executable, str(EXPERIMENT / "probe.py"),
                 "--litex-root", LITEX_ROOT, "--output-root", str(export),
                 "--width", "16", "--depth", "4"],
                text=True, capture_output=True, timeout=30, check=False)
            self.assertEqual(run.returncode, 0, run.stderr)
            env = os.environ.copy()
            env["PATH"] = os.pathsep.join((str(Path(IVERILOG).parent),
                                           str(Path(VVP).parent), env.get("PATH", "")))
            for top, expected in (("tb_fifo", "PASS standalone generated LiteX FIFO"),
                                  ("tb", "PASS unifpga PDM decoder + generated LiteX FIFO")):
                simulation = subprocess.run(
                    [sys.executable, str(ROOT / "unifpga"), "sim",
                     str(EXPERIMENT / "design"), "--component-export",
                     str(export / "manifest.json"), "--tb-top", top,
                     "--output-dir", str(scratch / top), "--no-wave"],
                    text=True, capture_output=True, timeout=60, check=False,
                    env=env)
                self.assertEqual(simulation.returncode, 0, simulation.stdout + simulation.stderr)
                self.assertIn(expected, simulation.stdout)


if __name__ == "__main__":
    unittest.main()
