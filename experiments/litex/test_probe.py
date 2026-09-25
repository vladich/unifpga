"""Run with the locked LiteX export probe environment and LITEX_ROOT."""

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from probe import validate_litex


PROBE = Path(__file__).resolve().parent / "probe.py"
LITEX_ROOT = os.environ.get("LITEX_ROOT")


@unittest.skipUnless(LITEX_ROOT, "set LITEX_ROOT to the pinned LiteX checkout")
class LiteXProbeTests(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory()
        self.addCleanup(self.scratch.cleanup)
        self.root = Path(self.scratch.name)

    def _run(self, output, *extra, litex_root=LITEX_ROOT):
        return subprocess.run(
            [sys.executable, str(PROBE), "--litex-root", str(litex_root),
             "--output-root", str(output), *extra],
            text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=15, check=False)

    def test_repeatable_named_stream_export(self):
        first = self.root / "first"
        second = self.root / "second"
        for output in (first, second):
            result = self._run(output)
            self.assertEqual(result.returncode, 0, result.stderr)
        for name in ("litex_sync_fifo.v", "manifest.json"):
            self.assertEqual((first / name).read_bytes(), (second / name).read_bytes())
        report = json.loads((first / "manifest.json").read_text(encoding="utf-8"))
        rtl = (first / "litex_sync_fifo.v").read_bytes()
        self.assertEqual(report["source"]["revision"],
                         "b6ae9e0b227354aecffef5339d3e946f2395ac09")
        self.assertEqual(report["generator"]["recipe_sha256"],
                         hashlib.sha256(PROBE.read_bytes()).hexdigest())
        self.assertEqual(report["parameters"], {"width": 8, "depth": 4})
        self.assertEqual(report["ports"]["sink_data"],
                         {"direction": "input", "width": 8})
        self.assertEqual(report["ports"]["level"],
                         {"direction": "output", "width": 3})
        self.assertEqual(report["interfaces"]["source"]["protocol"],
                         "ready-valid-packet")
        self.assertEqual(report["verification"], "export-only")
        self.assertEqual(report["files"][0]["sha256"], hashlib.sha256(rtl).hexdigest())
        self.assertIn(b"input sink_valid", rtl)
        self.assertIn(b"output sink_ready", rtl)
        self.assertIn(b"output source_valid", rtl)
        self.assertIn(b"input source_ready", rtl)

    def test_invalid_parameters_leave_no_output(self):
        for index, args in enumerate((("--width", "0"), ("--depth", "1"),
                                      ("--width", "65"), ("--depth", "257"))):
            output = self.root / str(index)
            result = self._run(output, *args)
            self.assertEqual(result.returncode, 2)
            self.assertFalse(output.exists())

    def test_non_power_of_two_parameters_keep_port_widths(self):
        output = self.root / "variant"
        result = self._run(output, "--width", "13", "--depth", "5")
        self.assertEqual(result.returncode, 0, result.stderr)
        rtl = (output / "litex_sync_fifo.v").read_text(encoding="utf-8")
        report = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(report["ports"]["sink_data"]["width"], 13)
        self.assertEqual(report["ports"]["level"]["width"], 3)
        self.assertIn("input [12:0] sink_data", rtl)
        self.assertIn("output [12:0] source_data", rtl)
        self.assertIn("output [2:0] level", rtl)

    def test_output_cannot_modify_source_checkout(self):
        output = Path(LITEX_ROOT) / "unifpga-probe-output"
        result = self._run(output)
        self.assertEqual(result.returncode, 2)
        self.assertIn("separate from the LiteX checkout", result.stderr)
        self.assertFalse(output.exists())

    def test_wrong_checkout_rejected(self):
        output = self.root / "wrong"
        result = self._run(output, litex_root=PROBE.parents[2])
        self.assertEqual(result.returncode, 2)
        self.assertIn("revision differs", result.stderr)
        self.assertFalse(output.exists())

    def test_nonempty_output_preserved(self):
        output = self.root / "occupied"
        output.mkdir()
        sentinel = output / "keep"
        sentinel.write_text("original", encoding="utf-8")
        result = self._run(output)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "original")

    def test_pinned_migen_fifo_holds_packet_under_backpressure(self):
        root, _, _ = validate_litex(LITEX_ROOT)
        sys.path.insert(0, str(root))
        from migen.sim import run_simulation
        from litex.soc.interconnect import stream
        self.assertEqual(Path(stream.__file__).resolve(),
                         root / "litex" / "soc" / "interconnect" / "stream.py")
        fifo = stream.SyncFIFO([("data", 8)], depth=4)

        def bench():
            yield fifo.sink.valid.eq(1)
            yield fifo.sink.data.eq(0x2a)
            yield fifo.sink.first.eq(1)
            yield fifo.sink.last.eq(1)
            yield fifo.source.ready.eq(0)
            yield
            yield fifo.sink.valid.eq(0)
            for _ in range(3):
                yield
                self.assertEqual((yield fifo.source.valid), 1)
                self.assertEqual((yield fifo.source.data), 0x2a)
                self.assertEqual((yield fifo.source.first), 1)
                self.assertEqual((yield fifo.source.last), 1)
            yield fifo.source.ready.eq(1)
            yield
            yield
            self.assertEqual((yield fifo.source.valid), 0)

        run_simulation(fifo, bench())


if __name__ == "__main__":
    unittest.main()
