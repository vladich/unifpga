// =============================================================================
// dvi_pmod_12b — the 1BitSquared DVI Pmod in its 12-bit mode on an iCE40
// board, exactly as BGM's icebreaker_dvi_12b_* board_specific_top.sv (the
// DVI_12B branch of the 24b top) drives it: BGM's `vga` raster from the pixel
// clock, the 4-4-4 colours, syncs and data enable through SB_IO
// PIN_OUTPUT_REGISTERED cells clocked by the pixel clock, and the Pmod clock
// pin as an SB_IO PIN_OUTPUT_DDR cell with D_OUT_0 = 0 / D_OUT_1 = 1 (the
// pin is low after the rising edge and high after the falling edge, an
// inverted pixel clock with the same clock-to-out as the data). iCE40-only:
// SB_IO is a Lattice primitive (rtl/sim/vendor_stubs.sv models it).
// =============================================================================

module dvi_pmod_12b
# (
    parameter int clk_mhz   = 25,
    parameter int pixel_mhz = 25
)
(
    input               clk,          // the pixel clock
    input               rst,

    output logic [9:0]  x,
    output logic [9:0]  y,

    input        [3:0]  red_i,
    input        [3:0]  green_i,
    input        [3:0]  blue_i,

    output       [3:0]  r,
    output       [3:0]  g,
    output       [3:0]  b,
    output              hs,
    output              vs,
    output              de,
    output              ck
);

    logic hsync, vsync, display_on;

    vga
    # (
        .HPOS_WIDTH ( 10        ),
        .VPOS_WIDTH ( 10        ),
        .CLK_MHZ    ( pixel_mhz ),
        .PIXEL_MHZ  ( pixel_mhz )
    )
    i_vga
    (
        .clk        ( clk        ),
        .rst        ( rst        ),
        .hsync      ( hsync      ),
        .vsync      ( vsync      ),
        .display_on ( display_on ),
        .hpos       ( x          ),
        .vpos       ( y          ),
        .pixel_clk  (            ),
        .red        ( '0         ),
        .green      ( '0         ),
        .blue       ( '0         ),
        .vga_r      (            ),
        .vga_g      (            ),
        .vga_b      (            )
    );

    // BGM: dvi_data = { red [3], red [1], green [3], green [1], red [2], red [0],
    //                   green [2], green [0], blue [3], blue [1], hsync, blue [2],
    //                   blue [0], display_on, vsync } on P1A1 .. P1B10 minus P1B2;
    // here each pin keeps its own name, the registered output stage is the same.
    wire [14:0] dvi_data = { red_i [3:0], green_i [3:0], blue_i [3:0], hsync, vsync, display_on };
    wire [14:0] pins;

    assign r  = pins [14:11];
    assign g  = pins [10:7];
    assign b  = pins [6:3];
    assign hs = pins [2];
    assign vs = pins [1];
    assign de = pins [0];

    SB_IO
    # (
        .PIN_TYPE ( 6'b01_0100 )  // PIN_OUTPUT_REGISTERED
    )
    dvi_data_iob [14:0]
    (
        .PACKAGE_PIN ( pins     ),
        .D_OUT_0     ( dvi_data ),
        .OUTPUT_CLK  ( clk      )
    );

    SB_IO
    # (
        .PIN_TYPE ( 6'b01_0000 )  // PIN_OUTPUT_DDR
    )
    dvi_clk_iob
    (
        .PACKAGE_PIN ( ck   ),
        .D_OUT_0     ( 1'b0 ),
        .D_OUT_1     ( 1'b1 ),
        .OUTPUT_CLK  ( clk  )
    );

endmodule
