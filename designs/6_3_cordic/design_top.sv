// =============================================================================
// 6_3_cordic
// =============================================================================
//
// requires:
//   buttons >= 1
//   seven_segment >= 1

module design_top
`include "design_top_interface.svh"


    //------------------------------------------------------------------------

       assign led        = '0;
    // assign abcdefgh   = '0;
    // assign digit      = '0;
       assign red        = '0;
       assign green      = '0;
       assign blue       = '0;
       assign sound      = '0;
       assign uart_tx    = '1;
       assign rgb_r      = '0;
       assign rgb_g      = '0;
       assign rgb_b      = '0;

    //------------------------------------------------------------------------

    logic key_0_r;

    always_ff @ (posedge clk)
        if (rst) key_0_r <= 1'b0;
        else     key_0_r <= btn [0];

    wire press_key_0 = ~ key_0_r & btn [0];

    //------------------------------------------------------------------------

    wire               start = press_key_0;
    logic       [15:0] angle;

    wire               calc;
    wire               finish;

    wire signed [15:0] cos_out;
    wire signed [15:0] sin_out;

    cordic i_cordic
    (
        .clk     ( clk ),
        .rst     ( rst ),

        .start   ( start ),
        .angle   ( angle ),

        .calc    ( calc ),
        .finish  ( finish ),

        .cos_out ( cos_out ),
        .sin_out ( sin_out )
    );

    //------------------------------------------------------------------------

    localparam angle_array_index_width = 4,
               angle_array_length      = 1 << angle_array_index_width;


    logic [15:0] angle_const_array [0:angle_array_length - 1];

        assign angle_const_array [ 0] = 16'h0000; //  0 degrees
        assign angle_const_array [ 1] = 16'h0444; //  6 degrees
        assign angle_const_array [ 2] = 16'h0889; // 12 degrees
        assign angle_const_array [ 3] = 16'h0ccd; // 18 degrees
        assign angle_const_array [ 4] = 16'h1111; // 24 degrees
        assign angle_const_array [ 5] = 16'h1555; // 30 degrees
        assign angle_const_array [ 6] = 16'h199a; // 36 degrees
        assign angle_const_array [ 7] = 16'h1dde; // 42 degrees
        assign angle_const_array [ 8] = 16'h2222; // 48 degrees
        assign angle_const_array [ 9] = 16'h2666; // 54 degrees
        assign angle_const_array [10] = 16'h2aab; // 60 degrees
        assign angle_const_array [11] = 16'h2eef; // 66 degrees
        assign angle_const_array [12] = 16'h3333; // 72 degrees
        assign angle_const_array [13] = 16'h3777; // 78 degrees
        assign angle_const_array [14] = 16'h3bbc; // 84 degrees
        assign angle_const_array [15] = 16'h4000; // 90 degrees


    //------------------------------------------------------------------------

    wire [angle_array_index_width - 1:0] angle_index;
    wire accept_start = start & ~calc;

    counter_with_enable
    # (angle_array_index_width)
    i_counter
    (
        .clk    ( clk ),
        .rst    ( rst ),
        .enable ( accept_start ),
        .cnt    ( angle_index )
    );

    assign angle = angle_const_array [angle_index];

    //------------------------------------------------------------------------

    logic [15:0] angle_sticky;
    logic [15:0] sin_out_sticky;

    always_ff @ (posedge clk)
    begin
        if (rst)
        begin
            angle_sticky   <= '0;
            sin_out_sticky <= '0;
        end
        else
        begin
            if (accept_start)
                angle_sticky <= angle;

            if (finish)
                sin_out_sticky <= sin_out;
        end
    end

    seven_segment_display # (w_digit) i_display
    (
        .clk,
        .rst,
        .number ({ angle_sticky, sin_out_sticky }),
        .dots ('0),
        .abcdefgh,
        .digit
    );
endmodule

