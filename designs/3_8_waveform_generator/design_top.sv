// =============================================================================
// 3_8_waveform_generator
// =============================================================================
//
// requires:
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

    wire [        2:0] octave   = 3'b0;
    wire [w_btn - 1:0] waveform = btn;

    assign             led      = waveform;
    assign             digit    = (1);

    //------------------------------------------------------------------------

    //     Control:
    // Key 0 - Sine     wave
    // Key 1 - Triangle wave
    // Key 2 - Square   wave

    waveform_gen
    # (
        .clk_mhz        (clk_mhz       ),
        .waveform_width (w_btn         ),
        .y_width        (w_sound       )
    )
    i_waveform_gen
    (
        .clk            ( clk          ),
        .rst            ( rst          ),
        .octave         ( octave       ),
        .waveform       ( waveform     ),

        .y              ( sound        )
    );

    //------------------------------------------------------------------------

    oscilloscope
    # (
        .clk_mhz       ( clk_mhz       ),

        .screen_width  ( screen_width  ),
        .screen_height ( screen_height ),
        .w_red         ( w_red         ),
        .w_green       ( w_green       ),
        .w_blue        ( w_blue        )
    )
    i_oscilloscope
    (
        .clk           ( clk           ),
        .rst           ( rst           ),
        .x             ( x             ),
        .y             ( y             ),
        .red           ( red           ),
        .green         ( green         ),
        .blue          ( blue          ),

        .mic_sample           ( sound         )
    );

    //------------------------------------------------------------------------

    always_ff @ (posedge clk or posedge rst)
        if (rst)
            abcdefgh <= 'b00000000;
        else
            case (waveform)
            'd1:    abcdefgh <= 'b10110110;  // S   // abcdefgh
            'd2:    abcdefgh <= 'b00011110;  // T
            'd4:    abcdefgh <= 'b11100110;  // Q   //   --a--
                                                    //  |     |
                                                    //  f     b
                                                    //  |     |
                                                    //   --g--
                                                    //  |     |
                                                    //  e     c
                                                    //  |     |
            default: abcdefgh <= 'b00000000;        //   --d--  h
            endcase
endmodule

