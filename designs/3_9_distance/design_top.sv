// =============================================================================
// 3_9_distance
// =============================================================================
//
// requires:
//   seven_segment >= 1
//   gpio >= 2

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

    // no RGB LEDs used
    assign rgb_r = '0;
    assign rgb_g = '0;
    assign rgb_b = '0;


    wire [7:0] distance;

    ultrasonic_distance_sensor
    # (
        .clk_frequency ( clk_mhz * 1000 * 1000 )
    )
    i_sensor
    (
        .clk,
        .rst,
        .trig              ( gpio [0] ),
        .echo              ( gpio [1] ),
        .relative_distance ( distance )
    );

    localparam w_display_number = w_digit * 4;

    seven_segment_display # (w_digit) i_7segment
    (
        .clk      ( clk                          ),
        .rst      ( rst                          ),
        .number   ( (distance) ),
        .dots     ( (0)                 ),
        .abcdefgh ( abcdefgh                     ),
        .digit    ( digit                        )
    );
endmodule

