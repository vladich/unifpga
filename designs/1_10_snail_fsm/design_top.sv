// =============================================================================
// 1_10_snail_fsm
// =============================================================================
//

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

    wire enable;
    wire fsm_in, moore_fsm_out, mealy_fsm_out;

    // Generate a strobe signal 3 times a second

    strobe_gen
    # (.clk_mhz (clk_mhz), .strobe_hz (3))
    i_strobe_gen
    (.strobe (enable), .*);

    shift_reg # (.depth (w_led)) i_shift_reg
    (
        .en      (   enable ),
        .seq_in  ( | btn    ),
        .seq_out (   fsm_in ),
        .par_out (   led    ),
        .*
    );

    snail_moore_fsm i_moore_fsm
        (.en (enable), .a (fsm_in), .y (moore_fsm_out), .*);

    snail_mealy_fsm i_mealy_fsm
        (.en (enable), .a (fsm_in), .y (mealy_fsm_out), .*);

    //------------------------------------------------------------------------

    //   --a--
    //  |     |
    //  f     b
    //  |     |
    //   --g--
    //  |     |
    //  e     c
    //  |     |
    //   --d--  h

    always_comb
    begin
        case ({ mealy_fsm_out, moore_fsm_out })
        2'b00: abcdefgh = 8'b0000_0000;
        2'b01: abcdefgh = 8'b1100_0110;  // Moore only
        2'b10: abcdefgh = 8'b0011_1010;  // Mealy only
        2'b11: abcdefgh = 8'b1111_1110;
        endcase

        digit = (1);
    end

    // Exercise: Implement FSM for recognizing other sequence,
    // for example 0101
endmodule

