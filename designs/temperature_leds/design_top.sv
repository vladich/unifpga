// The temperature on the LEDs: whole degrees Celsius, in binary (an on-board
// I2C temperature sensor, or any temperature provider).
//
// requires:
//   temperature
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
    // The latest reading in whole degrees (rounded down) on the LEDs, in binary:
    // 25.4 C lights 5'b11001; below 0 C the LEDs show two's complement.
    // -------------------------------------------------------------------------

    logic [11:0] degrees;

    always_ff @ (posedge clk)
        if (rst)
            degrees <= '0;
        else if (temp_valid)
            degrees <= temp [15:4];              // >>> 4: 1/16 C to whole degrees

    assign led = degrees;   // extended or cut to the rig's LEDs

endmodule
