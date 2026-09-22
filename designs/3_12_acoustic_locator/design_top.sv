// =============================================================================
// 3_12_acoustic_locator — auto-adapted by tools/adapt_designs.py from
//   basics-graphics-music/labs/3_music/3_12_acoustic_locator/lab_top.sv
// =============================================================================
//
// requires:
//   leds >= 4
//   seven_segment >= 1
//   screen >= 320x240
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


    //------------------------------------------------------------------------

    // microphone mic_1->right(2) mic_2->left(2) mic_3->bottom mic_4->top
    logic signed [12:0] mic_1, mic_2, mic_3, mic_4;
    logic               start, white, white_disp, red_disp, green_disp, blue_disp;
    logic        [ 3:0] min_index_h, min_index_v, av_index_h, av_index_v, counter;
    logic  [4:0] [w_x + w_y - 11:0] white_prev; // Repeating sound coordinates
    logic        [0:12] [31:0] data_rgb; // Data to be output to addressable LEDs
    wire         [15:0] vol;
    localparam          agc = 1'b0; // Enable automatic microphone level adjustment

    assign red     = {red_disp  , {w_red   - 1{white_disp}}}; //
    assign green   = {green_disp, {w_green - 1{white_disp}}}; // - color selection
    assign blue    = {blue_disp , {w_blue  - 1{white_disp}}}; //
    assign sound   = '0;
    assign uart_tx = '1;

    //------------------------------------------------------------------------

    // LEDs [3:0] are microphone overload
    // Adjusting level from the microphone to locator
    // The module is located in the common folder
    convert
    # (
        .w_in  ( 24        ),
        .w_out ( 13        ),
        .lev   ( 18        ),
        .agc   ( agc       )
    )
    i_convert_1
    (
        .clk   ( clk       ),
        .rst   ( rst       ),
        .in    ( mic_sample[1] +
                 mic_sample[2]    ),
        .out   ( mic_1     ),
        .led   ( led[0]    ),
        .vol   ( vol[2:0]  )
    );

    convert
    # (
        .w_in  ( 24        ),
        .w_out ( 13        ),
        .lev   ( 18        ),
        .agc   ( agc       )
    )
    i_convert_2
    (
        .clk   ( clk       ),
        .rst   ( rst       ),
        .in    ( mic_sample[4] +
                 mic_sample[5]    ),
        .out   ( mic_2     ),
        .led   ( led[1]    ),
        .vol   ( vol[6:4]  )
    );

    convert
    # (
        .w_in  ( 24        ),
        .w_out ( 13        ),
        .lev   ( 17        ),
        .agc   ( agc       )
    )
    i_convert_3
    (
        .clk   ( clk       ),
        .rst   ( rst       ),
        .in    ( mic_sample[3]    ),
        .out   ( mic_3     ),
        .led   ( led[2]    ),
        .vol   ( vol[10:8] )
    );

    convert
    # (
        .w_in  ( 24        ),
        .w_out ( 13        ),
        .lev   ( 17        ),
        .agc   ( agc       )
    )
    i_convert_4
    (
        .clk   ( clk       ),
        .rst   ( rst       ),
        .in    ( mic_sample[0]    ),
        .out   ( mic_4     ),
        .led   ( led[3]    ),
        .vol   ( vol[14:12])
    );

    // Display on an 8-bit display of the measured value at the top left
    // from 0 to F horizontally and vertically, the same thinned values
    // ​​and weakening of automatic gain control of each microphone
    always_ff @(posedge clk or posedge rst) begin
        if (rst) begin
            counter    <= '0;
            av_index_h <= '0;
            av_index_v <= '0;
        end
        else if (y > screen_height) begin
            start <= 1'b1;
            if (!start)
            counter <= counter + 1'b1;
        end
        else begin
            if (counter == 4'b1111) begin
            av_index_h <= min_index_h;
            av_index_v <= min_index_v;
            end
            start   <= 1'b0;
        end
    end

    // A dynamic seven-segment display
    // The module is located in the common folder
    seven_segment_display
    # (
        .w_digit  ( w_digit   ),
        .clk_mhz  ( clk_mhz   )
    )
    i_7segment
    (
        .clk      ( clk       ),
        .rst      ( rst       ),
        .number   ( {vol,
                  min_index_h,
                  min_index_v,
                  av_index_h,
                  av_index_v} ),
        .dots     (8'b01010101),
        .abcdefgh ( abcdefgh  ),
        .digit    ( digit     )
    );

    //------------------------------------------------------------------------

    // Determining direction by subtracting direct and delayed signals
    locator
    # (
        .clk_mhz     ( clk_mhz     )
    )
    i_locator
    (
        .clk         ( clk         ),
        .rst         ( rst         ),
        .start       ( start       ),
        .min_index_h ( min_index_h ),
        .min_index_v ( min_index_v ),
        .mic_1       ( mic_1       ),
        .mic_2       ( mic_2       ),
        .mic_3       ( mic_3       ),
        .mic_4       ( mic_4       )
    );

    // Drawing a position
    always_ff @(posedge clk or posedge rst) begin
        if (rst) begin
            white      <= 1'b0;
            red_disp   <= 1'b0;
            green_disp <= 1'b0;
            blue_disp  <= 1'b0;
            white_prev <= '0;
        end
        // When the end of line is reached repetitions are counted for filtering purposes
        else if ((y > screen_height) && !start) begin
            white_prev[1] <= white_prev[0];
                if (white_prev[1] == white_prev[0]) begin
                white_prev[2] <= white_prev[1];
                    if (white_prev[2] == white_prev[1]) begin
                    white_prev[3] <= white_prev[2];
                        if (white_prev[3] == white_prev[2])
                        white_prev[4] <= white_prev[3];
                    end
                end
        end
        else begin
            if (screen_width == 800) begin : screen_w_800
                // Determining the moment when sweep position coincides with measured value
                white <= (x[w_x - 1:5] == min_index_h + (min_index_h >> 1) +
                (min_index_h >> 3)) && (y[w_y - 1:5] == min_index_v - (min_index_v >> 3));
                blue_disp  <= white_prev[1] == {x[w_x - 1:5], y[w_y - 1:5]}; // Indication
                green_disp <= white_prev[2] == {x[w_x - 1:5], y[w_y - 1:5]}; // Indication
                red_disp   <= white_prev[3] == {x[w_x - 1:5], y[w_y - 1:5]}; // Indication
                white_disp <= white_prev[4] == {x[w_x - 1:5], y[w_y - 1:5]}; // Indication
                if (white && x[0]) // && x[0] prevents moving white_prev to the next pixel
                white_prev[0] <= {x[w_x - 1:5], y[w_y - 1:5]}; // Active position recording
            end
            else if (screen_width == 640) begin : screen_w_640
                white <= (x[w_x - 1:5] == min_index_h + (min_index_h >> 2) + (min_index_h >> 3)) &&
                         (y[w_y - 1:5] == min_index_v - (min_index_v >> 3));
                blue_disp  <= white_prev[1] == {x[w_x - 1:5], y[w_y - 1:5]};
                green_disp <= white_prev[2] == {x[w_x - 1:5], y[w_y - 1:5]};
                red_disp   <= white_prev[3] == {x[w_x - 1:5], y[w_y - 1:5]};
                white_disp <= white_prev[4] == {x[w_x - 1:5], y[w_y - 1:5]};
                if (white && x[0])
                white_prev[0] <= {x[w_x - 1:5], y[w_y - 1:5]};
            end
            else begin : screen_w_480
                white <= (x[w_x - 1:5] == min_index_h - (min_index_v >> 3)) &&
                         (y[w_y - 1:4] == min_index_v);
                blue_disp  <= white_prev[1] == {x[w_x - 1:5], y[w_y - 1:4]};
                green_disp <= white_prev[2] == {x[w_x - 1:5], y[w_y - 1:4]};
                red_disp   <= white_prev[3] == {x[w_x - 1:5], y[w_y - 1:4]};
                white_disp <= white_prev[4] == {x[w_x - 1:5], y[w_y - 1:4]};
                if (white && x[0])
                white_prev[0] <= {x[w_x - 1:5], y[w_y - 1:4]};
            end
        end
    end

    // Data to be output to addressable LEDs on Sipeed R6+1 Microphone Array Board and Dock
    // The module is located in the common folder
    led_strip_combo i_led_strip_combo
    (
        .clk         ( clk              ),
        .rst         ( rst              ),
        .data_rgb    ( data_rgb         ),
        .sk9822_clk  ( gpio[w_gpio - 1] ),
        .sk9822_data ( gpio[w_gpio - 2] )
    );

    // Sipeed R6+1 Microphone Array Board including 12 three-color LEDs
    assign data_rgb = {
    { 3'd7, 3'd0, {2{~| av_index_v[3:0]}},               24'h000011 }, // SK9822  U4  B G R
    { 3'd7, 4'd0, ~| av_index_v[3:1] |& av_index_h[3:2], 24'h001100 }, // SK9822  U5  B G R
    { 3'd7, 4'd0, ~| av_index_v[3:2] |& av_index_h[3:1], 24'h001100 }, // SK9822  U6  B G R
    { 3'd7, 3'd0, {2{ & av_index_h[3:0]}},               24'h000011 }, // SK9822  U9  B G R
    { 3'd7, 4'd0,  & av_index_v[3:2] |& av_index_h[3:1], 24'h001100 }, // SK9822  U10 B G R
    { 3'd7, 4'd0,  & av_index_v[3:1] |& av_index_h[3:2], 24'h001100 }, // SK9822  U11 B G R
    { 3'd7, 3'd0, {2{ & av_index_v[3:0]}},               24'h000011 }, // SK9822  U12 B G R
    { 3'd7, 4'd0, ~| av_index_h[3:2] |& av_index_v[3:1], 24'h001100 }, // SK9822  U15 B G R
    { 3'd7, 4'd0, ~| av_index_h[3:1] |& av_index_v[3:2], 24'h001100 }, // SK9822  U16 B G R
    { 3'd7, 3'd0, {2{~| av_index_h[3:0]}},               24'h000011 }, // SK9822  U17 B G R
    { 3'd7, 4'd0, ~| av_index_h[3:1]|~| av_index_v[3:2], 24'h001100 }, // SK9822  U18 B G R
    { 3'd7, 4'd0, ~| av_index_h[3:2]|~| av_index_v[3:1], 24'h001100 }, // SK9822  U3  B G R
    { 3'd7, 1'd0, {4{1'b1}},                           // Primer Dock LED WS2812
            4'd0, {4{green_disp}}, 4'd0, {4{red_disp}}, 4'd0, {4{blue_disp}} } // U17 G R B
    };
endmodule

