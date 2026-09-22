// =============================================================================
// pll_ecp5 — one Lattice ECP5 EHXPLLL making one clock, the structural
// equivalent of BGM's boards/colorlight75b_tm1638_ecp5_yosys/clock.v (25 MHz
// -> 250 MHz TMDS clock). Feedback from CLKOP, the requested clock on CLKOS.
// Codegen computes the dividers with tools/pll_solver.py `ecp5_pll`:
//
//     f_vco  = FCLKIN / CLKI_DIV * CLKFB_DIV * CLKOP_DIV      (400..800 MHz)
//     clkout = f_vco / CLKOS_DIV
//
// CPHASE = DIV - 1 on each output, as ecppll and BGM's clock.v set them.
// =============================================================================

module pll_ecp5
# (
    parameter int CLKI_DIV  = 1,
    parameter int CLKFB_DIV = 5,
    parameter int CLKOP_DIV = 4,
    parameter int CLKOS_DIV = 2
)
(
    input  clkin,
    output clkout,
    output lock
);

    wire clkop;

    (* ICP_CURRENT = "9" *) (* LPF_RESISTOR = "8" *) (* MFG_ENABLE_FILTEROPAMP = "1" *) (* MFG_GMCREF_SEL = "2" *)
    EHXPLLL
    # (
        .PLLRST_ENA       ( "DISABLED" ),
        .INTFB_WAKE       ( "DISABLED" ),
        .STDBY_ENABLE     ( "DISABLED" ),
        .DPHASE_SOURCE    ( "DISABLED" ),
        .OUTDIVIDER_MUXA  ( "DIVA"     ),
        .OUTDIVIDER_MUXB  ( "DIVB"     ),
        .OUTDIVIDER_MUXC  ( "DIVC"     ),
        .OUTDIVIDER_MUXD  ( "DIVD"     ),
        .CLKI_DIV         ( CLKI_DIV   ),
        .CLKOP_ENABLE     ( "ENABLED"  ),
        .CLKOP_DIV        ( CLKOP_DIV  ),
        .CLKOP_CPHASE     ( CLKOP_DIV - 1 ),
        .CLKOP_FPHASE     ( 0          ),
        .CLKOS_ENABLE     ( "ENABLED"  ),
        .CLKOS_DIV        ( CLKOS_DIV  ),
        .CLKOS_CPHASE     ( CLKOS_DIV - 1 ),
        .CLKOS_FPHASE     ( 0          ),
        .FEEDBK_PATH      ( "CLKOP"    ),
        .CLKFB_DIV        ( CLKFB_DIV  )
    )
    u_pll
    (
        .CLKI        ( clkin  ),
        .CLKFB       ( clkop  ),
        .CLKOP       ( clkop  ),
        .CLKOS       ( clkout ),
        .CLKOS2      (        ),
        .CLKOS3      (        ),
        .RST         ( 1'b0   ),
        .STDBY       ( 1'b0   ),
        .PHASESEL0   ( 1'b0   ),
        .PHASESEL1   ( 1'b0   ),
        .PHASEDIR    ( 1'b0   ),
        .PHASESTEP   ( 1'b0   ),
        .PHASELOADREG( 1'b0   ),
        .PLLWAKESYNC ( 1'b0   ),
        .ENCLKOP     ( 1'b0   ),
        .ENCLKOS     ( 1'b0   ),
        .ENCLKOS2    ( 1'b0   ),
        .ENCLKOS3    ( 1'b0   ),
        .LOCK        ( lock   ),
        .INTLOCK     (        ),
        .REFCLK      (        ),
        .CLKINTFB    (        )
    );

endmodule
