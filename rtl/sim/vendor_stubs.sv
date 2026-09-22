// =============================================================================
// Behavioural stand-ins for vendor primitives, for lint and simulation only
// (tools/lint_generated.py adds this file; the vendor flows never see it).
//
// They model just enough for iverilog / verilator to elaborate the generated
// top: a PLL is a clock whose period follows the divider parameters, LOCK is
// asserted after a few input cycles.
// =============================================================================

`ifndef SYNTHESIS

module rPLL
# (
    parameter FCLKIN = "27",
    parameter DYN_IDIV_SEL = "false", parameter IDIV_SEL = 0,
    parameter DYN_FBDIV_SEL = "false", parameter FBDIV_SEL = 0,
    parameter DYN_ODIV_SEL = "false", parameter ODIV_SEL = 8,
    parameter PSDA_SEL = "0000", parameter DYN_DA_EN = "true", parameter DUTYDA_SEL = "1000",
    parameter CLKOUT_FT_DIR = 1'b1, parameter CLKOUTP_FT_DIR = 1'b1,
    parameter CLKOUT_DLY_STEP = 0, parameter CLKOUTP_DLY_STEP = 0,
    parameter CLKFB_SEL = "internal",
    parameter CLKOUT_BYPASS = "false", parameter CLKOUTP_BYPASS = "false", parameter CLKOUTD_BYPASS = "false",
    parameter DYN_SDIV_SEL = 2, parameter CLKOUTD_SRC = "CLKOUT", parameter CLKOUTD3_SRC = "CLKOUT",
    parameter DEVICE = "GW1NR-9C"
)
(
    output reg  CLKOUT,
    output reg  LOCK,
    output reg  CLKOUTP,
    output reg  CLKOUTD,
    output reg  CLKOUTD3,
    input       RESET,
    input       RESET_P,
    input       CLKIN,
    input       CLKFB,
    input [5:0] FBDSEL,
    input [5:0] IDSEL,
    input [5:0] ODSEL,
    input [3:0] PSDA,
    input [3:0] DUTYDA,
    input [3:0] FDLY
);
    // Period model: CLKOUT = CLKIN * (FBDIV+1) / (IDIV+1); CLKOUTD = CLKOUT / SDIV.
    real t_in = 37.037, t_out;
    real last = 0.0;
    integer sdiv_cnt = 0;
    initial begin
        CLKOUT = 0; CLKOUTP = 0; CLKOUTD = 0; CLKOUTD3 = 0; LOCK = 0;
    end
    always @(posedge CLKIN) begin
        if (last != 0.0) t_in = $realtime - last;
        last = $realtime;
    end
    always begin
        t_out = t_in * (IDIV_SEL + 1) / (FBDIV_SEL + 1);
        #(t_out / 2.0) CLKOUT = ~CLKOUT;
    end
    always @(posedge CLKOUT) begin
        sdiv_cnt = (sdiv_cnt + 1) % DYN_SDIV_SEL;
        if (sdiv_cnt < DYN_SDIV_SEL / 2) CLKOUTD = 1; else CLKOUTD = 0;
    end
    initial #(t_in * 8) LOCK = 1;
endmodule


module PLLVR
# (
    parameter FCLKIN = "27",
    parameter DYN_IDIV_SEL = "false", parameter IDIV_SEL = 0,
    parameter DYN_FBDIV_SEL = "false", parameter FBDIV_SEL = 0,
    parameter DYN_ODIV_SEL = "false", parameter ODIV_SEL = 8,
    parameter PSDA_SEL = "0000", parameter DYN_DA_EN = "true", parameter DUTYDA_SEL = "1000",
    parameter CLKOUT_FT_DIR = 1'b1, parameter CLKOUTP_FT_DIR = 1'b1,
    parameter CLKOUT_DLY_STEP = 0, parameter CLKOUTP_DLY_STEP = 0,
    parameter CLKFB_SEL = "internal",
    parameter CLKOUT_BYPASS = "false", parameter CLKOUTP_BYPASS = "false", parameter CLKOUTD_BYPASS = "false",
    parameter DYN_SDIV_SEL = 2, parameter CLKOUTD_SRC = "CLKOUT", parameter CLKOUTD3_SRC = "CLKOUT",
    parameter DEVICE = "GW1NSR-4C"
)
(
    output CLKOUT, output LOCK, output CLKOUTP, output CLKOUTD, output CLKOUTD3,
    input RESET, input RESET_P, input CLKIN, input CLKFB,
    input [5:0] FBDSEL, input [5:0] IDSEL, input [5:0] ODSEL, input [3:0] PSDA, input [3:0] DUTYDA, input [3:0] FDLY,
    input VREN
);
    rPLL # (.FCLKIN(FCLKIN), .IDIV_SEL(IDIV_SEL), .FBDIV_SEL(FBDIV_SEL), .ODIV_SEL(ODIV_SEL), .DYN_SDIV_SEL(DYN_SDIV_SEL), .DEVICE(DEVICE))
        core (.CLKOUT(CLKOUT), .LOCK(LOCK), .CLKOUTP(CLKOUTP), .CLKOUTD(CLKOUTD), .CLKOUTD3(CLKOUTD3),
              .RESET(RESET), .RESET_P(RESET_P), .CLKIN(CLKIN), .CLKFB(CLKFB), .FBDSEL(FBDSEL), .IDSEL(IDSEL),
              .ODSEL(ODSEL), .PSDA(PSDA), .DUTYDA(DUTYDA), .FDLY(FDLY));
endmodule


module SB_PLL40_CORE
# (
    parameter FEEDBACK_PATH = "SIMPLE",
    parameter [3:0] DIVR = 0, parameter [6:0] DIVF = 66, parameter [2:0] DIVQ = 5,
    parameter [2:0] FILTER_RANGE = 1
)
(
    input  REFERENCECLK,
    output reg PLLOUTCORE,
    output reg PLLOUTGLOBAL,
    input  RESETB,
    input  BYPASS,
    output reg LOCK
);
    real t_in = 83.333, t_out;
    real last = 0.0;
    initial begin PLLOUTCORE = 0; PLLOUTGLOBAL = 0; LOCK = 0; end
    always @(posedge REFERENCECLK) begin
        if (last != 0.0) t_in = $realtime - last;
        last = $realtime;
    end
    always begin
        t_out = t_in * (DIVR + 1) * (1 << DIVQ) / (DIVF + 1);
        #(t_out / 2.0) begin PLLOUTCORE = ~PLLOUTCORE; PLLOUTGLOBAL = PLLOUTCORE; end
    end
    initial #(t_in * 8) LOCK = 1;
endmodule


module SB_PLL40_PAD
# (
    parameter FEEDBACK_PATH = "SIMPLE",
    parameter [3:0] DIVR = 0, parameter [6:0] DIVF = 66, parameter [2:0] DIVQ = 5,
    parameter [2:0] FILTER_RANGE = 1
)
(
    input  PACKAGEPIN,
    output PLLOUTCORE,
    output PLLOUTGLOBAL,
    input  RESETB,
    input  BYPASS,
    output LOCK
);
    SB_PLL40_CORE # (.FEEDBACK_PATH(FEEDBACK_PATH), .DIVR(DIVR), .DIVF(DIVF), .DIVQ(DIVQ),
                     .FILTER_RANGE(FILTER_RANGE))
        core (.REFERENCECLK(PACKAGEPIN), .PLLOUTCORE(PLLOUTCORE), .PLLOUTGLOBAL(PLLOUTGLOBAL),
              .RESETB(RESETB), .BYPASS(BYPASS), .LOCK(LOCK));
endmodule


// ---- differential output buffers -------------------------------------------

module TLVDS_OBUF (input I, output O, output OB);
    assign O = I; assign OB = ~I;
endmodule

module ELVDS_OBUF (input I, output O, output OB);
    assign O = I; assign OB = ~I;
endmodule

module OBUFDS # (parameter IOSTANDARD = "DEFAULT", parameter SLEW = "SLOW")
    (input I, output O, output OB);
    assign O = I; assign OB = ~I;
endmodule


// ---- Gowin clock dividers ---------------------------------------------------

module CLKDIV2 (input HCLKIN, input RESETN, output reg CLKOUT);
    initial CLKOUT = 0;
    always @(posedge HCLKIN or negedge RESETN)
        if (!RESETN) CLKOUT <= 0; else CLKOUT <= ~CLKOUT;
endmodule

module CLKDIV # (parameter DIV_MODE = "2", parameter GSREN = "false")
    (input HCLKIN, input RESETN, input CALIB, output reg CLKOUT);
    localparam integer N = (DIV_MODE == "8") ? 8 : (DIV_MODE == "5") ? 5 : (DIV_MODE == "4") ? 4 : 2;
    integer cnt = 0;
    initial CLKOUT = 0;
    always @(posedge HCLKIN or negedge RESETN)
        if (!RESETN) begin cnt <= 0; CLKOUT <= 0; end
        else begin
            cnt <= (cnt == N - 1) ? 0 : cnt + 1;
            CLKOUT <= (cnt < N / 2) ? 1'b1 : 1'b0;   // N odd: high for (N+1)/2 cycles
        end
endmodule


// ---- Xilinx MMCM + BUFG -----------------------------------------------------

module BUFG (input I, output O);
    assign O = I;
endmodule

module MMCME2_BASE
# (
    parameter BANDWIDTH = "OPTIMIZED", parameter real CLKIN1_PERIOD = 20.0,
    parameter integer DIVCLK_DIVIDE = 1, parameter real CLKFBOUT_MULT_F = 20.0, parameter real CLKFBOUT_PHASE = 0.0,
    parameter real CLKOUT0_DIVIDE_F = 4.0,
    parameter integer CLKOUT1_DIVIDE = 1, CLKOUT2_DIVIDE = 1, CLKOUT3_DIVIDE = 1,
    parameter integer CLKOUT4_DIVIDE = 1, CLKOUT5_DIVIDE = 1, CLKOUT6_DIVIDE = 1,
    parameter real CLKOUT0_DUTY_CYCLE = 0.5, CLKOUT1_DUTY_CYCLE = 0.5, CLKOUT2_DUTY_CYCLE = 0.5,
    parameter real CLKOUT0_PHASE = 0.0, CLKOUT1_PHASE = 0.0, CLKOUT2_PHASE = 0.0,
    parameter CLKOUT4_CASCADE = "FALSE", parameter real REF_JITTER1 = 0.010, parameter STARTUP_WAIT = "FALSE"
)
(
    input CLKIN1, input CLKFBIN, output CLKFBOUT, output CLKFBOUTB,
    output reg CLKOUT0, output CLKOUT0B, output reg CLKOUT1, output CLKOUT1B,
    output reg CLKOUT2, output CLKOUT2B, output CLKOUT3, output CLKOUT3B,
    output CLKOUT4, output CLKOUT5, output CLKOUT6,
    output reg LOCKED, input PWRDWN, input RST
);
    real t_vco;
    initial begin CLKOUT0 = 0; CLKOUT1 = 0; CLKOUT2 = 0; LOCKED = 0; end
    initial t_vco = CLKIN1_PERIOD * DIVCLK_DIVIDE / CLKFBOUT_MULT_F;
    always #(t_vco * CLKOUT0_DIVIDE_F / 2.0) CLKOUT0 = ~CLKOUT0;
    always #(t_vco * CLKOUT1_DIVIDE   / 2.0) CLKOUT1 = ~CLKOUT1;
    always #(t_vco * CLKOUT2_DIVIDE   / 2.0) CLKOUT2 = ~CLKOUT2;
    assign CLKFBOUT = CLKIN1;
    initial #(CLKIN1_PERIOD * 16) LOCKED = 1;
endmodule


// ---- Gowin Arora V PLL / PLLA ------------------------------------------------

module gw5_pll_model
# (parameter FCLKIN = "50", parameter integer IDIV_SEL = 1, FBDIV_SEL = 1, MDIV_SEL = 16,
   ODIV0_SEL = 100, ODIV1_SEL = 8, ODIV2_SEL = 8)
(input CLKIN, output reg CLKOUT0, output reg CLKOUT1, output reg CLKOUT2, output reg LOCK);
    real t_in = 20.0, t_vco;
    real last = 0.0;
    initial begin CLKOUT0 = 0; CLKOUT1 = 0; CLKOUT2 = 0; LOCK = 0; end
    always @(posedge CLKIN) begin
        if (last != 0.0) t_in = $realtime - last;
        last = $realtime;
    end
    always begin
        t_vco = t_in * IDIV_SEL / (FBDIV_SEL * MDIV_SEL);
        #(t_vco * ODIV0_SEL / 2.0) CLKOUT0 = ~CLKOUT0;
    end
    always begin
        #(t_in * IDIV_SEL / (FBDIV_SEL * MDIV_SEL) * ODIV1_SEL / 2.0) CLKOUT1 = ~CLKOUT1;
    end
    always begin
        #(t_in * IDIV_SEL / (FBDIV_SEL * MDIV_SEL) * ODIV2_SEL / 2.0) CLKOUT2 = ~CLKOUT2;
    end
    initial #(t_in * 8) LOCK = 1;
endmodule

module PLL
# (parameter DYN_IDIV_SEL = "FALSE", DYN_FBDIV_SEL = "FALSE", DYN_ODIV0_SEL = "FALSE", DYN_ODIV1_SEL = "FALSE",
   DYN_ODIV2_SEL = "FALSE", DYN_MDIV_SEL = "FALSE", FCLKIN = "50", CLKFB_SEL = "INTERNAL",
   parameter integer IDIV_SEL = 1, FBDIV_SEL = 1, ODIV0_SEL = 100, ODIV0_FRAC_SEL = 0, ODIV1_SEL = 8, ODIV2_SEL = 8,
   MDIV_SEL = 16, MDIV_FRAC_SEL = 0,
   parameter CLKOUT0_EN = "TRUE", CLKOUT1_EN = "FALSE", CLKOUT2_EN = "FALSE", CLKOUT3_EN = "FALSE",
   CLKOUT4_EN = "FALSE", CLKOUT5_EN = "FALSE", CLKOUT6_EN = "FALSE")
(output LOCK, output CLKOUT0, output CLKOUT1, output CLKOUT2, output CLKOUT3, output CLKOUT4, output CLKOUT5,
 output CLKOUT6, output CLKFBOUT, input CLKIN, input CLKFB, input RESET, input PLLPWD, input RESET_I, input RESET_O,
 input [5:0] FBDSEL, input [5:0] IDSEL, input [6:0] MDSEL, input [2:0] MDSEL_FRAC, input [6:0] ODSEL0,
 input [2:0] ODSEL0_FRAC, input [6:0] ODSEL1, input [6:0] ODSEL2, input [6:0] ODSEL3, input [6:0] ODSEL4,
 input [6:0] ODSEL5, input [6:0] ODSEL6, input [3:0] DT0, input [3:0] DT1, input [3:0] DT2, input [3:0] DT3,
 input [5:0] ICPSEL, input [2:0] LPFRES, input [1:0] LPFCAP, input [2:0] PSSEL, input PSDIR, input PSPULSE,
 input ENCLK0, input ENCLK1, input ENCLK2, input ENCLK3, input ENCLK4, input ENCLK5, input ENCLK6,
 input SSCPOL, input SSCON, input [6:0] SSCMDSEL, input [2:0] SSCMDSEL_FRAC);
    gw5_pll_model # (.FCLKIN(FCLKIN), .IDIV_SEL(IDIV_SEL), .FBDIV_SEL(FBDIV_SEL), .MDIV_SEL(MDIV_SEL),
                     .ODIV0_SEL(ODIV0_SEL), .ODIV1_SEL(ODIV1_SEL), .ODIV2_SEL(ODIV2_SEL))
        m (.CLKIN(CLKIN), .CLKOUT0(CLKOUT0), .CLKOUT1(CLKOUT1), .CLKOUT2(CLKOUT2), .LOCK(LOCK));
    assign CLKFBOUT = CLKIN;
    assign CLKOUT3 = 0; assign CLKOUT4 = 0; assign CLKOUT5 = 0; assign CLKOUT6 = 0;
endmodule

module PLLA
# (parameter FCLKIN = "50", CLKFB_SEL = "INTERNAL",
   parameter integer IDIV_SEL = 1, FBDIV_SEL = 1, ODIV0_SEL = 8, ODIV0_FRAC_SEL = 0, ODIV1_SEL = 8, ODIV2_SEL = 8,
   MDIV_SEL = 20, MDIV_FRAC_SEL = 0,
   parameter CLKOUT0_EN = "TRUE", CLKOUT1_EN = "FALSE", CLKOUT2_EN = "FALSE", CLKOUT3_EN = "FALSE",
   CLKOUT4_EN = "FALSE", CLKOUT5_EN = "FALSE", CLKOUT6_EN = "FALSE")
(output LOCK, output CLKOUT0, output CLKOUT1, output CLKOUT2, output CLKOUT3, output CLKOUT4, output CLKOUT5,
 output CLKOUT6, output CLKFBOUT, output [7:0] MDRDO, input CLKIN, input CLKFB, input RESET, input PLLPWD,
 input RESET_I, input RESET_O, input [2:0] PSSEL, input PSDIR, input PSPULSE, input SSCPOL, input SSCON,
 input [6:0] SSCMDSEL, input [2:0] SSCMDSEL_FRAC, input MDCLK, input [1:0] MDOPC, input MDAINC, input [7:0] MDWDI);
    gw5_pll_model # (.FCLKIN(FCLKIN), .IDIV_SEL(IDIV_SEL), .FBDIV_SEL(FBDIV_SEL), .MDIV_SEL(MDIV_SEL),
                     .ODIV0_SEL(ODIV0_SEL), .ODIV1_SEL(ODIV1_SEL), .ODIV2_SEL(ODIV2_SEL))
        m (.CLKIN(CLKIN), .CLKOUT0(CLKOUT0), .CLKOUT1(CLKOUT1), .CLKOUT2(CLKOUT2), .LOCK(LOCK));
    assign CLKFBOUT = CLKIN; assign MDRDO = 8'b0;
    assign CLKOUT3 = 0; assign CLKOUT4 = 0; assign CLKOUT5 = 0; assign CLKOUT6 = 0;
endmodule

`endif


