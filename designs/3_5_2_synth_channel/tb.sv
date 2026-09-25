// =============================================================================
// 3_5_2_synth_channel testbench
//   (slow_clk is derived inside design_top)
// =============================================================================

module tb;

    localparam clk_mhz = 50,
               w_btn   = 4,
               w_sw    = 4,
               w_led   = 8,
               w_digit = 8,
               w_gpio  = 100;

    //------------------------------------------------------------------------

    logic       clk;
    logic       rst;
    logic [3:0] btn;
    logic [3:0] sw;

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
            # 1 clk = ! clk;
    end

    //------------------------------------------------------------------------

    initial
    begin
        rst <= 'bx;
        repeat (2) @ (posedge clk);
        rst <= 1;
        repeat (2) @ (posedge clk);
        rst <= 0;
    end

    //------------------------------------------------------------------------

    always_comb
        btn = { {(w_btn - 1){1'b0}} , ~ rst};

    initial
    begin
        #0
        $dumpvars;

        #400000

        $finish;
    end

endmodule
