# Pinned LiteX stream and UART export probes

This is the first executable ECO-07A intake experiment. It exports one LiteX
`SyncFIFO` through an explicit, named ready/valid stream boundary. It proves
that a unifpga-side wrapper can give a generated module stable port names
without changing LiteX. It does **not** admit the FIFO to the catalog or prove
synthesis quality, board compatibility, or the two complex mixed-source
systems in [the plan](../../ECOSYSTEM_PLAN.md).

The probe also exports the pinned LiteX `RS232PHY` as an 8N1 UART component.
`--component rs232-phy --clk-freq 10000000 --baudrate 115200` selects its
clock/baud parameters; the default remains `sync-fifo`. Its TX input uses
ready/valid with `sink_data` held stable until `sink_ready`. Its RX output is a
one-cycle byte pulse: serial input cannot be stalled, and `source_ready=0` at
arrival reports `rx_overflow` rather than holding the byte. Invalid stop bits
report `rx_framing_error` and do not produce a byte. This contract requires a
buffer or loss policy before composition with a backpressured consumer.

The probe uses the clean `deps/litex` Git submodule pinned at
`b6ae9e0b227354aecffef5339d3e946f2395ac09` and Migen 0.9.2 from the
committed `uv.lock`. Set `LITEX_ROOT` to the absolute submodule path, and keep
the virtual environment, cache, output, and `TMPDIR` inside a manifested task
run. The probe rejects another revision or dirty checkout and emits only
one generated Verilog file and a digest-bearing `manifest.json` to an empty output
directory. The submodule pin is experiment provenance, not admission of every
LiteX core or its transitive dependencies to the product catalog.
This probe imports and executes trusted pinned LiteX Python in-process. A
catalog/service importer must run generators in an isolated worker with resource
limits and no ambient credentials; this script is not that worker.

```text
PROJECT_DIR="$(git rev-parse --show-toplevel)"
git submodule update --init deps/litex
UV_PROJECT_ENVIRONMENT="$TASK_RUN_ROOT/litex-venv" UV_CACHE_DIR="$TASK_RUN_ROOT/uv-cache" uv sync --project experiments/litex --locked
LITEX_ROOT="$PROJECT_DIR/deps/litex" IVERILOG="$IVERILOG" VVP="$VVP" TMPDIR="$TASK_RUN_ROOT/tmp" "$TASK_RUN_ROOT/litex-venv/bin/python" -m unittest discover -s experiments/litex -p 'test_*.py' -v
"$TASK_RUN_ROOT/litex-venv/bin/python" experiments/litex/probe.py --litex-root "$PROJECT_DIR/deps/litex" --output-root "$TASK_RUN_ROOT/export"
"$TASK_RUN_ROOT/litex-venv/bin/python" experiments/litex/probe.py --litex-root "$PROJECT_DIR/deps/litex" --output-root "$TASK_RUN_ROOT/uart-export" --component rs232-phy --clk-freq 10000000 --baudrate 115200
```

The first direct Migen conversion used ambiguous endpoint names such as
`valid` and `valid_1`. The wrapper exports `sink_*` and `source_*` names and
checks the emitted port inventory. Two fresh processes emitted identical RTL
and manifests for width 8, depth 4 in the initial run.
The nine focused probe tests pass, including a Migen-level packet and
backpressure scenario, parameter width checks, and invalid-input/source guards.
The Migen simulator test checks upstream FHDL behavior, not emitted RTL. A
separate `test_rtl.py` exports width 16/depth 4 RTL and uses Icarus Verilog to
simulate it standalone, in a mixed-source PDM capture, and through a
virtual-device GPIO/button wrapper. All three runs invoke
`unifpga sim` with the generic `--component-export` manifest input; unifpga
selects sources, compiles, and launches the RTL simulation. These RTL tests do
not use LiteX's builder or simulation runner; the separate Migen-level unit
test above still uses Migen's simulator. Set `IVERILOG` and
`VVP` to the corresponding binaries (or put them on `PATH`) and run unittest
discovery over `experiments/litex`. The test also invokes `unifpga prepare`
with the export manifest for a Gowin virtual-device target and checks that its
generated project reads the staged FIFO RTL before the design. The target is
selected through the public setup-derived build ID; the export manifest has
no board or rig configuration fields. The test skips clearly when either
Icarus binary is unavailable. The first independent run used Icarus 13.0 built
from the official `v13_0` tag at `dfeee909ed9f20b4870dd93423156c0170c0e1ff` in
task-local scratch; this repository does not install a simulator.

