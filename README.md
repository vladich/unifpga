# unifpga

A multi-vendor FPGA build abstraction. A design is written once against a
virtual device, `design_top`. A **rig** — a board together with what is on it:
the board's own parts that are used, the add-on modules on its headers, the
headers handed to the design as gpio, and how the design's bits map onto all
of that — says how the virtual device is realised. A toolchain driver turns the
rig and the design into a bitstream. A toolchain's `operations` and the board's
hardware evidence decide what can currently be built or programmed.

> **Status:** configuration and generated-project checks exist, but toolchain,
> simulation and physical-board coverage is incomplete. No board's pins are
> attested for hardware yet (no board file carries a `verification` record with
> `status: verified`), so builds and programming stop at the hardware-readiness
> gate. Treat any first physical run as a bring-up.

## Quick start

`./unifpga` needs Python 3 with the packages of `requirements.txt` (PyYAML,
jsonschema). It runs with the `python3` that has them; otherwise it makes the
repository's `.venv` with them on first use and runs with that (nothing is
installed into a Python of yours; `UNIFPGA_PYTHON=<interpreter>` names one to
use instead).

```bash
./unifpga board          # pick your rig once (remembered in settings.yml; -l lists them)
cd designs/1_06_binary_counter
../../unifpga prepare    # inspect generated files without running tools
# After board attestation and toolchain setup:
../../unifpga build      # or: ./unifpga build 1_06_binary_counter from the repo root
../../unifpga program    # build and load the bitstream onto the board
```

`board -l` lists every rig and every alias of one (a rig built with another
toolchain or chip, see below) as a configuration to choose from. Until a
board's pins have a reviewed attestation, use `../../unifpga prepare` to
inspect generated project files; `build` and `program` require that
attestation and an installed supported toolchain.

