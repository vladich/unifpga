// =============================================================================
// 3_8_waveform_generator — auto-adapted by tools/adapt_designs.py from
//   basics-graphics-music/labs/3_music/3_8_waveform_generator/lab_top.sv
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
                  w_y = (screen_height > 0) ? $clog2(screen_height) : 1,
                  // legacy basics-graphics-music parameters (BGM's tb overrides them)
                  w_sound       = 16
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

    wire [        2:0] octave   = 3'b0;
    wire [w_btn - 1:0] waveform = btn;

    assign             led      = waveform;
    assign             digit    = (1);

    //------------------------------------------------------------------------

    //     Control:
    // Key 0 - Sine     wave
    // Key 1 - Triangle wave
    // Key 2 - Square   wave

    waveform_gen
    # (
        .clk_mhz        (clk_mhz       ),
        .waveform_width (w_btn         ),
        .y_width        (w_sound       )
    )
    i_waveform_gen
    (
        .clk            ( clk          ),
        .rst            ( rst          ),
        .octave         ( octave       ),
        .waveform       ( waveform     ),

        .y              ( sound        )
    );

    //------------------------------------------------------------------------

    oscilloscope
    # (
        .clk_mhz       ( clk_mhz       ),

        .screen_width  ( screen_width  ),
        .screen_height ( screen_height ),
        .w_red         ( w_red         ),
        .w_green       ( w_green       ),
        .w_blue        ( w_blue        )
    )
    i_oscilloscope
    (
        .clk           ( clk           ),
        .rst           ( rst           ),
        .x             ( x             ),
        .y             ( y             ),
        .red           ( red           ),
        .green         ( green         ),
        .blue          ( blue          ),

        .mic_sample           ( sound         )
    );

    //------------------------------------------------------------------------

    always_ff @ (posedge clk or posedge rst)
        if (rst)
            abcdefgh <= 'b00000000;
        else
            case (waveform)
            'd1:    abcdefgh <= 'b10110110;  // S   // abcdefgh
            'd2:    abcdefgh <= 'b00011110;  // T
            'd4:    abcdefgh <= 'b11100110;  // Q   //   --a--
                                                    //  |     |
                                                    //  f     b
                                                    //  |     |
                                                    //   --g--
                                                    //  |     |
                                                    //  e     c
                                                    //  |     |
            default: abcdefgh <= 'b00000000;        //   --d--  h
            endcase
endmodule

