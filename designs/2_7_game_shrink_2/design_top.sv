// =============================================================================
// 2_7_game_shrink_2
// =============================================================================
//
// requires:
//   buttons >= 2
//   screen >= 320x240

`include "game_config.svh"

module design_top
`include "design_top_interface.svh"


    //------------------------------------------------------------------------

       assign led        = '0;
       assign abcdefgh   = '0;
       assign digit      = '0;
    // assign red        = '0;
    // assign green      = '0;
    // assign blue       = '0;
       assign sound      = '0;
       assign uart_tx    = '1;
       assign rgb_r      = '0;
       assign rgb_g      = '0;
       assign rgb_b      = '0;

    //------------------------------------------------------------------------

    wire [`GAME_RGB_WIDTH - 1:0] rgb;

    game_top
    # (
        .clk_mhz                           (clk_mhz                          ),
        .pixel_mhz                         (pixel_mhz                        ),
        .screen_width                      (screen_width  * 2                ),
        .screen_height                     (screen_height * 2                ),
        .strobe_to_update_xy_counter_width (strobe_to_update_xy_counter_width)
    )
    i_game_top
    (
        .clk              (   clk                ),
        .rst              (   rst                ),

        .launch_key       ( | btn                ),
        .left_right_keys  ( { btn [1], btn [0] } ),

        .display_on       (   display_on         ),

        .x                (   { x, 1'b0 }        ),
        .y                (   { y, 1'b0 }        ),

        .rgb              (   rgb                )
    );

    assign red   = { w_red   { rgb [2] } };
    assign green = { w_green { rgb [1] } };
    assign blue  = { w_blue  { rgb [0] } };
endmodule

