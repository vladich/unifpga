// =============================================================================
// 3_6_answer_with_scale — auto-adapted by tools/adapt_designs.py from
//   basics-graphics-music/labs/3_music/3_6_answer_with_scale/lab_top.sv
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

    // ---- slow_clk derivation (auto-inserted by adapt_designs.py) -------------
    // Original basics-graphics-music labs received `slow_clk` as a port. The
    // uni-fpga virtual device doesn't expose one, so derive a ~1 Hz tick from
    // the system clock.
    // clk_mhz <= 1 is BGM's testbench setting (tb.sv passes clk as slow_clk):
    // a two-bit divider keeps the simulation short.
    localparam int W_SLOW_CLK_DIV = (clk_mhz > 1) ? $clog2(clk_mhz * 1_000_000) : 2;
    logic [W_SLOW_CLK_DIV - 1 : 0] slow_clk_div;
    logic                          slow_clk;
    always_ff @(posedge clk or posedge rst)
        if (rst) slow_clk_div <= '0;
        else     slow_clk_div <= slow_clk_div + 1'b1;
    assign slow_clk = slow_clk_div[W_SLOW_CLK_DIV - 1];



    //------------------------------------------------------------------------

    wire [7:0] mic_abcdefgh;

    //------------------------------------------------------------------------

    lab_top_3_1_note_recognizer
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
    i_lab_top_3_1_note_recognizer
    (
        .clk           ( clk           ),
        .slow_clk      (               ),
        .rst           ( rst           ),

        .btn           ( '0            ),
        .sw            ( '0            ),
        .led           (               ),

        .abcdefgh      ( mic_abcdefgh  ),
        .digit         (               ),

        .x             (               ),
        .y             (               ),

        .red           (               ),
        .green         (               ),
        .blue          (               ),

        .mic_sample           ( mic_sample           ),
        .sound         (               ),

        .uart_rx       (               ),
        .uart_tx       (               ),

        .gpio          (               )
    );

    //------------------------------------------------------------------------

    logic mic_on;
    logic [w_btn - 1:0] mic_note;

    always_comb
    begin
        mic_on   = 1'b1;
        mic_note = '0;  // 'x

        case (mic_abcdefgh)
        8'b10011100 : mic_note = (  0 );  // C   // abcdefgh
        8'b10011101 : mic_note = (  1 );  // C#
        8'b01111010 : mic_note = (  2 );  // D   //   --a--
        8'b01111011 : mic_note = (  3 );  // D#  //  |     |
        8'b10011110 : mic_note = (  4 );  // E   //  f     b
        8'b10001110 : mic_note = (  5 );  // F   //  |     |
        8'b10001111 : mic_note = (  6 );  // F#  //   --g--
        8'b10111100 : mic_note = (  7 );  // G   //  |     |
        8'b10111101 : mic_note = (  8 );  // G#  //  e     c
        8'b11101110 : mic_note = (  9 );  // A   //  |     |
        8'b11101111 : mic_note = ( 10 );  // A#  //   --d--  h
        8'b00111110 : mic_note = ( 11 );  // B
        default     : mic_on   = 1'b0;           // Not recognized
        endcase
    end

    //------------------------------------------------------------------------

    logic enable;

    strobe_gen # (.clk_mhz (clk_mhz), .strobe_hz (1))
    i_strobe_gen (clk, rst, enable);

    //------------------------------------------------------------------------

    logic [2:0] cnt;
    logic [w_btn - 1:0] out_note, next_out_note;

    always_comb
        case (cnt)

        3'd0, 3'd1, 3'd3, 3'd4, 3'd5:  // Tone

            next_out_note
               = out_note < (10)
               ? out_note + ( 2)
               : out_note - (10);

        default:  // 3'd2, 3'd6        // Half-tone

            next_out_note
               = out_note < (11)
               ? out_note + ( 1)
               :            ( 0);

        endcase

    //------------------------------------------------------------------------

    always_ff @ (posedge clk or posedge rst)
        if (rst)
        begin
            cnt      <= '0;
            out_note <= '0;
        end
        else if (mic_on)
        begin
            cnt      <= '0;
            out_note <= mic_note;
        end
        else if (enable)
        begin
            if (cnt == 3'd6)
                cnt <= '0;
            else
                cnt <= cnt + 1'd1;

            out_note <= next_out_note;
        end

    //------------------------------------------------------------------------

    lab_top_3_3_note_synthesizer
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
    i_lab_top_3_3_note_synthesizer
    (
        .clk           ( clk           ),
        .slow_clk      (               ),
        .rst           ( rst           ),

        .btn           ( out_note      ),
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

    //------------------------------------------------------------------------

    wire [w_red   - 1:0] rx = (x);
    wire [w_green - 1:0] gx = (x);
    wire [w_green - 1:0] gy = (y);
    wire [w_blue  - 1:0] by = (y);

    always_comb
    begin
        red   = '0;
        green = '0;
        blue  = '0;

        if (x > { out_note, 5'b0 } & y > { out_note, 4'b0 })
        begin
            case (4' (out_note))
            4'd0  : begin red = '1; green = '0; blue = '0; end
            4'd1  : begin red = '1; green = gx; blue = '0; end
            4'd2  : begin red = '1; green = '0; blue = by; end
            4'd3  : begin red = '1; green = gx; blue = by; end
            4'd4  : begin red = '0; green = '1; blue = '0; end
            4'd5  : begin red = rx; green = '1; blue = '0; end
            4'd6  : begin red = '0; green = '1; blue = by; end
            4'd7  : begin red = rx; green = '1; blue = by; end
            4'd8  : begin red = '0; green = '0; blue = '1; end
            4'd9  : begin red = rx; green = '0; blue = '1; end
            4'd10 : begin red = '0; green = gy; blue = '1; end
            4'd11 : begin red = rx; green = gy; blue = '1; end
            endcase
        end
    end
endmodule

