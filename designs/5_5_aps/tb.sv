// =============================================================================
// 5_5_aps testbench — auto-adapted by tools/adapt_designs.py --testbenches from
//   basics-graphics-music/labs/5_cpu/5_5_aps/tb.sv
//   (lab_top -> design_top, key -> btn; slow_clk is derived inside design_top)
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
        // defaults of BGM's lab_top the testbench relies on
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
            #5;
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
        `ifdef __ICARUS__
            $dumpvars;
        `endif

        # 2_000;

        $finish;
    end

endmodule
