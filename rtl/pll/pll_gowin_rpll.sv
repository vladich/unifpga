// =============================================================================
// pll_gowin_rpll — generic wrapper around the Gowin rPLL primitive
// (GW1N / GW1NR / GW2A / GW2AR families; PLLVR on GW1NS / GW1NSR).
//
// Same instantiation as the Gowin_rPLL IP modules generated per board
// (gowin_rpll.v), with the divider settings passed as
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
    parameter        PRIMITIVE   = "rPLL",   // "rPLL", or "PLLVR" on GW1NS / GW1NSR (Tang Nano 4K)
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
    output clkoutd,      // CLKOUT / DYN_SDIV_SEL (when USE_CLKOUTD = 0, a second clock)
    output lock
);

    wire clkout_o;
    wire clkoutp_o;
    wire clkoutd_o;
    wire clkoutd3_o;
    wire gw_gnd = 1'b0;
    wire gw_vcc = 1'b1;

    generate
        if (PRIMITIVE == "PLLVR") begin : g_pllvr

            // GW1NS-4 / GW1NSR-4C have no rPLL ("EX0312: There is no rPLL
            // resource in current device"); PLLVR is the same block with an
            // internal regulator enable.
            PLLVR
            # (
                .FCLKIN           ( FCLKIN       ),
                .DYN_IDIV_SEL     ( "false"      ),
                .IDIV_SEL         ( IDIV_SEL     ),
                .DYN_FBDIV_SEL    ( "false"      ),
                .FBDIV_SEL        ( FBDIV_SEL    ),
                .DYN_ODIV_SEL     ( "false"      ),
                .ODIV_SEL         ( ODIV_SEL     ),
                .PSDA_SEL         ( "0000"       ),
                .DYN_DA_EN        ( "true"       ),
                .DUTYDA_SEL       ( "1000"       ),
                .CLKOUT_FT_DIR    ( 1'b1         ),
                .CLKOUTP_FT_DIR   ( 1'b1         ),
                .CLKOUT_DLY_STEP  ( 0            ),
                .CLKOUTP_DLY_STEP ( 0            ),
                .CLKFB_SEL        ( "internal"   ),
                .CLKOUT_BYPASS    ( "false"      ),
                .CLKOUTP_BYPASS   ( "false"      ),
                .CLKOUTD_BYPASS   ( "false"      ),
                .DYN_SDIV_SEL     ( DYN_SDIV_SEL ),
                .CLKOUTD_SRC      ( "CLKOUT"     ),
                .CLKOUTD3_SRC     ( "CLKOUT"     ),
                .DEVICE           ( DEVICE       )
            )
            rpll_inst
            (
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
                .FDLY     ( {gw_gnd, gw_gnd, gw_gnd, gw_gnd} ),
                .VREN     ( gw_vcc     )
            );

        end else begin : g_rpll

            rPLL
            # (
                .FCLKIN           ( FCLKIN       ),
                .DYN_IDIV_SEL     ( "false"      ),
                .IDIV_SEL         ( IDIV_SEL     ),
                .DYN_FBDIV_SEL    ( "false"      ),
                .FBDIV_SEL        ( FBDIV_SEL    ),
                .DYN_ODIV_SEL     ( "false"      ),
                .ODIV_SEL         ( ODIV_SEL     ),
                .PSDA_SEL         ( "0000"       ),
                .DYN_DA_EN        ( "true"       ),
                .DUTYDA_SEL       ( "1000"       ),
                .CLKOUT_FT_DIR    ( 1'b1         ),
                .CLKOUTP_FT_DIR   ( 1'b1         ),
                .CLKOUT_DLY_STEP  ( 0            ),
                .CLKOUTP_DLY_STEP ( 0            ),
                .CLKFB_SEL        ( "internal"   ),
                .CLKOUT_BYPASS    ( "false"      ),
                .CLKOUTP_BYPASS   ( "false"      ),
                .CLKOUTD_BYPASS   ( "false"      ),
                .DYN_SDIV_SEL     ( DYN_SDIV_SEL ),
                .CLKOUTD_SRC      ( "CLKOUT"     ),
                .CLKOUTD3_SRC     ( "CLKOUT"     ),
                .DEVICE           ( DEVICE       )
            )
            rpll_inst
            (
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

        end
    endgenerate

    assign clkout = USE_CLKOUTD ? clkoutd_o : clkout_o;
    assign clkoutd = clkoutd_o;

endmodule
