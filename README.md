# unifpga

A multi-vendor FPGA build abstraction. Designs use a virtual-device interface;
the catalog describes how board configurations and vendor or open-source
toolchains can provide it. `SupportedOperations` and board evidence determine
which catalog entries are currently eligible for a build or programming run.

> **Status:** configuration and generated-project checks exist, but full
> toolchain, simulation, and physical-board coverage is incomplete. No board
> pinmap is yet attested for hardware, so builds and programming stop at the
> hardware-readiness gate. Treat any first physical run as a bring-up.

## Quick start

```bash
./unifpga board          # pick your board once (remembered in settings.yml)
cd designs/1_06_binary_counter
../../unifpga prepare    # inspect generated files without running tools
# After board attestation and toolchain setup:
../../unifpga build      # or: ./unifpga build 1_06_binary_counter from the repo root
../../unifpga program    # build and load the bitstream onto the board
```

Until a board has a reviewed pinmap attestation, use `../../unifpga prepare`
to inspect generated project files; `build` and `program` require that
attestation and an installed supported toolchain.

Other commands: `unifpga sim` (Icarus Verilog on a design's `tb.sv`, then
gtkwave / surfer, for designs that ship a testbench), `unifpga gui` (the last
build in the vendor GUI), `unifpga prepare --all` (the run directories of every
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

The toolchain registry declares `SupportedOperations` separately from install
detection. `./unifpga tools` shows both. Catalogue-only toolchains have `[]`;
attempting their build or program operation exits before creating build output.
ISE and Libero SoC currently support synthesis but require an external
programming workflow. An implemented driver operation does not by itself
verify a board's pinmap or electrical constraints.

Physical builds and programming now require a reviewed `verification` record
in the board pinmap. A missing record, a placeholder status, a stale resolved
pinmap digest, an uncovered FPGA part, or missing pinout/electrical evidence
stops the operation before tool setup. No existing pinmap has yet been
attested for hardware, so the current catalog supports project inspection via
`UNIFPGA_DRY_RUN=1` while board evidence is collected. Dry-run success is not
a bitstream or a programmed device. To admit a board, review its exact pins
and I/O electrical settings against versioned vendor sources, then add:

```yaml
verification:
  status: verified
  pinmap_sha256: <sha256 of the resolved Board mapping except verification>
  parts: [<exact selected Part ordering code>]
  pinout: {source: <vendor schematic or constraint>, revision: <exact revision>}
  electrical: {source: <vendor electrical document>, revision: <exact revision>}
```

`config.init.pinmap_fingerprint(resolved["board_pinmap"])` computes the digest
for a configuration. Pin or I/O overrides change it, so an attestation of a
base board does not admit a different wiring variant. Evidence text is a
review record; it is not independently checked against the vendor document
at runtime. Build-artifact/device pairing remains a separate programming task.

Before a build or board-programming request, version admission also checks the
toolchain's configured `Version` against the detected install-directory version
and checks that version against any chip constraint, such as
`vivado[2024.1+]`. `*`, exact versions, minimum versions, and inclusive ranges
are supported. The detected version comes from the installation path; a
vendor-binary version probe is still needed for hardware-grade provenance.

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
| `config/toolchains.yml` | Toolchain registry, including the executable operations each driver supports. |
| `config/programmers.yml` | Registry of bundled vendor, third-party, and board-specific bitstream loaders. |
| `config/board_producers.yml` | Registry of board makers with URL, country, founding year, categories, and description. Each board's `BoardProducer:` references one of these by Id. |
| `config/features.yml` | Vocabulary of board-feature tokens across `memory`, `connectivity`, `display`, and other categories. Boards may list `Features: [ethernet_1gbe, hdmi_out, pmod_x4, …]` for filtering and display. |
| `config/configurations/<id>.yml` | Board × toolchain × peripheral attachments: the hardware, what sits on which pins, polarity, widths, clocks, and I/O standards. |
| `config/layouts/<board>.yml`, `config/modules/<id>.yml`, `config/setups/<id>.yml`, `config/connectors.yml` | Boards as rigs: connectors and on-board devices, add-on module pinouts, and setups that generate configurations; `tools/setup.py`, drawings by `tools/viewer.py`. |
| `config/profiles/<id>.yml` | Design-wiring profile: how a configuration's hardware is presented to `design_top` (which key resets, a TM1638 as the key/led/digit bus, keys as switches, mirrored bits, the lab clock, pins that follow the reset, what `uart_rx` reads with no UART pin, a bus wider than the bits wired to it (`lab_width`), components tied off (`drop`), the HEX decimal point routed onto LEDs (`bind`), a header the design only drives (`direction: out`)). Applied on top of the configuration by default; `synthesize.py --no-profile` (or `UNIFPGA_PROFILE=0`) generates the generic composition. |
| `config/peripherals/*.yml` | Peripheral definitions (`led_bank`, `vga_4bit`, `gpio_header`, `tm1638_led_key`, `inmp441_i2s_mic`, …). |
| `config/capabilities/*.yml` | Abstract user-facing capabilities (`leds`, `screen`, `gpio`, `audio_in`, …) with aggregation rules. |

| `rtl/peripherals/*.sv` | Driver SV modules for hardware peripherals (TM1638 controller, VGA, I²S mic, etc.). |
| `rtl/peripherals/designs_common/*.sv` | Reusable helpers (`seven_segment_display`, `shift_reg`, `strobe_gen`, …). |
| `rtl/peripherals/design_top_interface.sv` | Canonical `design_top` port list — copy and add your logic. |
| `designs/<name>/design_top.sv` | Example designs. |
| `tools/codegen.py` | Generates `top.sv` and per-toolchain constraint files from a resolved configuration. |
| `tools/lint_generated.py` | Lints every generated top with Verilator (locally or `remote --host <box>`). |
| `tools/verify_pinmap_against_vendor.py` | Checks board pinmaps against the vendor constraint files (Digilent XDC so far). |
| `toolchains/<id>/<id>.py` | Per-toolchain driver. Each defines `synthesize(...)` and `program(...)`. |

The main configuration registries reject duplicate YAML mapping keys and IDs,
and malformed entries. A contributor should correct the named source file rather
than relying on load order to choose a record. Board and chip family registries
also reject duplicate IDs across files; a board's pinmap stays under the same
producer/family directory as its catalog entry.

## Boards as rigs (preview)

For the Arty A7 and the Tang Primer 20K Dock a configuration can also be read
as the physical rig it describes: the board's connectors and on-board devices
(`config/layouts/<board>.yml`), the add-on modules and their pinouts
(`config/modules/`), and a setup that says what is used and which module pin
goes to which connector pin (`config/setups/<id>.yml`):

```yaml
Setup:
  id: arty_a7_pmod_mic3
  board: arty_a7
  toolchain: vivado
  toolchains: [vivado, nextpnr_openxc7]
  part: 35t
  parts: [35t, 100t]
  use:
    - onboard: clock
    - onboard: leds
    - module: digilent_pmod_mic3
      plug: {connector: jd, row: 2}
    - module: tm1638_led_key
      wires: {CLK: ck.IO40, STB: ck.IO41, DIO: ck.IO39}
    - gpio: ck
      params: {width: 30}
```

A rig exists once, whatever builds it: `toolchain:` and `part:` are its
defaults, `toolchains:` and `parts:` every toolchain and chip it is checked
with. Where a toolchain needs something else, `for_toolchain: {<toolchain>:
<patch>}` says only what changes — on a use (it moves with the use) or on
the setup for the rest (`config/overlay.py`). `aliases:` keeps the ids of the
per-toolchain and per-chip copies a rig replaced (`arty_a7_35_pmod_mic3_openxc7`
still builds `arty_a7_pmod_mic3` with nextpnr_openxc7 for the 35T); any other
build is `<rig>@<toolchain>[@<part>]`, or `synthesize.py -t <toolchain> --part <part>`.

```bash
./unifpga serve                # the board editor: http://127.0.0.1:8765/
./unifpga setup check          # each setup generates its configuration (data and text); rig errors
./unifpga setup generate [id]  # write config/configurations/<id>.yml from the setup
./unifpga setup derive <id>    # write a setup from an existing configuration
./unifpga view <setup id>      # the same drawing as a read-only HTML file (--board <board> for a board alone)
```

The board editor draws three columns: the virtual device `design_top` sees
(its ports and bits), the board (on-board devices, connectors with numbered
pins) and the add-on modules with their wires. Click a design bit, a header
pin, a wire, a module or a device to trace it end to end (design bit → part →
module pin → header pin → pinmap entry → FPGA pin); the table under the
drawing lists every connection. Editing: add a module, click one of its pins
and then a header pin to wire it (or pick the pin from a list), disconnect,
remove, reorder, use an on-board device or not, hand a connector to the design
as gpio, set parameters. Each edit is checked by the build's own code; Save
writes the setup and its configuration, Generate project writes the
SystemVerilog project for a chosen design (`designs/<design>/run/<setup>/`:
`top.sv`, constraints, toolchain project; downloadable as a zip). Links can
open a selection directly: `/?setup=<id>&sel=vbit:leds:led:2`, `sel=pin:jd:7`.

The check reports pins used twice (connectors that share FPGA pins with an
on-board connector included), module signals the peripheral does not have,
required signals left unwired and supply-voltage mismatches. Connector pin
numbers come from the vendors' files (Digilent's Arty XDC, Sipeed's Dock
schematic); module pinouts not checked against a vendor document say so
(`verified: false`).

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

