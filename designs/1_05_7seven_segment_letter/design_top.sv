// =============================================================================
// 1_05_7seven_segment_letter
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

       assign led        = '0;
    // assign abcdefgh   = '0;
    // assign digit      = '0;
       assign red        = '0;
       assign green      = '0;
       assign blue       = '0;
       assign sound      = '0;
       assign uart_tx    = '1;

    //------------------------------------------------------------------------

    //   --a--
    //  |     |
    //  f     b
    //  |     |
    //   --g--
    //  |     |
    //  e     c
    //  |     |
    //   --d--  h

    typedef enum bit [7:0]
    {
        F     = 8'b1000_1110,
        P     = 8'b1100_1110,
        G     = 8'b1011_1100,
        A     = 8'b1110_1110,
        space = 8'b0000_0000
    }
    seven_seg_encoding_e;

    assign abcdefgh = btn [0] ? P : F;
    assign digit    = (btn [1] ? 2'b10 : 2'b01);

    // Exercise 1: Display the first letters
    // of your first name and last name instead.

    // assign abcdefgh = ...
    // assign digit    = ...

    // Exercise 2: Display letters of a 4-character word
    // using this code to display letter of FPGA as an example

    /*
    seven_seg_encoding_e letter;

    always_comb
      case (4' (btn))
      4'b1000: letter = F;
      4'b0100: letter = P;
      4'b0010: letter = G;
      4'b0001: letter = A;
      default: letter = space;
      endcase

    assign abcdefgh = letter;
    assign digit    = (btn);
    */
endmodule

