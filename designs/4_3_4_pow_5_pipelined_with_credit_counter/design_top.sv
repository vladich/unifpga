// =============================================================================
// 4_3_4_pow_5_pipelined_with_credit_counter
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

    //------------------------------------------------------------------------

    localparam width = 12;

    // Upstream

    wire               up_vld   = btn [1];
    wire               up_rdy;
    wire [width - 1:0] up_data;

    // Downstream

    wire               down_vld;
    wire               down_rdy = btn [0];
    wire [width - 1:0] down_data;

    //--------------------------------------------------------------------------

    localparam max_cnt   = 5,
               cnt_width = $clog2 (max_cnt);

    logic [cnt_width - 1:0] cnt;

    always_ff @ (posedge slow_clk or posedge rst)
        if (rst)
            cnt <= '0;
        else if (up_vld & up_rdy)
            cnt <= (cnt == max_cnt ? '0 : cnt + 1'd1);

    assign up_data = width' (cnt);

    //--------------------------------------------------------------------------

    pow_5_pipelined_with_credit_counter
    # (.width (width))
    pow_5
    (
        .clk        ( slow_clk  ),
        .rst        ( rst       ),

        .up_vld     ( up_vld    ),
        .up_rdy     ( up_rdy    ),
        .up_data    ( up_data   ),

        .down_vld   ( down_vld  ),
        .down_rdy   ( down_rdy  ),
        .down_data  ( down_data )
    );

    //--------------------------------------------------------------------------

    wire [7:0] abcdefgh_pre;

    seven_segment_display # (w_digit) i_display
    (
        .clk      (clk),
        .number   ({ up_data [3:0], down_data [11:0] }),
        .dots     ('0),
        .abcdefgh (abcdefgh_pre),
        .digit    (digit),
        .*
    );

    localparam sign_nothing = 8'b00000000;

    assign abcdefgh =
          ( digit [3]   != 1'b0    & ~ up_rdy   )
        | ( digit [2:0] !=  3'b000 & ~ down_vld )
        |   digit [3:0] == 4'b0000
      ? sign_nothing : abcdefgh_pre;

    assign led = ({ up_vld, up_rdy, down_vld, down_rdy });
endmodule


`endif