# Same design, the configuration's other toolchain (open-flow alternative;
# -c basys3@nextpnr_openxc7 says the same):
PYTHONPATH=. python3 synthesize.py \
    -c basys3 -t nextpnr_openxc7 \
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
- **A design with multiple RTL files or ROM data**: add `fileset.yml` in the
  design directory when source order or alternative implementations matter.
  Its version 1 mapping has ordered `sources` (including `design_top.sv`),
  `simulation` (usually `tb.sv`), and `assets` (`.hex`/`.mem`) lists. Paths are
  relative to the design directory and must identify existing files inside it.
  `sources` feed both synthesis and simulation; `simulation` feeds only
  simulation. Assets are copied with their relative paths into each run's
  working directory, so `$readmemh` paths should be relative to that directory.
  Designs without a manifest use a recursive source scan that excludes `run/`
  and `build/` outputs. See `designs/5_5_aps/fileset.yml` for an ordered
  example that selects one of two CPU implementations.
- **A new board**: write its pinmap under
  `config/boards/<producer>/<family>/<id>.yml` (copy a board on a similar
  chip), list it in the family catalog `config/boards/<producer>/<family>.yml`,
  and add a configuration `config/configurations/<id>.yml` that attaches
  peripherals to its pin banks. For Digilent boards,
  `tools/verify_pinmap_against_vendor.py` checks the pins against the vendor XDC.
