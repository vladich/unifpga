// =============================================================================
// Lab 05 — Seven-segment display: a single letter
//
// Displays "F" (when btn[0] is released) or "P" (when pressed) on the first
// digit position of a multi-digit shared-segment 7-segment display.
//
// The board's seven-segment hardware is a shared segment bus + per-digit anode
// select. The user drives `abcdefgh` and `digit` (one-hot); the codegen wires
// these to the chosen physical display.
// =============================================================================
//
// requires:
//   buttons >= 2
//   seven_segment >= 2

module design_top
`include "design_top_interface.svh"

    assign led      = '0;
    assign rgb_r    = '0;
    assign rgb_g    = '0;
    assign rgb_b    = '0;
    assign red      = '0;
    assign green    = '0;
    assign blue     = '0;
    assign sound    = '0;
    assign uart_tx  = 1'b1;

    // 7-segment encoding: bit 0..6 = a..g, bit 7 = decimal point.
    //
    //   --a--
    //  |     |
    //  f     b
    //  |     |
    //   --g--
    //  |     |
    //  e     c
    //  |     |
    //   --d--   .h

    localparam logic [7:0] LETTER_F = 8'b1000_1110;  // a, e, f, g
    localparam logic [7:0] LETTER_P = 8'b1100_1110;  // a, b, e, f, g
    localparam logic [7:0] BLANK    = 8'b0000_0000;

    assign abcdefgh = btn[0] ? LETTER_P : LETTER_F;
    assign digit    = (btn[1] ? 'b10 : 'b01);

endmodule
