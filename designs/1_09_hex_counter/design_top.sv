// =============================================================================
// 1_09_hex_counter
// =============================================================================
//
// requires:
//   buttons >= 2
//   seven_segment >= 1

module design_top
`include "design_top_interface.svh"


    //------------------------------------------------------------------------

    // assign led        = '0;
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

    // Exercise 1. Synthesize the counter controlled by two keys.
    // When one btn is in pressed position - the frequency increases,
    // when another btn is in pressed position - the frequency decreases.
    // Change the period increment / decrement and see what happens.

    logic [31:0] period;

    localparam min_period = clk_mhz * 1000 * 1000 / 50,
               max_period = clk_mhz * 1000 * 1000 *  3;

    always_ff @ (posedge clk or posedge rst)
        if (rst)
            period <= 32' ((min_period + max_period) / 2);
        else if (btn [0] & period != max_period)
            period <= period + 32'h1;
        else if (btn [1] & period != min_period)
            period <= period - 32'h1;

    logic [31:0] cnt_1;

    always_ff @ (posedge clk or posedge rst)
        if (rst)
            cnt_1 <= '0;
        else if (cnt_1 == '0)
            cnt_1 <= period - 1'b1;
        else
            cnt_1 <= cnt_1 - 1'd1;

    logic [31:0] cnt_2;

    always_ff @ (posedge clk or posedge rst)
        if (rst)
            cnt_2 <= '0;
        else if (cnt_1 == '0)
            cnt_2 <= cnt_2 + 1'd1;

    assign led = cnt_2;

    //------------------------------------------------------------------------

    // 4 bits per hexadecimal digit
    localparam w_display_number = w_digit * 4;

    seven_segment_display # (w_digit) i_7segment
    (
        .clk      ( clk                       ),
        .rst      ( rst                       ),
        .number   ( (cnt_2) ),
        .dots     ( (0)              ),
        .abcdefgh ( abcdefgh                  ),
        .digit    ( digit                     )
    );

    //------------------------------------------------------------------------

    // Exercise 2: Change the example above to:
    //
    // 1. Double the frequency when one btn is pressed and released.
    // 2. Halve the frequency when another btn is pressed and released.
endmodule

