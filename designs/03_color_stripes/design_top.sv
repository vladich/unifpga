// =============================================================================
// Lab 03 — Color stripes (graphics)
//
// Draws vertical stripes of cycling RGB colours. Demonstrates the screen
// capability: the design receives raster (x, y) from the screen provider and
// computes the pixel colour combinationally.
// =============================================================================
//
// requires:
//   screen >= 320x240

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
    assign abcdefgh = '0;
    assign digit    = '0;
    assign rgb_r    = '0;
    assign rgb_g    = '0;
    assign rgb_b    = '0;
    assign sound    = '0;
    assign uart_tx  = 1'b1;

    // Use the upper coordinate bits to select stripe, leaving smooth gradients
    // within each stripe.
    wire [3:0] stripe = x[w_x - 1 -: 4];

    always_comb begin
        red   = '0;
        green = '0;
        blue  = '0;
        unique case (stripe[2:0])
            3'd0: red   = '1;                       // red
            3'd1: green = '1;                       // green
            3'd2: blue  = '1;                       // blue
            3'd3: begin red = '1; green = '1; end   // yellow
            3'd4: begin green = '1; blue = '1; end  // cyan
            3'd5: begin red = '1; blue = '1; end    // magenta
            3'd6: begin red = '1; green = '1; blue = '1; end  // white
            default: ;                              // black
        endcase
    end

endmodule
