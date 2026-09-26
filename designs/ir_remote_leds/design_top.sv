// An infrared remote's keys on the LEDs: the last key's NEC command byte
// (an on-board IR receiver, or any infrared-remote provider).
//
// requires:
//   ir_remote
//   leds >= 1

module design_top
`include "design_top_interface.svh"

    // -------------------------------------------------------------------------
    // Default tie-offs. Override below as needed.
    // -------------------------------------------------------------------------
    assign abcdefgh = '0;
    assign digit    = '0;
    assign rgb_r    = '0;
    assign rgb_g    = '0;
    assign rgb_b    = '0;
    assign red      = '0;
    assign green    = '0;
    assign blue     = '0;
    assign sound    = '0;
    assign uart_tx  = 1'b1;

    // -------------------------------------------------------------------------
    // The last key's command byte on the LEDs (the remote's key codes are in
    // its manual: on many NEC remotes the digits 0..9 are not in order).
    // -------------------------------------------------------------------------

    logic [7:0] key;

    always_ff @ (posedge clk)
        if (rst)
            key <= '0;
        else if (ir_valid)
            key <= ir_command;

    assign led = key;   // extended or cut to the rig's LEDs

endmodule
