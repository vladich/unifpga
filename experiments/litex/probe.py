"""Pinned LiteX stream FIFO export probe, not a catalog importer."""

import argparse
import hashlib
from importlib.metadata import PackageNotFoundError, version
import json
from pathlib import Path
import re
import subprocess
import sys


LITEX_REVISION = "b6ae9e0b227354aecffef5339d3e946f2395ac09"
MIGEN_VERSION = "0.9.2"
MODULE = "litex_sync_fifo"
PORTS = {
    "sys_clk": "input",
    "sys_rst": "input",
    "sink_valid": "input",
    "sink_ready": "output",
    "sink_first": "input",
    "sink_last": "input",
    "sink_data": "input",
    "source_valid": "output",
    "source_ready": "input",
    "source_first": "output",
    "source_last": "output",
    "source_data": "output",
    "level": "output",
}


class ProbeError(ValueError):
    pass


def _git(root, *args):
    result = subprocess.run(["git", "-C", str(root), *args], text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            check=False, timeout=10)
    if result.returncode:
        raise ProbeError("LiteX source is not an accessible Git checkout")
    return result.stdout.strip()


def validate_litex(root):
    root = Path(root).resolve(strict=True)
    if Path(_git(root, "rev-parse", "--show-toplevel")).resolve() != root:
        raise ProbeError("LiteX root must be the exact checkout root")
    revision = _git(root, "rev-parse", "HEAD")
    if revision != LITEX_REVISION:
        raise ProbeError("LiteX revision differs from the probe pin")
    if _git(root, "status", "--porcelain=v1", "--untracked-files=all"):
        raise ProbeError("LiteX checkout must be clean")
    source = root / "litex" / "soc" / "interconnect" / "stream.py"
    license_file = root / "LICENSE"
    if not source.is_file() or not license_file.is_file():
        raise ProbeError("LiteX stream source or license is missing")
    return root, source, license_file


def _digest(data):
    return hashlib.sha256(data).hexdigest()


def _expected_ports(width, depth):
    widths = {"sink_data": width, "source_data": width,
              "level": depth.bit_length()}
    return {name: {"direction": direction, "width": widths.get(name, 1)}
            for name, direction in PORTS.items()}


def _render(root, width, depth):
    try:
        installed_migen = version("migen")
    except PackageNotFoundError as exc:
        raise ProbeError("Migen is not installed in the locked probe environment") from exc
    if installed_migen != MIGEN_VERSION:
        raise ProbeError("Migen version differs from the probe pin")
    sys.path.insert(0, str(root))
    from migen import Module, Signal
    from migen.fhdl import verilog
    from litex.soc.interconnect import stream
    if Path(stream.__file__).resolve() != root / "litex" / "soc" / "interconnect" / "stream.py":
        raise ProbeError("imported LiteX module is not from the pinned checkout")

    class WrappedFIFO(Module):
        def __init__(self):
            self.sink_valid = Signal(name="sink_valid")
            self.sink_ready = Signal(name="sink_ready")
            self.sink_first = Signal(name="sink_first")
            self.sink_last = Signal(name="sink_last")
            self.sink_data = Signal(width, name="sink_data")
            self.source_valid = Signal(name="source_valid")
            self.source_ready = Signal(name="source_ready")
            self.source_first = Signal(name="source_first")
            self.source_last = Signal(name="source_last")
            self.source_data = Signal(width, name="source_data")
            self.level = Signal(max=depth + 1, name="level")
            self.submodules.fifo = fifo = stream.SyncFIFO([("data", width)], depth)
            self.comb += [
                fifo.sink.valid.eq(self.sink_valid),
                self.sink_ready.eq(fifo.sink.ready),
                fifo.sink.first.eq(self.sink_first),
                fifo.sink.last.eq(self.sink_last),
                fifo.sink.data.eq(self.sink_data),
                self.source_valid.eq(fifo.source.valid),
                fifo.source.ready.eq(self.source_ready),
                self.source_first.eq(fifo.source.first),
                self.source_last.eq(fifo.source.last),
                self.source_data.eq(fifo.source.data),
                self.level.eq(fifo.level),
            ]

    wrapped = WrappedFIFO()
    ios = [getattr(wrapped, name) for name in PORTS if name not in
           ("sys_clk", "sys_rst")]
    output = verilog.convert(wrapped, ios=set(ios), name=MODULE)
    if output.data_files:
        raise ProbeError("unexpected generated external data files")
    source_text = output.main_source
    port_declarations = re.findall(
        r"^\s*(input|output)(?: reg)?\s+"
        r"(?:\[(\d+):(\d+)\]\s+)?([A-Za-z_]\w*)\s*[,)]",
        source_text, re.MULTILINE)
    actual = {name: {"direction": direction,
                     "width": int(high) - int(low) + 1 if high else 1}
              for direction, high, low, name in port_declarations}
    if len(port_declarations) != len(actual):
        raise ProbeError("generated Verilog has duplicate port declarations")
    expected = _expected_ports(width, depth)
    if actual != expected:
        raise ProbeError("generated Verilog ports differ from the virtual stream contract: "
                         "missing {}; unexpected {}; changed {}".format(
                             sorted(set(expected) - set(actual)),
                             sorted(set(actual) - set(expected)),
                             sorted(name for name in actual.keys() & expected.keys()
                                    if actual[name] != expected[name])))
    return source_text


