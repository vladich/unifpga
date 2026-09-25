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
            for top in ("tb_fifo", "tb_pdm_capture"):
                binary = scratch / top
                compile_run = subprocess.run(
                    [IVERILOG, "-g2012", "-Wall", "-s", top, "-o", str(binary),
                     str(export / "litex_sync_fifo.v"),
                     str(ROOT / "rtl/peripherals/pdm_mic_decoder.sv"),
                     str(EXPERIMENT / "pdm_fifo_capture.sv"),
                     str(EXPERIMENT / "tb_pdm_fifo.sv")],
                    text=True, capture_output=True, timeout=30, check=False)
                self.assertEqual(compile_run.returncode, 0, compile_run.stderr)
                simulation = subprocess.run(
                    [VVP, str(binary)], text=True, capture_output=True,
                    timeout=30, check=False)
                self.assertEqual(simulation.returncode, 0, simulation.stderr)
                self.assertIn("PASS ", simulation.stdout)


if __name__ == "__main__":
    unittest.main()
