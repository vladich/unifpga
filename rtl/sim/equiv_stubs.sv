// =============================================================================
// equiv_stubs.sv — vendor primitives that BGM's board tops (or their IP
// wrappers) instantiate and rtl/sim/vendor_stubs.sv does not model. Used by
// tools/equiv_check.py, which compiles BGM's board_specific_top.sv and
// unifpga's generated top with the same stubs and compares the physical pins
// cycle by cycle. The models are deliberately simple but *data-dependent*:
// every output is a function of the inputs, so a wiring difference on either
// side of a primitive shows on the pins. PLL models derive their output
// frequency from the same parameters the vendor tool reads, so a parameter
// difference shows as a frequency difference.
// =============================================================================

`timescale 1 ns / 1 ps

// ---- Intel altpll megafunction (Quartus IP wrappers use defparam) ----------

module altpll
# (
    parameter intended_device_family = "Cyclone IV E",
    parameter operation_mode = "NORMAL", parameter pll_type = "AUTO", parameter compensate_clock = "CLK0",
    parameter lpm_hint = "", parameter lpm_type = "altpll", parameter port_areset = "PORT_UNUSED",
    parameter port_locked = "PORT_UNUSED", parameter port_inclk0 = "PORT_USED", parameter port_inclk1 = "PORT_UNUSED",
    parameter port_clk0 = "PORT_USED", port_clk1 = "PORT_UNUSED", port_clk2 = "PORT_UNUSED", port_clk3 = "PORT_UNUSED",
    parameter port_clk4 = "PORT_UNUSED", port_clk5 = "PORT_UNUSED",
    parameter port_activeclock = "PORT_UNUSED", port_clkbad0 = "PORT_UNUSED", port_clkbad1 = "PORT_UNUSED",
    parameter port_clkloss = "PORT_UNUSED", port_clkswitch = "PORT_UNUSED", port_configupdate = "PORT_UNUSED",
    parameter port_fbin = "PORT_UNUSED", port_fbout = "PORT_UNUSED", port_pfdena = "PORT_UNUSED",
    parameter port_phasecounterselect = "PORT_UNUSED", port_phasedone = "PORT_UNUSED", port_phasestep = "PORT_UNUSED",
    parameter port_phaseupdown = "PORT_UNUSED", port_pllena = "PORT_UNUSED", port_scanaclr = "PORT_UNUSED",
    parameter port_scanclk = "PORT_UNUSED", port_scanclkena = "PORT_UNUSED", port_scandata = "PORT_UNUSED",
    parameter port_scandataout = "PORT_UNUSED", port_scandone = "PORT_UNUSED", port_scanread = "PORT_UNUSED",
    parameter port_scanwrite = "PORT_UNUSED", port_clkena0 = "PORT_UNUSED", port_clkena1 = "PORT_UNUSED",
    parameter port_clkena2 = "PORT_UNUSED", port_clkena3 = "PORT_UNUSED", port_clkena4 = "PORT_UNUSED",
    parameter port_clkena5 = "PORT_UNUSED", port_extclk0 = "PORT_UNUSED", port_extclk1 = "PORT_UNUSED",
    parameter port_extclk2 = "PORT_UNUSED", port_extclk3 = "PORT_UNUSED", port_extclkena0 = "PORT_UNUSED",
    parameter port_extclkena1 = "PORT_UNUSED", port_extclkena2 = "PORT_UNUSED", port_extclkena3 = "PORT_UNUSED",
    parameter self_reset_on_loss_lock = "OFF", parameter width_clock = 5, parameter width_phasecounterselect = 3,
    parameter bandwidth_type = "AUTO", parameter inclk0_input_frequency = 20000, parameter inclk1_input_frequency = 20000,
    parameter clk0_divide_by = 1, clk0_multiply_by = 1, clk0_duty_cycle = 50, clk0_phase_shift = "0",
    parameter clk1_divide_by = 1, clk1_multiply_by = 1, clk1_duty_cycle = 50, clk1_phase_shift = "0",
    parameter clk2_divide_by = 1, clk2_multiply_by = 1, clk2_duty_cycle = 50, clk2_phase_shift = "0",
    parameter clk3_divide_by = 1, clk3_multiply_by = 1, clk3_duty_cycle = 50, clk3_phase_shift = "0",
    parameter clk4_divide_by = 1, clk4_multiply_by = 1, clk4_duty_cycle = 50, clk4_phase_shift = "0",
    parameter clk5_divide_by = 1, clk5_multiply_by = 1, clk5_duty_cycle = 50, clk5_phase_shift = "0",
    parameter vco_frequency_control = "AUTO", parameter vco_multiply_by = 0, parameter vco_divide_by = 0,
    parameter charge_pump_current = 0, parameter loop_filter_c = 0, parameter loop_filter_r = "",
    parameter pll_compensation_delay = 0, parameter primary_clock = "INCLK0", parameter switch_over_type = "AUTO",
    parameter inclk0_input_frequency_ps = 0
)
(
    input  [1:0] inclk,
    output [width_clock - 1:0] clk,
    output locked,
    output activeclock,
    input  areset,
    output [1:0] clkbad,
    input  [5:0] clkena,
    output clkloss,
    input  clkswitch,
    input  configupdate,
    output enable0,
    output enable1,
    output [3:0] extclk,
    input  [3:0] extclkena,
    input  fbin,
    inout  fbmimicbidir,
    output fbout,
    output fref,
    output icdrclk,
    input  pfdena,
    input  [width_phasecounterselect - 1:0] phasecounterselect,
    output phasedone,
    input  phasestep,
    input  phaseupdown,
    input  pllena,
    input  scanaclr,
    input  scanclk,
    input  scanclkena,
    input  scandata,
    output scandataout,
    output scandone,
    input  scanread,
    input  scanwrite,
    output sclkout0,
    output sclkout1,
    output vcooverrange,
    output vcounderrange
);
    // Period model per output: t_in * divide_by / multiply_by, t_in measured.
    real t_in = 20.0;
    real last = 0.0;
    reg  [5:0] q = 6'b0;
    reg  lck = 1'b0;

    always @ (posedge inclk [0]) begin
        if (last != 0.0) t_in = $realtime - last;
        last = $realtime;
    end

    always #(t_in * clk0_divide_by / clk0_multiply_by / 2.0) q [0] = ~ q [0];
    always #(t_in * clk1_divide_by / clk1_multiply_by / 2.0) q [1] = ~ q [1];
    always #(t_in * clk2_divide_by / clk2_multiply_by / 2.0) q [2] = ~ q [2];
    always #(t_in * clk3_divide_by / clk3_multiply_by / 2.0) q [3] = ~ q [3];
    always #(t_in * clk4_divide_by / clk4_multiply_by / 2.0) q [4] = ~ q [4];
    always #(t_in * clk5_divide_by / clk5_multiply_by / 2.0) q [5] = ~ q [5];

    initial #(t_in * 16) lck = 1'b1;

    assign clk          = areset ? '0 : q [width_clock - 1:0];
    assign locked       = lck & ~ areset;
    assign activeclock  = 1'b0;
    assign clkbad       = 2'b00;
    assign clkloss      = 1'b0;
    assign enable0      = 1'b1;
    assign enable1      = 1'b1;
    assign extclk       = { 4 { inclk [0] } };
    assign fbout        = inclk [0];
    assign fref         = inclk [0];
    assign icdrclk      = inclk [0];
    assign phasedone    = 1'b1;
    assign scandataout  = 1'b0;
    assign scandone     = 1'b0;
    assign sclkout0     = inclk [0];
    assign sclkout1     = inclk [0];
    assign vcooverrange = 1'b0;
    assign vcounderrange = 1'b0;
endmodule

// ---- Xilinx 7-series ---------------------------------------------------------

module IBUF # (parameter IOSTANDARD = "DEFAULT", parameter IBUF_LOW_PWR = "TRUE") (input I, output O);
    assign O = I;
endmodule

module OBUF # (parameter IOSTANDARD = "DEFAULT", parameter DRIVE = 12, parameter SLEW = "SLOW") (input I, output O);
    assign O = I;
endmodule

module IBUFDS # (parameter IOSTANDARD = "DEFAULT", parameter DIFF_TERM = "FALSE", parameter IBUF_LOW_PWR = "TRUE")
    (input I, input IB, output O);
    assign O = I;
endmodule

module MMCME2_ADV
# (
    parameter BANDWIDTH = "OPTIMIZED", parameter real CLKIN1_PERIOD = 20.0, parameter real CLKIN2_PERIOD = 0.0,
    parameter integer DIVCLK_DIVIDE = 1, parameter real CLKFBOUT_MULT_F = 20.0, parameter real CLKFBOUT_PHASE = 0.0,
    parameter CLKFBOUT_USE_FINE_PS = "FALSE",
    parameter real CLKOUT0_DIVIDE_F = 4.0,
    parameter integer CLKOUT1_DIVIDE = 1, CLKOUT2_DIVIDE = 1, CLKOUT3_DIVIDE = 1,
    parameter integer CLKOUT4_DIVIDE = 1, CLKOUT5_DIVIDE = 1, CLKOUT6_DIVIDE = 1,
    parameter real CLKOUT0_DUTY_CYCLE = 0.5, CLKOUT1_DUTY_CYCLE = 0.5, CLKOUT2_DUTY_CYCLE = 0.5,
    parameter real CLKOUT3_DUTY_CYCLE = 0.5, CLKOUT4_DUTY_CYCLE = 0.5, CLKOUT5_DUTY_CYCLE = 0.5, CLKOUT6_DUTY_CYCLE = 0.5,
    parameter real CLKOUT0_PHASE = 0.0, CLKOUT1_PHASE = 0.0, CLKOUT2_PHASE = 0.0, CLKOUT3_PHASE = 0.0,
    parameter real CLKOUT4_PHASE = 0.0, CLKOUT5_PHASE = 0.0, CLKOUT6_PHASE = 0.0,
    parameter CLKOUT0_USE_FINE_PS = "FALSE", CLKOUT1_USE_FINE_PS = "FALSE", CLKOUT2_USE_FINE_PS = "FALSE",
    parameter CLKOUT3_USE_FINE_PS = "FALSE", CLKOUT4_USE_FINE_PS = "FALSE", CLKOUT5_USE_FINE_PS = "FALSE",
    parameter CLKOUT6_USE_FINE_PS = "FALSE", parameter CLKOUT4_CASCADE = "FALSE", parameter COMPENSATION = "ZHOLD",
    parameter real REF_JITTER1 = 0.010, parameter real REF_JITTER2 = 0.010, parameter STARTUP_WAIT = "FALSE",
    parameter SS_EN = "FALSE", parameter SS_MODE = "CENTER_HIGH", parameter integer SS_MOD_PERIOD = 10000
)
(
    input CLKIN1, input CLKIN2, input CLKINSEL, input CLKFBIN, output CLKFBOUT, output CLKFBOUTB,
    output reg CLKOUT0, output CLKOUT0B, output reg CLKOUT1, output CLKOUT1B,
    output reg CLKOUT2, output CLKOUT2B, output reg CLKOUT3, output CLKOUT3B,
    output CLKOUT4, output CLKOUT5, output CLKOUT6,
    input [6:0] DADDR, input DCLK, input DEN, input [15:0] DI, output [15:0] DO, output DRDY, input DWE,
    input PSCLK, input PSEN, input PSINCDEC, output PSDONE,
    output reg LOCKED, output CLKINSTOPPED, output CLKFBSTOPPED, input PWRDWN, input RST
);
    localparam real T_VCO = (CLKIN1_PERIOD > 0.0 ? CLKIN1_PERIOD : 20.0) * DIVCLK_DIVIDE / CLKFBOUT_MULT_F;
    initial begin CLKOUT0 = 0; CLKOUT1 = 0; CLKOUT2 = 0; CLKOUT3 = 0; LOCKED = 0; end
    always #(T_VCO * CLKOUT0_DIVIDE_F / 2.0) CLKOUT0 = ~CLKOUT0;
    always #(T_VCO * CLKOUT1_DIVIDE   / 2.0) CLKOUT1 = ~CLKOUT1;
    always #(T_VCO * CLKOUT2_DIVIDE   / 2.0) CLKOUT2 = ~CLKOUT2;
    always #(T_VCO * CLKOUT3_DIVIDE   / 2.0) CLKOUT3 = ~CLKOUT3;
    assign CLKOUT0B = ~CLKOUT0; assign CLKOUT1B = ~CLKOUT1; assign CLKOUT2B = ~CLKOUT2; assign CLKOUT3B = ~CLKOUT3;
    assign CLKOUT4 = 1'b0; assign CLKOUT5 = 1'b0; assign CLKOUT6 = 1'b0;
    assign CLKFBOUT = CLKIN1; assign CLKFBOUTB = ~CLKIN1;
    assign DO = 16'h0; assign DRDY = 1'b0; assign PSDONE = 1'b0;
    assign CLKINSTOPPED = 1'b0; assign CLKFBSTOPPED = 1'b0;
    initial #(CLKIN1_PERIOD * 16) LOCKED = 1;
endmodule

// Xilinx parameterized macro: asynchronous assert, deassert synchronised
// through DEST_SYNC_FF flops (BGM's a7_lite_35t reset).
module xpm_cdc_async_rst
# (parameter integer DEST_SYNC_FF = 4, parameter integer INIT_SYNC_FF = 0,
   parameter integer RST_ACTIVE_HIGH = 0, parameter integer SIM_ASSERT_CHK = 0)
(input src_arst, input dest_clk, output dest_arst);
    reg [DEST_SYNC_FF - 1:0] sync = {DEST_SYNC_FF{1'b0}};
    wire active = RST_ACTIVE_HIGH ? src_arst : ~ src_arst;
    always @ (posedge dest_clk or posedge active)
        if (active) sync <= {DEST_SYNC_FF{1'b1}};
        else        sync <= {sync [DEST_SYNC_FF - 2:0], 1'b0};
    assign dest_arst = RST_ACTIVE_HIGH ? sync [DEST_SYNC_FF - 1] : ~ sync [DEST_SYNC_FF - 1];
endmodule

// ---- Lattice iCE40 -----------------------------------------------------------

module SB_GB (input USER_SIGNAL_TO_GLOBAL_BUFFER, output GLOBAL_BUFFER_OUTPUT);
    assign GLOBAL_BUFFER_OUTPUT = USER_SIGNAL_TO_GLOBAL_BUFFER;
endmodule

// PIN_TYPE[5:2] output modes used by BGM: 0110 simple, 0101 registered,
// 0100 DDR (D_OUT_0 while OUTPUT_CLK is high, D_OUT_1 while low).
module SB_IO
# (
    parameter [5:0] PIN_TYPE = 6'b000001, parameter PULLUP = 1'b0, parameter NEG_TRIGGER = 1'b0,
    parameter IO_STANDARD = "SB_LVCMOS"
)
(
    inout  PACKAGE_PIN,
    input  LATCH_INPUT_VALUE,
    input  CLOCK_ENABLE,
    input  INPUT_CLK,
    input  OUTPUT_CLK,
    input  OUTPUT_ENABLE,
    input  D_OUT_0,
    input  D_OUT_1,
    output D_IN_0,
    output D_IN_1
);
    reg q = 1'b0;
    always @ (posedge OUTPUT_CLK) q <= D_OUT_0;
    wire drive = (PIN_TYPE [5:2] == 4'b0110) ? D_OUT_0 :
                 (PIN_TYPE [5:2] == 4'b0101) ? q :
                 (PIN_TYPE [5:2] == 4'b0100) ? (OUTPUT_CLK ? D_OUT_0 : D_OUT_1) : 1'bz;
    wire oe = (PIN_TYPE [5:4] == 2'b01) | (PIN_TYPE [5:4] == 2'b10 & OUTPUT_ENABLE);
    assign PACKAGE_PIN = oe ? drive : 1'bz;
    assign D_IN_0 = PACKAGE_PIN;
    assign D_IN_1 = PACKAGE_PIN;
endmodule

// ---- Gowin DVI_TX encrypted IP -------------------------------------------------
// No behaviour to copy; each TMDS lane folds the colour and control inputs
// it carries so any change in what is wired to the IP changes the pins.

module DVI_TX_Top
(
    input        I_rst_n,
    input        I_serial_clk,
    input        I_rgb_clk,
    input        I_rgb_vs,
    input        I_rgb_hs,
    input        I_rgb_de,
    input  [7:0] I_rgb_r,
    input  [7:0] I_rgb_g,
    input  [7:0] I_rgb_b,
    output       O_tmds_clk_p,
    output       O_tmds_clk_n,
    output [2:0] O_tmds_data_p,
    output [2:0] O_tmds_data_n
);
    assign O_tmds_clk_p     = I_rgb_clk & I_rst_n;
    assign O_tmds_clk_n     = ~ O_tmds_clk_p;
    assign O_tmds_data_p [0] = I_rst_n & (^ I_rgb_b ^ I_rgb_hs ^ I_rgb_vs ^ I_serial_clk);
    assign O_tmds_data_p [1] = I_rst_n & (^ I_rgb_g ^ I_rgb_de ^ I_serial_clk);
    assign O_tmds_data_p [2] = I_rst_n & (^ I_rgb_r ^ I_serial_clk);
    assign O_tmds_data_n     = ~ O_tmds_data_p;
endmodule

// ---- Lattice ECP5 ---------------------------------------------------------------

module DCCA (input CLKI, input CE, output CLKO);
    assign CLKO = CLKI & (CE === 1'b0 ? 1'b0 : 1'b1);
endmodule

module ODDRX1F (input SCLK, input RST, input D0, input D1, output Q);
    assign Q = RST ? 1'b0 : (SCLK ? D0 : D1);
endmodule

module IDDRX1F (input SCLK, input RST, input D, output Q0, output Q1);
    reg q0 = 1'b0, q1 = 1'b0;
    always @ (posedge SCLK) q0 <= D;
    always @ (negedge SCLK) q1 <= D;
    assign Q0 = q0;
    assign Q1 = q1;
endmodule

module OSCG # (parameter DIV = 128) (output reg OSC);
    // 310 MHz / DIV internal oscillator
    initial OSC = 1'b0;
    always #(1000.0 / 310.0 * DIV / 2.0) OSC = ~ OSC;
endmodule

module CLKDIVF # (parameter GSR = "DISABLED", parameter DIV = "2.0")
    (input CLKI, input RST, input ALIGNWD, output reg CDIVX);
    integer n = 0;
    initial CDIVX = 1'b0;
    always @ (posedge CLKI or posedge RST)
        if (RST) begin n <= 0; CDIVX <= 1'b0; end
        else begin
            n <= n + 1;
            if (n + 1 >= (DIV == "3.5" ? 4 : (DIV == "2.0" ? 1 : 2))) begin n <= 0; CDIVX <= ~ CDIVX; end
        end
endmodule

module DELAYF # (parameter DEL_MODE = "USER_DEFINED", parameter DEL_VALUE = 0)
    (input A, input LOADN, input MOVE, input DIRECTION, output Z, output CFLAG);
    assign Z = A;
    assign CFLAG = 1'b0;
endmodule

module ECLKSYNCB (input ECLKI, input STOP, output ECLKO);
    assign ECLKO = STOP ? 1'b0 : ECLKI;
endmodule

// ---- Gowin OSER10 (10:1 serializer): a data-dependent fold, like DVI_TX_Top ----

module OSER10 # (parameter GSREN = "false", parameter LSREN = "true")
    (input D0, D1, D2, D3, D4, D5, D6, D7, D8, D9, input FCLK, input PCLK, input RESET, output Q);
    reg [3:0] n = 4'd0;
    always @ (posedge FCLK or posedge RESET)
        if (RESET) n <= 4'd0;
        else n <= (n == 4'd9) ? 4'd0 : n + 4'd1;
    wire [9:0] d = {D9, D8, D7, D6, D5, D4, D3, D2, D1, D0};
    assign Q = RESET ? 1'b0 : d [n];
endmodule
