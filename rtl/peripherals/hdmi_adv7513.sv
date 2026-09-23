// =============================================================================
// hdmi_adv7513 — the parallel-RGB HDMI transmitter (Analog Devices ADV7513) of
// the Terasic DE10-Nano and Cyclone V GX Starter Kit: the `vga` timing
// generator runs from the board clock and derives the pixel clock, the 24-bit
// bus is the design's colours padded with ones, and Terasic's I2C
// configuration module programs the transmitter once after reset (the C5GX
// table also programs the SSM2603 audio codec that shares the I2C bus).
//
//     assign HDMI_TX_CLK = pixel_clk;
//     assign HDMI_TX_D   = {{red,{(8 - w_red){1'b1}}}, {green,...}, {blue,...}};
//     assign HDMI_TX_DE  = display_on;  HDMI_TX_HS = hs;  HDMI_TX_VS = vs;
//     I2C_HDMI_Config i_i2c_hdmi_conf (.iCLK (clk), .iRST_N (~ rst),
//         .I2C_SCLK (HDMI_I2C_SCL), .I2C_SDAT (HDMI_I2C_SDA), .HDMI_TX_INT (HDMI_TX_INT));
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

    generate
        if (CONFIG_TABLE == "c5gx") begin : g_c5gx

            I2C_HDMI_Config_c5gx i_conf
            (
                .iCLK        ( clk     ),
                .iRST_N      ( ~ rst   ),
                .I2C_SCLK    ( i2c_scl ),
                .I2C_SDAT    ( i2c_sda ),
                .HDMI_TX_INT ( tx_int  ),
                .READY       (         )
            );

        end else begin : g_de10_nano

            I2C_HDMI_Config i_conf
            (
                .iCLK        ( clk     ),
                .iRST_N      ( ~ rst   ),
                .I2C_SCLK    ( i2c_scl ),
                .I2C_SDAT    ( i2c_sda ),
                .HDMI_TX_INT ( tx_int  ),
                .READY       (         )
            );

        end
    endgenerate

endmodule
