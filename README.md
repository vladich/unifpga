# unifpga

A multi-vendor FPGA build abstraction. One design, written against a fixed
virtual-device interface, synthesizes against any of 130 board configurations
across **twelve toolchains** — six vendor (Vivado, Quartus Prime, Quartus II
13.x, Gowin EDA Educational, Gowin EDA Standard, Efinity) and six open-source
yosys+nextpnr flows (openxc7 for Xilinx 7-series, icestorm for iCE40, trellis
for ECP5, apicula for Gowin, mistral for Cyclone V, himbaechel-gatemate for
Cologne Chip GateMate) — without changing the design.

> **Status:** configurations are checked by building them with their
> toolchains and by simulating the generated tops; few have been run on a
> physical board. Treat a board's first run as a bring-up.

## Quick start

```bash
./unifpga board          # pick your board once (remembered in settings.yml)
cd designs/1_06_binary_counter
../../unifpga build      # or: ./unifpga build 1_06_binary_counter from the repo root
../../unifpga program    # build and load the bitstream onto the board
```

Other commands: `unifpga sim` (Icarus Verilog on the design's `tb.sv`, then
gtkwave / surfer; 88 designs ship a testbench), `unifpga gui` (the last build
in the vendor GUI), `unifpga prepare --all` (the run directories of every
design, no tools run), `unifpga program --no-build` (load the last build's
bitstream again), `unifpga clean` (`--all`: every design).

`build` writes everything (generated `top.sv`, constraints, the toolchain
project and bitstream) to `run/<configuration>/` inside the design directory;
`../../unifpga clean` removes `run/`. `./unifpga -h` lists the other commands
(`board -l`, `tools`, `designs`); `UNIFPGA_BOARD=<id>` overrides the remembered
choice for one command. `synthesize.py` (below) remains the full-control form;
`program.py -c <id> -o <build dir>` loads an existing build.

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

## Examples

The example designs in `designs/` and most board pin maps started from
[basics-graphics-music](https://github.com/yuri-panchul/basics-graphics-music)
by Yuri Panchul and contributors; see
[`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md) for their licences.

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
| `config/configurations/<id>.yml` | Board × toolchain × peripheral attachments (134 configurations): the hardware, what sits on which pins, polarity, widths, clocks, I/O standards. |
| `config/profiles/<id>.yml` | Design-wiring profile: how a configuration's hardware is presented to `design_top` (which key resets, a TM1638 as the key/led/digit bus, keys as switches, mirrored bits, the lab clock, pins that follow the reset, what `uart_rx` reads with no UART pin, a bus wider than the bits wired to it (`lab_width`), components tied off (`drop`), the HEX decimal point routed onto LEDs (`bind`), a header the design only drives (`direction: out`)). Applied on top of the configuration by default; `synthesize.py --no-profile` (or `UNIFPGA_PROFILE=0`) generates the generic composition. |
| `config/peripherals/*.yml` | 37 peripheral definitions (`led_bank`, `vga_4bit`, `pmod_12pin`, `tm1638_led_key`, `inmp441_i2s_mic`, …). |
| `config/capabilities/*.yml` | 12 abstract user-facing capabilities (`leds`, `screen`, `gpio`, `audio_in`, …) with aggregation rules. |
| `rtl/peripherals/*.sv` | Driver SV modules for hardware peripherals (TM1638 controller, VGA, I²S mic, etc.). |
| `rtl/peripherals/designs_common/*.sv` | Reusable helpers (`seven_segment_display`, `shift_reg`, `strobe_gen`, …). |
| `rtl/peripherals/design_top_interface.sv` | Canonical `design_top` port list — copy and add your logic. |
| `designs/<name>/design_top.sv` | 97 example designs. |
| `tools/codegen.py` | Generates `top.sv` and per-toolchain constraint files from a resolved configuration. |
| `tools/lint_generated.py` | Lints every generated top with Verilator (locally or `remote --host <box>`). |
| `tools/verify_pinmap_against_vendor.py` | Checks board pinmaps against the vendor constraint files (Digilent XDC so far). |
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
- **A new board**: write its pinmap under
  `config/boards/<producer>/<family>/<id>.yml` (copy a board on a similar
  chip), list it in the family catalog `config/boards/<producer>/<family>.yml`,
  and add a configuration `config/configurations/<id>.yml` that attaches
  peripherals to its pin banks. For Digilent boards,
  `tools/verify_pinmap_against_vendor.py` checks the pins against the vendor XDC.
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
│   ├── profiles/<id>.yml      # design-wiring profiles
│   ├── peripherals/*.yml      # 37 peripheral definitions
│   └── capabilities/*.yml     # 12 abstract capabilities
├── designs/<name>/design_top.sv  # 97 example designs
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
│   ├── cli.py                 # the ./unifpga command line
│   ├── design_requirements.py # // requires: parser
│   ├── sweep_boards.sh        # board × design grid run
│   └── check_all_designs.sh   # smoke-check every design
└── tests/test_config_consistency.py
```
