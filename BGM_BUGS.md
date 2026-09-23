# BGM bugs and the differences they cause

unifpga generates each board's top from YAML and compares it against the matching
`boards/<variant>/board_specific_top.sv` in
[basics-graphics-music](https://github.com/yuri-panchul/basics-graphics-music) (BGM).
This file lists the bugs that comparison found in BGM itself.

**Every statement below was checked in BGM's own files** (its Verilog tops, constraint
files, IP wrappers and build scripts) at BGM revision `d2c6e885` (2026-09-21), and where a
statement is about the board, against the board vendor's published files. unifpga's own
pinmaps, configurations and tools were not used as evidence, since they are converted
from BGM and may carry conversion errors of their own. Where the vendor side could not be
checked, the item says so.

Names: a *BGM variant* is a directory under BGM's `boards/`. unifpga's *configurations*
often share a BGM variant (for example `nexys4_openxc7` is unifpga's second toolchain
for BGM's `nexys4`); they are listed separately where they are affected.

**Policy.** unifpga reproduces BGM's correct behaviour, not its bugs. When
`tools/sync_from_bgm.py` recognises one of these bugs it records it under `bgm_bugs:`
in `config/bgm/<configuration>.yml` (documentation only; nothing is applied from it), and
`tools/equiv_check.py` and `tools/audit_configs.py` print it next to the board.

## Overview

unifpga's latest comparison (run 28, 2026-09-23) over its 134 configurations: 96 PASS,
5 PASS after a behaviour-neutral repair of a BGM source error (section 3), 20 DIFF, 4 BGM
sources that do not compile, 9 without a BGM variant or `lab_top` to compare with. Each of
the 20 differences comes from one of these BGM bugs:

| BGM variant(s) | Bug | unifpga configurations that differ | Section |
|---|---|---|---|
| a7_lite_35t | Reset synchroniser with the wrong polarity | a7_lite_35t, a7_lite_35t_openxc7 | 1.1 |
| nexys4 | Green and blue VGA channels swapped | nexys4, nexys4_openxc7 | 1.2 |
| tang_nano_9k_lcd_480_272_tm1638_yosys | Builds the 800x480 panel | same name | 1.3 |
| tang_nano_9k_lcd_480_272_no_tm1638_yosys | Panel clocked at 129.6 MHz | same name | 1.4 |
| tang_primer_20k_dock_* (8), tang_primer_20k_lite | A switch port with no pin | same names | 2.1 |
| tang_nano_9k_hdmi_no_ip_tm1638 | HDMI line counter never initialised | same name | 2.2 |
| orangepi_msoc | Undriven coordinates and switch net | same name | 2.3 |
| orangecrab_ecp5_yosys | Two modules drive the same pins | same name | 2.4 |
| de10_lite_tm1638_virtual_switches | Undriven TM1638 keys in the lab's key bus | same name | 2.5 |
| ice40hx8k_evb_yosys | No VGA timing generator | same name | 2.6 |

## 1. Bugs unifpga does not reproduce

These boards work correctly in unifpga and therefore differ from BGM by design.

### 1.1 a7_lite_35t: reset synchroniser with the wrong polarity

`boards/a7_lite_35t/board_specific_top.sv:55-60` (no preprocessor condition, no
parameter override anywhere in the variant):

```systemverilog
xpm_cdc_async_rst i_xpm_cdc_async_rst
(
   .dest_clk    (   clk       ),
   .dest_arst   (   rst       ),
   .src_arst    ( ~ RESETN    )
);
```

AMD's UG953 gives `RST_ACTIVE_HIGH` a default of 0, "Active-Low asynchronous reset
signal", and says the output "asserts asynchronously but deasserts synchronously to
dest_clk". BGM feeds it an active-high request (`~ RESETN`) and uses the output as the
active-high `rst`. The level inversions cancel, but the timing is reversed: with the
button pressed (`RESETN` low, as BGM's `~ RESETN` reads it) the lab reset asserts four
clocks later, and it releases at once when the button is let go.

unifpga keeps a normal synchroniser. Fix in BGM: `# (.RST_ACTIVE_HIGH (1))`.

### 1.2 nexys4: green and blue swapped

`boards/nexys4/board_specific_top.sv:112-113`, unconditional:

```systemverilog
assign vgaBlue  = display_on ? green : '0;
assign vgaGreen = display_on ? blue  : '0;
```

BGM's `board_specific.xdc` places `vgaRed`, `vgaGreen` and `vgaBlue` on exactly the pins
Digilent's `Nexys-4-Master.xdc` gives them (green A5 / A6 / B6 / C6, blue B7 / C7 / D7 /
D8), so the swap reaches the connector: the green pins carry blue and the other way round.

### 1.3 tang_nano_9k_lcd_480_272_tm1638_yosys builds the 800x480 panel

The variant's whole top is:

```systemverilog
`define USE_LCD_800_480
`include "../tang_nano_9k_lcd_480_272_tm1638/board_specific_top.sv"
```

which is identical to `tang_nano_9k_lcd_800_480_tm1638_yosys`. The directory name says
480x272; the build is the 800x480 timing and clock. (The two variants' constraint files
are identical too: both panels use the same connector.) unifpga builds the 480x272 panel.

### 1.4 tang_nano_9k_lcd_480_272_no_tm1638_yosys clocks its panel at 129.6 MHz

The top defines no `USE_LCD_800_480`, so the included top takes its 480x272 branch
(`tang_nano_9k_lcd_480_272_tm1638/board_specific_top.sv:372-374`), where the rPLL's
`clkout` drives the panel clock `LARGE_LCD_CK`. BGM's yosys flow
(`scripts/steps/00_setup_yosys.source_bash`) compiles the variant's own directory, and this
directory's `gowin_rpll.v` is the 800x480 one: `IDIV_SEL = 4`, `FBDIV_SEL = 23`, so
`clkout` = 27 × 24 / 5 = 129.6 MHz. The 480x272 variant's own `gowin_rpll.v`
(`IDIV_SEL = 2`, `FBDIV_SEL = 0`, `ODIV_SEL = 48`) gives the intended 9 MHz. unifpga
clocks the panel at 9 MHz.

### 1.5 tang_primer_20k_dock_hdmi_tm1638_yosys (and _no_tm1638_yosys): TMDS lanes out of order

`boards/tang_primer_20k_dock_hdmi_tm1638_yosys/board_specific_top.sv:299` onwards
puts `red_serial` on `TMDS_D_P[0]` and `blue_serial` on `TMDS_D_P[2]`. BGM's `dvi_top`
(`peripherals/dvi.sv:63-67`) encodes hsync / vsync into the blue encoder, so the syncs go
out on lane 2 as well.

- The DVI 1.0 specification (§3.2.1, Figure 3-2, page 26) maps `BLU[7:0]`, `HSYNC` and
  `VSYNC` to channel 0 and `RED[7:0]` to channel 2.
- Sipeed's own HDMI example for this board (`HDMI/src/dk_video.cst` in
  `sipeed/TangPrimer-20K-example`) puts `O_tmds_data_p[0]` on H14 / H16, the pins BGM
  calls `TMDS_D_P[0]`; the same board's non-yosys BGM variant uses identical pins.

