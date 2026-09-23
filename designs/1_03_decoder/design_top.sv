// =============================================================================
// 1_03_decoder
// =============================================================================
//
// requires:
//   buttons >= 2

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
       assign abcdefgh   = '0;
       assign digit      = '0;
       assign red        = '0;
       assign green      = '0;
       assign blue       = '0;
       assign sound      = '0;
       assign uart_tx    = '1;

    //------------------------------------------------------------------------

    wire [1:0] in  = { btn [1], btn [0] };

    //------------------------------------------------------------------------

    // Implementation 1: tedious

    wire [3:0] dec0;

    assign dec0 [0] = ~ in [1] & ~ in [0];
    assign dec0 [1] = ~ in [1] &   in [0];
    assign dec0 [2] =   in [1] & ~ in [0];
    assign dec0 [3] =   in [1] &   in [0];

    // Implementation 2: case

    logic [3:0] dec1;

    always_comb
        case (in)
        2'b00: dec1 = 4'b0001;
        2'b01: dec1 = 4'b0010;
        2'b10: dec1 = 4'b0100;
        2'b11: dec1 = 4'b1000;
        endcase

    // Implementation 3: shift

    wire [3:0] dec2 = 4'b0001 << in;

    // Implementation 4: index

    logic [3:0] dec3;

    always_comb
    begin
        dec3 = '0;
        dec3 [in] = 1'b1;
    end

    //------------------------------------------------------------------------

    assign led = ({ dec0, dec1, dec2, dec3 });
endmodule

