// A spirit level: the lit LED follows the board's tilt along X (an on-board
// accelerometer, or any acceleration provider).
//
// requires:
//   accelerometer
//   leds >= 2

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
    // A spirit level on the LEDs: one LED lit, its position following the tilt
    // along X (about -1 g at one end, +1 g at the other, level in the middle).
    // -------------------------------------------------------------------------

    logic signed [15:0] tilt;               // clamped to -1024 .. 1023 milli-g

    always_ff @ (posedge clk)
        if (rst)
            tilt <= '0;
        else if (acc_valid)
            tilt <= $signed (acc_x) < -16'sd1024 ? -16'sd1024
                  : $signed (acc_x) >  16'sd1023 ?  16'sd1023
                  : $signed (acc_x);

    // 0 .. 2047 above the low end, scaled to 0 .. w_led - 1
    wire [10:0] above = 11'(tilt + 16'sd1024);
    wire [31:0] index = (32'(above) * w_led) >> 11;

    always_comb
    begin
        led = '0;
        for (int i = 0; i < w_led; i++)
            if (index == i)
                led [i] = 1'b1;
    end

endmodule
