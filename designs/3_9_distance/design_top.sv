// =============================================================================
// 3_9_distance
// =============================================================================
//
// requires:
//   seven_segment >= 1
//   gpio >= 2

module design_top
`include "design_top_interface.svh"

    // no RGB LEDs used
    assign rgb_r = '0;
    assign rgb_g = '0;
    assign rgb_b = '0;


    wire [7:0] distance;

    ultrasonic_distance_sensor
    # (
        .clk_frequency ( clk_mhz * 1000 * 1000 )
    )
    i_sensor
    (
        .clk,
        .rst,
        .trig              ( gpio [0] ),
        .echo              ( gpio [1] ),
        .relative_distance ( distance )
    );

    localparam w_display_number = w_digit * 4;

    seven_segment_display # (w_digit) i_7segment
    (
        .clk      ( clk                          ),
        .rst      ( rst                          ),
        .number   ( (distance) ),
        .dots     ( (0)                 ),
        .abcdefgh ( abcdefgh                     ),
        .digit    ( digit                        )
    );
endmodule

