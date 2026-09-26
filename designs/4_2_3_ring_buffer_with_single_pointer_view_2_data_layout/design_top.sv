// =============================================================================
// 4_2_3_ring_buffer_with_single_pointer_view_2_data_layout
// =============================================================================
//
// requires:
//   seven_segment >= 1

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

    localparam width = 4, depth = w_digit;

    wire               in_valid = | btn;  // Any btn is pressed
    wire [width - 1:0] in_data;

    wire               out_valid;
    wire [width - 1:0] out_data;

    //------------------------------------------------------------------------

    wire [31:0] debug_ptr;

    wire [$clog2 (depth) - 1:0] rw_ptr
        = debug_ptr [ 0 +: $clog2 (depth)];

    wire [w_digit - 1:0] dots_from_ptr
        =   ((1) << (depth - 1 - rw_ptr));

    wire [depth - 1:0]              debug_valid;
    wire [depth - 1:0][width - 1:0] debug_data;

    wire [depth - 1:0]              debug_valid_mirrored;
    wire [depth - 1:0][width - 1:0] debug_data_mirrored;

    generate
        genvar i;

        for (i = 0; i < depth; i++)
        begin : gen
            assign debug_valid_mirrored [i] = debug_valid [depth - 1 - i];
            assign debug_data_mirrored  [i] = debug_data  [depth - 1 - i];
        end

    endgenerate

    //------------------------------------------------------------------------


    logic [width - 1:0] in_data_const_array [0:2 ** width - 1];

    assign in_data_const_array [ 0] = 4'h2;
    assign in_data_const_array [ 1] = 4'h6;
    assign in_data_const_array [ 2] = 4'hd;
    assign in_data_const_array [ 3] = 4'hb;
    assign in_data_const_array [ 4] = 4'h7;
    assign in_data_const_array [ 5] = 4'he;
    assign in_data_const_array [ 6] = 4'hc;
    assign in_data_const_array [ 7] = 4'h4;
    assign in_data_const_array [ 8] = 4'h1;
    assign in_data_const_array [ 9] = 4'h0;
    assign in_data_const_array [10] = 4'h9;
    assign in_data_const_array [11] = 4'ha;
    assign in_data_const_array [12] = 4'hf;
    assign in_data_const_array [13] = 4'h5;
    assign in_data_const_array [14] = 4'h8;
    assign in_data_const_array [15] = 4'h3;


    //------------------------------------------------------------------------

    wire [width - 1:0] in_data_index;

    counter_with_enable # (width) i_counter
    (
        .clk    (slow_clk),
        .enable (in_valid),
        .cnt    (in_data_index),
        .*
    );

    assign in_data = in_data_const_array [in_data_index];

    //------------------------------------------------------------------------

    ring_buffer_with_single_pointer_and_debug_2
    # (
        .width (width),
        .depth (depth)
    )
    i_ring_buffer (.clk (slow_clk), .*);

    //------------------------------------------------------------------------

    wire [7:0] abcdefgh_pre;

    seven_segment_display # (w_digit) i_display
    (
        .clk      (clk),
        .number   (debug_data_mirrored),
        .dots     (dots_from_ptr),
        .abcdefgh (abcdefgh_pre),
        .digit    (digit),
        .*
    );

    //------------------------------------------------------------------------

    localparam sign_empty_entry = 8'b00000000;

    always_comb
        if ((digit & debug_valid_mirrored) == '0)
            abcdefgh = sign_empty_entry | abcdefgh_pre [0];
        else
            abcdefgh = abcdefgh_pre;
endmodule


`endif
