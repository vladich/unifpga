// =============================================================================
// 9_demo
// =============================================================================
//
// requires:
//   buttons >= 2
//   seven_segment >= 1
//   screen >= 320x240

module design_top
`include "design_top_interface.svh"


    //------------------------------------------------------------------------

    // assign led        = '0;
    // assign abcdefgh   = '0;
    // assign digit      = '0;
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

    strobe_gen # (.clk_mhz (clk_mhz), .strobe_hz (30))
    i_strobe_gen (clk, rst, pulse);

    logic [7:0] c;

    always_ff @ (posedge clk or posedge rst)
        if (rst)
            c <= 0;
        else if (pulse)
            c <= c + 1 + btn [0] - btn [1];

    assign led = c;

    always_comb
    begin
        red = 0; green = 0; blue = 0;

        if (  x > 100 + c * 2 & x < 150 + c * 2
            & y > 100 + c     & y < 200 + c )
        begin
            red   = 30;
            blue  = btn [1] ? x : 0;
        end

        if ((x - c) ** 2 + y ** 2 < 100 ** 2)
            blue = 30;

        if (x * y > 100 ** 2)
            green = 10;
    end

    seven_segment_display # (w_digit) i_7segment
    (
        .clk      ( clk      ),
        .rst      ( rst      ),
        .number   ( c        ),
        .dots     ( 0        ),
        .abcdefgh ( abcdefgh ),
        .digit    ( digit    )
    );
endmodule

