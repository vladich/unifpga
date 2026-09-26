// Analog input 0 on the LEDs: the top bits of its 12-bit code (an on-board
// A/D converter, or any analog-input provider).
//
// requires:
//   adc
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
    // Channel 0 on the LEDs: its code's top bits (a bar graph's worth of it:
    // all LEDs dark at 0 V, the top one lit from half the full scale up).
    // -------------------------------------------------------------------------

    logic [11:0] level;

    always_ff @ (posedge clk)
        if (rst)
            level <= '0;
        else if (adc_valid && adc_channel == 4'd0)
            level <= adc_value;

    always_comb
    begin
        led = '0;
        for (int i = 0; i < w_led && i < 12; i++)
            led [w_led - 1 - i] = level [11 - i];
    end

endmodule
