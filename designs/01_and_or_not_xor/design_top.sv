// =============================================================================
// Lab 01 — AND, OR, NOT, XOR, De Morgan's laws
//
// Differences from the original lab:
//   - `key`     -> `btn`     (the canonical name in uni-fpga's virtual device)
//   - dropped `slow_clk`     (derive locally if needed)
//   - dropped `mic`          (renamed to `mic_sample`/`mic_valid`; not used here)
//   - added a `// requires:` block so synthesize.py rejects boards that
//     don't have at least 2 buttons + 2 LEDs.
// =============================================================================
//
// requires:
//   buttons >= 2
//   leds    >= 2

module design_top
`include "design_top_interface.svh"

    // ---- Tie off everything we don't drive --------------------------------
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

    // ---- The design itself ---------------------------------------------------
    wire a = btn[0];
    wire b = btn[1];

    assign led[0] = a ^ b;
    assign led[1] = btn[0] ^ btn[1];

    generate
        if (w_led > 2) begin : tie_off_led
            assign led[w_led-1:2] = '0;
        end
    endgenerate

    // Exercise 1: assign led[2] to a & b (AND).
    // Exercise 2: assign led[3] to a XOR b without using "^".
    // Exercise 3: illustrate De Morgan: ~(a & b) == ~a | ~b.

endmodule
