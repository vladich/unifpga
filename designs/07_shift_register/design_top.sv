// =============================================================================
// Lab 07 — Shift register (light moving across the LED bar)
//
// A button-controlled bit shifts through the LED bus, refreshed at ~6 Hz.
// =============================================================================
//
// requires:
//   leds    >= 4
//   buttons >= 1

module design_top
`include "design_top_interface.svh"

    assign abcdefgh = '0;
    assign digit    = '0;
    assign rgb_r    = '0;
    assign rgb_g    = '0;
    assign rgb_b    = '0;
    assign red      = '0;
    assign green    = '0;
    assign blue     = '0;
    assign sound    = '0;
    assign uart_tx  = 1'b1;

    logic [w_cnt - 1 : 0] cnt;

    always_ff @(posedge clk or posedge rst)
        if (rst) cnt <= '0;
        else     cnt <= cnt + 1'b1;

    wire enable = (cnt == '0);

    wire button_on = | btn;

    logic [w_led - 1 : 0] shift_reg;

    always_ff @(posedge clk or posedge rst)
        if (rst)            shift_reg <= '1;
        else if (enable)    shift_reg <= { button_on, shift_reg[w_led - 1 : 1] };

    assign led = shift_reg;

endmodule
