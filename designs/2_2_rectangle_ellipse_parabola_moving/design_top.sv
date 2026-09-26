// =============================================================================
// 2_2_rectangle_ellipse_parabola_moving
// =============================================================================
//
// requires:
//   buttons >= 2
//   screen >= 320x240

module design_top
`include "design_top_interface.svh"


    //------------------------------------------------------------------------

       assign led        = '0;
       assign abcdefgh   = '0;
       assign digit      = '0;
    // assign red        = '0;
    // assign green      = '0;
    // assign blue       = '0;
       assign sound      = '0;
       assign uart_tx    = '1;
       assign rgb_r      = '0;
       assign rgb_g      = '0;
       assign rgb_b      = '0;

    //------------------------------------------------------------------------

    wire enable;

    // Generate a strobe signal 10 times a second

    strobe_gen
    # (.clk_mhz (clk_mhz), .strobe_hz (30))
    i_strobe_gen
    (.strobe (enable), .*);

    //------------------------------------------------------------------------

    wire inv_key_0 = ~ btn [0];
    wire inv_key_1 = ~ btn [1];

    logic [7:0] dx, dy;

    always_ff @ (posedge clk)
        if (rst)
        begin
            dx <= 4'b0;
            dy <= 4'b0;
        end
        else if (enable)
        begin
            dx <= dx + inv_key_0;
            dy <= dy + inv_key_1;
        end

    //------------------------------------------------------------------------

    wire [w_x * 2 - 1:0] x_2 = x * x;

    // These additional wires are needed
    // because some graphics interfaces have up to 10 bits per color channel

    wire [10:0] x11 = 11' (x);
    wire [ 9:0] y10 = 10' (y);

    always_comb
    begin
        red   = '0;
        green = '0;
        blue  = '0;

        if (   x + dx >= screen_width  / 2
             & x + dx <  screen_width  * 2 / 3
             & y      >= screen_height / 2
             & y      <  screen_height * 2 / 3 )
        begin
            if (btn [0])
                green = '1;
            else
                green = x11 [$left (x11) - 1 -: w_green];
        end

        if (x * x  + 2 * y * y  < (screen_width + dx) * (screen_width + dy) / 4)  // Ellipse
        begin
            red = x11 [$left (x11) - 1 -: w_red];
        end

        if (x_2 [w_x +: w_y] < y + dy)  // Parabola
            blue = btn [1] ? '1 : y10 [$left (y10) -: w_blue];
    end
endmodule

