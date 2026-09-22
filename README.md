# unifpga

A multi-vendor FPGA build abstraction. One design, written against a fixed
virtual-device interface, synthesizes against any of 130 board configurations
across **twelve toolchains** — six vendor (Vivado, Quartus Prime, Quartus II
13.x, Gowin EDA Educational, Gowin EDA Standard, Efinity) and six open-source
yosys+nextpnr flows (openxc7 for Xilinx 7-series, icestorm for iCE40, trellis
for ECP5, apicula for Gowin, mistral for Cyclone V, himbaechel-gatemate for
Cologne Chip GateMate) — without changing the design.

> **Status (2026-09):** an audit against upstream BGM found the generated
> hardware wrong on most configurations (7-segment wiring, clock frequency,
> reset, GPIO direction, PLLs, TM1638 on `_no_tm1638` variants, ...).
> The remediation plan is in [`PLAN.md`](PLAN.md); the per-configuration
> state is in [`docs/board_issue_matrix.md`](docs/board_issue_matrix.md)
> and is enforced as a ratchet by `tests/test_issue_gate.py`. Until a
> configuration's row reads `clean` and it has passed the BGM parity check,
> treat its output as unverified.

## Quick start

```bash
./unifpga board          # pick your board once (remembered in settings.yml)
cd designs/1_06_binary_counter
../../unifpga build      # or: ./unifpga build 1_06_binary_counter from the repo root
../../unifpga program

BGM lab script | `unifpga` command
--- | ---
`01_clean.bash` | `unifpga clean` (`--all`: every design)
`02_simulate_rtl.bash` | `unifpga sim` (Icarus Verilog on the design's `tb.sv`, imported from BGM for 88 designs, then gtkwave / surfer)
`03_synthesize_for_fpga.bash` + `04_configure_fpga.bash` | `unifpga program` (`unifpga build` stops after the bitstream)
`05_run_gui_for_fpga_synthesis.bash` | `unifpga gui`
`06_choose_another_fpga_board.bash` | `unifpga board`
`check_setup_and_choose_fpga_board.bash` | `unifpga board`, then `unifpga prepare --all` (the run directories of every design, no tools run; `board` offers it after an interactive choice)
```

`build` writes everything (generated `top.sv`, constraints, the toolchain
project and bitstream) to `run/<configuration>/` inside the design directory;
`../../unifpga clean` removes `run/`. `./unifpga -h` lists the other commands
(`board -l`, `tools`, `designs`); `UNIFPGA_BOARD=<id>` overrides the remembered
choice for one command. `synthesize.py` (below) remains the full-control form.

## Why it exists

A typical FPGA design is married to one board's pinout, that board's display
and audio I/O, that board's clock, and that vendor's preferred toolchain.
Moving to a different board means rewriting top-level wiring, constraints,
and build scripts. uni-fpga decouples the three concerns:

1. **The design** is plain SystemVerilog written against a fixed virtual-device
   interface (see `rtl/peripherals/design_top_interface.sv`). It refers to abstract
   capabilities — `led`, `btn`, `sw`, `abcdefgh`/`digit`, `red`/`green`/`blue`
   pixel out, `mic_sample` — never to physical pins.
2. **The board** is described by a YAML pinmap in `config/boards/<id>.yml`
   plus a configuration in `config/configurations/<id>.yml` that "attaches"
   peripherals (`led_bank`, `vga_4bit`, `tm1638_led_key`, `inmp441_i2s_mic`,
   …) to specific pin banks.
3. **The toolchain** lives in `toolchains/<id>/<id>.py` and knows how to
   drive its vendor / open-source tool in batch mode (Vivado XDC, Quartus
   QSF/SDC, Gowin CST, Efinity peri.xml + project.xml, iCE40 PCF, ECP5 LPF,
   GateMate CCF, openxc7 simple-form XDC, mistral QSF re-use).

