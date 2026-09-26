// =============================================================================
// 3_3_note_synthesizer
// =============================================================================
//

module design_top
`include "design_top_interface.svh"


    //------------------------------------------------------------------------

    // assign led        = '0;
    // assign abcdefgh   = '0;
    // assign digit      = '0;
       assign red        = '0;
       assign green      = '0;
       assign blue       = '0;
    // assign sound      = '0;
       assign uart_tx    = '1;
       assign rgb_r      = '0;
       assign rgb_g      = '0;
       assign rgb_b      = '0;

    //------------------------------------------------------------------------

    wire            [2:0] octave = 3'b0;
    wire  [w_btn   - 1:0] note   = btn;

    assign led  = note;

    //------------------------------------------------------------------------

    tone_sel
    # (
        .clk_mhz    (clk_mhz),
        .note_width (w_btn  ),
        .y_width    (w_sound)
    )
    wave_gen
    (
        .clk       ( clk       ),
        .reset     ( rst       ),
        .octave    ( octave    ),
        .note      ( note      ),
        .y         ( sound     )
    );

    //------------------------------------------------------------------------

    assign digit = 1'b1;   // the rightmost digit on (extended to the rig's digits)

    always_ff @ (posedge clk or posedge rst)
        if (rst)
            abcdefgh <= 'b00000000;
        else
            case (note)
            'd0:    abcdefgh <= 'b10011100;  // C   // abcdefgh
            'd1:    abcdefgh <= 'b10011101;  // C#
            'd2:    abcdefgh <= 'b01111010;  // D   //   --a--
            'd3:    abcdefgh <= 'b01111011;  // D#  //  |     |
            'd4:    abcdefgh <= 'b10011110;  // E   //  f     b
            'd5:    abcdefgh <= 'b10001110;  // F   //  |     |
            'd6:    abcdefgh <= 'b10001111;  // F#  //   --g--
            'd7:    abcdefgh <= 'b10111100;  // G   //  |     |
            'd8:    abcdefgh <= 'b10111101;  // G#  //  e     c
            'd9:    abcdefgh <= 'b11101110;  // A   //  |     |
            'd10:   abcdefgh <= 'b11101111;  // A#  //   --d--  h
            'd11:   abcdefgh <= 'b00111110;  // B
            default: abcdefgh <= 'b00000000;
            endcase
endmodule

