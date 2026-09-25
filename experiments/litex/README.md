# Pinned LiteX stream export probe

This is the first executable ECO-07A intake experiment. It exports one LiteX
`SyncFIFO` through an explicit, named ready/valid stream boundary. It proves
that a unifpga-side wrapper can give a generated module stable port names
without changing LiteX. It does **not** admit the FIFO to the catalog or prove
its behavior, synthesis quality, board compatibility, or the two mixed-source
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
and manifests for width 8, depth 4 in the initial run. The generated module
still needs independent Verilog simulation and a mixed-source composition test.
The seven focused probe tests pass, including a Migen-level packet and
backpressure scenario, parameter width checks, and invalid-input/source guards.
The Migen simulator test checks the upstream FHDL behavior, not the emitted RTL.
The current host has no Icarus, Verilator, or Yosys, so this experiment reports
`export-only` rather than claiming those checks passed. No LiteX source was
modified; any LiteX-side patch needs a demonstrated export limitation and its
own tests.
