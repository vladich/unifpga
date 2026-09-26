"""Independently simulate generated LiteX RTL alone and with unifpga RTL."""

import os
import re
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
    def test_standalone_composed_and_virtual_device_flows(self):
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
                                  ("tb", "PASS unifpga PDM decoder + generated LiteX FIFO"),
                                  ("tb_virtual", "PASS virtual-device PDM/FIFO adapter")):
                simulation = subprocess.run(
                    [sys.executable, str(ROOT / "unifpga"), "sim",
                     str(EXPERIMENT / "design"), "--component-export",
                     str(export / "manifest.json"), "--tb-top", top,
                     "--output-dir", str(scratch / top), "--no-wave"],
                    text=True, capture_output=True, timeout=60, check=False,
                    env=env)
                self.assertEqual(simulation.returncode, 0, simulation.stdout + simulation.stderr)
                self.assertIn(expected, simulation.stdout)
                snapshots = list((scratch / top).glob("component-exports-*/0/litex_sync_fifo.v"))
                self.assertEqual(len(snapshots), 1)
                self.assertEqual(snapshots[0].read_bytes(), (export / "litex_sync_fifo.v").read_bytes())
                self.assertIn(str(snapshots[0]), (scratch / top / "log.txt").read_text())

            uart_export = scratch / "uart-export"
            uart = subprocess.run(
                [sys.executable, str(EXPERIMENT / "probe.py"),
                 "--litex-root", LITEX_ROOT, "--output-root", str(uart_export),
                 "--component", "rs232-phy", "--clk-freq", "24000000",
                 "--baudrate", "115200"],
                text=True, capture_output=True, timeout=30, check=False)
            self.assertEqual(uart.returncode, 0, uart.stderr)
            for top, expected in (
                    ("tb_packetizer", "PASS native sample-to-UART packetizer backpressure and reset"),
                    ("tb_pdm_uart", "PASS PDM decoder + LiteX FIFO + native packetizer + LiteX UART")):
                simulation = subprocess.run(
                    [sys.executable, str(ROOT / "unifpga"), "sim",
                     str(EXPERIMENT / "design"),
                     "--component-export", str(export / "manifest.json"),
                     "--component-export", str(uart_export / "manifest.json"),
                     "--tb-top", top, "--output-dir", str(scratch / top),
                     "--no-wave"],
                    text=True, capture_output=True, timeout=60, check=False,
                    env=env)
                self.assertEqual(simulation.returncode, 0, simulation.stdout + simulation.stderr)
                self.assertIn(expected, simulation.stdout)
                for index, (name, source) in enumerate((
                        ("litex_sync_fifo.v", export),
                        ("litex_rs232_phy.v", uart_export))):
                    snapshots = list((scratch / top).glob(
                        "component-exports-*/{}/{}".format(index, name)))
                    self.assertEqual(len(snapshots), 1)
                    self.assertEqual(snapshots[0].read_bytes(), (source / name).read_bytes())

            design = scratch / "design"
            design.mkdir()
            for name in ("design_top.sv", "tb.sv", "fileset.yml"):
                shutil.copy2(EXPERIMENT / "design" / name, design / name)
            cfg = "tang_nano_9k_hdmi_no_tm1638"
            prepared = subprocess.run(
                [sys.executable, str(ROOT / "unifpga"), "prepare", str(design),
                 "-b", cfg,
                 "--component-export", str(export / "manifest.json"),
                 "--component-export", str(uart_export / "manifest.json")],
                text=True, capture_output=True, timeout=60, check=False, env=env)
            self.assertEqual(prepared.returncode, 0, prepared.stdout + prepared.stderr)
            output = design / "run" / cfg
            script = (output / "build.tcl").read_text()
            self.assertIn("litex_sync_fifo.v", script)
            self.assertLess(script.index("litex_sync_fifo.v"), script.index("design_top.sv"))
            self.assertIn("litex_rs232_phy.v", script)
            self.assertLess(script.index("litex_rs232_phy.v"), script.index("design_top.sv"))
            self.assertIn("pdm_mic_decoder.sv", script)
            snapshots = list(output.glob("component-exports-*/0/manifest.json"))
            self.assertEqual(len(snapshots), 1)
            self.assertEqual(snapshots[0].read_bytes(), (export / "manifest.json").read_bytes())
            uart_snapshots = list(output.glob("component-exports-*/1/manifest.json"))
            self.assertEqual(len(uart_snapshots), 1)
            self.assertEqual(uart_snapshots[0].read_bytes(),
                             (uart_export / "manifest.json").read_bytes())

            # Elaborate the exact sources our Gowin project lists. The vendor
            # primitives are simulation-only stand-ins; LiteX is not the builder.
            sources = re.findall(r"^add_file \{([^}]+)\}$", script, re.MULTILINE)
            self.assertTrue(sources)
            stubs = ROOT / "rtl" / "sim" / "vendor_stubs.sv"
            source_dirs = sorted({str(Path(path).parent) for path in sources + [str(stubs)]})
            compile_cmd = [IVERILOG, "-g2012", "-s", "top", "-o", str(scratch / "top.vvp")]
            compile_cmd += [arg for directory in source_dirs for arg in ("-I", directory)]
            compile_cmd += sources + [str(stubs)]
            compiled = subprocess.run(compile_cmd, text=True, capture_output=True,
                                      timeout=60, check=False, env=env)
            self.assertEqual(compiled.returncode, 0, compiled.stdout + compiled.stderr)


if __name__ == "__main__":
    unittest.main()
