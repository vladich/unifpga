// =============================================================================
// pll_gowin_gw5 — one Gowin Arora V PLL with up to three outputs, the
// structural equivalent of the `Gowin_PLL` IP wrapper BGM generates per board
// (tang_mega_138k*: primitive PLL on GW5AST, tang_primer_25k: primitive PLLA
// on GW5A). Codegen computes the dividers with tools/pll_solver.py
// `gowin_gw5_pll`:
//
//     f_pfd = FCLKIN / IDIV_SEL                 (IDIV 1..64)
//     f_vco = f_pfd * FBDIV_SEL * MDIV_SEL      (FBDIV 1..64, MDIV 2..128, 800..1600 MHz)
//     clkout<i> = f_vco / ODIV<i>_SEL           (ODIV 1..128)
//
// checked against BGM's gowin_pll.ipc (138K Pro: 50 MHz, IDIV 1, FBDIV 1,
// MDIV 16, ODIV0 100 -> Clkout0ExpectedFrequency=8).
//
// Only the parameters that differ per instance are exposed; everything else
// keeps the primitive's defaults (BGM's generated file spells all of them
// out with those same defaults).
// =============================================================================

module pll_gowin_gw5
# (
    parameter        PRIMITIVE  = "PLL",     // "PLL" (GW5AST / GW5AT) or "PLLA" (GW5A)
    parameter        FCLKIN     = "50",
    parameter int    IDIV_SEL   = 1,
    parameter int    FBDIV_SEL  = 1,
    parameter int    MDIV_SEL   = 16,
    parameter int    ODIV0_SEL  = 100,
    parameter int    ODIV1_SEL  = 8,
    parameter int    ODIV2_SEL  = 8,
    parameter        CLKOUT1_EN = "FALSE",
    parameter        CLKOUT2_EN = "FALSE"
)
(
    input  clkin,
    output clkout0,
    output clkout1,
    output clkout2,
    output lock
);

    wire gw_gnd = 1'b0;
    wire gw_vcc = 1'b1;

    generate
        if (PRIMITIVE == "PLLA") begin : g_plla

            wire [7:0] mdrdo_o;

            PLLA
            # (
                .FCLKIN        ( FCLKIN     ),
                .IDIV_SEL      ( IDIV_SEL   ),
                .FBDIV_SEL     ( FBDIV_SEL  ),
                .CLKFB_SEL     ( "INTERNAL" ),
                .ODIV0_SEL     ( ODIV0_SEL  ),
                .ODIV0_FRAC_SEL( 0          ),
                .ODIV1_SEL     ( ODIV1_SEL  ),
                .ODIV2_SEL     ( ODIV2_SEL  ),
                .MDIV_SEL      ( MDIV_SEL   ),
                .MDIV_FRAC_SEL ( 0          ),
                .CLKOUT0_EN    ( "TRUE"     ),
                .CLKOUT1_EN    ( CLKOUT1_EN ),
                .CLKOUT2_EN    ( CLKOUT2_EN ),
                .CLKOUT3_EN    ( "FALSE"    ),
                .CLKOUT4_EN    ( "FALSE"    ),
                .CLKOUT5_EN    ( "FALSE"    ),
                .CLKOUT6_EN    ( "FALSE"    )
            )
            u_pll
            (
                .LOCK          ( lock    ),
                .CLKOUT0       ( clkout0 ),
                .CLKOUT1       ( clkout1 ),
                .CLKOUT2       ( clkout2 ),
                .CLKOUT3       (         ),
                .CLKOUT4       (         ),
                .CLKOUT5       (         ),
                .CLKOUT6       (         ),
                .CLKFBOUT      (         ),
                .MDRDO         ( mdrdo_o ),
                .CLKIN         ( clkin   ),
                .CLKFB         ( gw_gnd  ),
                .RESET         ( gw_gnd  ),
                .PLLPWD        ( gw_gnd  ),
                .RESET_I       ( gw_gnd  ),
                .RESET_O       ( gw_gnd  ),
                .PSSEL         ( 3'b000  ),
                .PSDIR         ( gw_gnd  ),
                .PSPULSE       ( gw_gnd  ),
                .SSCPOL        ( gw_gnd  ),
                .SSCON         ( gw_gnd  ),
                .SSCMDSEL      ( 7'b0    ),
                .SSCMDSEL_FRAC ( 3'b0    ),
                .MDCLK         ( gw_gnd  ),
                .MDOPC         ( 2'b00   ),
                .MDAINC        ( gw_gnd  ),
                .MDWDI         ( 8'b0    )
            );

        end else begin : g_pll

            PLL
            # (
                .DYN_IDIV_SEL  ( "FALSE"    ),
                .DYN_FBDIV_SEL ( "FALSE"    ),
                .DYN_ODIV0_SEL ( "FALSE"    ),
                .DYN_ODIV1_SEL ( "FALSE"    ),
                .DYN_ODIV2_SEL ( "FALSE"    ),
                .DYN_MDIV_SEL  ( "FALSE"    ),
                .FCLKIN        ( FCLKIN     ),
                .IDIV_SEL      ( IDIV_SEL   ),
                .FBDIV_SEL     ( FBDIV_SEL  ),
                .CLKFB_SEL     ( "INTERNAL" ),
                .ODIV0_SEL     ( ODIV0_SEL  ),
                .ODIV0_FRAC_SEL( 0          ),
                .ODIV1_SEL     ( ODIV1_SEL  ),
                .ODIV2_SEL     ( ODIV2_SEL  ),
                .MDIV_SEL      ( MDIV_SEL   ),
                .MDIV_FRAC_SEL ( 0          ),
                .CLKOUT0_EN    ( "TRUE"     ),
                .CLKOUT1_EN    ( CLKOUT1_EN ),
                .CLKOUT2_EN    ( CLKOUT2_EN ),
                .CLKOUT3_EN    ( "FALSE"    ),
                .CLKOUT4_EN    ( "FALSE"    ),
                .CLKOUT5_EN    ( "FALSE"    ),
                .CLKOUT6_EN    ( "FALSE"    )
            )
            u_pll
            (
                .LOCK          ( lock    ),
                .CLKOUT0       ( clkout0 ),
                .CLKOUT1       ( clkout1 ),
                .CLKOUT2       ( clkout2 ),
                .CLKOUT3       (         ),
                .CLKOUT4       (         ),
                .CLKOUT5       (         ),
                .CLKOUT6       (         ),
                .CLKFBOUT      (         ),
                .CLKIN         ( clkin   ),
                .CLKFB         ( gw_gnd  ),
                .RESET         ( gw_gnd  ),
                .PLLPWD        ( gw_gnd  ),
                .RESET_I       ( gw_gnd  ),
                .RESET_O       ( gw_gnd  ),
                .FBDSEL        ( 6'b0    ),
                .IDSEL         ( 6'b0    ),
                .MDSEL         ( 7'b0    ),
                .MDSEL_FRAC    ( 3'b0    ),
                .ODSEL0        ( 7'b0    ),
                .ODSEL0_FRAC   ( 3'b0    ),
                .ODSEL1        ( 7'b0    ),
                .ODSEL2        ( 7'b0    ),
                .ODSEL3        ( 7'b0    ),
                .ODSEL4        ( 7'b0    ),
                .ODSEL5        ( 7'b0    ),
                .ODSEL6        ( 7'b0    ),
                .DT0           ( 4'b0    ),
                .DT1           ( 4'b0    ),
                .DT2           ( 4'b0    ),
                .DT3           ( 4'b0    ),
                .ICPSEL        ( 6'b0    ),
                .LPFRES        ( 3'b0    ),
                .LPFCAP        ( 2'b0    ),
                .PSSEL         ( 3'b0    ),
                .PSDIR         ( gw_gnd  ),
                .PSPULSE       ( gw_gnd  ),
                .ENCLK0        ( gw_vcc  ),
                .ENCLK1        ( gw_vcc  ),
                .ENCLK2        ( gw_vcc  ),
                .ENCLK3        ( gw_vcc  ),
                .ENCLK4        ( gw_vcc  ),
                .ENCLK5        ( gw_vcc  ),
                .ENCLK6        ( gw_vcc  ),
                .SSCPOL        ( gw_gnd  ),
                .SSCON         ( gw_gnd  ),
                .SSCMDSEL      ( 7'b0    ),
                .SSCMDSEL_FRAC ( 3'b0    )
            );

        end
    endgenerate

endmodule