So on this variant a sink receives red and the syncs on the wrong lanes. unifpga keeps the
DVI order. These configurations still PASS in unifpga's comparison, because the serial
TMDS lanes are reported there but not judged (section 5), and the sync does not record
this bug yet.

## 2. Bugs that leave BGM undefined or conflicting

On these variants BGM's own top reads an undriven or conflicting value. unifpga cannot and
does not reproduce an undefined value.

### 2.1 Tang Primer 20K Dock and Lite: a switch port with no pin

| BGM variant(s) | Switches declared (`w_sw`) | Switches placed in `board_specific.cst` |
|---|---|---|
| tang_primer_20k_dock_hdmi_tm1638, _hdmi_tm1638_alt, _lcd_800_480_tm1638, _lcd_800_480_tm1638_alt, and the four variants that include them (_hdmi_no_tm1638, _lcd_800_480_no_tm1638, _no_hdmi_no_tm1638, _no_hdmi_tm1638) | 5 | 4 (`SW[3:0]`) |
| tang_primer_20k_dock_hdmi_tm1638_yosys, _hdmi_no_tm1638_yosys | 4 | 4 |
| tang_primer_20k_lite | 3 | 2 (`SW[1:0]`) |

The lab reads `SW[4]` (Dock) or `SW[2]` (Lite), a top port no constraint places.