`design/design_top.sv` contains a virtual-device-facing `design_top` and a
`pdm_fifo_capture` subsystem around the unifpga `pdm_mic_decoder` and
generated LiteX FIFO. The wrapper maps PDM data/clock/LR select to three GPIO
bits and FIFO consumer readiness to one button. The microphone cannot be stalled,
so samples arriving at a full FIFO are discarded and counted (saturating at
65535). Consumers can stall the FIFO output. The composed test checks the first
positive and negative sample, a full queue, stable backpressured data, packet
boundaries, loss accounting, drain, and reset. It also caught and fixes the
decoder's first-window off-by-one sum. This is one mixed-source *subsystem*, not
one of the two required complex P2a systems. Its generated RTL is admitted to
the ordinary unifpga simulation path by a bounded `unifpga-component-export/v1`
manifest. The ordinary `prepare` path now snapshots the export and generates a
toolchain project, but no vendor synthesis or hardware run has passed. It has
no catalog registration, visual authoring, SoC firmware, synthesis/area/timing
comparison, or board electrical and microphone clock validation. The probe
manifest still says `export-only` because export alone does
not guarantee that a downstream caller ran the separate RTL test. No LiteX
source was modified; any LiteX-side patch needs a demonstrated export
limitation and its own tests.

The UART RTL test exports the PHY, then runs two independent Icarus simulations
through `unifpga sim`: one checks a received byte and overflow when the
consumer is not ready; the other pairs the generated PHY with a native
one-byte echo adapter and checks a complete serial round-trip plus invalid-stop
framing detection. Both tests use the ordinary component-export snapshot path,
not LiteX's build or simulation runner. This is a second reusable component
boundary and a small mixed-source adapter. It is not the required CPU/control
system, a validated virtual-device UART mapping, or a catalog admission.

`pdm_uart_stream` is the next stream-system candidate: authored PDM decoder →
pinned LiteX 16-bit FIFO → authored two-byte, big-endian packetizer → pinned
LiteX UART PHY. Its recipe assumes a real 24 MHz system clock, which gives a
3 MHz PDM clock. Decimation by 1024 yields about 2,930 samples/s, below the
UART's 5,760 two-byte samples/s ceiling at 115200 baud and 8N1 framing. The
packetizer holds each byte through the UART's late acknowledgement; the FIFO
still counts dropped PDM samples if a consumer falls behind. `test_rtl.py`
passes **both** component manifests to `unifpga sim`, checks packetizer stalls
and reset independently, then checks two positive and one negative PDM sample
over the serial wire, sample cadence, and zero loss in that bounded run.
The pilot has no frame synchronization or error recovery on the serial wire,
no microphone waveform or sound-quality validation, no clock/baud binding in
the catalog, and no synthesis or board test. It is not yet a P2a acceptance.

The APS CPU/control candidate now exercises a generated LiteX UART TX PHY
through a native memory-mapped adapter at APS slot 6. The `tb_litex_uart`
testbench selects `processor_system.USE_LITEX_UART_TX`, while the normal
`design_top` keeps its native UART and remains runnable without a LiteX export.
The fixture firmware waits for 100 real 10 MHz timer ticks, writes the LED,
and issues two consecutive UART stores. `test_uart_rtl.py` exports the pinned
10 MHz / 115200 baud PHY, invokes `unifpga sim --component-export`, checks
both serial bytes and the staged RTL snapshot, and checks that elaboration
fails when the required export is absent. The adapter holds each byte until
LiteX reports TX completion. Its only supported registers are data write at
offset 0 and ready read at offset 8; other offsets keep the APS bus stalled.
This proves a CPU, instruction memory, timer, LED, bus, adapter, and generated
PHY together. The virtual-device `design_top` cannot yet select this backend
through the configuration format, and the test has no UART RX/interrupt,
synthesis, timing, or board evidence. It remains a CPU/control candidate rather
than a completed P2a system.
