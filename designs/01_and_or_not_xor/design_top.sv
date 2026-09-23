// =============================================================================
// Lab 01 — AND, OR, NOT, XOR, De Morgan's laws
//
// Differences from the original lab:
//   - `key`     -> `btn`     (the canonical name in uni-fpga's virtual device)
//   - dropped `slow_clk`     (derive locally if needed)
//   - dropped `mic`          (renamed to `mic_sample`/`mic_valid`; not used here)
//   - added a `// requires:` block so synthesize.py rejects boards that
//     don't have at least 2 buttons + 2 LEDs.
// =============================================================================
//
// requires:
//   buttons >= 2
//   leds    >= 2

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

    // ---- Tie off everything we don't drive --------------------------------
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

    // ---- The design itself ---------------------------------------------------
    wire a = btn[0];
    wire b = btn[1];

    assign led[0] = a ^ b;
    assign led[1] = btn[0] ^ btn[1];

    generate
        if (w_led > 2) begin : tie_off_led
            assign led[w_led-1:2] = '0;
        end
    endgenerate

    // Exercise 1: assign led[2] to a & b (AND).
    // Exercise 2: assign led[3] to a XOR b without using "^".
    // Exercise 3: illustrate De Morgan: ~(a & b) == ~a | ~b.

endmodule
