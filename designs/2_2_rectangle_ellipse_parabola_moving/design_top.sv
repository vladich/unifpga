// =============================================================================
// 2_2_rectangle_ellipse_parabola_moving
// =============================================================================
//
// requires:
//   buttons >= 2
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


    //------------------------------------------------------------------------

       assign led        = '0;
       assign abcdefgh   = '0;
       assign digit      = '0;
    // assign red        = '0;
    // assign green      = '0;
    // assign blue       = '0;
       assign sound      = '0;
       assign uart_tx    = '1;
       assign rgb_r      = '0;
       assign rgb_g      = '0;
       assign rgb_b      = '0;

    //------------------------------------------------------------------------

    wire enable;

    // Generate a strobe signal 10 times a second

    strobe_gen
    # (.clk_mhz (clk_mhz), .strobe_hz (30))
    i_strobe_gen
    (.strobe (enable), .*);

    //------------------------------------------------------------------------

    wire inv_key_0 = ~ btn [0];
    wire inv_key_1 = ~ btn [1];

    logic [7:0] dx, dy;

    always_ff @ (posedge clk)
        if (rst)
        begin
            dx <= 4'b0;
            dy <= 4'b0;
        end
        else if (enable)
        begin
            dx <= dx + inv_key_0;
            dy <= dy + inv_key_1;
        end

    //------------------------------------------------------------------------

    wire [w_x * 2 - 1:0] x_2 = x * x;

    // These additional wires are needed
    // because some graphics interfaces have up to 10 bits per color channel

    wire [10:0] x11 = 11' (x);
    wire [ 9:0] y10 = 10' (y);

    always_comb
    begin
        red   = '0;
        green = '0;
        blue  = '0;

        if (   x + dx >= screen_width  / 2
             & x + dx <  screen_width  * 2 / 3
             & y      >= screen_height / 2
             & y      <  screen_height * 2 / 3 )
        begin
            if (btn [0])
                green = '1;
            else
                green = x11 [$left (x11) - 1 -: w_green];
        end

        `ifdef YOSYS
        if (x * x  + 2 * y * y  < (screen_width + dx) * (screen_width + dy) / 4)  // Ellipse
        `else
        if (x ** 2 + 2 * y ** 2 < (screen_width / 2 + dx) ** 2)  // Ellipse
        `endif
        begin
            red = x11 [$left (x11) - 1 -: w_red];
        end

        if (x_2 [w_x +: w_y] < y + dy)  // Parabola
            blue = btn [1] ? '1 : y10 [$left (y10) -: w_blue];
    end
endmodule

