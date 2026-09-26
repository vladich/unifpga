// =============================================================================
// 6_rectangles_moving_using_pulse
// =============================================================================
//
// requires:
//   screen >= 320x240

module design_top
`include "design_top_interface.svh"


    //------------------------------------------------------------------------

    // assign led        = '0;
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
endmodule