// Lattice ECP5 PLL (rtl/pll/pll_ecp5.sv): lint-only behavioural shell.
module EHXPLLL
# (
    parameter CLKI_DIV = 1, CLKFB_DIV = 1, CLKOP_DIV = 8, CLKOS_DIV = 8, CLKOS2_DIV = 8, CLKOS3_DIV = 8,
              CLKOP_ENABLE = "ENABLED", CLKOS_ENABLE = "DISABLED", CLKOS2_ENABLE = "DISABLED", CLKOS3_ENABLE = "DISABLED",
              CLKOP_CPHASE = 0, CLKOS_CPHASE = 0, CLKOS2_CPHASE = 0, CLKOS3_CPHASE = 0,
              CLKOP_FPHASE = 0, CLKOS_FPHASE = 0, CLKOS2_FPHASE = 0, CLKOS3_FPHASE = 0,
              FEEDBK_PATH = "CLKOP", CLKOP_TRIM_POL = "FALLING", CLKOP_TRIM_DELAY = 0,
              CLKOS_TRIM_POL = "FALLING", CLKOS_TRIM_DELAY = 0, OUTDIVIDER_MUXA = "DIVA", OUTDIVIDER_MUXB = "DIVB",
              OUTDIVIDER_MUXC = "DIVC", OUTDIVIDER_MUXD = "DIVD", PLL_LOCK_MODE = 0, PLL_LOCK_DELAY = 200,
              STDBY_ENABLE = "DISABLED", REFIN_RESET = "DISABLED", SYNC_ENABLE = "DISABLED", INT_LOCK_STICKY = "ENABLED",
              DPHASE_SOURCE = "DISABLED", PLLRST_ENA = "DISABLED", INTFB_WAKE = "DISABLED"
)
(
    input  CLKI, CLKFB, PHASESEL1, PHASESEL0, PHASEDIR, PHASESTEP, PHASELOADREG, STDBY, PLLWAKESYNC,
    input  RST, ENCLKOP, ENCLKOS, ENCLKOS2, ENCLKOS3,
    output CLKOP, CLKOS, CLKOS2, CLKOS3, LOCK, INTLOCK, REFCLK, CLKINTFB
);
    assign CLKOP = CLKI;
    assign CLKOS = CLKI;
    assign CLKOS2 = CLKI;
    assign CLKOS3 = CLKI;
    assign LOCK = 1'b1;
    assign INTLOCK = 1'b1;
    assign REFCLK = CLKI;
    assign CLKINTFB = CLKI;
endmodule
