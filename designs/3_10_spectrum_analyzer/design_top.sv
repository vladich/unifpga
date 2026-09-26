// =============================================================================
// 3_10_spectrum_analyzer
// =============================================================================
//
// requires:
//   screen >= 320x240

module design_top
`include "design_top_interface.svh"


    //------------------------------------------------------------------------

    // assign led        = '0;
       assign abcdefgh   = '0;
       assign digit      = '0;
    // assign red        = '0;
    // assign green      = '0;
    // assign blue       = '0;
       assign sound      = '0;
       assign uart_tx    = '1;
       assign rgb_r      = '0;
       assign rgb_g      = '0;
       assign rgb_b      = '0;

    //------------------------------------------------------------------------

    logic signed [10:0] mic_11bit;

    always_ff @(posedge clk or posedge rst) begin
        if (rst) begin
            mic_11bit <= '0;
            led       <= '0;
        end
        else if (mic_sample[17] != mic_sample[18]) begin // overflow prevention
            mic_11bit <= {mic_sample[18], {10 {~mic_sample[18]}}};
            led       <= '1;                 // overflow warning
        end
        else begin
            mic_11bit <= mic_sample[17:7];
            led       <= '0;
        end
    end

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
        .freq          ({14'd600, 14'd525, 14'd458, 14'd400, 14'd348, 14'd303,
                         14'd264, 14'd230, 14'd200, 14'd174, 14'd152, 14'd132})
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

