// =============================================================================
// 4_2_11_gearbox_2_to_1
// =============================================================================
//
// requires:
//   buttons >= 2
//   seven_segment >= 2

`ifndef SIMULATION

module design_top
`include "design_top_interface.svh"

    // ---- slow_clk derivation --------------------------------------------------
    // design_top has no `slow_clk` port, so derive a ~1 Hz tick from
    // the system clock.
    // clk_mhz <= 1 is the testbench setting (tb.sv passes clk as slow_clk):
    // a two-bit divider keeps the simulation short.
    localparam int W_SLOW_CLK_DIV = (clk_mhz > 1) ? $clog2(clk_mhz * 1_000_000) : 2;
    logic [W_SLOW_CLK_DIV - 1 : 0] slow_clk_div;
    logic                          slow_clk;
    always_ff @(posedge clk or posedge rst)
        if (rst) slow_clk_div <= '0;
        else     slow_clk_div <= slow_clk_div + 1'b1;
    assign slow_clk = slow_clk_div[W_SLOW_CLK_DIV - 1];



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

    localparam width = 4;

    // Upstream

    wire                   up_vld = btn [1];
    wire                   up_rdy;
    wire [2 * width - 1:0] up_data;
    // Downstream

    wire                   down_vld;
    wire                   down_rdy = ~ btn [0]; // Ready is ON by default
    wire [    width - 1:0] down_data;

    //------------------------------------------------------------------------


    logic [width - 1:0] up_data_const_array [0:2 ** width - 1];

    assign up_data_const_array [ 0] = 4'h2;
    assign up_data_const_array [ 1] = 4'h6;
    assign up_data_const_array [ 2] = 4'hd;
    assign up_data_const_array [ 3] = 4'hb;
    assign up_data_const_array [ 4] = 4'h7;
    assign up_data_const_array [ 5] = 4'he;
    assign up_data_const_array [ 6] = 4'hc;
    assign up_data_const_array [ 7] = 4'h4;
    assign up_data_const_array [ 8] = 4'h1;
    assign up_data_const_array [ 9] = 4'h0;
    assign up_data_const_array [10] = 4'h9;
    assign up_data_const_array [11] = 4'ha;
    assign up_data_const_array [12] = 4'hf;
    assign up_data_const_array [13] = 4'h5;
    assign up_data_const_array [14] = 4'h8;
    assign up_data_const_array [15] = 4'h3;


    //------------------------------------------------------------------------

    wire [width - 1:0] up_data_index;

    counter_with_enable # (width) i_counter
    (
        .clk    (slow_clk),
        .enable (up_vld & up_rdy),
        .cnt    (up_data_index),
        .*
    );

    assign up_data
        = { up_data_const_array [up_data_index    ],
            up_data_const_array [up_data_index ^ 2]  };

    //------------------------------------------------------------------------

    gearbox_2_to_1 # ( .width (width) )
    i_gearbox21 ( .clk (slow_clk), .* );

    //------------------------------------------------------------------------

    wire [          7:0] abcdefgh_pre;
    wire [w_digit - 1:0] digit_pre;

    seven_segment_display # (w_digit) i_display
    (
        .clk      (clk),
        .number   ({ up_data, 4'b0, down_data }),
        .dots     ({ { 2 { up_vld } }, 2'b0 }),
        .abcdefgh (abcdefgh_pre),
        .digit    (digit_pre),
        .*
    );

    //------------------------------------------------------------------------

    localparam sign_nothing = 8'b0000_0000;

    always_comb
        if (    digit [1]
            | ( digit [0] & ~ down_vld ))
            abcdefgh = sign_nothing;
        else
            abcdefgh = abcdefgh_pre;

    assign digit = (digit_pre & 4'b1111);

    assign led = ({ slow_clk, up_vld, up_rdy, down_vld, down_rdy });
endmodule


`endif
