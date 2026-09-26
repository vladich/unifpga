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
UART_MODULE = "litex_rs232_phy"
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
UART_PORTS = {
    "sys_clk": "input", "sys_rst": "input",
    "serial_rx": "input", "serial_tx": "output",
    "sink_valid": "input", "sink_ready": "output", "sink_data": "input",
    "source_valid": "output", "source_ready": "input", "source_data": "output",
    "rx_framing_error": "output", "rx_overflow": "output",
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


def _verify_ports(source_text, expected):
    port_declarations = re.findall(
        r"^\s*(input|output)(?: reg)?\s+"
        r"(?:\[(\d+):(\d+)\]\s+)?([A-Za-z_]\w*)\s*[,)]",
        source_text, re.MULTILINE)
    actual = {name: {"direction": direction,
                     "width": int(high) - int(low) + 1 if high else 1}
              for direction, high, low, name in port_declarations}
    if len(port_declarations) != len(actual):
        raise ProbeError("generated Verilog has duplicate port declarations")
    if actual != expected:
        raise ProbeError("generated Verilog ports differ from the component contract: "
                         "missing {}; unexpected {}; changed {}".format(
                             sorted(set(expected) - set(actual)),
                             sorted(set(actual) - set(expected)),
                             sorted(name for name in actual.keys() & expected.keys()
                                    if actual[name] != expected[name])))


def _require_migen():
    try:
        installed_migen = version("migen")
    except PackageNotFoundError as exc:
        raise ProbeError("Migen is not installed in the locked probe environment") from exc
    if installed_migen != MIGEN_VERSION:
        raise ProbeError("Migen version differs from the probe pin")


def _render(root, width, depth):
    _require_migen()
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
    _verify_ports(source_text, _expected_ports(width, depth))
    return source_text


def _render_uart(root, clk_freq, baudrate):
    _require_migen()
    sys.path.insert(0, str(root))
    from migen import Module, Signal
    from migen.fhdl import verilog
    from litex.soc.cores import uart
    if Path(uart.__file__).resolve() != root / "litex" / "soc" / "cores" / "uart.py":
        raise ProbeError("imported LiteX UART is not from the pinned checkout")

    class WrappedUART(Module):
        def __init__(self):
            self.serial_rx = Signal(name="serial_rx")
            self.serial_tx = Signal(name="serial_tx")
            self.sink_valid = Signal(name="sink_valid")
            self.sink_ready = Signal(name="sink_ready")
            self.sink_data = Signal(8, name="sink_data")
            self.source_valid = Signal(name="source_valid")
            self.source_ready = Signal(name="source_ready")
            self.source_data = Signal(8, name="source_data")
            self.rx_framing_error = Signal(name="rx_framing_error")
            self.rx_overflow = Signal(name="rx_overflow")
            pads = uart.UARTPads()
            self.submodules.phy = phy = uart.RS232PHY(pads, clk_freq, baudrate)
            self.comb += [
                pads.rx.eq(self.serial_rx), self.serial_tx.eq(pads.tx),
                phy.sink.valid.eq(self.sink_valid),
                self.sink_ready.eq(phy.sink.ready),
                phy.sink.data.eq(self.sink_data),
                self.source_valid.eq(phy.source.valid),
                phy.source.ready.eq(self.source_ready),
                self.source_data.eq(phy.source.data),
                self.rx_framing_error.eq(phy.rx_framing_error),
                self.rx_overflow.eq(phy.rx_overflow),
            ]

    wrapped = WrappedUART()
    ios = {getattr(wrapped, name) for name in UART_PORTS
           if name not in ("sys_clk", "sys_rst")}
    output = verilog.convert(wrapped, ios=ios, name=UART_MODULE)
    if output.data_files:
        raise ProbeError("unexpected generated external data files")
    source_text = output.main_source
    expected = {name: {"direction": direction,
                       "width": 8 if name in ("sink_data", "source_data") else 1}
                for name, direction in UART_PORTS.items()}
    _verify_ports(source_text, expected)
    return source_text, expected


def _validate_output(root, output_root):
    output_root = Path(output_root).resolve()
    if output_root.is_relative_to(root) or root.is_relative_to(output_root):
        raise ProbeError("output root must be separate from the LiteX checkout")
    if output_root.exists() and any(output_root.iterdir()):
        raise ProbeError("output root must be empty")
    return output_root


def _emit(root, output_root, module, component, source_text, parameters,
          ports, interfaces, source_checksums):
    output_root = _validate_output(root, output_root)
    if len(source_text.encode("utf-8")) > 1024 * 1024:
        raise ProbeError("generated Verilog exceeds 1 MiB probe limit")
    output_root.mkdir(parents=True, exist_ok=True)
    rtl = source_text.encode("utf-8")
    (output_root / (module + ".v")).write_bytes(rtl)
    report = {
        "schema": "unifpga-component-export/v1",
        "component": component,
        "implementation": "migen-generated-verilog",
        "source": {"repository": "https://github.com/enjoy-digital/litex.git",
                   "revision": LITEX_REVISION,
                   "license": "BSD-2-Clause", **source_checksums},
        "generator": {"migen_version": MIGEN_VERSION,
                      "python_version": ".".join(map(str, sys.version_info[:3])),
                      "recipe": "experiments/litex/probe.py",
                      "recipe_sha256": _digest(Path(__file__).read_bytes()),
                      "lock_sha256": _digest(Path(__file__).with_name("uv.lock").read_bytes())},
        "parameters": parameters,
        "ports": ports,
        "interfaces": interfaces,
        "reset": {"signal": "sys_rst", "polarity": "active-high",
                  "domain": "sys_clk"},
        "files": [{"path": module + ".v", "sha256": _digest(rtl),
                   "size": len(rtl)}],
        "verification": "export-only",
    }
    (output_root / "manifest.json").write_text(
        json.dumps(report, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return report


def run_probe(litex_root, output_root, width=8, depth=4):
    if type(width) is not int or not 1 <= width <= 64:
        raise ProbeError("width must be an integer from 1 to 64")
    if type(depth) is not int or not 2 <= depth <= 256:
        raise ProbeError("depth must be an integer from 2 to 256")
    root, source, license_file = validate_litex(litex_root)
    _validate_output(root, output_root)
    source_text = _render(root, width, depth)
    interfaces = {
        "sink": {"role": "consumer", "protocol": "ready-valid-packet",
                 "clock": "sys_clk", "reset": "sys_rst"},
        "source": {"role": "producer", "protocol": "ready-valid-packet",
                   "clock": "sys_clk", "reset": "sys_rst"},
    }
    return _emit(root, output_root, MODULE, "litex:stream:sync_fifo",
                 source_text, {"width": width, "depth": depth},
                 _expected_ports(width, depth), interfaces,
                 {"stream_sha256": _digest(source.read_bytes()),
                  "license_sha256": _digest(license_file.read_bytes())})


def run_uart_probe(litex_root, output_root, clk_freq=10000000, baudrate=115200):
    if type(clk_freq) is not int or not 1000000 <= clk_freq <= 200000000:
        raise ProbeError("clock frequency must be 1..200 MHz")
    if type(baudrate) is not int or not 300 <= baudrate <= 1000000:
        raise ProbeError("baudrate must be 300..1000000")
    if baudrate * 8 > clk_freq:
        raise ProbeError("clock frequency must be at least eight times baudrate")
    root, stream_source, license_file = validate_litex(litex_root)
    uart_source = root / "litex" / "soc" / "cores" / "uart.py"
    if not uart_source.is_file():
        raise ProbeError("LiteX UART source is missing")
    _validate_output(root, output_root)
    source_text, ports = _render_uart(root, clk_freq, baudrate)
    interfaces = {
        "sink": {"role": "consumer", "protocol": "ready-valid-byte",
                 "clock": "sys_clk", "reset": "sys_rst"},
        "source": {"role": "producer", "protocol": "pulse-byte-no-backpressure",
                   "clock": "sys_clk", "reset": "sys_rst"},
        "serial": {"role": "bidirectional", "protocol": "uart-8n1",
                   "clock": "sys_clk", "reset": "sys_rst"},
    }
    return _emit(root, output_root, UART_MODULE, "litex:uart:rs232_phy",
                 source_text, {"clk_freq": clk_freq, "baudrate": baudrate},
                 ports, interfaces,
                 {"uart_sha256": _digest(uart_source.read_bytes()),
                  "stream_sha256": _digest(stream_source.read_bytes()),
                  "license_sha256": _digest(license_file.read_bytes())})


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--litex-root", required=True, type=Path)
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument("--component", choices=("sync-fifo", "rs232-phy"),
                        default="sync-fifo")
    parser.add_argument("--width", type=int)
    parser.add_argument("--depth", type=int)
    parser.add_argument("--clk-freq", type=int)
    parser.add_argument("--baudrate", type=int)
    args = parser.parse_args(argv)
    try:
        if args.component == "sync-fifo":
            if args.clk_freq is not None or args.baudrate is not None:
                raise ProbeError("UART parameters require --component rs232-phy")
            report = run_probe(args.litex_root, args.output_root,
                               width=8 if args.width is None else args.width,
                               depth=4 if args.depth is None else args.depth)
        else:
            if args.width is not None or args.depth is not None:
                raise ProbeError("FIFO parameters require --component sync-fifo")
            report = run_uart_probe(args.litex_root, args.output_root,
                                    clk_freq=10000000 if args.clk_freq is None else args.clk_freq,
                                    baudrate=115200 if args.baudrate is None else args.baudrate)
    except (ProbeError, OSError, subprocess.TimeoutExpired) as exc:
        parser.exit(2, "LiteX probe: {}\n".format(exc))
    print(json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    main()