`synthesize.py` glues them together: it codegens a `top.sv` wrapper that
maps physical pins to the design's virtual capability ports, emits the right
constraint file, then dispatches to the toolchain driver.

A `// requires:` block at the top of any design declares hard capability
needs (`screen >= 320x240`, `leds >= 4`, `gpio >= 8`, …) which `synthesize.py`
checks against the resolved configuration before invoking any tool. Boards
that don't meet the requirements **skip** instead of failing — surfacing the
real reason cleanly.

## Relationship to basics-graphics-music

This project is a re-architecture of, and tightly coupled to, the
[basics-graphics-music](https://github.com/yuri-panchul/basics-graphics-music)
(BGM) repo. uni-fpga consumes BGM as a source of truth for example designs:

- **Designs** in `designs/<name>/` are mechanically adapted from
  `basics-graphics-music/labs/.../<name>/lab_top.sv` by `tools/adapt_designs.py`.
  The adapter retargets each design to uni-fpga's canonical port list (e.g.
  `key` → `btn`, `mic` → `mic_sample`/`mic_valid`), strips per-board includes
  that codegen replaces, infers `// requires:` blocks from access patterns,
  and applies a small per-toolchain compatibility pass (move package imports
  out of ANSI port lists, strip `<param>'(expr)` size casts, etc.).
- **Board pinmaps** in `config/boards/<id>.yml` are auto-curated from BGM's
  `boards/<id>/board_specific.{xdc,qsf,cst,pcf,lpf,peri.xml}` files by
  `tools/curate_board.py`. Each pin bank we name (`onboard_leds`,
  `onboard_7seg.anodes`, `pmod_jc`, `gpio_0`, …) maps directly to the rows
  of those BGM constraint files.
- **Configurations** (the per-board peripheral attachment plans) in
  `config/configurations/<id>.yml` are bootstrapped by
  `tools/generate_variants.py` from BGM directory naming conventions
  (`tang_nano_9k_lcd_480_272_no_tm1638_yosys`, `nexys4_ddr_default`, etc.).

The two repos are expected to live as **siblings** in a parent directory:

```
some-parent/
├── basics-graphics-music/   # upstream (read-only here)
└── uni-fpga/                # this repo
```

`tools/adapt_designs.py`, `tools/curate_board.py`, and
`tools/generate_variants.py` walk into `../basics-graphics-music/` directly.
You don't need to modify BGM — uni-fpga consumes it and writes adapted
artifacts into `designs/`, `config/boards/`, and `config/configurations/`.

## What's in the box

