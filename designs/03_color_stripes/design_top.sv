// =============================================================================
// Lab 03 — Color stripes (graphics)
//
// Draws vertical stripes of cycling RGB colours. Demonstrates the screen
// capability: the design receives raster (x, y) from the screen provider and
// computes the pixel colour combinationally.
// =============================================================================
//
// requires:
//   screen >= 320x240

module design_top
`include "design_top_interface.svh"

    assign led      = '0;
    assign abcdefgh = '0;
    assign digit    = '0;
    assign rgb_r    = '0;
    assign rgb_g    = '0;
    assign rgb_b    = '0;
    assign sound    = '0;
    assign uart_tx  = 1'b1;

    // Use the upper coordinate bits to select stripe, leaving smooth gradients
    // within each stripe.
    wire [3:0] stripe = x[w_x - 1 -: 4];

    always_comb begin
        red   = '0;
        green = '0;
        blue  = '0;
        unique case (stripe[2:0])
            3'd0: red   = '1;                       // red
            3'd1: green = '1;                       // green
            3'd2: blue  = '1;                       // blue
            3'd3: begin red = '1; green = '1; end   // yellow
            3'd4: begin green = '1; blue = '1; end  // cyan
            3'd5: begin red = '1; blue = '1; end    // magenta
            3'd6: begin red = '1; green = '1; blue = '1; end  // white
            default: ;                              // black
        endcase
    end

endmodule
