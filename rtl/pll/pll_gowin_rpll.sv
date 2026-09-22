// =============================================================================
// pll_gowin_rpll — generic wrapper around the Gowin rPLL primitive
// (GW1N / GW1NR / GW1NS / GW1NSR / GW2A / GW2AR families).
//
// Same instantiation as the Gowin_rPLL modules BGM ships per board
// (boards/<variant>/gowin_rpll.v), with the divider settings passed as
// parameters. Codegen computes them with tools/pll_solver.py from the
// board clock and the frequency a peripheral asks for:
//
//   f_pfd   = FCLKIN / (IDIV_SEL + 1)
//   CLKOUT  = f_pfd * (FBDIV_SEL + 1)         (VCO = CLKOUT * ODIV_SEL, 400..1200 MHz)
//   CLKOUTD = CLKOUT / DYN_SDIV_SEL
//
// USE_CLKOUTD selects which of the two feeds `clkout`.
// =============================================================================

module pll_gowin_rpll
# (
    parameter        FCLKIN      = "27",
    parameter int    IDIV_SEL    = 0,
    parameter int    FBDIV_SEL   = 0,
    parameter int    ODIV_SEL    = 8,
    parameter int    DYN_SDIV_SEL = 2,
    parameter bit    USE_CLKOUTD = 1'b0,
    parameter        DEVICE      = "GW1NR-9C"
)
(
    input  clkin,
    output clkout,
    output lock
);

    wire clkout_o;
    wire clkoutp_o;
    wire clkoutd_o;
    wire clkoutd3_o;
    wire gw_gnd = 1'b0;

    rPLL rpll_inst (
        .CLKOUT   ( clkout_o   ),
        .LOCK     ( lock       ),
        .CLKOUTP  ( clkoutp_o  ),
        .CLKOUTD  ( clkoutd_o  ),
        .CLKOUTD3 ( clkoutd3_o ),
        .RESET    ( gw_gnd     ),
        .RESET_P  ( gw_gnd     ),
        .CLKIN    ( clkin      ),
        .CLKFB    ( gw_gnd     ),
        .FBDSEL   ( {gw_gnd, gw_gnd, gw_gnd, gw_gnd, gw_gnd, gw_gnd} ),
        .IDSEL    ( {gw_gnd, gw_gnd, gw_gnd, gw_gnd, gw_gnd, gw_gnd} ),
        .ODSEL    ( {gw_gnd, gw_gnd, gw_gnd, gw_gnd, gw_gnd, gw_gnd} ),
        .PSDA     ( {gw_gnd, gw_gnd, gw_gnd, gw_gnd} ),
        .DUTYDA   ( {gw_gnd, gw_gnd, gw_gnd, gw_gnd} ),
        .FDLY     ( {gw_gnd, gw_gnd, gw_gnd, gw_gnd} )
    );

    defparam rpll_inst.FCLKIN          = FCLKIN;
    defparam rpll_inst.DYN_IDIV_SEL    = "false";
    defparam rpll_inst.IDIV_SEL        = IDIV_SEL;
    defparam rpll_inst.DYN_FBDIV_SEL   = "false";
    defparam rpll_inst.FBDIV_SEL       = FBDIV_SEL;
    defparam rpll_inst.DYN_ODIV_SEL    = "false";
    defparam rpll_inst.ODIV_SEL        = ODIV_SEL;
    defparam rpll_inst.PSDA_SEL        = "0000";
    defparam rpll_inst.DYN_DA_EN       = "true";
    defparam rpll_inst.DUTYDA_SEL      = "1000";
    defparam rpll_inst.CLKOUT_FT_DIR   = 1'b1;
    defparam rpll_inst.CLKOUTP_FT_DIR  = 1'b1;
    defparam rpll_inst.CLKOUT_DLY_STEP = 0;
    defparam rpll_inst.CLKOUTP_DLY_STEP = 0;
    defparam rpll_inst.CLKFB_SEL       = "internal";
    defparam rpll_inst.CLKOUT_BYPASS   = "false";
    defparam rpll_inst.CLKOUTP_BYPASS  = "false";
    defparam rpll_inst.CLKOUTD_BYPASS  = "false";
    defparam rpll_inst.DYN_SDIV_SEL    = DYN_SDIV_SEL;
    defparam rpll_inst.CLKOUTD_SRC     = "CLKOUT";
    defparam rpll_inst.CLKOUTD3_SRC    = "CLKOUT";
    defparam rpll_inst.DEVICE          = DEVICE;

    assign clkout = USE_CLKOUTD ? clkoutd_o : clkout_o;

endmodule
