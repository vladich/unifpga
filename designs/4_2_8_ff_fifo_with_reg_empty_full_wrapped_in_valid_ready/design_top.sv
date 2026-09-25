// =============================================================================
// 4_2_8_ff_fifo_with_reg_empty_full_wrapped_in_valid_ready
// =============================================================================
//
// requires:
//   buttons >= 2
//   seven_segment >= 4

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

    // Upstream

    wire               up_valid   = btn [1];
    wire               up_ready;
    wire [width - 1:0] up_data;

    // Downstream

    wire               down_valid;
    wire               down_ready = btn [0];
    wire [width - 1:0] down_data;

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
        .enable (up_valid & up_ready),
        .cnt    (up_data_index),
        .*
    );

    assign up_data = up_data_const_array [up_data_index];

    //------------------------------------------------------------------------

    ff_fifo_wrapped_in_valid_ready
    # (.width (width), .depth (depth))
    wrapped_fifo (.clk (slow_clk), .*);

    //------------------------------------------------------------------------

    localparam w_number = w_digit * 4;

    wire [7:0] abcdefgh_pre;

    seven_segment_display # (w_digit) i_display
    (
        .clk      (clk),
        .number   (({ up_data, 4'd0, 4'd0, down_data })),
        .dots     ('0),
        .abcdefgh (abcdefgh_pre),
        .digit    (digit),
        .*
    );

    //------------------------------------------------------------------------

    localparam sign_valid   = 8'b10000000,
               sign_ready   = 8'b00000010,
               sign_nothing = 8'b00000000;

    function [7:0] valid_ready_to_abcdefgh (logic valid, ready);

        case ({ valid, ready })
        2'b00: valid_ready_to_abcdefgh = sign_nothing;
        2'b10: valid_ready_to_abcdefgh = sign_valid;
        2'b01: valid_ready_to_abcdefgh = sign_ready;
        2'b11: valid_ready_to_abcdefgh = sign_valid | sign_ready;
        endcase

    endfunction

    //------------------------------------------------------------------------

    always_comb
        case (digit [3:0])
        4'b0100:  abcdefgh = valid_ready_to_abcdefgh ( up_valid   , up_ready   );
        4'b0010:  abcdefgh = valid_ready_to_abcdefgh ( down_valid , down_ready );
        4'b0001:  abcdefgh = down_valid ? abcdefgh_pre : sign_nothing;
        4'b1000:  abcdefgh = abcdefgh_pre;
        default:  abcdefgh = sign_nothing;
        endcase
endmodule


`endif
