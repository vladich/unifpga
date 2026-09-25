// =============================================================================
// 4_2_6_fifo_view_3_interface_only
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

    localparam fifo_width = 4, fifo_depth = 5;

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

    wire [31:0] debug;

    //------------------------------------------------------------------------


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

    flip_flop_fifo_with_counter
    # (
        .width (fifo_width),
        .depth (fifo_depth)
    )
    i_fifo (.clk (slow_clk), .*);

    wire [3:0] wr_ptr = debug [19:16];
    wire [3:0] rd_ptr = debug [ 3: 0];

    //------------------------------------------------------------------------

    localparam read_data_digit  = 0,
               write_data_digit = w_digit >= 4 ? 3 : 1,
               digit_mask       = ~ (~ 0 << (write_data_digit + 1));

    localparam w_number = w_digit * 4;

    wire [w_number - 1:0] number
        = w_digit >= 4 ?
              ({ write_data , wr_ptr , rd_ptr , read_data })  // 4-digit
            : ({ write_data , read_data });           // 2-digit display

    wire [7:0] abcdefgh_pre;

    seven_segment_display # (w_digit) i_display
    (
        .clk      (clk),
        .number   (number),
        .dots     ('0),
        .abcdefgh (abcdefgh_pre),
        .digit    (digit),
        .*
    );

    localparam sign_full    = 8'b1000_0000,
               sign_empty   = 8'b0001_0000,
               sign_nothing = 8'b0000_0000;

    always_comb
        if (digit [read_data_digit] & empty)
        begin
            abcdefgh = sign_empty;
        end
        else if (digit [write_data_digit] & full)
            abcdefgh = sign_full;
        else if (digit & digit_mask)
            abcdefgh = abcdefgh_pre;
        else
            abcdefgh = sign_nothing;
endmodule


`endif
