// =============================================================================
// 7_moving_rectangles_and_7seg_display
// =============================================================================
//
// requires:
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
       assign sound      = '0;
       assign uart_tx    = '1;
       assign rgb_r      = '0;
       assign rgb_g      = '0;
       assign rgb_b      = '0;

    //------------------------------------------------------------------------
    //
    //  NOTE! Since the 9_maker_faire series of Verilog examples
    //  is for absolute beginners,
    //  we are using a simplified, more relaxed Verilog style
    //  that may have many lint issues. For example,
    //  we use "100" instead of "(100)" or "screen_width / 2".
    //
    //------------------------------------------------------------------------

    logic pulse;

    strobe_gen
    # (
        .clk_mhz   ( clk_mhz ),
        .strobe_hz ( 30      )
    )
    i_strobe_gen (clk, rst, pulse);

    //------------------------------------------------------------------------

    logic [7:0] counter;

    always_ff @ (posedge clk or posedge rst)
        if (rst)
            counter <= 0;
        else if (pulse)
            counter <= counter + 1;

    assign led = counter;

    //------------------------------------------------------------------------

    always_comb
    begin
        red   = 0;
        green = 0;
        blue  = 0;

        if (  x > 100 + counter
            & x < 150 + counter
            & y > 100
            & y < 200 )
        begin
            red = 30;
        end

        if (  x > 100 + counter * 2
            & x < 150 + counter * 2
            & y > 100 + counter
            & y < 200 + counter )
        begin
            green = 30;
        end
    end

    //------------------------------------------------------------------------

    seven_segment_display # (w_digit) i_7segment
    (
        .clk      ( clk      ),
        .rst      ( rst      ),
        .number   ( counter  ),
        .dots     ( 0        ),
        .abcdefgh ( abcdefgh ),
        .digit    ( digit    )
    );
endmodule

