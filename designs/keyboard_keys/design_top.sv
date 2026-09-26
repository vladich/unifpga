// Keyboard keys on the LEDs: the HID usage code of the key held down (a PS/2
// keyboard on a board's PS/2 port, or any keyboard provider).
//
// requires:
//   keyboard
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
    // The last key pressed, while it is held: its HID usage code on the LEDs
    // (A = 8'h04 lights LED 2), dark once it is released.
    // -------------------------------------------------------------------------

    logic [7:0] held;

    always_ff @ (posedge clk)
        if (rst)
            held <= '0;
        else if (kbd_valid)
            held <= kbd_down ? kbd_key : (kbd_key == held ? 8'h00 : held);

    assign led = held;   // extended or cut to the rig's LEDs

endmodule
