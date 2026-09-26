// =============================================================================
// 4_4_1_round_robin_arbiter
// =============================================================================
//

`define IMPLEMENTATION_1
// `define IMPLEMENTATION_2
// `define IMPLEMENTATION_3

module design_top
`include "design_top_interface.svh"


    //------------------------------------------------------------------------

    // assign led        = '0;
    // assign abcdefgh   = '0;
    // assign digit      = '0;
       assign red        = '0;
       assign green      = '0;
       assign blue       = '0;
       assign sound      = '0;
       assign uart_tx    = '1;
       assign rgb_r      = '0;
       assign rgb_g      = '0;
       assign rgb_b      = '0;

    //------------------------------------------------------------------------

    wire [7:0] req;

    generate
        if (w_btn >= 3)
        begin : use_keys
            assign req = 8' (btn);
        end
        else
        begin : use_keys_and_switches
            assign req = 8' ({ sw, btn });
        end
    endgenerate

    wire [7:0] gnt1, gnt2;

    assign led      = (gnt1);
    assign abcdefgh = gnt2;
    assign digit    = (gnt2);

    wire enable;

    //------------------------------------------------------------------------

    `ifdef IMPLEMENTATION_1

    // Generate a strobe signal 3 times a second

    strobe_gen
    # (.clk_mhz (clk_mhz), .strobe_hz (3))
    i_strobe_gen
    (
        .clk    ( clk    ),
        .rst    ( rst    ),
        .strobe ( enable )
    );

       arbiter_1_dumb_big_blob
    // arbiter_2_rotate_priority_rotate_verbose
    // arbiter_3_rotate_priority_case_rotate
    // arbiter_4_rotate_priority_3_assigns_rotate
    // arbiter_5_rotate_priority_rotate_brief
    arb1
    (
        .clk ( clk    ),
        .rst ( rst    ),
        .ena ( enable ),
        .req ( req    ),
        .gnt ( gnt1   )
    );

    // arbiter_1_dumb_big_blob
       arbiter_2_rotate_priority_rotate_verbose
    // arbiter_3_rotate_priority_case_rotate
    // arbiter_4_rotate_priority_3_assigns_rotate
    // arbiter_5_rotate_priority_rotate_brief
    arb2
    (
        .clk ( clk    ),
        .rst ( rst    ),
        .ena ( enable ),
        .req ( req    ),
        .gnt ( gnt2   )
    );

    `endif

    //------------------------------------------------------------------------

    `ifdef IMPLEMENTATION_2

    // New shorter System Verilog syntax

    strobe_gen # (.clk_mhz (clk_mhz), .strobe_hz (3))
    i_strobe_gen (.strobe (enable), .*);

    strobe_gen # (.width (24)) enable_src (.strobe (enable), .*);

    arbiter_3_rotate_priority_case_rotate arb1
        (.ena (enable), .gnt (gnt1), .*);

    arbiter_4_rotate_priority_3_assigns_rotate arb2
        (.ena (enable), .gnt (gnt2), .*);

    `endif

    //------------------------------------------------------------------------

    `ifdef IMPLEMENTATION_3

    // Passing signals by position - not recommended.
    // Can lead to difficult to debug bugs
    // in industrial code with many signals.

    strobe_gen # (.clk_mhz (clk_mhz), .strobe_hz (3))
    i_strobe_gen (clk, rst, enable);

    arbiter_1_dumb_big_blob
    arb1 (clk, rst, enable, req, gnt1);

    arbiter_5_rotate_priority_rotate_brief
    arb2 (clk, rst, enable, req, gnt2);

    `endif
endmodule

