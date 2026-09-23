// =============================================================================
// Lab 05 — Seven-segment display: a single letter
//
// Displays "F" (when btn[0] is released) or "P" (when pressed) on the first
// digit position of a multi-digit shared-segment 7-segment display.
//
// The board's seven-segment hardware is a shared segment bus + per-digit anode
// select. The user drives `abcdefgh` and `digit` (one-hot); the codegen wires
// these to the chosen physical display.
// =============================================================================
//
// requires:
//   buttons >= 2
//   seven_segment >= 2

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

    assign led      = '0;
    assign rgb_r    = '0;
    assign rgb_g    = '0;
    assign rgb_b    = '0;
    assign red      = '0;
    assign green    = '0;
    assign blue     = '0;
    assign sound    = '0;
    assign uart_tx  = 1'b1;

    // 7-segment encoding: bit 0..6 = a..g, bit 7 = decimal point.
    //
    //   --a--
    //  |     |
    //  f     b
    //  |     |
    //   --g--
    //  |     |
    //  e     c
    //  |     |
    //   --d--   .h

    localparam logic [7:0] LETTER_F = 8'b1000_1110;  // a, e, f, g
    localparam logic [7:0] LETTER_P = 8'b1100_1110;  // a, b, e, f, g
    localparam logic [7:0] BLANK    = 8'b0000_0000;

    assign abcdefgh = btn[0] ? LETTER_P : LETTER_F;
    assign digit    = (btn[1] ? 'b10 : 'b01);

endmodule
