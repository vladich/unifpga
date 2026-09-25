// =============================================================================
// 5_5_aps testbench
//   a 50 MHz clock: the design divides it to 10 MHz (the CPU) and 25 MHz (VGA),
//   so it must be a multiple of both
// =============================================================================

module tb;

    localparam clk_mhz = 50,
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

    initial begin
        clk = 1'b0;
        forever begin
            #10;                                 // 20 ns: 50 MHz
            clk = ~clk;
        end
    end

    initial begin
        #10;
        rst = 1;
        #10;
        rst = 0;
    end

    initial
    begin
        $dumpvars;

        # 2_000;

        $finish;
    end

endmodule
