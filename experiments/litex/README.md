# Pinned LiteX stream export probe

This is the first executable ECO-07A intake experiment. It exports one LiteX
`SyncFIFO` through an explicit, named ready/valid stream boundary. It proves
that a unifpga-side wrapper can give a generated module stable port names
without changing LiteX. It does **not** admit the FIFO to the catalog or prove
synthesis quality, board compatibility, or the two complex mixed-source
systems in [the plan](../../ECOSYSTEM_PLAN.md).

The probe uses the clean `deps/litex` Git submodule pinned at
`b6ae9e0b227354aecffef5339d3e946f2395ac09` and Migen 0.9.2 from the
committed `uv.lock`. Set `LITEX_ROOT` to the absolute submodule path, and keep
the virtual environment, cache, output, and `TMPDIR` inside a manifested task
run. The probe rejects another revision or dirty checkout and emits only
`litex_sync_fifo.v` and a digest-bearing `manifest.json` to an empty output
directory. The submodule pin is experiment provenance, not admission of every
LiteX core or its transitive dependencies to the product catalog.
This probe imports and executes trusted pinned LiteX Python in-process. A
catalog/service importer must run generators in an isolated worker with resource
limits and no ambient credentials; this script is not that worker.

```text
PROJECT_DIR="$(git rev-parse --show-toplevel)"
git submodule update --init deps/litex
UV_PROJECT_ENVIRONMENT="$TASK_RUN_ROOT/litex-venv" UV_CACHE_DIR="$TASK_RUN_ROOT/uv-cache" uv sync --project experiments/litex --locked
LITEX_ROOT="$PROJECT_DIR/deps/litex" TMPDIR="$TASK_RUN_ROOT/tmp" "$TASK_RUN_ROOT/litex-venv/bin/python" experiments/litex/test_probe.py -v
"$TASK_RUN_ROOT/litex-venv/bin/python" experiments/litex/probe.py --litex-root "$PROJECT_DIR/deps/litex" --output-root "$TASK_RUN_ROOT/export"
```

The first direct Migen conversion used ambiguous endpoint names such as
`valid` and `valid_1`. The wrapper exports `sink_*` and `source_*` names and
checks the emitted port inventory. Two fresh processes emitted identical RTL
and manifests for width 8, depth 4 in the initial run.
The seven focused probe tests pass, including a Migen-level packet and
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
generated project reads the staged FIFO RTL before the design. It skips clearly
when either Icarus binary is unavailable. The first independent run used Icarus 13.0 built from the
official `v13_0` tag at `dfeee909ed9f20b4870dd93423156c0170c0e1ff` in
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
