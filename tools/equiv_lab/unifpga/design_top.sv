// Equivalence-check lab in unifpga's design_top dialect; the function lives
// in ../equiv_lab_body.svh (see tools/equiv_check.py). Same shape as the
// designs tools/adapt_designs.py produces from BGM's labs.

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

    localparam eq_w_key = w_btn;
    wire [w_btn - 1:0] eq_key = btn;
    wire [       23:0] eq_mic = mic_sample;

    `include "equiv_lab_body.svh"

    // RGB LEDs exist only on this side (BGM's lab_top has no such port).
    wire [w_rgb_led - 1:0] rgb_r_w, rgb_g_w, rgb_b_w;
    generate
        for (i = 0; i < w_rgb_led; i++) begin : g_rgb
            assign rgb_r_w [i] = all_in [(i * 29 + 8) % W_IN] ^ mic_valid ^ cnt [(i + 1) % 32];
            assign rgb_g_w [i] = all_in [(i * 31 + 9) % W_IN] ^ cnt [(i + 2) % 32];
            assign rgb_b_w [i] = all_in [(i * 37 + 10) % W_IN] ^ cnt [(i + 3) % 32];
        end
    endgenerate
    assign rgb_r = rgb_r_w;
    assign rgb_g = rgb_g_w;
    assign rgb_b = rgb_b_w;

endmodule