Other commands: `unifpga sim` (Icarus Verilog on a design's `tb.sv`, then
gtkwave / surfer, for designs that ship a testbench), `unifpga gui` (the last
build in the vendor GUI), `unifpga prepare --all` (the run directories of every
design, no tools run), `unifpga program --no-build` (load the last build's
bitstream again), `unifpga clean` (`--all`: every design), `unifpga tools`
(where each toolchain was found, or why not), `unifpga designs`. The
configuration commands are listed under [Rigs](#rigs).

`unifpga sim`, `build`, `prepare`, and `program` accept repeatable
`--component-export <manifest.json>` inputs. Each versioned manifest names
generated `.v`/`.sv` sources with byte counts and SHA-256 digests. All four
commands check and snapshot the exact RTL bytes before invoking Icarus or a
toolchain driver. The snapshots and manifests live in a content-addressed
directory under each run output. The exporter generates RTL; unifpga owns
source selection, project generation, and build orchestration. `--tb-top
<module>` selects another simulation top; `--output-dir <empty-directory>`
isolates simulation outputs. A nextpnr `gui` rerun also accepts the exports;
if the run contains an export snapshot, supply the manifests again. This
experimental intake does not resolve RTL include files or prove generated
code's behavior, board electrical limits, or timing.

`build` writes everything (generated `top.sv`, constraints, the toolchain
project and bitstream) to `run/<configuration>/` inside the design directory;
`../../unifpga clean` removes `run/`. `./unifpga -h` lists every command;
`UNIFPGA_BOARD=<id>` overrides the remembered choice for one command.
`synthesize.py` (below) remains the full-control form; `program.py -c <id> -o
<build dir>` loads an existing build.

## Why it exists

A typical FPGA design is married to one board's pinout, that board's display
and audio I/O, that board's clock, and that vendor's preferred toolchain.
Moving to a different board means rewriting top-level wiring, constraints,
and build scripts. unifpga separates three concerns:

1. **The design** is plain SystemVerilog written against a fixed virtual-device
   interface (`rtl/peripherals/design_top_interface.sv`, rendered from the
   capabilities). It refers to abstract capabilities — `led`, `btn`, `sw`,
   `abcdefgh`/`digit`, `red`/`green`/`blue` pixel out, `mic_sample`, `gpio`,
   `uart_rx`/`uart_tx`, `kbd_key`, `mem_addr`, … — never to physical pins.
2. **The board and the rig.** A board is one file,
   `config/boards/<producer>/<family>/<id>.yml`: its catalogue fields, its
   banks of pins and, as drawn, its headers and parts. A rig is one file,
   `config/setups/<id>.yml`: which board, which of its parts are used, which
   modules sit on which header pins, which headers the design gets as gpio,
   and a `design:` section (which key resets, the design clock, which physical
   bits are which design bits). The build expands the rig into its
   configuration — peripherals such as `led_bank`, `vga_4bit`,
   `tm1638_led_key` attached to pin banks — when it loads it; that
   configuration is never a file.
3. **The toolchain** lives in `toolchains/<id>/<id>.py` and drives its vendor
   or open-source tool in batch mode. `tools/codegen.py` emits the constraint
   and project files each one reads: Vivado XDC, openxc7's simple-form XDC,
   ISE UCF, Quartus QSF + SDC, Gowin CST + project, Efinity `peri.xml` +
   project XML, iCE40 PCF, ECP5 LPF, Microchip PDC, Lattice Nexus PDC,
   GateMate CCF.

`synthesize.py` glues them together: it codegens a `top.sv` wrapper that
maps physical pins to the design's virtual capability ports, emits the right
constraint file, then dispatches to the toolchain driver.

The toolchain registry (`config/toolchains.yml`, 33 toolchains) declares each
toolchain's `operations` separately from install detection; `./unifpga tools`
shows both. 18 toolchains have a driver: 16 synthesize and program, ISE and
Libero SoC synthesize only (programming needs the vendor's own workflow). The
other 15 are catalogue entries with `operations: []`, named by chip families so
their boards are catalogued correctly; asking them to build exits before any
output is created. An implemented driver does not by itself verify a board's
pins or electrical constraints.

Physical builds and programming require a reviewed `verification` record in
the board file. `config.init.require_hardware_readiness` stops the operation
before tool setup when the record is missing or not `verified`, when its digest
of the resolved banks is missing or stale, when it does not cover the chip
selected, or when its pinout or electrical evidence lacks a source and a
revision. No board is attested yet, so the catalogue currently supports
project inspection (`./unifpga prepare`, or `UNIFPGA_DRY_RUN=1` with
`synthesize.py`) while evidence is collected. Dry-run success is not a
bitstream or a programmed device. To admit a board, review its exact pins and
I/O electrical settings against versioned vendor sources, then add:

```yaml
verification:
  status: verified
  banks_sha256: <sha256 of the board's id, banks, defaults and toolchain_options as resolved for the rig>
  parts: [<the exact chip id selected>]
  pinout:
    source: <vendor schematic or constraint>
    revision: <exact revision>
  electrical:
    source: <vendor electrical document>
    revision: <exact revision>
```

`config.init.board_fingerprint(resolved["board"])` computes the digest for a
configuration. Pin or I/O overrides change it, so an attestation of a base
board does not admit a different wiring variant. Evidence text is a review
record; it is not independently checked against the vendor document at
runtime. Build-artifact/device pairing remains a separate programming task.

Before a build or board-programming request, version admission also checks
the toolchain's configured `version` against the detected install-directory
version and checks that version against any chip constraint, such as
`vivado[2024.1+]`. `*`, an exact version, a minimum (`2024.1+`) and an
inclusive range (`2023.2-2024.2`) are the accepted forms
(`tools/toolchain_detect.py matches_version`). The detected version comes
from the installation path; a vendor-binary version probe is still needed for
hardware-grade provenance.

A `// requires:` block at the top of a design declares hard capability needs
(`switches >= 4`, `screen >= 640x480`, `gpio >= 16`, conditions such as
`switches >= 3 if !(w_btn >= 3)`) which `synthesize.py` checks against the
resolved configuration before invoking any tool (94 of the 105 designs carry
one). A rig that does not meet them **skips** instead of failing — surfacing
the real reason cleanly.

## Where the material comes from

The example designs under `designs/` and their testbenches, 22 of the RTL
drivers and the `designs_common` helpers under `rtl/peripherals/`, and the pin
assignments of 48 board files were adapted from
[basics-graphics-music](https://github.com/yuri-panchul/basics-graphics-music)
by Yuri Panchul and contributors. The pin assignments of 46 more boards come
from LiteX-Boards, of 14 from Digilent's XDC files, and the rest from AMD's
board files and the vendors' documents (each board's `documents:`).
[`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md) lists every file and
licence.

## What's in the box

| Path | What it holds |
|---|---|
| `unifpga` | The command line (`board`, `build`, `program`, `sim`, `gui`, `prepare`, `clean`, `tools`, `designs`, `setup`, `check`, `interface`, `layout`, `sources`, `inventory`, `view`, `serve`); the launcher brings the Python (above), the logic is `tools/cli.py`. Remembers the rig in `settings.yml`, builds into `<design>/run/<configuration>/`. |
| `synthesize.py`, `program.py` | The full-control entry points: resolve a configuration, codegen `top.sv`, dispatch to the toolchain; load an existing build. |
| `config/boards/<producer>/<family>/<id>.yml` | One board, one file — 726 boards under 13 producer directories and 85 chip-family directories. Every file has the catalogue fields (name, chip or chip variants, programmer, bridges, features, devices); 170 have banks of pins; 68 list the documents their facts were read from (`documents:`; `./unifpga sources fetch` records their digests, `verify` checks the facts read from them against the banks); the 53 boards a rig uses are also drawn: their own connector types, headers and parts (`./unifpga layout draft <board>` regenerates that section from the banks, the rigs and the documents' facts). 9 boards are one product in several FPGA sizes (`chips:`; a rig picks one with `part:`). |
| `config/chips/<producer>/<family>.yml` | One chip family, one file — 86 families, 547 chips. Each names its producer, the family name the vendor's tools want, its chips and the toolchains that build for them (with optional `[version]` constraints; a chip may name its own). Boards reference chips by id, programmers reference families by id. |
| `config/toolchains.yml`, `config/programmers.yml` | 33 toolchains with the operations each driver supports, its configured version and install directory; 27 bitstream loaders (18 bundled with a toolchain, the rest third-party or board-specific). |
| `config/producers.yml`, `config/mezzanines/<producer>/<family>.yml` | 76 producers of boards, chips and modules (URL, country, founding year, categories, description; a board's, a chip family's and a mezzanine's `producer:` references one by id); 88 SoMs and daughtercards in 16 files, catalogue only. |
| `config/features.yml`, `config/kinds.yml`, `config/devices.yml` | The vocabulary behind a board's catalogue fields: 115 features (browsable classes such as `user_leds`, `hdmi_output`, `sdr_sdram`, each with the capabilities a device of the class can provide), 50 kinds a board's banks give their devices (each with the features it implies; `./unifpga check` reports a board whose banks imply a feature it does not list), 171 named chips and modules with the peripherals that drive them. |
| `config/setups/<id>.yml` | A rig, one file — 97 rigs on 53 boards, 80 of them with add-on modules. Board, toolchain(s) and chip(s), the ordered `use:` list (on-board parts, modules on connectors, headers as gpio, raw attaches) with each use's `params`, `pins`, `bind` and `design_bits`, and the `design:` section (`reset`, `clock`, `uart_rx`, `width`, `tie`). `tools/setup.py` expands it into the configuration the build reads (`./unifpga setup show <id>` prints it); `UNIFPGA_PROFILE=0` / `synthesize.py --no-profile` leaves the design section out (buses in attach order, a power-up reset). |
| `config/modules/<id>.yml`, `config/connectors.yml` | 16 add-on modules with their pinouts (5 checked against a vendor document, `verified: true`) and 4 connector types (how a header is numbered, which pins are power). |
| `config/peripherals/*.yml` | 55 peripheral definitions (`led_bank`, `vga_4bit`, `gpio_header`, `tm1638_led_key`, `inmp441_i2s_mic`, …): signals, parameters, the driver module, the bank device kinds each one models. |
| `config/capabilities/*.yml`, `config/design_top.yml` | 22 abstract capabilities (`leds`, `screen`, `gpio`, `audio_in`, …) with aggregation rules and what each puts on `design_top` (its `design:` block, with the prose of the interface); `design_top.yml` orders them into the virtual device. `rtl/peripherals/design_top_interface.sv` is rendered from both, and so is every design's module header: a design says `module design_top` then `` `include "design_top_interface.svh" ``, and every build renders that include beside it (the device without the optional capabilities the design's `// requires:` does not name; `./unifpga interface --write` renders them all and converts a hand-written header). |
| `config/schema/` | The schema of the configuration: one JSON Schema (2020-12) per kind of file and `entities.yml`, which names every entity, where its records live and its relationships to the others; `./unifpga check` validates every file and resolves every reference against it (`tools/check.py`). |
| `rtl/peripherals/*.sv`, `rtl/peripherals/designs_common/*.sv` | 45 driver modules for hardware peripherals (TM1638 controller, VGA, I²S microphone, HDMI, SDRAM, PS/2, …) and 10 reusable helpers (`seven_segment_display`, `shift_reg`, `strobe_gen`, …). Designs and shared RTL are vendor-neutral; codegen emits the vendor layer. |
| `designs/<name>/design_top.sv` | 105 example designs; 88 ship a `tb.sv`, 4 a `fileset.yml`. |
| `tools/` | `codegen.py` (top.sv and constraint emitters), `setup.py` (rigs → configurations, auto-wiring, checks), `check.py`, `layout_draft.py` (the drawn section of a board), `inventory.py` (board inventories into board files), `board_sources.py` (documents, digests, fact verification), `design_top.py` (the rendered interface), `trace.py` (design bit → pin traces for the editor), `studio.py` + `studio/` (the board editor), `source_set.py` (a design's files), `toolchain_detect.py`, `pll_solver.py`, `yamltext.py` (the one YAML emitter behind every writer), `lint_generated.py` (every generated top through Verilator, locally or `remote --host <box>`), `verify_pinmap_against_vendor.py` (a board's banks against the Digilent XDC among its documents), `catalog_snapshot.py` / `catalog_candidate.py`, and the import frontends `sv_import_candidate.py` / `edam_import_input.py` of the experiments. |
| `toolchains/<id>/<id>.py` | The drivers. `quartus_prime_lite`, `quartus_prime_standard`, `quartus_prime_pro` and `quartus2` re-export the shared `toolchains/quartus_prime/quartus_prime.py` with their own install directory; `gowin_standard` re-exports `gowin_eda`; `nextpnr_oxide` re-exports `nextpnr_nexus`. |
| `tests/` | 48 test files (`.venv/bin/python -m pytest`, see below); `tests/sim/` holds the testbenches of the RTL driver tests, which skip without Icarus Verilog. |

The configuration readers reject duplicate YAML mapping keys and duplicate ids
across files, and `./unifpga check` rejects a file that does not match its
schema or a reference that does not resolve; correct the named source file
rather than relying on load order.

## Rigs

Every board with a rig is drawn (its headers and parts, in its file), so a rig
can be read as the physical thing it describes: the board's connectors and
on-board devices, the add-on modules and their pinouts, and which module pin
goes to which connector pin. `config/setups/arty_a7_pmod_mic3.yml`, abridged:

```yaml
Setup:
  id: arty_a7_pmod_mic3
  board: arty_a7
  toolchain: vivado
  toolchains: [vivado, nextpnr_openxc7]
  part: 35t
  parts: [35t, 100t]
  aliases:
    arty_a7_100_pmod_mic3:
      part: 100t
    arty_a7_100_pmod_mic3_openxc7:
      toolchain: nextpnr_openxc7
      part: 100t
    arty_a7_35_pmod_mic3:
      part: 35t
    arty_a7_35_pmod_mic3_openxc7:
      toolchain: nextpnr_openxc7
      part: 35t
  use:
    - onboard: clock
    - onboard: switches
      design_bits:
        switches: [0, 1, 2, 3]
    # ... the LEDs, buttons, UART, reset and XADC shield of the board
    - module: digilent_pmod_mic3
      plug:
        connector: jd
        row: 2
    - module: tm1638_led_key
      wires:
        STB: ck.IO41
        CLK: ck.IO40
        DIO: ck.IO39
    - gpio: ck
      params:
        width: 30
    # ... a Pmod VGA wired pin by pin on jb and jc
```

A rig exists once, whatever builds it: `toolchain:` and `part:` are its
defaults, `toolchains:` and `parts:` every toolchain and chip it is checked
with. Where a toolchain needs something else, `for_toolchain:` names it and
says only what changes — on a use (it moves with the use) or on the setup for
the rest (`config/overlay.py`). `aliases:` keeps the ids of the per-toolchain
and per-chip copies a rig replaced (41 rigs carry 55 of them;
`arty_a7_35_pmod_mic3_openxc7` still builds `arty_a7_pmod_mic3` with
nextpnr_openxc7 for the 35T); any other build is `<rig>@<toolchain>[@<part>]`,
or `synthesize.py -t <toolchain> --part <part>`.

```bash
./serve.sh                       # the board editor: http://127.0.0.1:8765/ (= ./unifpga serve; the next free port when 8765 is taken, --port for one of your own)
./unifpga setup check [id...]    # each rig expands and resolves; rig errors (pins used twice, unwired signals, ...)
./unifpga setup show <id>        # the configuration a rig expands to (what the build reads; not a file)
./unifpga check [entity...]      # every configuration file against its schema (config/schema/), every reference resolved
./unifpga layout draft <board>|--all   # the drawn section of a board's file, from its banks, its rigs and its documents' facts
./unifpga sources fetch|text|verify [board...]   # a board's documents: fetch them into the cache, show their text, check the facts read from them against its banks
./unifpga inventory import <file...>   # a board inventory (every on-board device with its pins and sources) into the board's file
./unifpga interface [--write]    # the design_top interface rendered from config/design_top.yml and the capabilities: the .sv and each design's include
./unifpga view <rig id>          # the editor's drawing as a read-only HTML file (--board <board id> for a board alone)
```

Run the project suite with `.venv/bin/python -m pytest` (pytest is not in
`requirements.txt`: `.venv/bin/python -m pip install pytest` once, when the
launcher made the `.venv`; any Python with PyYAML, jsonschema and pytest does).
The Slang, LiteX and FuseSoC probes under `experiments/` each use a separate
locked Python environment and have their own test commands in their READMEs;
they are not part of the default collection. A trusted local SystemVerilog
fileset can produce a review-only import candidate with `python -m
tools.sv_import_candidate --root <source-root> --request <request.json>` in the
locked Slang environment (`experiments/slang/README.md`); for a resolved
FuseSoC EDAM, `python -m tools.edam_import_input` admits its source closure and
can run the same frontend with `--candidate` (`experiments/fusesoc/README.md`).

The board editor draws three columns: the virtual device `design_top` sees
(its ports and bits), the board (on-board devices, connectors with numbered
pins) and the add-on modules with their wires. Click a design bit, a header
pin, a wire, a module or a device to trace it end to end (design bit → part →
module pin → header pin → bank reference → FPGA pin); the table under the
drawing lists every connection. Editing: add a module, click one of its pins
and then a header pin to wire it (or pick the pin from a list), auto-wire it,
disconnect, remove, reorder, use an on-board device or not, hand a connector
to the design as gpio, set parameters. Each edit is checked by the build's own
code; Save writes the rig file, Generate project writes the SystemVerilog
project for a chosen design (`designs/<design>/run/<rig>/`: `top.sv`,
constraints, toolchain project; downloadable as a zip). Links can open a
selection directly: `/?setup=<id>&sel=vbit:leds:led:2`, `sel=pin:jd:7`,
`sel=wire:11:CLK`, `sel=use:11`, `sel=onboard:leds`, `sel=conn:ck`.

The rig check reports pins used twice (connectors that share FPGA pins with
an on-board device included), unknown connectors or pins, module signals the
peripheral does not have, required signals left unwired and supply-voltage
mismatches. Header pin numbers are verified against a vendor document for two
boards (the Arty A7 from Digilent's XDC, the Tang Primer 20K Dock from Sipeed's
schematic); the other 51 drawn boards list a header's pins in the bank's
order and say the physical numbers are not verified yet. Module pinouts not
checked against a vendor document say so (`verified: false`; 11 of 16).

## Toolchain coverage

What each driver builds today, from the rigs and the chip families
(`./unifpga tools` says which are installed on this machine). The version is
the one configured in `config/toolchains.yml`; every rig on a board builds
with every toolchain in its `toolchains:` list. No result here is hardware
evidence: no board is attested, and earlier synthesis sweeps (git history,
May 2026) have not been re-run against the current tree.

| Toolchain | Rigs | Boards | Chip families naming it | Configured version |
|---|---|---|---|---|
| `vivado` | 12 | A7-Lite 35T, Alinx AX7035B, Arty A7, Basys 3, Eclypse Z7, Nexys 4, Nexys 4 DDR, Nexys A7, QMtech Kintex 7, Zybo Z7 | 14 (7-series, UltraScale(+), Versal) | 2023.2 |
| `nextpnr_openxc7` | 11 | the same boards, open flow | 5 | git-master |
| `quartus_prime_lite` | 24 | Alinx AX301, Alinx AX4010, three EP4CE6 core boards, EPI-MiniCY4, Omdazz Cyclone IV, Zeowaa A-C4E6, Cyclone V GX Starter Kit, DE0-CV, DE0-Nano, DE0-Nano-SoC, DE1-SoC, DE10-Lite, DE10-Nano, DE2-115, Terasic SoCKit | 5 (Cyclone IV / V / V SoC / 10, MAX 10) | 23.1std |
| `quartus_prime_standard`, `quartus_prime_pro` | 0 | — | 3 (Arria V, Stratix IV / V); 6 (Arria 10, Stratix 10, Agilex) | — |
| `quartus2` | 6 | Cyclone III Development Kit, DE0, DE1, DE2, Marsohod MCY316, Omdazz EPM570 | 4 (Cyclone II / III, MAX II, Stratix III) | 13.0sp1 |
| `nextpnr_mistral` | 7 | Cyclone V GX Starter Kit, DE0-CV, DE0-Nano-SoC, DE1-SoC, DE10-Nano, Terasic SoCKit | 2 (Cyclone V, Cyclone V SoC) | git-master-d6bd02c |
| `gowin_eda` | 29 | Marsohod3 GW, Tang Nano 4K / 9K / 20K, Tang Primer 20K Dock / Lite | 8 (LittleBee GW1N*, Arora GW2A*) | 1.9.11.03.Educational |
| `gowin_standard` | 8 | Orange Pi MSOC, Tang Mega 138K, Tang Mega 138K Pro, Tang Primer 25K | 3 (AroraV GW5A / GW5AST / GW5AT) | 1.9.9.02 |
| `nextpnr_apicula` | 8 | Tang Nano 9K, Tang Primer 20K Dock | 8 | git-master |
| `nextpnr_icestorm` | 8 | iCE40HX8K-EVB, iCEBreaker | 2 (iCE40, iCE40 UltraPlus) | git-master |
| `nextpnr_trellis` | 3 | Colorlight 5A-75B, Karnix ASB-254, OrangeCrab | 3 (ECP5, MachXO2, MachXO3) | git-master |
| `nextpnr_nexus` (`nextpnr_oxide` re-exports it) | 1 | CrossLink-NX Evaluation Board | 4 (Certus-NX, CertusPro-NX, CrossLink-NX, MachXO5-NX) | git-master |
| `efinity` | 1 | FireAnt | 3 (Trion, Titanium, Topaz) | 2023.2 |
| `libero_soc` (synthesize only) | 1 | SmartFusion2 Starter Kit (M2S025T) | 5 (SmartFusion2, IGLOO2, PolarFire, PolarFire SoC, RTG4) | 2024.1 |
| `ise` (synthesize only) | 1 | Mojo v3 | 7 (Spartan-3 / 3A / 3E / 6, Virtex-4 / 5 / 6) | 14.7 |
| `nextpnr_gatemate` | 1 | GateMate Evaluation Board A1 | 1 | git-master |

Catalogue only (no driver, `operations: []`): `libero_ide`, `pango_ds`,
`lattice_radiant`, `lattice_diamond`, `icecube2`, `isplever`,
`isplever_classic`, `max_plus_2`, `actel_designer`, `ace`, `td`,
`forge_fpga`, `nanoxmap`, `quickworks`, `qorc_sdk`.

### Where the drivers look for their tools

`config/toolchains.yml` `install_dir` is the configured location the drivers
and `./unifpga tools` check: `~/Xilinx/Vivado/2023.2/`,
`~/intelFPGA_lite/23.1std/quartus/`, `~/altera/13.0sp1/quartus/`,
`/usr/local/microchip/Libero_SoC_v2024.1/Libero/`, `~/efinity/2023.2/`,
`~/Gowin/1.9.11.03.Educational/`, `~/Gowin/1.9.9.02/`. The open flows use
yosys and nextpnr from `oss-cad-suite` on the PATH, with two exceptions:
`nextpnr_openxc7` uses the openxc7 snap (`/snap/openxc7/current/...`) and
caches chip databases under `~/.cache/openxc7/`; `nextpnr_mistral` looks for
`nextpnr-mistral` on the PATH or in `~/Projects/nextpnr/build` (a local build
against mistral commit `d6bd02c`, the last with both `pos_t` and `rnode_t`).
`gowin_standard` needs a NODELOCK licence file at `~/Gowin/gowin_E_<HOST_ID>.lic`
named in `~/Gowin/1.9.9.02/IDE/bin/gwlicense.ini` (the driver's docstring has
the steps): the Educational edition ships the GW5* device tables but does not
unlock them. `openFPGALoader` from oss-cad-suite programs most boards.

## Advanced use: `synthesize.py` directly

`./unifpga build` runs `synthesize.py` for you; call it yourself for include
directories, a custom output directory or a throw-away temp build.

```bash
# Pick a configuration and a design:
PYTHONPATH=. python3 synthesize.py \
    -c basys3 \
    --top designs/2_9_pong/design_top.sv \
    -o build/ \
    --step elaborate     # or --step pnr, or --step full to produce a bitstream

# Same design, the rig's other toolchain (open-flow alternative;
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

- **A new design**: make `designs/<your_design>/design_top.sv` that starts
  with `module design_top` and, on the next line, `` `include
  "design_top_interface.svh" `` (every build renders that include beside it;
  `./unifpga interface --write` renders it now and shows the ports), add your
  logic in the body, optionally add a `// requires:` block.
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
  A separate pinned LiteX UART TX variant is exercised at the APS subsystem
  boundary by `experiments/litex/test_uart_rtl.py`; see
  `experiments/litex/README.md` for its fixed-rate bus contract and limits.
- **A new board**: write its file `config/boards/<producer>/<family>/<id>.yml`
  (copy a board on a similar chip: the catalogue fields, then its banks of
  pins, each bank's `device:` and `source:`; list the documents the pins were
  read from and `./unifpga sources fetch <id>` records their digests;
  `./unifpga check` validates the file, `./unifpga sources verify <id>` the
  facts), write a rig `config/setups/<id>.yml` (or make it in the board
  editor) and draw the board (`./unifpga layout draft <id>`). A board
  inventory (every on-board device with its pins and sources) goes in with
  `./unifpga inventory import`. For a board with a Digilent XDC among its
  documents, `tools/verify_pinmap_against_vendor.py` checks the pins against
  it.
- **A new toolchain**: add `toolchains/<id>/<id>.py` exposing `synthesize`
  and `program`, plus a `config/toolchains.yml` entry with an explicit
  `operations` list. Mark unfinished methods unsupported and make them return
  a nonzero error. Existing drivers are templates — `vivado.py` for vendor TCL
  flows, `nextpnr_icestorm.py` for yosys/nextpnr open flows,
  `nextpnr_gatemate.py` for himbaechel-uarch flows, `quartus2.py` /
  `gowin_standard.py` / `nextpnr_oxide.py` for a re-export of an adjacent
  driver under another id and install directory.

## Repository layout

```
.
├── unifpga                    # the command line (and the launcher that brings its Python)
├── serve.sh                   # the board editor (= ./unifpga serve)
├── synthesize.py, program.py  # the full-control entry points
├── requirements.txt           # PyYAML, jsonschema
├── config/
│   ├── toolchains.yml         # 33 toolchains: operations, version, install_dir
│   ├── programmers.yml        # 27 bitstream loaders
│   ├── producers.yml          # 76 makers of boards, chips and modules
│   ├── features.yml, kinds.yml, devices.yml   # the catalogue vocabulary
│   ├── connectors.yml         # 4 connector types
│   ├── design_top.yml         # the sections of the virtual device
│   ├── chips/<producer>/<family>.yml     # 86 chip families, 547 chips
│   ├── boards/<producer>/<family>/<id>.yml   # 726 boards: catalogue fields, banks, drawing
│   ├── mezzanines/<producer>/<family>.yml    # 88 SoMs and daughtercards, catalogue only
│   ├── setups/<id>.yml        # 97 rigs: parts, modules, gpio headers, the design section
│   ├── modules/<id>.yml       # 16 add-on modules (Pmods, breakouts)
│   ├── peripherals/*.yml      # 55 peripheral definitions
│   ├── capabilities/*.yml     # 22 abstract capabilities
│   ├── schema/                # JSON Schemas + entities.yml: the entities and their relationships
│   └── init.py, overlay.py, references.py, xdc.py   # the readers
├── designs/<name>/design_top.sv  # 105 example designs (tb.sv, fileset.yml where they have one)
├── rtl/peripherals/           # 45 driver modules, designs_common/ (10 helpers), design_top_interface.sv (rendered)
├── toolchains/<id>/<id>.py    # 18 drivers (and the shared quartus_prime implementation)
├── tools/                     # codegen, setup, check, layout_draft, inventory, board_sources, studio, ...
├── tests/                     # 48 test files; tests/sim/ testbenches
└── experiments/               # slang, litex, fusesoc probes, each with its own environment
```
