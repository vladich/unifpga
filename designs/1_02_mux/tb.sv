// =============================================================================
// 1_02_mux testbench — auto-adapted by tools/adapt_designs.py --testbenches from
//   basics-graphics-music/labs/1_basics/1_02_mux/tb.sv
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

    logic               clk;
    logic               rst;
    logic [w_btn - 1:0] btn;
    logic [w_sw  - 1:0] sw;
    wire  [w_led - 1:0] led;

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
        .sw       ( sw  ),
        .led      ( led )
    );

    //------------------------------------------------------------------------

    task check (input sel, a, b);

        logic result, expected;
        int n_muxes_to_check;

        // Back-box testing - checking the output

        result   = led [0];
        expected = sel ? a : b;

        if (result !== expected)
            $display ("Mismatch: %b ? %b : %b. expected: %b actual: %b",
                sel, a, b, expected, result);

        //--------------------------------------------------------------------
        // Checking multiple bits

        if ($bits (i_design_top.all_muxes) < $bits (led))
            n_muxes_to_check = $bits (i_design_top.all_muxes);
        else
            n_muxes_to_check = $bits (led);

        for (int i = 0; i < n_muxes_to_check; i ++)
        begin
            result = led [i];

            if (result !== expected)
                $display ("Mismatch in led bit %0d: %b ? %b : %b. expected: %b actual: %b",
                    i, sel, a, b, expected, result);
        end

        //--------------------------------------------------------------------
        // White-box testing - checking XMR (external module reference)

        for (int i = 0; i < $bits (i_design_top.all_muxes); i ++)
        begin
            result = i_design_top.all_muxes [i];

            if (result !== expected)
                $display ("Mismatch in mux %0d: %b ? %b : %b. expected: %b actual: %b",
                    i, sel, a, b, expected, result);
        end

    endtask

    //------------------------------------------------------------------------

    // The stimulus generation

    logic sel, a, b;

    initial
    begin
        `ifdef __ICARUS__
            $dumpvars;
        `endif

        // Exhaustive direct testing aka brute force testing

        for (int isel = 0; isel <= 1; isel ++)
        for (int ia   = 0; ia   <= 1; ia   ++)
        for (int ib   = 0; ib   <= 1; ib   ++)
        begin
             sel = 1' ( isel );
             a   = 1' ( ia   );
             b   = 1' ( ib   );

             btn <= w_btn' ({ sel, a, b });
             sw  <= $urandom ();  // The result should not depend on sw

             # 10

             check (sel, a, b);
        end

        // Another way of doing it

        for (int i = 0; i < 8; i ++)
        begin
             btn <= w_btn'   (i);
             sw  <= $urandom ();

             # 10

             sel = btn [2];
             a   = btn [1];
             b   = btn [0];

             check (sel, a, b);
        end

        // Randomized testing

        repeat (8)
        begin
             btn <= $urandom ();
             sw  <= $urandom ();

             # 10

             sel = btn [2];
             a   = btn [1];
             b   = btn [0];

             check (sel, a, b);
        end

        $finish;
    end

endmodule