| Path | What it holds |
|---|---|
| `unifpga` | Short command line (`board`, `build`, `program`, `sim`, `gui`, `prepare`, `clean`, `tools`, `designs`); logic in `tools/cli.py`. Remembers the board in `settings.yml`, builds into `<design>/run/<configuration>/`. |
| `synthesize.py` | Top-level entry point. Resolves a configuration, codegens `top.sv`, dispatches to the toolchain. |
| `config/boards/<producer>/<family>.yml` | Family-level board catalog: list of boards on that chip family + family description. |
| `config/boards/<producer>/<family>/<id>.yml` | Per-board pinmap (when available). |
| `config/chips/<producer>/<family>.yml` | Chip registry per family — each chip lists eligible toolchains (with optional `[version]` constraints). Boards reference these chips by Id. |
| `config/toolchains.yml` | Registry of synthesis toolchains (33 entries, vendor + open-flow). |
| `config/programmers.yml` | Registry of bitstream loaders (27 entries: bundled vendor programmers + third-party + board-specific). |
| `config/board_producers.yml` | Registry of board makers (75 entries: Digilent, Terasic, Sipeed, Trenz, BittWare, …) with URL, country, founding year, categories, description. Each board's `BoardProducer:` references one of these by Id. |
| `config/board_features.yml` | Vocabulary of board-feature tokens (91 entries across `memory`, `connectivity`, `display`, etc.). Boards may list `Features: [ethernet_1gbe, hdmi_out, pmod_x4, …]` for filtering / display. |
| `config/configurations/<id>.yml` | Board × toolchain × peripheral attachments (134 configurations). |
| `config/peripherals/*.yml` | 37 peripheral definitions (`led_bank`, `vga_4bit`, `pmod_12pin`, `tm1638_led_key`, `inmp441_i2s_mic`, …). |
| `config/capabilities/*.yml` | 12 abstract user-facing capabilities (`leds`, `screen`, `gpio`, `audio_in`, …) with aggregation rules. |
| `rtl/peripherals/*.sv` | Driver SV modules for hardware peripherals (TM1638 controller, VGA, I²S mic, etc.). |
| `rtl/peripherals/designs_common/*.sv` | Reusable helpers (`seven_segment_display`, `shift_reg`, `strobe_gen`, …). |
| `rtl/peripherals/design_top_interface.sv` | Canonical `design_top` port list — copy and add your logic. |
| `designs/<name>/design_top.sv` | 92 designs adapted from BGM. |
| `tools/codegen.py` | Generates `top.sv` and per-toolchain constraint files from a resolved configuration. |
| `tools/adapt_designs.py` | Mechanically rewrites BGM designs into uni-fpga form. |
| `tools/curate_board.py` | Builds `config/boards/<id>.yml` from BGM constraint files. |
| `tools/generate_variants.py` | Bootstraps `config/configurations/<id>.yml` from BGM directory naming. |
| `tools/equiv_check.py` | Proves a configuration's generated top is the same circuit as BGM's board top: both sides co-simulated with Icarus on the physical pins under identical stimulus, every differing pin named with its port on each side (`remote --host <box>`, `summary`). `tools/equiv_lab` is the lab on both sides, `rtl/sim/equiv_stubs.sv` the extra vendor stand-ins. |
| `toolchains/<id>/<id>.py` | Per-toolchain driver. Each defines `synthesize(...)` and `program(...)`. |

## Toolchain coverage

