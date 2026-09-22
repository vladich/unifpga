// =============================================================================
// 3_11_microphone_monitor — auto-adapted by tools/adapt_designs.py from
//   basics-graphics-music/labs/3_music/3_11_microphone_monitor/lab_top.sv
// =============================================================================
//
// requires:
//   leds >= 2
//   seven_segment >= 1
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

    // assign led        = '0;
    // assign abcdefgh   = '0;
    // assign digit      = '0;
    // assign red        = '0;
    // assign green      = '0;
    // assign blue       = '0;
    // assign sound      = '0;
       assign uart_tx    = '1;

    //------------------------------------------------------------------------

    logic signed [10:0] mic_11bit;
    wire         [ 7:0] vol;

    // Spectrum analyzer level adjust
    convert
    # (
        .w_in  ( 24        ),
        .w_out ( 11        ),
        .lev   ( 17        ),
        .agc   ( 1         )
    )
    i_convert
    (
        .clk   ( clk       ),
        .rst   ( rst       ),
        .in    ( mic_sample       ),
        .out   ( mic_11bit ),
        .led   ( led[0]    ),
        .vol   ( vol[2:0]  )
    );

    // Sound output level adjust
    convert
    # (
        .w_in  ( 24        ),
        .w_out ( 16        ),
        .lev   ( 17        ),
        .agc   ( 1         )
    )
    i_convert_line
    (
        .clk   ( clk       ),
        .rst   ( rst       ),
        .in    ( mic_sample       ),
        .out   ( sound     ),
        .led   ( led[1]    ),
        .vol   ( vol[6:4]  )
    );

    seven_segment_display i_7segment
    (
        .clk      ( clk       ),
        .rst      ( rst       ),
        .number   ( 8' (vol)  ),
        .dots     ( 2'b0      ),
        .abcdefgh ( abcdefgh  ),
        .digit    ( digit     )
    );

    //------------------------------------------------------------------------

    spectrum
    # (
        .clk_mhz       ( clk_mhz       ),

        .screen_width  ( screen_width  ),
        .screen_height ( screen_height ),
        .w_red         ( w_red         ),
        .w_green       ( w_green       ),
        .w_blue        ( w_blue        ),

    // Frequency bands of the spectrum analyzer
        .freq          ({14'd525, 14'd458, 14'd400, 14'd348,
                         14'd303, 14'd264, 14'd230, 14'd200})
    )
    i_spectrum
    (
        .clk           ( clk           ),
        .rst           ( rst           ),
        .x             ( x             ),
        .y             ( y             ),
        .red           ( red           ),
        .green         ( green         ),
        .blue          ( blue          ),
        .mic_sample           ( mic_11bit     )
    );
endmodule

