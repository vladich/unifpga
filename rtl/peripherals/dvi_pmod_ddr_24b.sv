// =============================================================================
// dvi_pmod_ddr_24b — the 1BitSquared DVI Pmod in its 24-bit mode on an iCE40
// board, exactly as BGM's icebreaker_dvi_24b_* board_specific_top.sv drives
// it: BGM's `vga` raster from the pixel clock, the 8-8-8 colours and syncs
// packed into a rising-edge and a falling-edge half, and sixteen SB_IO
// PIN_OUTPUT_DDR cells clocked by the pixel clock. The Pmod pins are the
// SB_IO pads, so this module is iCE40-only (SB_IO is a Lattice primitive;
// rtl/sim/vendor_stubs.sv models it for simulation and lint).
//
//     rising_edge_data  = { red[7],   red[5],   red[3],  red[1],
//                           red[6],   red[4],   red[2],  red[0],
//                           green[7], green[5], 1'b1,    hsync,
//                           green[6], green[4], display_on, vsync };
//     falling_edge_data = { green[3], green[1], blue[7], blue[5],
//                           green[2], green[0], blue[6], blue[4],
//                           blue[3],  blue[1],  1'b0,    hsync,
//                           blue[2],  blue[0],  display_on, vsync };
//     { P1A1 .. P1A10, P1B1 .. P1B10 } = ddr_data;   (MSB first)
// =============================================================================

module dvi_pmod_ddr_24b
# (
    parameter int clk_mhz   = 25,
    parameter int pixel_mhz = 25
)
(
    input               clk,          // the pixel clock
    input               rst,

    output logic [9:0]  x,
    output logic [9:0]  y,

    input        [7:0]  red_i,
    input        [7:0]  green_i,
    input        [7:0]  blue_i,

    // The 16 physical pins, header order: pmod_a[0] = P1A1 ... pmod_a[7] = P1A10,
    // pmod_b[0] = P1B1 ... pmod_b[7] = P1B10.
    output       [7:0]  pmod_a,
    output       [7:0]  pmod_b
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

    logic [15:0] rising_edge_data, falling_edge_data;

    always_ff @ (posedge clk) begin
        rising_edge_data <= {
            red_i   [7], red_i   [5], red_i [3], red_i [1],
            red_i   [6], red_i   [4], red_i [2], red_i [0],
            green_i [7], green_i [5], 1'b1,      hsync,
            green_i [6], green_i [4], display_on, vsync
        };
        falling_edge_data <= {
            green_i [3], green_i [1], blue_i [7], blue_i [5],
            green_i [2], green_i [0], blue_i [6], blue_i [4],
            blue_i  [3], blue_i  [1], 1'b0,       hsync,
            blue_i  [2], blue_i  [0], display_on, vsync
        };
    end

    // ddr_data [15] is P1A1 = pmod_a [0], ddr_data [0] is P1B10 = pmod_b [7]
    wire [15:0] ddr_data;

    genvar i;
    generate
        for (i = 0; i < 8; i++) begin : g_pins
            assign pmod_a [i] = ddr_data [15 - i];
            assign pmod_b [i] = ddr_data [ 7 - i];
        end
    endgenerate

    SB_IO
    # (
        .PIN_TYPE ( 6'b01_0000 )  // PIN_OUTPUT_DDR
    )
    dvi_ddr_iob [15:0]
    (
        .PACKAGE_PIN ( ddr_data          ),
        .D_OUT_0     ( rising_edge_data  ),
        .D_OUT_1     ( falling_edge_data ),
        .OUTPUT_CLK  ( clk               )
    );

endmodule
