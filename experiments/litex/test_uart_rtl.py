"""Exercise generated UART, native echo, and APS firmware through unifpga sim."""

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
class GeneratedUARTRTLTests(unittest.TestCase):
    def test_standalone_and_native_echo(self):
        with tempfile.TemporaryDirectory() as scratch:
            scratch = Path(scratch)
            export = scratch / "export"
            generated = subprocess.run(
                [sys.executable, str(EXPERIMENT / "probe.py"),
                 "--litex-root", LITEX_ROOT, "--output-root", str(export),
                 "--component", "rs232-phy", "--clk-freq", "10000000",
                 "--baudrate", "115200"],
                text=True, capture_output=True, timeout=30, check=False)
            self.assertEqual(generated.returncode, 0, generated.stderr)

            env = os.environ.copy()
            env["PATH"] = os.pathsep.join((str(Path(IVERILOG).parent),
                                           str(Path(VVP).parent), env.get("PATH", "")))
            for design, top, expected in (
                    (EXPERIMENT / "uart_design", "tb_phy",
                     "PASS generated LiteX UART RX overflow contract"),
                    (EXPERIMENT / "uart_design", "tb",
                     "PASS native byte echo + generated LiteX UART PHY"),
                    (ROOT / "designs" / "5_5_aps", "tb_litex_uart_bridge",
                     "APS_LITEX_UART_BRIDGE_PASS unsupported offset and request withdrawal"),
                    (ROOT / "designs" / "5_5_aps", "tb_litex_uart",
                     "APS_LITEX_UART_PASS timer LED and two ordered UART bytes")):
                result = subprocess.run(
                    [sys.executable, str(ROOT / "unifpga"), "sim",
                     str(design), "--component-export",
                     str(export / "manifest.json"), "--tb-top", top,
                     "--output-dir", str(scratch / top), "--no-wave"],
                    text=True, capture_output=True, timeout=60, check=False,
                    env=env)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn(expected, result.stdout)
                snapshots = list((scratch / top).glob(
                    "component-exports-*/0/litex_rs232_phy.v"))
                self.assertEqual(len(snapshots), 1)
                self.assertEqual(snapshots[0].read_bytes(),
                                 (export / "litex_rs232_phy.v").read_bytes())

            missing = subprocess.run(
                [sys.executable, str(ROOT / "unifpga"), "sim",
                 str(ROOT / "designs" / "5_5_aps"), "--tb-top", "tb_litex_uart",
                 "--output-dir", str(scratch / "missing-phy"), "--no-wave"],
                text=True, capture_output=True, timeout=60, check=False,
                env=env)
            self.assertNotEqual(missing.returncode, 0)
            self.assertIn("litex_rs232_phy", missing.stdout + missing.stderr)


if __name__ == "__main__":
    unittest.main()