- **A new toolchain**: add `toolchains/<id>/<id>.py` exposing `synthesize`
  and `program`, plus a `config/toolchains.yml` entry with an explicit
  `SupportedOperations` list. Mark unfinished methods unsupported and make
  them return a nonzero error. Existing functional drivers are templates —
  `vivado.py` for vendor TCL flows,
  `nextpnr_icestorm.py` for yosys/nextpnr open flows, `nextpnr_gatemate.py`
  for himbaechel-uarch flows, `quartus2.py` / `gowin_standard.py` for thin
  re-exports of an adjacent driver with a different InstallDir.

## Repository layout

```
.
├── synthesize.py
├── config/
│   ├── toolchains.yml         # registry of synthesis toolchains
│   ├── programmers.yml        # registry of bitstream loaders
│   ├── chips/                 # chip registry, per-family
│   │   ├── xilinx_amd/<family>.yml   # chips + DefaultToolchains[version_constraint]
│   │   ├── altera_intel/<family>.yml
│   │   └── ...                # other chip families
│   ├── boards/                # board catalogs + pinmaps
│   │   ├── xilinx_amd/        # producer dirs
│   │   │   ├── artix_7.yml    # family catalog: boards reference chips by Id
│   │   │   └── artix_7/<id>.yml  # per-board pinmaps
│   │   └── ...                # other family catalogs and pinmaps
│   ├── configurations/<id>.yml # per-config peripheral attachments
│   ├── profiles/<id>.yml      # design-wiring profiles
│   ├── peripherals/*.yml      # peripheral definitions
│   └── capabilities/*.yml     # abstract capabilities
├── designs/<name>/design_top.sv  # example designs
├── rtl/
│   └── peripherals/              # SV peripheral drivers
│       ├── designs_common/       # reusable helpers
│       └── design_top_interface.sv  # canonical user-design interface
├── toolchains/                   # driver modules
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
