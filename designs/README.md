# Lab examples

Each design is a self-contained `design_top.sv` that targets the canonical
uni-fpga virtual-device interface (see `rtl/peripherals/design_top_interface.sv`).

To build a design on a particular board:

    PYTHONPATH=. python3 synthesize.py \
        -c nexys4_ddr_default \
        --top designs/01_and_or_not_xor/design_top.sv \
        -o build/01_and_or_not_xor

The generated `build/<design>/top.sv`, `constraints.xdc`, and `build.tcl` are
fed to Vivado in batch mode. Set `UNIFPGA_DRY_RUN=1` to produce all
artifacts without actually invoking Vivado (useful in CI or on machines
without a vendor toolchain installed).

## Adaptation rules

When porting a design written against the `lab_top` interface, apply these
changes:

| `lab_top` | uni-fpga | Why |
|---|---|---|
| `w_key`, `key` | `w_btn`, `btn` | The canonical capability is `buttons` |
| `slow_clk` (port) | derive locally from `clk` | uni-fpga doesn't expose a slow clock |
| `mic` (24-bit) | `mic_sample` (24-bit) + `mic_valid` (1-bit) | Decoupled valid strobe matches the `audio_in` capability contract |
| `\`include "config.svh"` | (delete) | uni-fpga doesn't use feature flags |

Add a `// requires:` block at the top of the file declaring minimum capability
dimensions. `synthesize.py` parses this block and refuses to build if the
chosen configuration doesn't meet the requirements.