Vendor side: Sipeed's Tang Primer 20K page describes the Dock's one 5-position DIP
switch and says to "put the 1 switch on the dip switch down" to enable the core board,
so that position is not a user switch. Four user switches match BGM's constraint file and
its own yosys variants (`w_sw = 4`); the other Dock variants' `w_sw = 5` is the bug. For
the Lite, Sipeed's page lists only two slide switches, described as "Switch USB
function"; it does not say whether any reaches the FPGA, so only BGM's own mismatch
(three declared, two placed) is established.

### 2.2 tang_nano_9k_hdmi_no_ip_tm1638: HDMI line counter never initialised

`boards/tang_nano_9k_hdmi_no_ip_tm1638/board_specific_top.sv:368` declares
`logic [9:0] CounterX, CounterY;` and line 385 is its only assignment:

```systemverilog
always @(posedge pixclk) if(CounterX==799) CounterY <= (CounterY==524) ? 0 : CounterY + 1;
```

No reset and no initial value. The FPGA powers it up at 0, but in simulation it stays x,
and so do the lab's `y` (line 407) and everything computed from it.

### 2.3 orangepi_msoc: undriven coordinates and switch net

`boards/orangepi_msoc/board_specific_top.sv`:

- `x` and `y` are declared (lines 89-90) and never driven: the variant has no display.
  Lines 125-126 still feed the lab `mirrored_x = w_x' (screen_width - 1 - x)` and the same
  for `y`.
- Line 154 connects the lab's `sw` to `lab_sw`, which is never declared or driven, so
  Verilog makes it an implicit 1-bit net and the lab's switch bus floats.

### 2.4 orangecrab_ecp5_yosys: two modules drive the same pins

The microphone receiver (`board_specific_top.sv:251-268`) and the audio output
(`:273` onwards) both use `GPIO[3..6]`; for example `GPIO[3]` is the microphone's `lr`
(line 261) and the audio output's `mclk` (line 287). Labs that enable both interfaces,
such as `labs/2_graphics/2_4_game` (both `define`s unconditional in its
`lab_specific_board_config.svh`), therefore drive those pins from two outputs.

### 2.5 de10_lite_tm1638_virtual_switches: undriven TM1638 keys

`board_specific_top.sv:113` declares `tm_key`, and all three ways the top builds the lab's
key bus read it (lines 127, 135, 149, e.g. `assign lab_key = { tm_key, ~ KEY };`). Nothing
assigns it: this variant's TM1638 module, `tm1638_virtual_switches`, outputs switches
only. The lab's upper key bits float.

### 2.6 ice40hx8k_evb_yosys: no VGA timing generator

The top instantiates no `vga` and never declares `x` or `y`, so they become implicit 1-bit
nets. `VGA_HS` and `VGA_VS` are never driven, and the lab's colours go straight to the
pins without blanking. The lab's `.x` / `.y` connections are also written twice (lines 145
and 148; section 3). Graphics cannot work on this build.

## 3. BGM sources that do not compile

### Rejected outright (unverified in unifpga's comparison)

| BGM variant | Error |
|---|---|
| eclypse_z7 | `lab_led` declared twice with different widths: `wire [w_lab_led - 1:0] lab_led;` (line 75) and `wire [w_led - 1:0] lab_led;` (line 94), both unconditional |
| icebreaker_bare | `output LED_RGB[0],` (lines 47-49) is not valid port syntax; `wire [w_key - 1:0] key = { BTN1, BTN2, BTN3 }` (line 105) has no semicolon |
| tang_nano_9k_tm1638_sd | Instantiates `Gowin_rPLL` (line 376) and the DVI_TX IP, but the variant's directory contains no `.v` files at all |

unifpga's `eclypse_z7_openxc7` shares BGM's `eclypse_z7` and is unverified for the same
reason.

### Repaired for the comparison (PASS-REPAIRED)

unifpga's checker repairs these only at the line Icarus reports and only where the repair
cannot change behaviour, and lists each repair in its output:

| BGM variant | Error | Repair |
|---|---|---|
| dk_dev_3c120n | `.vsync ( )`, `.hsync ( )` on `lab_top` (lines 107-108) | Drop the empty connections |
| qmtech_kintex_7 | `.vsync ( '0 )`, `.hsync ( '0 )` on `lab_top` (lines 105-106) | Drop the constant connections |
| tang_primer_25k_pmod_vga (included by tang_primer_25k_pmod_hdmi) | `wire display_on;` (line 116), then `wire hsync, vsync, display_on, pixel_clk;` (line 268) in the same module scope | Drop the second, identical declaration |

