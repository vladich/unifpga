# unifpga

A multi-vendor FPGA build abstraction. Designs use a virtual-device interface;
the catalog describes how board configurations and vendor or open-source
toolchains can provide it. A toolchain's `operations` and board evidence determine
which catalog entries are currently eligible for a build or programming run.

> **Status:** configuration and generated-project checks exist, but full
> toolchain, simulation, and physical-board coverage is incomplete. No board's
> pins are yet attested for hardware, so builds and programming stop at the
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

Until a board's pins have a reviewed attestation, use `../../unifpga prepare`
to inspect generated project files; `build` and `program` require that
attestation and an installed supported toolchain.

Other commands: `unifpga sim` (Icarus Verilog on a design's `tb.sv`, then
gtkwave / surfer, for designs that ship a testbench), `unifpga gui` (the last
build in the vendor GUI), `unifpga prepare --all` (the run directories of every
design, no tools run), `unifpga program --no-build` (load the last build's
bitstream again), `unifpga clean` (`--all`: every design).

`unifpga sim`, `build`, `prepare`, and `program` accept repeatable
`--component-export <manifest.json>` inputs. Each versioned manifest names
generated `.v`/`.sv` sources with byte counts and SHA-256 digests. All four
commands check and snapshot the exact RTL bytes before invoking Icarus or a
toolchain driver. The snapshots and manifests live in a content-addressed
directory under each run output. The exporter generates RTL; unifpga owns
source selection, project generation,
and build orchestration. `--tb-top <module>` selects another simulation top;
`--output-dir <empty-directory>` isolates simulation outputs. A nextpnr `gui`
rerun also accepts the exports; if the run contains an export snapshot, supply
the manifests again. This experimental intake does not resolve RTL include
files or prove generated code's behavior, board electrical limits, or timing.

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
2. **The board and the rig.** A board is one file,
   `config/boards/<producer>/<family>/<id>.yml`: its catalogue fields, its banks
   of pins and, as drawn, its headers and parts. A rig is
   one file, `config/setups/<id>.yml`: what is on the board and how the design
   sees it — the parts used, the modules on its headers, and a `design:`
   section (which key resets, the design clock, which physical bits are which
   design bits). The build expands the setup into the rig's configuration
   (peripherals such as `led_bank`, `vga_4bit`, `tm1638_led_key` attached to
   pin banks) when it loads it; that configuration is never a file.
3. **The toolchain** lives in `toolchains/<id>/<id>.py` and knows how to
   drive its vendor / open-source tool in batch mode (Vivado XDC, Quartus
   QSF/SDC, Gowin CST, Efinity peri.xml + project.xml, iCE40 PCF, ECP5 LPF,
   GateMate CCF, openxc7 simple-form XDC, mistral QSF re-use).

`synthesize.py` glues them together: it codegens a `top.sv` wrapper that
maps physical pins to the design's virtual capability ports, emits the right
constraint file, then dispatches to the toolchain driver.

The toolchain registry declares each toolchain's `operations` separately from install
detection. `./unifpga tools` shows both. Catalogue-only toolchains have `[]`;
attempting their build or program operation exits before creating build output.
ISE and Libero SoC currently support synthesis but require an external
programming workflow. An implemented driver operation does not by itself
verify a board's pins or electrical constraints.

Physical builds and programming now require a reviewed `verification` record
in the board file. A missing record, a placeholder status, a stale digest of
the resolved banks, an uncovered FPGA part, or missing pinout/electrical evidence
stops the operation before tool setup. No board has yet been
attested for hardware, so the current catalog supports project inspection via
`UNIFPGA_DRY_RUN=1` while board evidence is collected. Dry-run success is not
a bitstream or a programmed device. To admit a board, review its exact pins
and I/O electrical settings against versioned vendor sources, then add:

```yaml
verification:
  status: verified
  banks_sha256: <sha256 of the board's id, banks, defaults and toolchain_options as resolved for the rig>
  parts: [<the exact chip id selected>]
  pinout: {source: <vendor schematic or constraint>, revision: <exact revision>}
  electrical: {source: <vendor electrical document>, revision: <exact revision>}
```

`config.init.board_fingerprint(resolved["board"])` computes the digest
for a configuration. Pin or I/O overrides change it, so an attestation of a
base board does not admit a different wiring variant. Evidence text is a
review record; it is not independently checked against the vendor document
at runtime. Build-artifact/device pairing remains a separate programming task.

Before a build or board-programming request, version admission also checks the
toolchain's configured `version` against the detected install-directory version
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

