// =============================================================================
// 4_4_2_round_robin_arbiter_from_2_fifos_wrapped_in_valid_ready — auto-adapted by tools/adapt_designs.py from
//   basics-graphics-music/labs/4_microarchitecture/4_4_2_round_robin_arbiter_from_2_fifos_wrapped_in_valid_ready/lab_top.sv
// =============================================================================
//
// requires:
//   buttons >= 3
//   switches >= 1
//   seven_segment >= 4

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

    localparam width = 4, depth = 4;

    wire               a_valid;
    wire               a_ready;
    wire [width - 1:0] a_data;

    wire               b_valid;
    wire               b_ready;
    wire [width - 1:0] b_data;

    wire               out_valid;
    wire               out_ready;
    wire [width - 1:0] out_data;

    //------------------------------------------------------------------------

    generate
        if (w_btn >= 3)
        begin : three_keys
            // For example Saulinx board

            assign a_valid   =   btn [2];
            assign b_valid   =   btn [1];
            assign out_ready = ~ btn [0];  // is not pressed - ready is ON by default
        end
        else if (w_btn >= 2 && w_sw > 0)
        begin : two_keys_and_switch
            // For example DE0-Lite board

            assign a_valid   =   btn [0];  // Top btn is pressed
            assign b_valid   =   btn [1];  // Bottom btn is pressed
            assign out_ready = ~ sw  [0];  // Switch is not ON
        end
        else
        begin : single_key
            assign a_valid   =   btn [0];
            assign b_valid   =   btn [0];
            assign out_ready = ~ btn [0];
        end
    endgenerate

    //------------------------------------------------------------------------

    wire [width - 2:0] a_data_pre;

    counter_with_enable # (width - 1) i_a_counter
    (
        .clk    (slow_clk),
        .enable (a_valid & a_ready),
        .cnt    (a_data_pre),
        .*
    );

    assign a_data = { 1'b0, a_data_pre };  // 0, 1, 2, ... 7

    //------------------------------------------------------------------------

    wire [width - 2:0] b_data_pre;

    counter_with_enable # (width - 1) i_b_counter
    (
        .clk    (slow_clk),
        .enable (b_valid & b_ready),
        .cnt    (b_data_pre),
        .*
    );

    assign b_data = { 1'b1, b_data_pre };  // 8, 9, a, ... f

    //------------------------------------------------------------------------

    round_robin_arbiter_from_2_fifos_wrapped_in_valid_ready
    # (.width (width), .depth (depth))
    i_rra_from_fifos (.clk (slow_clk), .*);

    //------------------------------------------------------------------------

    localparam w_number = w_digit * 4;

    wire [7:0] abcdefgh_pre;

    seven_segment_display # (w_digit) i_display
    (
        .clk      (clk),
        .number   (({ a_data, b_data, 4'd0, out_data })),
        .dots     ('0),
        .abcdefgh (abcdefgh_pre),
        .digit    (digit),
        .*
    );

    //------------------------------------------------------------------------

    localparam sign_ready_a   = 8'b10000000,
               sign_ready_b   = 8'b00000010,
               sign_ready_out = 8'b00010000,
               sign_nothing   = 8'b00000000;

    always_comb
        case (digit [3:0])
        4'b0001: abcdefgh = out_valid ? abcdefgh_pre : sign_nothing;

        4'b0010:
        begin
            abcdefgh = sign_nothing;

            if ( a_ready   ) abcdefgh |= sign_ready_a;
            if ( b_ready   ) abcdefgh |= sign_ready_b;
            if ( out_ready ) abcdefgh |= sign_ready_out;
        end

        4'b0100,
        4'b1000: abcdefgh = abcdefgh_pre;

        default: abcdefgh = sign_nothing;
        endcase
endmodule

