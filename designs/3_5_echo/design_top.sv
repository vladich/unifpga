// =============================================================================
// 3_5_echo
// =============================================================================
//
// requires:
//   seven_segment >= 1
//   screen >= 320x240

module design_top
`include "design_top_interface.svh"

    // no RGB LEDs used
    assign rgb_r = '0;
    assign rgb_g = '0;
    assign rgb_b = '0;

    // ---- slow_clk derivation --------------------------------------------------
    // design_top has no `slow_clk` port, so derive a ~1 Hz tick from
    // the system clock.
    // clk_mhz <= 1 is the testbench setting (tb.sv passes clk as slow_clk):
    // a two-bit divider keeps the simulation short.
    localparam int W_SLOW_CLK_DIV = (clk_mhz > 1) ? $clog2(clk_mhz * 1_000_000) : 2;
    logic [W_SLOW_CLK_DIV - 1 : 0] slow_clk_div;
    logic                          slow_clk;
    always_ff @(posedge clk or posedge rst)
        if (rst) slow_clk_div <= '0;
        else     slow_clk_div <= slow_clk_div + 1'b1;
    assign slow_clk = slow_clk_div[W_SLOW_CLK_DIV - 1];



    //------------------------------------------------------------------------

    wire [7:0] intern_abcdefgh;

    //------------------------------------------------------------------------

    note_recognizer_3_1
    # (
        .clk_mhz       ( clk_mhz         ),
        .w_btn         ( w_btn           ),
        .w_sw          ( w_sw            ),
        .w_led         ( w_led           ),
        .w_digit       ( w_digit         ),
        .w_gpio        ( w_gpio          ),

        .screen_width  ( screen_width    ),
        .screen_height ( screen_height   ),

        .w_red         ( w_red           ),
        .w_green       ( w_green         ),
        .w_blue        ( w_blue          )
    )
    i_note_recognizer_3_1
    (
        .clk           ( clk             ),
        .slow_clk      (                 ),
        .rst           ( rst             ),

        .btn           ( '0              ),
        .sw            ( '0              ),
        .led           (                 ),

        .abcdefgh      ( intern_abcdefgh ),
        .digit         (                 ),

        .x             (                 ),
        .y             (                 ),

        .red           (                 ),
        .green         (                 ),
        .blue          (                 ),

        .mic_sample           ( mic_sample             ),
        .sound         (                 ),

        .uart_rx       (                 ),
        .uart_tx       (                 ),

        .gpio          (                 )
    );

    //------------------------------------------------------------------------

    logic [w_btn - 1:0] intern_key;

    always @ (posedge clk)
        if (rst)
            intern_key <= '0;
        else
            case (intern_abcdefgh)
            8'b10011100 : intern_key <= (  0 );  // C   // abcdefgh
            8'b10011101 : intern_key <= (  1 );  // C#
            8'b01111010 : intern_key <= (  2 );  // D   //   --a--
            8'b01111011 : intern_key <= (  3 );  // D#  //  |     |
            8'b10011110 : intern_key <= (  4 );  // E   //  f     b
            8'b10001110 : intern_key <= (  5 );  // F   //  |     |
            8'b10001111 : intern_key <= (  6 );  // F#  //   --g--
            8'b10111100 : intern_key <= (  7 );  // G   //  |     |
            8'b10111101 : intern_key <= (  8 );  // G#  //  e     c
            8'b11101110 : intern_key <= (  9 );  // A   //  |     |
            8'b11101111 : intern_key <= ( 10 );  // A#  //   --d--  h
            8'b00111110 : intern_key <= ( 11 );  // B
            8'b00000010 :                            ;  // Previous one
            endcase

    //------------------------------------------------------------------------

    note_synthesizer_3_3
    # (
        .clk_mhz       ( clk_mhz       ),
        .w_btn         ( w_btn         ),
        .w_sw          ( w_sw          ),
        .w_led         ( w_led         ),
        .w_digit       ( w_digit       ),
        .w_gpio        ( w_gpio        ),

        .screen_width  ( screen_width  ),
        .screen_height ( screen_height ),

        .w_red         ( w_red         ),
        .w_green       ( w_green       ),
        .w_blue        ( w_blue        )
    )
    i_note_synthesizer_3_3
    (
        .clk           ( clk           ),
        .slow_clk      (               ),
        .rst           ( rst           ),

        .btn           ( intern_key    ),
        .sw            ( '0            ),
        .led           (               ),

        .abcdefgh      ( abcdefgh      ),
        .digit         ( digit         ),

        .x             (               ),
        .y             (               ),

        .red           (               ),
        .green         (               ),
        .blue          (               ),

        .mic_sample           (               ),
        .sound         ( sound         ),

        .uart_rx       (               ),
        .uart_tx       (               ),

        .gpio          (               )
    );
endmodule

