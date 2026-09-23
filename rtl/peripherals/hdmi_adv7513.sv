// =============================================================================
// hdmi_adv7513 — the parallel-RGB HDMI transmitter (Analog Devices ADV7513) of
// the Terasic DE10-Nano and Cyclone V GX Starter Kit: the `vga` timing
// generator runs from the board clock and derives the pixel clock, the 24-bit
// bus is the design's colours padded with ones, and i2c_reg_writer programs
// the transmitter after reset and after every hot-plug interrupt (on the
// C5GX it first programs the SSM2603 audio codec that shares the I2C bus).
//
//     assign HDMI_TX_CLK = pixel_clk;
//     assign HDMI_TX_D   = {{red,{(8 - w_red){1'b1}}}, {green,...}, {blue,...}};
//     assign HDMI_TX_DE  = display_on;  HDMI_TX_HS = hs;  HDMI_TX_VS = vs;
//     i2c_reg_writer # (.TABLE (...)) i_conf (.scl (HDMI_I2C_SCL), .sda (HDMI_I2C_SDA),
//         .restart (~ HDMI_TX_INT), ...);
// =============================================================================

module hdmi_adv7513
# (
    parameter CLK_MHZ      = 50,
              PIXEL_MHZ    = 25,
              H_DISPLAY    = 640,
              V_DISPLAY    = 480,
              W_RED        = 4,
              W_GREEN      = 4,
              W_BLUE       = 4,
              CONFIG_TABLE = "de10_nano"     // "de10_nano" (ADV7513 only) or "c5gx" (codec + ADV7513)
)
(
    input                  clk,
    input                  rst,
    input  [W_RED   - 1:0] red,
    input  [W_GREEN - 1:0] green,
    input  [W_BLUE  - 1:0] blue,
    output [          9:0] hpos,
    output [          9:0] vpos,
    output                 tx_clk,
    output                 tx_de,
    output                 tx_hs,
    output                 tx_vs,
    output [         23:0] tx_d,
    output                 i2c_scl,
    inout                  i2c_sda,
    input                  tx_int
);

    wire display_on;

    vga
    # (
        .H_DISPLAY ( H_DISPLAY ),
        .V_DISPLAY ( V_DISPLAY ),
        .CLK_MHZ   ( CLK_MHZ   ),
        .PIXEL_MHZ ( PIXEL_MHZ )
    )
    i_vga
    (
        .clk        ( clk        ),
        .rst        ( rst        ),
        .hsync      ( tx_hs      ),
        .vsync      ( tx_vs      ),
        .display_on ( display_on ),
        .hpos       ( hpos       ),
        .vpos       ( vpos       ),
        .pixel_clk  ( tx_clk     ),
        .red        ( '0         ),
        .green      ( '0         ),
        .blue       ( '0         ),
        .vga_r      (            ),
        .vga_g      (            ),
        .vga_b      (            )
    );

    assign tx_de = display_on;
    assign tx_d  = { red,   { (8 - W_RED)   { 1'b1 } },
                     green, { (8 - W_GREEN) { 1'b1 } },
                     blue,  { (8 - W_BLUE)  { 1'b1 } } };

    `include "wm8731_init_table.svh"

    // ADV7513 register writes (I2C address 0x72): the fixed values its
    // programming guide requires, 24-bit RGB 4:4:4 input with separate syncs,
    // HDMI mode, colour-space converter off, RGB in the AVI infoframe, audio
    // N = 6144 (48 kHz), interrupts cleared (0x96).
    localparam [31 * 24 - 1:0] HDMI_TABLE =
    {
        24'h72_9803, 24'h72_0100, 24'h72_0218, 24'h72_0300, 24'h72_1470, 24'h72_1520,
        24'h72_1630, 24'h72_1846, 24'h72_4080, 24'h72_4110, 24'h72_49A8, 24'h72_5510,
        24'h72_5608, 24'h72_96F6, 24'h72_7307, 24'h72_761F, 24'h72_9803, 24'h72_9902,
        24'h72_9AE0, 24'h72_9C30, 24'h72_9D61, 24'h72_A2A4, 24'h72_A3A4, 24'h72_A504,
        24'h72_AB40, 24'h72_AF16, 24'h72_BA60, 24'h72_D1FF, 24'h72_DE10, 24'h72_E460,
        24'h72_FA7D
    };

    // The C5GX's SSM2603 codec shares the bus: its table goes first, and the
    // transmitter's hot-plug interrupt (active low) rewrites only the ADV7513.
    generate
        if (CONFIG_TABLE == "c5gx") begin : g_c5gx

            i2c_reg_writer
            # (
                .CLK_MHZ    ( CLK_MHZ                           ),
                .N          ( WM8731_INIT_N + 31                ),
                .RESTART_AT ( WM8731_INIT_N                     ),
                .TABLE      ( { WM8731_INIT_TABLE, HDMI_TABLE } )
            )
            i_conf
            (
                .clk        ( clk      ),
                .rst        ( rst      ),
                .restart    ( ~ tx_int ),
                .scl        ( i2c_scl  ),
                .sda        ( i2c_sda  ),
                .done       (          )
            );

        end else begin : g_de10_nano

            i2c_reg_writer
            # (
                .CLK_MHZ    ( CLK_MHZ    ),
                .N          ( 31         ),
                .TABLE      ( HDMI_TABLE )
            )
            i_conf
            (
                .clk        ( clk      ),
                .rst        ( rst      ),
                .restart    ( ~ tx_int ),
                .scl        ( i2c_scl  ),
                .sda        ( i2c_sda  ),
                .done       (          )
            );

        end
    endgenerate

endmodule
