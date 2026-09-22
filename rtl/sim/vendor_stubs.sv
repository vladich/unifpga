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

`endif
