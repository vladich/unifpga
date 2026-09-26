// =============================================================================
// 4_1_1_pow5_single_cycle
// =============================================================================
//
// requires:
//   seven_segment >= 1

module design_top
`include "design_top_interface.svh"


    //------------------------------------------------------------------------

       assign led        = '0;
    // assign abcdefgh   = '0;
    // assign digit      = '0;
       assign red        = '0;
       assign green      = '0;
       assign blue       = '0;
       assign sound      = '0;
       assign uart_tx    = '1;
       assign rgb_r      = '0;
       assign rgb_g      = '0;
       assign rgb_b      = '0;

    //------------------------------------------------------------------------

    // Truncate used SW number to 4 to keep STA passing
    localparam w_sw_actual = (w_sw > 8) ? 8
                                        : w_sw;

    //------------------------------------------------------------------------

    logic [w_sw_actual-1:0] pow_input;

    logic [(5*w_sw_actual)-1:0] pow5;

    logic [(5*w_sw_actual)-1:0] pow_output;


    always_ff @ (posedge clk or posedge rst)
        if (rst)
            pow_input <= '0;
        else
            pow_input <= sw;


    assign pow5 = pow_input * pow_input * pow_input * pow_input * pow_input;


    always_ff @ (posedge clk or posedge rst)
        if (rst)
            pow_output <= '0;
        else
            pow_output <= pow5;


    localparam w_display_number = w_digit * 4;

    seven_segment_display # (w_digit) i_7segment
    (
        .clk      ( clk                            ),
        .rst      ( rst                            ),
        .number   ( (pow_output) ),
        .dots     ( (0)                   ),
        .abcdefgh ( abcdefgh                       ),
        .digit    ( digit                          )
    );
endmodule

