// =============================================================================
// 8_control_using_keys
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

    strobe_gen
    # (
        .clk_mhz   ( clk_mhz ),
        .strobe_hz ( 30      )
    )
    i_strobe_gen (clk, rst, pulse);

    //------------------------------------------------------------------------

    logic [7:0] dx, dy;

    always_ff @ (posedge clk or posedge rst)
        if (rst)
        begin
            dx <= 0;
            dy <= 0;
        end
        else if (pulse)
        begin
            if (btn [0])
                dx <= dx - 1;
            else
                dx <= dx + 1;

            if (btn [1])
                dy <= dy - 1;
            else
                dy <= dy + 1;
        end

    assign led = (dx);

    //------------------------------------------------------------------------

    always_comb
    begin
        red   = 0;
        green = 0;
        blue  = 0;

        if (  x > 100 + dx
            & x < 150 + dx
            & y > 100 + dy
            & y < 200 + dy )
        begin
            red = 30;
        end

        if (  x > 100 + dx * 2
            & x < 150 + dx * 2
            & y > 100 + dy
            & y < 200 + dy     )
        begin
            green = 30;
        end
    end

    //------------------------------------------------------------------------

    seven_segment_display # (w_digit) i_7segment
    (
        .clk      ( clk      ),
        .rst      ( rst      ),
        .number   ( counter  ),
        .dots     ( 0        ),
        .abcdefgh ( abcdefgh ),
        .digit    ( digit    )
    );
endmodule