def run_probe(litex_root, output_root, width=8, depth=4):
    if type(width) is not int or not 1 <= width <= 64:
        raise ProbeError("width must be an integer from 1 to 64")
    if type(depth) is not int or not 2 <= depth <= 256:
        raise ProbeError("depth must be an integer from 2 to 256")
    root, source, license_file = validate_litex(litex_root)
    output_root = Path(output_root).resolve()
    if output_root.is_relative_to(root) or root.is_relative_to(output_root):
        raise ProbeError("output root must be separate from the LiteX checkout")
    if output_root.exists() and any(output_root.iterdir()):
        raise ProbeError("output root must be empty")
    source_text = _render(root, width, depth)
    if len(source_text.encode("utf-8")) > 1024 * 1024:
        raise ProbeError("generated Verilog exceeds 1 MiB probe limit")
    output_root.mkdir(parents=True, exist_ok=True)
    rtl = source_text.encode("utf-8")
    (output_root / (MODULE + ".v")).write_bytes(rtl)
    report = {
        "schema": "unifpga-component-export/v1",
        "component": "litex:stream:sync_fifo",
        "implementation": "migen-generated-verilog",
        "source": {"repository": "https://github.com/enjoy-digital/litex.git",
                   "revision": LITEX_REVISION,
                   "stream_sha256": _digest(source.read_bytes()),
                   "license_sha256": _digest(license_file.read_bytes()),
                   "license": "BSD-2-Clause"},
        "generator": {"migen_version": MIGEN_VERSION,
                      "python_version": ".".join(map(str, sys.version_info[:3])),
                      "recipe": "experiments/litex/probe.py",
                      "recipe_sha256": _digest(Path(__file__).read_bytes()),
                      "lock_sha256": _digest(Path(__file__).with_name("uv.lock").read_bytes())},
        "parameters": {"width": width, "depth": depth},
        "ports": _expected_ports(width, depth),
        "interfaces": {
            "sink": {"role": "consumer", "protocol": "ready-valid-packet",
                     "clock": "sys_clk", "reset": "sys_rst"},
            "source": {"role": "producer", "protocol": "ready-valid-packet",
                       "clock": "sys_clk", "reset": "sys_rst"},
        },
        "reset": {"signal": "sys_rst", "polarity": "active-high",
                  "domain": "sys_clk"},
        "files": [{"path": MODULE + ".v", "sha256": _digest(rtl),
                   "size": len(rtl)}],
        "verification": "export-only",
    }
    (output_root / "manifest.json").write_text(
        json.dumps(report, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--litex-root", required=True, type=Path)
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument("--width", type=int, default=8)
    parser.add_argument("--depth", type=int, default=4)
    args = parser.parse_args(argv)
    try:
        report = run_probe(args.litex_root, args.output_root,
                           width=args.width, depth=args.depth)
    except (ProbeError, OSError, subprocess.TimeoutExpired) as exc:
        parser.exit(2, "LiteX probe: {}\n".format(exc))
    print(json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    main()
