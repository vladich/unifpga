// =============================================================================
// 4_1_7_syst_array_ws
// =============================================================================
//

`timescale 1ns / 1ps

module design_top
`include "design_top_interface.svh"

    // no RGB LEDs used
    assign rgb_r = '0;
    assign rgb_g = '0;
    assign rgb_b = '0;


    assign abcdefgh = '0;
    assign digit    = '0;
    assign red      = '0;
    assign green    = '0;
    assign blue     = '0;
    assign sound    = '0;
    assign uart_tx  = 1'b1;

    // The array's inputs from the switches (the same byte on all three), its
    // first column's sum on the LEDs (extended or cut to the rig's LEDs)

    wire  [7:0] x_in = sw;
    wire [18:0] y1, y2;

    syst_array_ws i_array (.clk (clk), .rst (rst), .x1 (x_in), .x2 (x_in), .x3 (x_in), .y1 (y1), .y2 (y2));

    assign led = y1;

endmodule

