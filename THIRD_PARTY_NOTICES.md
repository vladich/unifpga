# Third-party notices

unifpga's own code is under the MIT licence in [`LICENSE`](LICENSE). The material
below comes from other projects and stays under its original licence; the full texts
are in [`LICENSES/`](LICENSES/). Files that carry their own licence header keep it.

## basics-graphics-music (MIT)

Licence: [`LICENSES/MIT-basics-graphics-music.txt`](LICENSES/MIT-basics-graphics-music.txt),
Copyright (c) 2013-2023 Yuri Panchul and the contributors named there.
Source: https://github.com/yuri-panchul/basics-graphics-music

Adapted from it, in whole or in part:

- the example designs and their testbenches under `designs/`;
- in `rtl/peripherals/`: I2C_AUDIO_Config.v, I2C_Controller.v, I2C_HDMI_Config.v, I2C_HDMI_Config_c5gx.v, I2C_WRITE_WDATA.v, audio_pwm.sv, designs_common/*.sv, digilent_pmod_mic3_spi_receiver.sv, dvi.sv, dvi_config.svh, dvi_pmod_12b.sv, dvi_pmod_ddr_24b.sv, hdmi_adv7513.sv, hub75e_led_matrix.sv, i2s_audio_out.sv, imitate_reset_on_power_up.sv, inmp441_mic_i2s_receiver.sv, inmp441_mic_i2s_receiver_alt.sv, lcd_480_272.sv, lcd_480_272_ml6485.sv, lcd_800_480.sv, seven_seg_shared_to_per_digit.sv, slow_clk_gen.sv, tm1638_board_controller.sv, tm1638_registers.sv, vga.sv, wm8731_i2s_out.sv, adc_parallel_sampler.sv;
- pin assignments in the board files `a7_lite_35t`, `alinx_ax301`, `alinx_ax4010`, `alinx_ax7035b`, `arty_a7`, `basys3`, `c5gx`, `colorlight75b`, `de0`, `de0_cv`, `de0_nano`, `de0_nano_soc`, `de1`, `de10_lite`, `de10_nano`, `de1_soc`, `de2`, `de2_115`, `dk_dev_3c120n`, `eclypse_z7`, `emooc_cc`, `ice40hx8k_evb`, `icebreaker`, `karnix_ecp5`, `marsohod3gw2`, `marsohod_mcy316`, `nexys4`, `nexys4_ddr`, `nexys_a7`, `omdazz`, `omdazz_epm570`, `orangecrab_ecp5`, `orangepi_msoc`, `piswords6`, `qmtech_kintex_7`, `rzrd`, `saylinx`, `tang_mega_138k`, `tang_mega_138k_pro`, `tang_nano_20k`, `tang_nano_4k`, `tang_nano_9k`, `tang_primer_20k_dock`, `tang_primer_20k_lite`, `tang_primer_25k`, `terasic_sockit`, `zeowaa`, `zybo_z7`.

## LiteX-Boards (BSD-2-Clause)

Licence: [`LICENSES/BSD-2-Clause-litex-boards.txt`](LICENSES/BSD-2-Clause-litex-boards.txt),
Copyright 2012-2026 Enjoy-Digital and the LiteX-Hub community.
Source: https://github.com/litex-hub/litex-boards

Pin assignments in the board files `alchitry_au`, `alchitry_cu`, `alchitry_pt`, `alinx_ax7203`, `avnet_aes_ku040_db`, `bunnie_netv2`, `colorlight_i9_plus`, `digilent_atlys`, `digilent_pynq_z1`, `efinix_t120_bga576_devkit`, `efinix_t20_mipi_devkit`, `efinix_ti375_devkit`, `efinix_topaz_tz170_j484_devkit`, `enclustra_mercury_xu5`, `fomu_pvt`, `lattice_certusnx_versa`, `lattice_certuspro_nx_evn`, `lattice_certuspro_nx_vvml`, `lattice_crosslink_nx_evn`, `numato_aller`, `numato_nereid`, `panologic_g2`, `papilio_pro`, `qmtech_c4_starter`, `qmtech_cyclone_ep4cgx150_core`, `sipeed_tang_console`, `terasic_max10_deca`, `tinyfpga_bx`, `trenz_te0725`, `trenz_te0890`, `tul_pynq_z2`, `xilinx_ac701`, `xilinx_alveo_u200`, `xilinx_alveo_u250`, `xilinx_alveo_u280`, `xilinx_kc705`, `xilinx_kcu105`, `xilinx_kcu116`, `xilinx_sp605`, `xilinx_vc707`, `xilinx_vcu118`, `xilinx_vcu128`, `xilinx_zc706`, `xilinx_zcu102`, `xilinx_zcu104`, `xilinx_zcu106`.

## AMD board files (Apache-2.0)

Licence: [`LICENSES/Apache-2.0.txt`](LICENSES/Apache-2.0.txt), Copyright (C) 2023 Advanced Micro Devices, Inc.
Source: the ZCU208 board files shipped with Vivado (XilinxBoardStore `boards/Xilinx/zcu208/production/2.0`).

Pin assignments in the board file `xilinx_zcu208`.

## Digilent XDC (MIT)

Licence: [`LICENSES/MIT-digilent-xdc.txt`](LICENSES/MIT-digilent-xdc.txt), Copyright (c) 2017 Digilent.
Source: https://github.com/Digilent/digilent-xdc

Pin assignments in the board files `arty_s7_25`, `arty_s7_50`, `arty_z7_10`, `arty_z7_20`, `cmod_a7`, `cmod_s7`, `cora_z7_07s`, `cora_z7_10`, `digilent_genesys_zu_3eg`, `digilent_genesys_zu_5ev`, `digilent_usb104_a7`, `digilent_zedboard`, `digilent_zybo`, `genesys_2`, `nexys_video`, `usb104_a7_100t`, `zedboard`.

## Code with its own licence header

| Files | Origin | Licence |
|---|---|---|
| `designs/5_1_schoolriscv/`, `designs/5_2_schoolriscv_cache/` | schoolRISCV, Stanislav Zhelnio and Aleksandr Romanov | MIT, [`LICENSES/MIT-schoolRISCV.txt`](LICENSES/MIT-schoolRISCV.txt) |
| `designs/5_3_picorv32/picorv32.v` | PicoRV32, Claire Xenia Wolf | ISC, [`LICENSES/ISC-picorv32.txt`](LICENSES/ISC-picorv32.txt) |
| `designs/5_4_yrv/`, `designs/5_4_yrv_plus/`, `designs/5_4_1_yrv_plus_systemrdl/` | YRV processor | Apache-2.0 WITH SHL-2.1, [`LICENSES/Apache-2.0.txt`](LICENSES/Apache-2.0.txt), [`LICENSES/SHL-2.1.txt`](LICENSES/SHL-2.1.txt) |
| `designs/5_5_aps/peripheral_modules/uart_rx.sv`, `uart_tx.sv` | ETH Zurich and University of Bologna | SHL-0.51, [`LICENSES/SHL-0.51.txt`](LICENSES/SHL-0.51.txt) |
| `rtl/peripherals/tm1638_board_controller.sv`, `tm1638_registers.sv` | Alexander Kirichenko, Ruslan Zalata, Alan Garfield | Apache-2.0, [`LICENSES/Apache-2.0.txt`](LICENSES/Apache-2.0.txt) |
| `rtl/peripherals/I2C_*.v` | Terasic Technologies reference code | Terasic's own terms (in each file): use and modification only for Terasic boards; **not an open licence** |

## Factual pin data from vendor documents

Pin assignments taken from vendor documents or example projects that state no licence:

- Alinx example repositories (https://github.com/alinxalinx): `alinx_ax7010`, `alinx_ax7100`, `alinx_ax7101`, `alinx_ax7102`, `alinx_ax7103`, `alinx_ax7450`, `alinx_axu15eg`, `alinx_axu2cga`, `alinx_axu2cgb`, `alinx_axu4ev_p`, `alinx_axu5ev_p`, `alinx_axu9eg`, `alinx_z19`, `alinx_z19_p`, `alinx_z7_p`;
- Alinx board documentation: `alinx_av7k300`, `alinx_av7k325`, `alinx_ax1006`, `alinx_ax1016`, `alinx_ax1025`, `alinx_ax309`, `alinx_ax515`, `alinx_ax7015`, `alinx_ax7020`, `alinx_ax7021`, `alinx_ax7325`, `alinx_ax7350`, `alinx_ax7z010`, `alinx_ax7z020`, `alinx_ax7z035`, `alinx_ax7z045`, `alinx_ax7z100b`, `alinx_axau15`, `alinx_axau25`, `alinx_axsu35`, `alinx_axvu13f`, `alinx_axvu13g`, `alinx_axvu13p`, `alinx_fx200`, `alinx_hea13`, `alinx_vd100`;
- Efinix's T20F169 dev-kit example project shipped with Efinity: `efinix_t20_bga169_devkit`.
- Terasic and other vendor user manuals: `alinx_ax7035`, `alinx_ax7050`, `terasic_de10_agilex`, `terasic_de10_pro`, `terasic_de10_pro_stratix10_gx`, `terasic_de23_lite`, `terasic_de25_nano`, `terasic_de25_standard`.
