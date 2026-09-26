// =============================================================================
// 2_3_color_stripes
// =============================================================================
//
// requires:
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

    wire [3:0] x4;

    generate
        if (w_x > 6)
        begin : wide_x
            assign x4 = x [6:3];
        end
        else
        begin
            assign x4 = x;
        end
    endgenerate

    //------------------------------------------------------------------------

    logic [3:0] red_4, green_4, blue_4;

    always_comb
    begin
        red_4   = '0;
        green_4 = '0;
        blue_4  = '0;

        // This should be removed after we finish with display_on in wrapper
        if (x < screen_width && y < screen_height)
        begin

                 if (x <     128)  red_4   = x4;

            else if (x < 2 * 128)  green_4 = x4;

            else if (x < 3 * 128)  blue_4  = x4;

            else if (x < 4 * 128)
            begin
                red_4   = x4;
                green_4 = x4;
            end

            else if (x < 5 * 128)
            begin
                green_4 = x4;
                blue_4  = x4;
            end
        end
    end


    generate
        if (w_red > 4 & w_green > 4 & w_blue > 4)
        begin : wide_rgb
            assign red   = { red_4   , { w_red   - 4 { 1'b0 } } };
            assign green = { green_4 , { w_green - 4 { 1'b0 } } };
            assign blue  = { blue_4  , { w_blue  - 4 { 1'b0 } } };
        end
        else
        begin : narrow_rgb
            assign red   = ( red_4   );
            assign green = ( green_4 );
            assign blue  = ( blue_4  );
        end
    endgenerate

endmodule

