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
    // CLKOUTD rises with CLKOUT's first edge: a PLL's divided output is
    // phase-aligned with its other outputs (the count starts one short)
    integer sdiv_cnt = DYN_SDIV_SEL - 1;
    initial begin
        CLKOUT = 0; CLKOUTP = 0; CLKOUTD = 0; CLKOUTD3 = 0; LOCK = 0;
    end
    always @(posedge CLKIN) begin
        if (last != 0.0) t_in = $realtime - last;
        last = $realtime;
    end
    initial #1 CLKOUT = 1'b1;              // first rising edge at 1 ns
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
    initial #1 begin PLLOUTCORE = 1'b1; PLLOUTGLOBAL = 1'b1; end
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
    // A constant VCO period: an `initial`-assigned real races the `always`
    // delays at time 0 (a #0 loop that never advances time).
    localparam real T_VCO = (CLKIN1_PERIOD > 0.0 ? CLKIN1_PERIOD : 20.0) * DIVCLK_DIVIDE / CLKFBOUT_MULT_F;
    initial begin CLKOUT0 = 0; CLKOUT1 = 0; CLKOUT2 = 0; LOCKED = 0; end
    initial #1 begin CLKOUT0 = 1'b1; CLKOUT1 = 1'b1; CLKOUT2 = 1'b1; end
    always #(T_VCO * CLKOUT0_DIVIDE_F / 2.0) CLKOUT0 = ~CLKOUT0;
    always #(T_VCO * CLKOUT1_DIVIDE   / 2.0) CLKOUT1 = ~CLKOUT1;
    always #(T_VCO * CLKOUT2_DIVIDE   / 2.0) CLKOUT2 = ~CLKOUT2;
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
    initial #1 begin CLKOUT0 = 1'b1; CLKOUT1 = 1'b1; CLKOUT2 = 1'b1; end
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
   DYN_ODIV2_SEL = "FALSE", DYN_ODIV3_SEL = "FALSE", DYN_ODIV4_SEL = "FALSE", DYN_ODIV5_SEL = "FALSE",
   DYN_ODIV6_SEL = "FALSE", DYN_MDIV_SEL = "FALSE", DYN_DT0_SEL = "FALSE", DYN_DT1_SEL = "FALSE",
   DYN_DT2_SEL = "FALSE", DYN_DT3_SEL = "FALSE", DYN_ICP_SEL = "FALSE", DYN_LPF_SEL = "FALSE",
   DYN_PE0_SEL = "FALSE", DYN_PE1_SEL = "FALSE", DYN_PE2_SEL = "FALSE", DYN_PE3_SEL = "FALSE",
   DYN_PE4_SEL = "FALSE", DYN_PE5_SEL = "FALSE", DYN_PE6_SEL = "FALSE", DYN_DPA_EN = "FALSE",
   FCLKIN = "50", CLKFB_SEL = "INTERNAL", CLKOUT0_PE_COARSE = 0, CLKOUT0_PE_FINE = 0, CLKOUT1_PE_COARSE = 0,
   CLKOUT1_PE_FINE = 0, CLKOUT2_PE_COARSE = 0, CLKOUT2_PE_FINE = 0, CLKOUT3_PE_COARSE = 0, CLKOUT3_PE_FINE = 0,
   CLKOUT4_PE_COARSE = 0, CLKOUT4_PE_FINE = 0, CLKOUT5_PE_COARSE = 0, CLKOUT5_PE_FINE = 0, CLKOUT6_PE_COARSE = 0,
   CLKOUT6_PE_FINE = 0, DE0_EN = "FALSE", DE1_EN = "FALSE", DE2_EN = "FALSE", DE3_EN = "FALSE", DE4_EN = "FALSE",
   DE5_EN = "FALSE", DE6_EN = "FALSE", DT0_SEL = 0, DT1_SEL = 0, DT2_SEL = 0, DT3_SEL = 0, ICP_SEL = 0, LPF_RES = 0,
   LPF_CAP = 0, SSC_EN = "FALSE", RESET_I_EN = "FALSE", RESET_O_EN = "FALSE", CLKOUT0_DT_DIR = 1'b1,
   CLKOUT1_DT_DIR = 1'b1, CLKOUT2_DT_DIR = 1'b1, CLKOUT3_DT_DIR = 1'b1, CLKOUT0_DT_STEP = 0, CLKOUT1_DT_STEP = 0,
   CLKOUT2_DT_STEP = 0, CLKOUT3_DT_STEP = 0, CLK0_IN_SEL = 1'b0, CLK0_OUT_SEL = 1'b0, CLK1_IN_SEL = 1'b0,
   CLK1_OUT_SEL = 1'b0, CLK2_IN_SEL = 1'b0, CLK2_OUT_SEL = 1'b0, CLK3_IN_SEL = 1'b0, CLK3_OUT_SEL = 1'b0,
   CLK4_IN_SEL = 2'b00, CLK4_OUT_SEL = 1'b0, CLK5_IN_SEL = 1'b0, CLK5_OUT_SEL = 1'b0, CLK6_IN_SEL = 1'b0,
   CLK6_OUT_SEL = 1'b0, CLKOUT3_PE_COARSE_ = 0,
   parameter integer IDIV_SEL = 1, FBDIV_SEL = 1, ODIV0_SEL = 100, ODIV0_FRAC_SEL = 0, ODIV1_SEL = 8, ODIV2_SEL = 8,
   ODIV3_SEL = 8, ODIV4_SEL = 8, ODIV5_SEL = 8, ODIV6_SEL = 8, MDIV_SEL = 16, MDIV_FRAC_SEL = 0,
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
   CLKOUT4_EN = "FALSE", CLKOUT5_EN = "FALSE", CLKOUT6_EN = "FALSE",
   // accepted for the generated IP wrappers' defparams, not modelled
   parameter CLK0_IN_SEL = 0, CLK0_OUT_SEL = 0, CLK1_IN_SEL = 0, CLK1_OUT_SEL = 0, CLK2_IN_SEL = 0, CLK2_OUT_SEL = 0, CLK3_IN_SEL = 0, CLK3_OUT_SEL = 0, CLK4_IN_SEL = 0, CLK4_OUT_SEL = 0, CLK5_IN_SEL = 0, CLK5_OUT_SEL = 0, CLK6_IN_SEL = 0, CLK6_OUT_SEL = 0, CLKOUT0_DT_DIR = 0, CLKOUT0_DT_STEP = 0, CLKOUT0_PE_COARSE = 0, CLKOUT0_PE_FINE = 0, CLKOUT1_DT_DIR = 0, CLKOUT1_DT_STEP = 0, CLKOUT1_PE_COARSE = 0, CLKOUT1_PE_FINE = 0, CLKOUT2_DT_DIR = 0, CLKOUT2_DT_STEP = 0, CLKOUT2_PE_COARSE = 0, CLKOUT2_PE_FINE = 0, CLKOUT3_DT_DIR = 0, CLKOUT3_DT_STEP = 0, CLKOUT3_PE_COARSE = 0, CLKOUT3_PE_FINE = 0, CLKOUT4_PE_COARSE = 0, CLKOUT4_PE_FINE = 0, CLKOUT5_PE_COARSE = 0, CLKOUT5_PE_FINE = 0, CLKOUT6_PE_COARSE = 0, CLKOUT6_PE_FINE = 0, DE0_EN = 0, DE1_EN = 0, DE2_EN = 0, DE3_EN = 0, DE4_EN = 0, DE5_EN = 0, DE6_EN = 0, DYN_DPA_EN = 0, DYN_PE0_SEL = 0, DYN_PE1_SEL = 0, DYN_PE2_SEL = 0, DYN_PE3_SEL = 0, DYN_PE4_SEL = 0, DYN_PE5_SEL = 0, DYN_PE6_SEL = 0, ICP_SEL = 0, LPF_CAP = 0, LPF_RES = 0, ODIV3_SEL = 0, ODIV4_SEL = 0, ODIV5_SEL = 0, ODIV6_SEL = 0, RESET_I_EN = 0, RESET_O_EN = 0, SSC_EN = 0)
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

// ---- Lattice iCE40 -----------------------------------------------------------

module SB_GB (input USER_SIGNAL_TO_GLOBAL_BUFFER, output GLOBAL_BUFFER_OUTPUT);
    assign GLOBAL_BUFFER_OUTPUT = USER_SIGNAL_TO_GLOBAL_BUFFER;
endmodule

// PIN_TYPE[5:2] output modes used: 0110 simple, 0101 registered,
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

