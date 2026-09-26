// =============================================================================
// 3_11_microphone_monitor
// =============================================================================
//
// requires:
//   leds >= 2
//   seven_segment >= 1
//   screen >= 320x240

module design_top
`include "design_top_interface.svh"


    //------------------------------------------------------------------------

    // assign led        = '0;
    // assign abcdefgh   = '0;
    // assign digit      = '0;
    // assign red        = '0;
    // assign green      = '0;
    // assign blue       = '0;
    // assign sound      = '0;
       assign uart_tx    = '1;
       assign rgb_r      = '0;
       assign rgb_g      = '0;
       assign rgb_b      = '0;

    //------------------------------------------------------------------------

    logic signed [10:0] mic_11bit;
    wire         [ 7:0] vol;

    // Spectrum analyzer level adjust
    convert
    # (
        .w_in  ( 24        ),
        .w_out ( 11        ),
        .lev   ( 17        ),
        .agc   ( 1         )
    )
    i_convert
    (
        .clk   ( clk       ),
        .rst   ( rst       ),
        .in    ( mic_sample       ),
        .out   ( mic_11bit ),
        .led   ( led[0]    ),
        .vol   ( vol[2:0]  )
    );

    // Sound output level adjust
    convert
    # (
        .w_in  ( 24        ),
        .w_out ( 16        ),
        .lev   ( 17        ),
        .agc   ( 1         )
    )
    i_convert_line
    (
        .clk   ( clk       ),
        .rst   ( rst       ),
        .in    ( mic_sample       ),
        .out   ( sound     ),
        .led   ( led[1]    ),
        .vol   ( vol[6:4]  )
    );

    seven_segment_display i_7segment
    (
        .clk      ( clk       ),
        .rst      ( rst       ),
        .number   ( 8' (vol)  ),
        .dots     ( 2'b0      ),
        .abcdefgh ( abcdefgh  ),
        .digit    ( digit     )
    );

    //------------------------------------------------------------------------

    spectrum
    # (
        .clk_mhz       ( clk_mhz       ),

        .screen_width  ( screen_width  ),
        .screen_height ( screen_height ),
        .w_red         ( w_red         ),
        .w_green       ( w_green       ),
        .w_blue        ( w_blue        ),

    // Frequency bands of the spectrum analyzer
        .freq          ({14'd525, 14'd458, 14'd400, 14'd348,
                         14'd303, 14'd264, 14'd230, 14'd200})
    )
    i_spectrum
    (
        .clk           ( clk           ),
        .rst           ( rst           ),
        .x             ( x             ),
        .y             ( y             ),
        .red           ( red           ),
        .green         ( green         ),
        .blue          ( blue          ),
        .mic_sample           ( mic_11bit     )
    );
endmodule

