// =============================================================================
// 4_2_4_flip_flop_fifo_with_counter_view_1_functional — auto-adapted by tools/adapt_designs.py from
//   basics-graphics-music/labs/4_microarchitecture/4_2_4_flip_flop_fifo_with_counter_view_1_functional/lab_top.sv
// =============================================================================
//
// requires:
//   buttons >= 2
//   seven_segment >= 1

`ifndef SIMULATION

module design_top
# (
    parameter int clk_mhz       = 50,
                  w_sw          = 0,
                  w_btn         = 0,
                  w_led         = 0,
                  w_digit       = 0,
                  w_rgb_led     = 0,
                  screen_width  = 0,
                  screen_height = 0,
                  w_red         = 0,
                  w_green       = 0,
                  w_blue        = 0,
                  w_gpio        = 0,
                  w_x = (screen_width  > 0) ? $clog2(screen_width ) : 1,
                  w_y = (screen_height > 0) ? $clog2(screen_height) : 1
)
(
    input                            clk,
    input                            rst,
    input        [w_sw     - 1 : 0]  sw,
    input        [w_btn    - 1 : 0]  btn,
    output logic [w_led    - 1 : 0]  led,
    output logic [          7 : 0]   abcdefgh,
    output logic [w_digit  - 1 : 0]  digit,
    output logic [w_rgb_led- 1 : 0]  rgb_r,
    output logic [w_rgb_led- 1 : 0]  rgb_g,
    output logic [w_rgb_led- 1 : 0]  rgb_b,
    input        [w_x      - 1 : 0]  x,
    input        [w_y      - 1 : 0]  y,
    output logic [w_red    - 1 : 0]  red,
    output logic [w_green  - 1 : 0]  green,
    output logic [w_blue   - 1 : 0]  blue,
    input        [         23 : 0]   mic_sample,
    input                            mic_valid,
    output logic [         15 : 0]   sound,
    input                            uart_rx,
    output logic                     uart_tx,
    inout        [w_gpio   - 1 : 0]  gpio
);

    // ---- slow_clk derivation (auto-inserted by adapt_designs.py) -------------
    // Original basics-graphics-music labs received `slow_clk` as a port. The
    // uni-fpga virtual device doesn't expose one, so derive a ~1 Hz tick from
    // the system clock.
    // clk_mhz <= 1 is BGM's testbench setting (tb.sv passes clk as slow_clk):
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

    //------------------------------------------------------------------------

    localparam fifo_width = 4, fifo_depth = w_digit;

    wire [fifo_width - 1:0] write_data;
    wire [fifo_width - 1:0] read_data;
    wire empty, full;

    wire push = ~ full  & btn [1];
    wire pop  = ~ empty & btn [0];

    // With this implementation of FIFO
    // we can actually push into a full FIFO
    // if we are performing pop in the same cycle.
    //
    // However we are not going to do this
    // because we assume that the logic that pushes
    // is separated from the logic that pops.
    //
    // wire push = (~ full | pop) & btn [1];

    wire [fifo_depth - 1:0]                   debug_valid;
    wire [fifo_depth - 1:0][fifo_width - 1:0] debug_data;

    //------------------------------------------------------------------------

    `ifdef __ICARUS__

        logic [fifo_width - 1:0] write_data_const_array [0:2 ** fifo_width - 1];

        assign write_data_const_array [ 0] = 4'h2;
        assign write_data_const_array [ 1] = 4'h6;
        assign write_data_const_array [ 2] = 4'hd;
        assign write_data_const_array [ 3] = 4'hb;
        assign write_data_const_array [ 4] = 4'h7;
        assign write_data_const_array [ 5] = 4'he;
        assign write_data_const_array [ 6] = 4'hc;
        assign write_data_const_array [ 7] = 4'h4;
        assign write_data_const_array [ 8] = 4'h1;
        assign write_data_const_array [ 9] = 4'h0;
        assign write_data_const_array [10] = 4'h9;
        assign write_data_const_array [11] = 4'ha;
        assign write_data_const_array [12] = 4'hf;
        assign write_data_const_array [13] = 4'h5;
        assign write_data_const_array [14] = 4'h8;
        assign write_data_const_array [15] = 4'h3;

    `else

        // New SystemVerilog syntax for array assignment

        wire [fifo_width - 1:0] write_data_const_array [0:2 ** fifo_width - 1]
            = '{ 4'h2, 4'h6, 4'hd, 4'hb, 4'h7, 4'he, 4'hc, 4'h4,
                 4'h1, 4'h0, 4'h9, 4'ha, 4'hf, 4'h5, 4'h8, 4'h3 };

    `endif

    //------------------------------------------------------------------------

    wire [fifo_width - 1:0] write_data_index;

    counter_with_enable # (fifo_width) i_counter
    (
        .clk    (slow_clk),
        .enable (push),
        .cnt    (write_data_index),
        .*
    );

    assign write_data = write_data_const_array [write_data_index];

    //------------------------------------------------------------------------

    flip_flop_fifo_with_counter_and_debug_1
    # (
        .width (fifo_width),
        .depth (fifo_depth)
    )
    i_fifo (.clk (slow_clk), .*);

    //------------------------------------------------------------------------

    wire [7:0] abcdefgh_pre;

    seven_segment_display # (w_digit) i_display
    (
        .clk      (clk),
        .number   (debug_data),
        .dots     ({ w_digit { full } }),
        .abcdefgh (abcdefgh_pre),
        .digit    (digit),
        .*
    );

    localparam sign_empty_head  = 8'b11110000,
               sign_empty_entry = 8'b10010000;

    always_comb
        if (digit == (1) & empty)
            abcdefgh = sign_empty_head;
        else if ((digit & debug_valid) == '0)
            abcdefgh = sign_empty_entry;
        else
            abcdefgh = abcdefgh_pre;
endmodule


`endif
