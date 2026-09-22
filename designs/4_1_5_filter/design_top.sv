// =============================================================================
// 4_1_5_filter — auto-adapted by tools/adapt_designs.py from
//   basics-graphics-music/labs/4_microarchitecture/4_1_5_filter/lab_top.sv
// =============================================================================
//
// requires:
//   seven_segment >= 1

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

       assign led        = '0;
    // assign abcdefgh   = '0;
    // assign digit      = '0;
       assign red        = '0;
       assign green      = '0;
       assign blue       = '0;
       assign sound      = '0;
       assign uart_tx    = '1;

    //------------------------------------------------------------------------

    // Truncate used SW number to 8
    localparam w_sw_actual = (w_sw > 8) ? 8
                                        : w_sw;

    //------------------------------------------------------------------------


    logic [w_sw_actual-1:0] input_stage_0_ff;
    logic [w_sw_actual-1:0] input_stage_1_ff;
    logic [w_sw_actual-1:0] input_stage_2_ff;
    logic [w_sw_actual-1:0] input_stage_3_ff;

    logic [w_sw_actual+1:0] register_summ;
    logic [w_sw_actual-1:0] filter_output;


    // Data pipeline
    always_ff @ (posedge slow_clk or posedge rst)
        if (rst) begin
            input_stage_0_ff <= '0;
            input_stage_1_ff <= '0;
            input_stage_2_ff <= '0;
            input_stage_3_ff <= '0;
        end
        else begin
            input_stage_0_ff <= sw;
            input_stage_1_ff <= input_stage_0_ff;
            input_stage_2_ff <= input_stage_1_ff;
            input_stage_3_ff <= input_stage_2_ff;
        end


    assign register_summ = input_stage_0_ff
                         + input_stage_1_ff
                         + input_stage_2_ff
                         + input_stage_3_ff;

    assign filter_output = register_summ >> 2;


    localparam w_display_number = w_digit * 4;

    seven_segment_display # (w_digit) i_7segment
    (
        .clk      ( clk                               ),
        .rst      ( rst                               ),
        .number   ( (filter_output) ),
        .dots     ( (0)                      ),
        .abcdefgh ( abcdefgh                          ),
        .digit    ( digit                             )
    );
endmodule

