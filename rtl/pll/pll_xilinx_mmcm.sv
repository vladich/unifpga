// =============================================================================
// pll_xilinx_mmcm — one MMCME2_BASE with up to three output clocks, the
// structural equivalent of the `clk_wiz` core generated per board
// (a7_lite_35t: 50 MHz in -> 250 MHz serial, 50 MHz lab, 25 MHz pixel).
// Codegen computes the dividers with tools/pll_solver.py `xilinx_mmcm`:
//
//     f_pfd = f_in / DIVCLK_DIVIDE            10 .. 450 MHz
//     f_vco = f_pfd * CLKFBOUT_MULT_F         600 .. 1200 MHz (7-series -1)
//     clkout<i> = f_vco / CLKOUT<i>_DIVIDE
//
// Every used output goes through a BUFG. Unused outputs: leave the port open
// and keep the divider at 1.
// =============================================================================

module pll_xilinx_mmcm
# (
    parameter real CLKIN_PERIOD    = 20.0,   // ns
    parameter int  DIVCLK_DIVIDE   = 1,
    parameter real CLKFBOUT_MULT_F = 20.0,
    parameter real CLKOUT0_DIVIDE  = 4.0,
    parameter int  CLKOUT1_DIVIDE  = 1,
    parameter int  CLKOUT2_DIVIDE  = 1
)
(
    input  clkin,
    output clkout0,
    output clkout1,
    output clkout2,
    output lock
);

    wire clkfb, clkfb_buf;
    wire clkout0_raw, clkout1_raw, clkout2_raw;

    MMCME2_BASE
    # (
        .BANDWIDTH          ( "OPTIMIZED"     ),
        .CLKIN1_PERIOD      ( CLKIN_PERIOD    ),
        .DIVCLK_DIVIDE      ( DIVCLK_DIVIDE   ),
        .CLKFBOUT_MULT_F    ( CLKFBOUT_MULT_F ),
        .CLKFBOUT_PHASE     ( 0.0             ),
        .CLKOUT0_DIVIDE_F   ( CLKOUT0_DIVIDE  ),
        .CLKOUT1_DIVIDE     ( CLKOUT1_DIVIDE  ),
        .CLKOUT2_DIVIDE     ( CLKOUT2_DIVIDE  ),
        .CLKOUT3_DIVIDE     ( 1               ),
        .CLKOUT4_DIVIDE     ( 1               ),
        .CLKOUT5_DIVIDE     ( 1               ),
        .CLKOUT6_DIVIDE     ( 1               ),
        .CLKOUT0_DUTY_CYCLE ( 0.5             ),
        .CLKOUT1_DUTY_CYCLE ( 0.5             ),
        .CLKOUT2_DUTY_CYCLE ( 0.5             ),
        .CLKOUT0_PHASE      ( 0.0             ),
        .CLKOUT1_PHASE      ( 0.0             ),
        .CLKOUT2_PHASE      ( 0.0             ),
        .CLKOUT4_CASCADE    ( "FALSE"         ),
        .REF_JITTER1        ( 0.010           ),
        .STARTUP_WAIT       ( "FALSE"         )
    )
    u_mmcm
    (
        .CLKIN1    ( clkin       ),
        .CLKFBIN   ( clkfb_buf   ),
        .CLKFBOUT  ( clkfb       ),
        .CLKFBOUTB (             ),
        .CLKOUT0   ( clkout0_raw ),
        .CLKOUT0B  (             ),
        .CLKOUT1   ( clkout1_raw ),
        .CLKOUT1B  (             ),
        .CLKOUT2   ( clkout2_raw ),
        .CLKOUT2B  (             ),
        .CLKOUT3   (             ),
        .CLKOUT3B  (             ),
        .CLKOUT4   (             ),
        .CLKOUT5   (             ),
        .CLKOUT6   (             ),
        .LOCKED    ( lock        ),
        .PWRDWN    ( 1'b0        ),
        .RST       ( 1'b0        )
    );

    BUFG u_bufg_fb (.I (clkfb),       .O (clkfb_buf));
    BUFG u_bufg_0  (.I (clkout0_raw), .O (clkout0));
    BUFG u_bufg_1  (.I (clkout1_raw), .O (clkout1));
    BUFG u_bufg_2  (.I (clkout2_raw), .O (clkout2));

endmodule
