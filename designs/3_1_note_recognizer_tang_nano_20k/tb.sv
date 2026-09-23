// =============================================================================
// 3_1_note_recognizer_tang_nano_20k testbench
//   (slow_clk is derived inside design_top)
// =============================================================================

// This testbench is for microphone module only

module tb;

    logic       clk;
    logic       rst;
    wire        lr;
    wire        ws;
    wire        sck;
    logic       sd;
    wire [23:0] value;

    //------------------------------------------------------------------------

    inmp441_mic_i2s_receiver dut (.*);

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

        @ (negedge rst);

        repeat (100000)
        begin
            sd <= $urandom ();
            @ (posedge clk);
        end

        $finish;
    end

endmodule

//----------------------------------------------------------------------------

`ifdef UNDEFINED

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

        repeat (50)
        begin
            @ (posedge clk);

            btn <= $urandom ();
            sw  <= $urandom ();
        end

        $finish;
    end

endmodule

`endif