Only 3 of BGM's 100 `lab_top.sv` files have `hsync` / `vsync` ports, all experimental or
unfinished (`labs/99_experimental/99_01_camera`, `99_03_yrv_sd`,
`labs/8_unfinished/8_1_uart/to_integrate`); with any other lab the first two rows fail.
ice40hx8k_evb_yosys also needs a repair (the repeated `.x ( x )` / `.y ( y )`), then
differs for the reason in 2.6.

## 4. Constraint and port mistakes

These are wrong in BGM's files; checked in BGM's tops and constraint files only.

- **The lab's UART is not connected to a placed pin.**
  - alinx_ax301, alinx_ax4010, saylinx_pmod_mic3 include `../saylinx/board_specific_top.sv`,
    whose ports are `UART_RXD` / `UART_TXD` (lines 53-54); their `board_specific.qsf`
    assign `UART_RX` / `UART_TX`, names the top does not have.
  - tang_mega_138k_lcd_480_272_tm1638 and tang_mega_138k_pro_lcd_480_272_tm1638 declare
    ports `UART2_RXD` / `UART2_TXD` but connect the lab to `UART_RX` / `UART_TX`, which
    are never declared (implicit nets). The Pro's `board_specific.cst` places `UART2_*`;
    the non-Pro's has those lines commented out.
  - colorlight75b_tm1638_ecp5_yosys connects the lab to `UART_RX` / `UART_TX`, which are
    never declared, and its `board_specific.lpf` has no UART pins.
  - tang_primer_20k_lite declares ports `UART_RX` / `UART_TX` (lines 31-32), but its
    `board_specific.cst` places neither.
- **Tang Mega 138K Pro `PMOD_0[1]`.** `tang_mega_138k_pro_lcd_480_272_tm1638/board_specific.cst:55`
  says N26 and `tang_mega_138k_pro_lcd_480_272_no_tm1638/board_specific.cst:55` says L22.
  Not verified against the vendor: none of the constraint files in Sipeed's
  `TangMega-138KPro-example` use any of BGM's `PMOD_0` pins, and Sipeed's schematic could
  not be retrieved. unifpga currently follows each variant.

## 5. Differences that are not BGM bugs

- **HDMI serial lanes are reported, not judged.** On the Gowin boards BGM uses Gowin's
  encrypted `DVI_TX` IP; unifpga uses an open serializer. The pixel timing is matched, but
  the lane waveforms cannot be compared.
- **The lab's `uart_rx` where BGM leaves it unconnected (open decision).** de10_lite,
  de10_lite_tm1638_virtual_switches, de1_soc, eclypse_z7, tang_nano_4k_hdmi_tm1638 (and
  tang_nano_4k_hdmi_no_tm1638, which includes it) and terasic_sockit connect
  `.uart_rx ( )`; qmtech_kintex_7 has no `uart_rx` connection at all. Together with the
  unplaced ports in section 4, the lab's UART receive input is undriven. Vendor tools
  typically tie such an input to 0, which a UART reads as a line held at break rather than
  idle (1). unifpga currently feeds 0 on 20 configurations (`uart_rx: 0` in their
  overlays); whether to switch them to idle is not decided.

## Sources

- AMD UG953, [XPM_CDC_ASYNC_RST](https://docs.amd.com/r/en-US/ug953-vivado-7series-libraries/XPM_CDC_ASYNC_RST)
- Digilent, [Nexys-4-Master.xdc](https://github.com/Digilent/digilent-xdc/blob/master/Nexys-4-Master.xdc)
- Sipeed, [Tang Primer 20K documentation](https://wiki.sipeed.com/hardware/en/tang/tang-primer-20k/primer-20k.html)
  and [TangPrimer-20K-example](https://github.com/sipeed/TangPrimer-20K-example) (`HDMI/src/dk_video.cst`)
- Sipeed, [Tang Mega 138K Pro documentation](https://wiki.sipeed.com/hardware/en/tang/tang-mega-138k/mega-138k-pro.html)
  and [TangMega-138KPro-example](https://github.com/sipeed/TangMega-138KPro-example)
- DDWG, [Digital Visual Interface Specification 1.0](https://glenwing.github.io/docs/DVI-1.0.pdf), §3.2.1, Figure 3-2