The example designs in `designs/` and most boards' pin data started from
[basics-graphics-music](https://github.com/yuri-panchul/basics-graphics-music)
by Yuri Panchul and contributors; see
[`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md) for their licences.

## What's in the box

| Path | What it holds |
|---|---|
| `unifpga` | Short command line (`board`, `build`, `program`, `sim`, `gui`, `prepare`, `clean`, `tools`, `designs`); logic in `tools/cli.py`. Remembers the board in `settings.yml`, builds into `<design>/run/<configuration>/`. |
| `synthesize.py` | Top-level entry point. Resolves a configuration, codegens `top.sv`, dispatches to the toolchain. |
| `config/boards/<producer>/<family>/<id>.yml` | One board, one file: its catalogue fields (name, chip or chip variants, programmer, bridges, features, devices), its banks of pins, its verification for hardware builds the documents its model is checked against (`documents:`; `./unifpga sources fetch` records their digests) with each bank, header and part read from one carrying its `source`, and, as drawn, its own connector types, headers and parts (`./unifpga layout draft <board>` regenerates the drawn section from the banks, the rigs and the board-sources registry). The directory is the board's chip family. |
| `config/chips/<producer>/<family>.yml` | One chip family, one file: its producer, its name as the vendor's tools want it, its chips and the toolchains that build for them (with optional `[version]` constraints; a chip may name its own). Boards reference chips by id, programmers reference families by id. |
| `config/toolchains.yml` | Toolchain registry, including the executable operations each driver supports. |
| `config/programmers.yml` | Registry of bundled vendor, third-party, and board-specific bitstream loaders. |
| `config/producers.yml` | Registry of producers of boards, chips and modules with URL, country, founding year, categories, and description. A board's, a chip family's and a mezzanine's `producer:` references one of these by id. |
| `config/features.yml`, `config/kinds.yml`, `config/devices.yml` | The vocabulary behind a board's catalogue fields: features (browsable classes such as `user_leds`, `hdmi_output`, `sdr_sdram`, each with the capabilities a device of the class can provide), the kinds a board's banks give their devices (each with the features it implies; `./unifpga check` reports a board whose banks imply a feature it does not list), and the named chips and modules with the peripherals that drive them. |
| `config/setups/<id>.yml` | A rig, one file: board, toolchain(s), the ordered `use:` list (on-board parts, modules on connectors, headers as gpio, raw attaches) with each use's `params`, `pins`, `bind` and `design_bits`, and the `design:` section (`reset`, `clock`, `uart_rx`, `width`, `tie`). `tools/setup.py` expands it into the configuration the build reads (`./unifpga setup show <id>` prints it); `UNIFPGA_PROFILE=0` / `synthesize.py --no-profile` leaves the design section out (buses in attach order, a power-up reset). |
| `config/modules/<id>.yml`, `config/connectors.yml` | Add-on modules with their pinouts, and the connector types (how a header is numbered). |
| `config/peripherals/*.yml` | Peripheral definitions (`led_bank`, `vga_4bit`, `gpio_header`, `tm1638_led_key`, `inmp441_i2s_mic`, …). |
| `config/capabilities/*.yml`, `config/design_top.yml` | Abstract user-facing capabilities (`leds`, `screen`, `gpio`, `audio_in`, …) with aggregation rules and what each puts on `design_top` (its `design:` block, with the prose of the interface); `design_top.yml` orders them into the virtual device. `rtl/peripherals/design_top_interface.sv` is rendered from both, and so is every design's module header: a design says `module design_top` then `` `include "design_top_interface.svh" ``, and every build renders that include beside it (the device without the optional capabilities the design's `// requires:` does not name; `./unifpga interface --write` renders them all and converts a hand-written header). |
| `config/schema/` | The schema of the configuration: one JSON Schema (2020-12) per kind of file and `entities.yml`, which names every entity, where its records live and its relationships to the others; `./unifpga check` validates every file and resolves every reference against it (`tools/check.py`). |

| `rtl/peripherals/*.sv` | Driver SV modules for hardware peripherals (TM1638 controller, VGA, I²S mic, etc.). |
| `rtl/peripherals/designs_common/*.sv` | Reusable helpers (`seven_segment_display`, `shift_reg`, `strobe_gen`, …). |
| `rtl/peripherals/design_top_interface.sv` | Canonical `design_top` port list — copy and add your logic. |
| `designs/<name>/design_top.sv` | Example designs. |
| `tools/codegen.py` | Generates `top.sv` and per-toolchain constraint files from a resolved configuration. |
| `tools/lint_generated.py` | Lints every generated top with Verilator (locally or `remote --host <box>`). |
| `tools/verify_pinmap_against_vendor.py` | Checks a board's banks against the vendor constraint file its documents list (Digilent XDC so far), fetched into the document cache. |
| `toolchains/<id>/<id>.py` | Per-toolchain driver. Each defines `synthesize(...)` and `program(...)`. |

The main configuration registries reject duplicate YAML mapping keys and IDs,
and malformed entries. A contributor should correct the named source file rather
than relying on load order to choose a record. Board and chip family registries
also reject duplicate IDs across files; a board is one file under its
producer/family directory.

## Boards as rigs (preview)

For the Arty A7 and the Tang Primer 20K Dock a configuration can also be read
as the physical rig it describes: the board's connectors and on-board devices
(its headers and parts, drawn in its file), the add-on modules and their pinouts
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
./serve.sh                     # the board editor: http://127.0.0.1:8765/ (makes .venv with requirements.txt when python3 lacks them)
./unifpga serve                # the same, with a Python that has PyYAML (--port for another port)
./unifpga setup check [id]     # each rig expands and resolves; rig errors (pins used twice, unwired signals, ...)
./unifpga setup show <id>      # the configuration a rig expands to (what the build reads; not a file)
./unifpga check [entity...]    # every configuration file against its schema (config/schema/), every reference resolved
./unifpga sources fetch|verify [board]   # the documents a board's file lists: fetch them, check the facts read from them against its banks
./unifpga interface [--write]  # the design_top interface rendered from config/design_top.yml and the capabilities: the .sv and each design's include
./unifpga view <setup id>      # the same drawing as a read-only HTML file (--board <board> for a board alone)
```

Run the project suite with `python3 -m pytest`; pytest discovers `tests/` by
default. The Slang, LiteX and FuseSoC probes under `experiments/` each use a
separate locked Python environment and have their own test commands in their
READMEs. Run those explicitly when changing a probe or its integration; they
are not part of the project's default pytest collection.
A trusted local SystemVerilog fileset can now produce a review-only import
candidate with `python -m tools.sv_import_candidate --root <source-root> --request <request.json>`
in the locked Slang environment; `experiments/slang/README.md` describes its
evidence and limits.

The board editor draws three columns: the virtual device `design_top` sees
(its ports and bits), the board (on-board devices, connectors with numbered
pins) and the add-on modules with their wires. Click a design bit, a header
pin, a wire, a module or a device to trace it end to end (design bit → part →
module pin → header pin → bank reference → FPGA pin); the table under the
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
| `nextpnr_gatemate` | 1 | gatemate_evb_a1 (Cologne Chip CCGM1A1) | Smoke-tested (placeholder pins; needs verified pads for hardware target) |

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
  example that selects one of two CPU implementations. Its additional
  `tb_firmware` scenario runs through `./unifpga sim designs/5_5_aps
  --tb-top tb_firmware --no-wave`: UART's four-byte finish command releases
  the CPU, and the preloaded RISC-V program reads the timer and writes two
  successive LED values. The testbench moves the timer counter near each
  ten-million-tick threshold to keep simulation bounded; it does not validate
  a full second of timing, synthesis, or any LiteX component integration.
- **A new board**: write its file
  `config/boards/<producer>/<family>/<id>.yml` (copy a board on a similar
  chip: the catalogue fields, then its banks of pins; `./unifpga check`
  validates it), draw it (`./unifpga layout draft <id>`) and write a rig
  `config/setups/<id>.yml` (or make it in the board editor). For Digilent boards,
  `tools/verify_pinmap_against_vendor.py` checks the pins against the vendor XDC
  the board lists among its documents.
- **A new toolchain**: add `toolchains/<id>/<id>.py` exposing `synthesize`
  and `program`, plus a `config/toolchains.yml` entry with an explicit
  `operations` list. Mark unfinished methods unsupported and make
  them return a nonzero error. Existing functional drivers are templates —
  `vivado.py` for vendor TCL flows,
  `nextpnr_icestorm.py` for yosys/nextpnr open flows, `nextpnr_gatemate.py`
  for himbaechel-uarch flows, `quartus2.py` / `gowin_standard.py` for thin
  re-exports of an adjacent driver with a different `install_dir`.

## Repository layout

```
.
├── synthesize.py
├── config/
│   ├── toolchains.yml         # registry of synthesis toolchains
│   ├── programmers.yml        # registry of bitstream loaders
│   ├── producers.yml          # who makes boards, chips and modules
│   ├── chips/                 # chip families, one file each
│   │   ├── xilinx_amd/<family>.yml   # producer, name, chips + default_toolchains[version_constraint]
│   │   ├── intel_altera/<family>.yml
│   │   └── ...                # other producers' families
│   ├── boards/                # boards, one file each, grouped like the chip families
│   │   ├── xilinx_amd/        # producer dirs
│   │   │   └── artix_7/<id>.yml  # a board of that family: catalogue fields, banks, drawing
│   │   └── ...
│   ├── mezzanines/<producer>/<family>.yml  # SoMs and daughtercards, catalogue only
│   ├── setups/<id>.yml        # rigs: parts, modules, and how the design sees them
│   ├── modules/<id>.yml       # add-on modules (Pmods, breakouts)
│   ├── peripherals/*.yml      # peripheral definitions
│   ├── capabilities/*.yml     # abstract capabilities
│   └── schema/                # JSON Schemas + entities.yml: the entities and their relationships
├── designs/<name>/design_top.sv  # example designs
├── rtl/
│   └── peripherals/              # SV peripheral drivers
│       ├── designs_common/       # reusable helpers
│       └── design_top_interface.sv  # the user-design interface, rendered from the capabilities
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
│   ├── check.py               # ./unifpga check: schemas, identities, references, rules
│   ├── design_requirements.py # // requires: parser
│   ├── sweep_boards.sh        # board × design grid run
│   └── check_all_designs.sh   # smoke-check every design
└── tests/                        # test_check.py (schemas and relationships), test_config_consistency.py, ...
```