| Toolchain | Configs | Sample boards | Status |
|---|---|---|---|
| **Proprietary** | | | |
| `vivado` | 17 | Nexys 4 DDR, Basys 3, Arty A7 (35/100), Zybo Z7, Eclypse Z7, Nexys A7, qmtech Kintex 7 | Validated end-to-end (Vivado 2023.2) |
| `quartus_prime` | 24 | DE0-CV, DE10-Lite, DE10-Nano, DE2-115, c5gx, atlas_soc | Validated end-to-end (Quartus Prime 23.1std) |
| `quartus2` | 7 | DE0, DE1, DE2, omdazz, marsohod | Validated (Quartus II 13.0sp1 for legacy Cyclone II/III/MAX II/V) |
| `gowin_eda` | 29 | Tang Nano 4K/9K/20K, Tang Primer 20K Dock/Lite, marsohod3gw2 | Validated (Gowin EDA 1.9.11.03.Educational — covers GW1N* / GW2A* parts) |
| `gowin_standard` | 8 | Tang Primer 25K (×4), Tang Mega 138K, Tang Mega 138K Pro, orangepi_msoc | Validated (Gowin EDA 1.9.9.02 + NODELOCK license — required for AroraV/GW5* parts which Educational doesn't unlock) |
| `efinity` | 1 | FireAnt (Trion T8) | Validated end-to-end (Efinity 2023.2) |
| `libero_soc` | 1 | M2S025T Starter Kit (SmartFusion2) | Smoke-tested (Libero SoC 2024.1) |
| **Open-source (yosys + nextpnr)** | | | |
| `nextpnr_openxc7` | 17 | Same as `vivado` (open-flow alternative) | 1135/1564 OK on full sweep — uses yosys 0.41+ from oss-cad-suite |
| `nextpnr_icestorm` | 8 | iCEBreaker, iCE40HX8K-EVB, OrangeBoard, MyStorm | Validated |
| `nextpnr_trellis` | 3 | Colorlight 5A-75B, OrangeCrab, Karnix | Validated |
| `nextpnr_apicula` | 8 | Tang Nano 9K, Tang Primer 20K Dock (open-flow) | Validated |
| `nextpnr_nexus` | 1 | Lattice CrossLink-NX EVN (LIFCL-40) | Smoke-tested (yosys synth_nexus → nextpnr-nexus → prjoxide pack) |
| `nextpnr_oxide` | 1 | Same as `nextpnr_nexus` (historical alias) | Thin re-export of `nextpnr_nexus` — kept for compat with downstream docs |
| `nextpnr_mistral` | 7 | DE0-CV, DE0-Nano-SoC (vga666 + vga_pmod), DE1-SoC, DE10-Nano, c5gx, terasic_sockit | Validated (327/644 OK; needs locally-built nextpnr-mistral against mistral commit `d6bd02c`) |
| `nextpnr_gatemate` | 1 | gatemate_evb_a1 (Cologne Chip CCGM1A1) | Smoke-tested (placeholder pinmap; needs verified pads for hardware target) |

The remaining failures on yosys-based flows cluster around a small set of
designs using SystemVerilog 2009 features yosys still doesn't fully accept
(multi-dim packed arrays, certain `'{...}` array-init forms) — these are
the same designs across every nextpnr-based toolchain, not flaky failures.

### Build / install prerequisites beyond oss-cad-suite

oss-cad-suite ships yosys 0.41+ and nextpnr-{ice40,ecp5,gowin,nexus,machxo2,
generic} out of the box. Several flows still need extra setup:

| Toolchain | Needs |
|---|---|
| `nextpnr_openxc7` | `prjxray` python tools at `~/Projects/prjxray/` for the FASM→bit step |
| `nextpnr_mistral` | `nextpnr-mistral` built from `~/Projects/nextpnr` against `~/Projects/mistral` at commit `d6bd02c` (last commit with both `pos_t` and `rnode_t` typedefs — newer mistral renamed them and breaks nextpnr-mistral) |
| `nextpnr_gatemate` | `nextpnr-himbaechel` (gatemate uarch) built from `~/Projects/nextpnr` with `-DARCH=himbaechel -DHIMBAECHEL_UARCH=gatemate -DHIMBAECHEL_PEPPERCORN_PATH=~/Projects/prjpeppercorn`; `gmpack` from `~/Projects/prjpeppercorn/libgm` |
| `gowin_standard` | Gowin EDA 1.9.9.02 at `~/Gowin/1.9.9.02/`; NODELOCK license at `~/Gowin/gowin_E_<HOST_ID>.lic` (HOST_ID must be one of the machine's MACs); `~/Gowin/1.9.9.02/IDE/bin/gwlicense.ini` line `lic="<absolute path to .lic>"` |
| programming | `openFPGALoader` from `~/oss-cad-suite/bin` works for most boards |

## Advanced use: `synthesize.py` directly

`./unifpga build` runs `synthesize.py` for you; call it yourself for include
directories, a custom output directory or a throw-away temp build.

```bash
# Pick a configuration and a design:
PYTHONPATH=. python3 synthesize.py \
    -c basys3 \
    --top designs/2_9_pong/design_top.sv \
    -o build/ \
    --step elaborate     # or --step full to produce a bitstream

# Same design, different toolchain (open-flow alternative):
PYTHONPATH=. python3 synthesize.py \
    -c basys3_openxc7 \
    --top designs/2_9_pong/design_top.sv \
    -o build/

# Program the connected board (after --step full):
PYTHONPATH=. python3 synthesize.py \
    -c basys3 \
    --top designs/2_9_pong/design_top.sv \
    -o build/ \
    --step full \
    --program
```

A configuration that doesn't meet the design's `// requires:` block exits
with code `2` and a message naming the missing capability — that's the
intended SKIP, not a failure.

## Adding things

- **A new design**: copy `rtl/peripherals/design_top_interface.sv` to
  `designs/<your_design>/design_top.sv`, add your logic in the body, optionally
  add a `// requires:` block.
- **A new board**: drop the BGM-style constraint file under
  `basics-graphics-music/boards/<id>/` and run
  `python3 tools/curate_board.py` then `python3 tools/generate_variants.py`.
  Hand-edit the configuration's peripheral `attach:` list as needed.
- **A new toolchain**: add `toolchains/<id>/<id>.py` exposing `synthesize`
  and `program`, plus a `config/toolchains.yml` entry. The twelve existing
  drivers are good templates — `vivado.py` for vendor TCL flows,
  `nextpnr_icestorm.py` for yosys/nextpnr open flows, `nextpnr_gatemate.py`
  for himbaechel-uarch flows, `quartus2.py` / `gowin_standard.py` for thin
  re-exports of an adjacent driver with a different InstallDir.

## Repository layout

```
.
├── synthesize.py
├── config/
│   ├── toolchains.yml         # registry of synthesis toolchains (33)
│   ├── programmers.yml        # registry of bitstream loaders (27)
│   ├── chips/                 # chip registry, per-family
│   │   ├── xilinx_amd/<family>.yml   # chips + DefaultToolchains[version_constraint]
│   │   ├── altera_intel/<family>.yml
│   │   └── ...                # 76 family files, 274 chips referenced by boards
│   ├── boards/                # board catalogs + pinmaps
│   │   ├── xilinx_amd/        # producer dirs
│   │   │   ├── artix_7.yml    # family catalog: boards reference chips by Id
│   │   │   └── artix_7/<id>.yml  # per-board pinmaps
│   │   └── ...                # 76 family catalogs, 65 pinmaps
│   ├── configurations/<id>.yml # per-config peripheral attachments (134 configs)
│   ├── peripherals/*.yml      # 37 peripheral definitions
│   └── capabilities/*.yml     # 12 abstract capabilities
├── designs/<name>/design_top.sv  # 92 designs (BGM-derived)
├── rtl/
│   └── peripherals/              # SV peripheral drivers
│       ├── designs_common/       # reusable helpers
│       └── design_top_interface.sv  # canonical user-design interface
├── toolchains/                   # 12 driver modules
│   ├── vivado/                   #  Xilinx 7-series / Ultrascale / Versal
│   ├── quartus_prime/            #  Cyclone IV / V / 10, MAX 10, Arria, Stratix
│   ├── quartus2/                 #  Cyclone II / III, MAX II / V (Q13.0sp1)
│   ├── gowin_eda/                #  GW1N* / GW2A* (Educational license)
│   ├── gowin_standard/           #  GW5* (NODELOCK Standard license)
│   ├── efinity/                  #  Efinix Trion / Titanium
│   ├── nextpnr_openxc7/          #  Xilinx 7-series open flow
│   ├── nextpnr_icestorm/         #  iCE40
│   ├── nextpnr_trellis/          #  ECP5 / MachXO2
│   ├── nextpnr_apicula/          #  Gowin LittleBee open flow
│   ├── nextpnr_mistral/          #  Cyclone V open flow
│   └── nextpnr_gatemate/         #  Cologne Chip GateMate (himbaechel uarch)
├── tools/
│   ├── codegen.py             # top.sv + constraint emitters
│   ├── adapt_designs.py       # BGM → uni-fpga design adapter
│   ├── curate_board.py        # BGM → board pinmap
│   ├── generate_variants.py   # BGM → configuration bootstrap
│   ├── design_requirements.py # // requires: parser
│   ├── sweep_boards.sh        # board × design grid run
│   └── check_all_designs.sh   # smoke-check every design
└── tests/test_config_consistency.py
```
