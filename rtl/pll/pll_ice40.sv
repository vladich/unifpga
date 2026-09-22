// =============================================================================
// pll_ice40 — generic wrapper around the Lattice iCE40 SB_PLL40 primitive
// (SIMPLE feedback), the way BGM instantiates it on the iCEBreaker for the
// 25.125 MHz DVI pixel clock. Codegen computes DIVR / DIVF / DIVQ /
// FILTER_RANGE with tools/pll_solver.py:
//
//   f_pfd = f_in / (DIVR + 1)        10 .. 133 MHz
//   f_vco = f_pfd * (DIVF + 1)       533 .. 1066 MHz
//   f_out = f_vco / 2^DIVQ
//
// USE_PAD = 1 (default, BGM's choice): SB_PLL40_PAD, `clkin` is the clock
// pad itself. USE_PAD = 0: SB_PLL40_CORE fed from fabric.
// =============================================================================

module pll_ice40
# (
    parameter [3:0] DIVR         = 4'd0,
    parameter [6:0] DIVF         = 7'd66,
    parameter [2:0] DIVQ         = 3'd5,
    parameter [2:0] FILTER_RANGE = 3'd1,
    parameter bit   USE_PAD      = 1'b1
)
(
    input  clkin,
    output clkout,
    output lock
);

    generate
        if (USE_PAD) begin : g_pad

            SB_PLL40_PAD
            # (
                .FEEDBACK_PATH ( "SIMPLE"     ),
                .DIVR          ( DIVR         ),
                .DIVF          ( DIVF         ),
                .DIVQ          ( DIVQ         ),
                .FILTER_RANGE  ( FILTER_RANGE )
            )
            pll
            (
                .LOCK          ( lock   ),
                .RESETB        ( 1'b1   ),
                .BYPASS        ( 1'b0   ),
                .PACKAGEPIN    ( clkin  ),
                .PLLOUTCORE    ( clkout )
            );

        end else begin : g_core

            SB_PLL40_CORE
            # (
                .FEEDBACK_PATH ( "SIMPLE"     ),
                .DIVR          ( DIVR         ),
                .DIVF          ( DIVF         ),
                .DIVQ          ( DIVQ         ),
                .FILTER_RANGE  ( FILTER_RANGE )
            )
            pll
            (
                .LOCK          ( lock   ),
                .RESETB        ( 1'b1   ),
                .BYPASS        ( 1'b0   ),
                .REFERENCECLK  ( clkin  ),
                .PLLOUTCORE    ( clkout )
            );

        end
    endgenerate

endmodule
