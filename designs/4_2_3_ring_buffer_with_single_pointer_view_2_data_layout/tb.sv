// =============================================================================
// 4_2_3_ring_buffer_with_single_pointer_view_2_data_layout testbench
//   (slow_clk is derived inside design_top)
// =============================================================================

module tb;

    localparam width = 8, depth = 5;

    //------------------------------------------------------------------------

    logic               clk;
    logic               rst;

    logic               in_valid;
    logic [width - 1:0] in_data;

    logic               out_valid;
    logic [width - 1:0] out_data;

    logic [depth - 1:0]              debug_valid;
    logic [depth - 1:0][width - 1:0] debug_data;

    //------------------------------------------------------------------------

    ring_buffer_with_single_pointer_and_debug_2
    # (
        .width (width),
        .depth (depth)
    )
    ring_buffer_with_single_pointer (.*);

    //------------------------------------------------------------------------

    initial
    begin
        clk = '0;
        forever #5 clk = ~ clk;
    end

    //------------------------------------------------------------------------

    initial
    begin
        $dumpvars;

        //--------------------------------------------------------------------
        // Initialization

        in_valid <= '0;

        //--------------------------------------------------------------------
        // Reset

        # 3 rst <= '1;
        repeat (5) @ (posedge clk);
        rst <= '0;

        //--------------------------------------------------------------------
        // Random stimuli

        repeat (100)
        begin
            in_valid <= $urandom ();
            in_data  <= $urandom ();

            @ (posedge clk);

            $write (".");

            if (in_valid)
                $write (" in: %h", in_data);
            else if (out_valid)
                $write ("       ");

            if (out_valid)
                $write (" out: %h", out_data);

            $display;
        end

        //--------------------------------------------------------------------
        $finish;
    end

endmodule
