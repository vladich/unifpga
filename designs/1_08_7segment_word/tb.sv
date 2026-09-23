// =============================================================================
// 1_08_7segment_word testbench
//   (slow_clk is derived inside design_top)
// =============================================================================

module tb;

    localparam clk_mhz = 1,
               w_btn   = 4,
               w_sw    = 8,
               w_led   = 8,
               w_digit = 8,
               w_gpio  = 100;

    //------------------------------------------------------------------------

    logic       clk;
    logic       rst;
    logic [3:0] btn;
    logic [7:0] sw;

    //------------------------------------------------------------------------

    design_top
    # (
        .clk_mhz ( clk_mhz ),
        .w_btn   ( w_btn   ),
        .w_sw    ( w_sw    ),
        .w_led   ( w_led   ),
        .w_digit ( w_digit ),
        .w_gpio  ( w_gpio  ),
        // parameter defaults the testbench relies on
        .screen_width  ( 640 ),
        .screen_height ( 480 ),
        .w_red         ( 4 ),
        .w_green       ( 4 ),
        .w_blue        ( 4 )
    )
    i_design_top
    (
        .clk      ( clk ),
        .rst      ( rst ),
        .btn      ( btn ),
        .sw       ( sw  )
    );

    //------------------------------------------------------------------------

    initial
    begin
        clk = 1'b0;

        forever
            # 5 clk = ~ clk;
    end

    //------------------------------------------------------------------------

    initial
    begin
        rst <= 1'bx;
        repeat (2) @ (posedge clk);
        rst <= 1'b1;
        repeat (2) @ (posedge clk);
        rst <= 1'b0;
    end

    //------------------------------------------------------------------------

    initial
    begin
        `ifdef __ICARUS__
            $dumpvars;
        `endif

        btn <= '0;
        sw  <= '0;

        @ (negedge rst);

        for (int i = 0; i < 50; i ++)
        begin
            // Enable override

            if (i == 20)
                force i_design_top.enable = 1'b1;
            else if (i == 40)
                release i_design_top.enable;

            @ (posedge clk);

            if (i >= 20 && i <= 40)
                btn <= $urandom_range (0, 1);
            else
                btn <= $urandom ();
        end

        $finish;
    end

endmodule
