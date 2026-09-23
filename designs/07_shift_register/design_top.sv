// =============================================================================
// Lab 07 — Shift register (light moving across the LED bar)
//
// A button-controlled bit shifts through the LED bus, refreshed at ~6 Hz.
// =============================================================================
//
// requires:
//   leds    >= 4
//   buttons >= 1

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
                  w_y = (screen_height > 0) ? $clog2(screen_height) : 1,
                  // 23-bit divider yields ~6 Hz at 50 MHz; recompute for clk_mhz.
                  w_cnt = $clog2(clk_mhz * 100_000)
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

    assign abcdefgh = '0;
    assign digit    = '0;
    assign rgb_r    = '0;
    assign rgb_g    = '0;
    assign rgb_b    = '0;
    assign red      = '0;
    assign green    = '0;
    assign blue     = '0;
    assign sound    = '0;
    assign uart_tx  = 1'b1;

    logic [w_cnt - 1 : 0] cnt;

    always_ff @(posedge clk or posedge rst)
        if (rst) cnt <= '0;
        else     cnt <= cnt + 1'b1;

    wire enable = (cnt == '0);

    wire button_on = | btn;

    logic [w_led - 1 : 0] shift_reg;

    always_ff @(posedge clk or posedge rst)
        if (rst)            shift_reg <= '1;
        else if (enable)    shift_reg <= { button_on, shift_reg[w_led - 1 : 1] };

    assign led = shift_reg;

endmodule
