// =============================================================================
// 4_1_3_pow5_pipelined_valid testbench
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

    initial
    begin
        `ifdef __ICARUS__
            $dumpvars;
        `endif
        btn[3:1] = '0;

        repeat (32)
        begin
             # 10
             sw  <= $urandom ();
             btn[0] <= $urandom ();
        end

        $finish;
    end

endmodule
