// =============================================================================
// Lab 06 — Binary counter
//
// Drives the LED bus from a free-running counter clocked by `clk`. Useful for
// verifying the clock frequency announced by the configuration matches the
// board's actual oscillator.
// =============================================================================
//
// requires:
//   leds >= 4

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

    // Top w_led bits of the counter give a slow visible toggle on each LED.
    assign led = cnt[$left(cnt) -: w_led];

endmodule
