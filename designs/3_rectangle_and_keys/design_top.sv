// =============================================================================
// 3_rectangle_and_keys
// =============================================================================
//
// requires:
//   buttons >= 4
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
    //
    //  NOTE! Since the 9_maker_faire series of Verilog examples
    //  is for absolute beginners,
    //  we are using a simplified, more relaxed Verilog style
    //  that may have many lint issues. For example,
    //  we use "100" instead of "(100)" or "screen_width / 2".
    //
    //------------------------------------------------------------------------

    logic [9:0] w;

    always_comb
    begin
        red   = 0;
        green = 0;
        blue  = 0;

        if (x > 100 & x < 150 & y > 100 & y < 200)
        begin
            red   = btn [0] ? 30 : 0;
            green = btn [1] ? 30 : 0;
            blue  = btn [2] ? 30 : 0;
        end

        if (btn [3])
            w = 200;
        else
            w = 50;

        if (x > 200 & x < 200 + w & y > 0 & y < 100)
            green = 30;
    end
endmodule

