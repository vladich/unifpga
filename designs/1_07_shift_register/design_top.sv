// =============================================================================
// 1_07_shift_register
// =============================================================================
//
// requires:
//   leds >= 2

module design_top
`include "design_top_interface.svh"


    //------------------------------------------------------------------------

    // assign led        = '0;
       assign abcdefgh   = '0;
       assign digit      = '0;
       assign red        = '0;
       assign green      = '0;
       assign blue       = '0;
       assign sound      = '0;
       assign uart_tx    = '1;
       assign rgb_r      = '0;
       assign rgb_g      = '0;
       assign rgb_b      = '0;

    //------------------------------------------------------------------------

    logic [31:0] cnt;

    always_ff @ (posedge clk or posedge rst)
        if (rst)
            cnt <= '0;
        else
            cnt <= cnt + 1'd1;

    wire enable = (cnt [22:0] == '0);

    //------------------------------------------------------------------------

    wire button_on = | btn;

    logic [w_led - 1:0] shift_reg;

    always_ff @ (posedge clk or posedge rst)
        if (rst)
            shift_reg <= '1;
        else if (enable)
            shift_reg <= { button_on, shift_reg [w_led - 1:1] };

    assign led = shift_reg;

    // Exercise 1: Make the light move in the opposite direction.

    // Exercise 2: Make the light moving in a loop.
    // Use another btn to reset the moving lights back to no lights.

    // Exercise 3: Display the state of the shift register
    // on a seven-segment display, moving the light in a circle.
endmodule

